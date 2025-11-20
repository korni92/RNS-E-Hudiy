#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# Audi DIS (Cluster) DDP Protocol Driver - V2.6
#
# Changes in V2.6:
# - ADDED Inter-Block Pacing: Implemented a 20ms delay after every successful
#   block ACK for White DIS clusters. This mimics RNS-E behavior ("piece by piece")
#   and prevents buffer overruns when sending rapid updates (scrolling) or
#   large payloads (bitmaps) that span multiple blocks.
#
import time
import logging
import can
from typing import List, Optional
from enum import Enum, auto

# Get the logger for this module
logger = logging.getLogger(__name__)

# --- Custom Exceptions ---

class DDPError(Exception):
    """Base exception for DDP errors."""
    pass

class DDPCANError(DDPError):
    """Error related to CAN bus setup or communication."""
    pass

class DDPAckTimeoutError(DDPError):
    """Raised when an ACK is not received in time."""
    pass

class DDPHandshakeError(DDPError):
    """Raised when a handshake or initialization step fails."""
    pass


# --- Protocol State & Mode ---

class DDPState(Enum):
    """Defines the connection state."""
    DISCONNECTED = auto()
    SESSION_ACTIVE = auto()  # Keep-Alive (A-packets) session is open
    INITIALIZING = auto()    # DDP (B/1x/2x packets) handshake in progress
    READY = auto()           # Ready to send/receive data frames
    PAUSED = auto()          # Cluster claimed screen (Warning/Menu)

class DisMode(Enum):
    """Defines the detected cluster type."""
    UNKNOWN = auto()
    WHITE = auto()
    RED = auto()

# --- Message Constants ---

class DDPMessages:
    """Constants for specific DDP protocol messages."""
    # Cluster is busy (Warning/Menu active)
    STAT_BUSY_HALF       = [0x53, 0x84]
    STAT_BUSY_WARN_HALF  = [0x53, 0x04]
    STAT_BUSY_FULL       = [0x53, 0x88]
    STAT_BUSY_WARN_FULL  = [0x53, 0x08]

    # Cluster is free (Warning cleared)
    STAT_FREE_HALF       = [0x53, 0x05]
    STAT_FREE_FULL       = [0x53, 0x0A]

    # Re-Initialization Request (Sent by Cluster)
    CMD_REINIT_REQ       = [0x2E] 
    # Re-Initialization Confirmation (We send this back)
    CMD_REINIT_CONF      = [0x2F]


class DDPProtocol:
    """
    Handles the low-level DDP protocol state machine and CAN bus communication.
    Supports both White and Red DIS clusters via auto-detection.
    """

    # --- CAN & Protocol Constants ---
    CAN_ID_SEND = 0x6C0
    CAN_ID_RECV = 0x6C1
    CAN_MASK_RECV = 0x7FF
    CAN_PACING_DELAY_S = 0.002  # Critical 2ms pacing delay for packets

    # -- Keep-Alive (KA) Payloads --
    KA_WHITE_OPEN = [0xA0, 0x0F, 0x8A, 0xFF, 0x4A, 0xFF]  # Session Open Request
    KA_WHITE_ACCEPT = [0xA1, 0x0F, 0x8A, 0xFF, 0x4A, 0xFF] # Session Accept / Pong
    KA_KEEP_PING = [0xA3]                                  # Keep-Alive Ping (We send)
    KA_CLOSE = [0xA8]                                      # Session Close

    KA_RED_PRESENT = [0xA0, 0x07, 0x00]                    # Cluster broadcast
    KA_RED_OPEN = [0xA1, 0x0F]                             # Our reply to PRESENT
    KA_RED_ACCEPT = [0xA1, 0x0F]                           # Cluster reply to PING

    # -- DDP Packet Type Masks --
    PKT_TYPE_MASK = 0xF0
    PKT_SEQ_MASK = 0x0F
    PKT_TYPE_DATA_END = 0x10  # 0x1x (end of frame, expects ACK)
    PKT_TYPE_DATA_BODY = 0x20 # 0x2x (frame body, no ACK)
    PKT_TYPE_ACK = 0xB0       # 0xBx (ACK)
    
    # -- Block Limits -- Thanks to domnulvlad fot the help with this
    MAX_BYTES_PER_BLOCK = 42

    def __init__(self, config: dict):
        self.cfg = config
        self.state = DDPState.DISCONNECTED
        self.dis_mode = DisMode.UNKNOWN
        self.i_am_opener = False
        self.last_ka_sent = 0.0
        self.send_seq_num = 0

        # For _recv_specific to store stray packets
        self._last_received_ack = None
        self._last_received_data = None

        self.channel = config.get('can_channel', 'can0')
        self.bitrate = config.get('can_bitrate', 100000)
        
        logger.debug(f"CAN config: {{'bitrate': {self.bitrate}, 'interface': 'socketcan', 'channel': '{self.channel}'}}")
        
        try:
            self.bus = can.Bus(
                interface='socketcan',
                channel=self.channel,
                bitrate=self.bitrate,
                timeout=0.01  # Non-blocking
            )
            self.bus.set_filters([
                {"can_id": self.CAN_ID_RECV, "can_mask": self.CAN_MASK_RECV, "extended": False}
            ])
        except Exception as e:
            logger.error(f"Failed to open CAN-Bus '{self.channel}': {e}")
            logger.error(f"Make sure '{self.channel}' is up (e.g., sudo ip link set {self.channel} up type can bitrate {self.bitrate})")
            raise DDPCANError(f"Failed to open CAN bus: {e}")

    def __del__(self):
        """Shuts down the CAN bus connection on exit."""
        if hasattr(self, 'bus'):
            logger.debug("Shutting down CAN bus.")
            self.bus.shutdown()

    # --- State and Helper Functions ---

    def _set_state(self, new_state: DDPState):
        """Centralized state transition function."""
        if self.state == new_state:
            return
        
        logger.info(f"State transition: {self.state.name} -> {new_state.name}")
        self.state = new_state
        
        # Reset context on disconnection
        if new_state == DDPState.DISCONNECTED:
            self.dis_mode = DisMode.UNKNOWN
            self.i_am_opener = False
            self.send_seq_num = 0

    def payload_is(self, data: List[int], expected_payload: List[int]) -> bool:
        """Helper to check payload regardless of the sequence number (first byte)."""
        if not data or len(data) < 1: return False
        return data[1:] == expected_payload

    # --- Low-Level CAN & DDP I/O ---

    def send_can(self, can_id: int, data: List[int]):
        """Sends a raw CAN message to the bus with pacing."""
        data_hex = ' '.join(f'{b:02X}' for b in data)
        logger.debug("-> 0x%03X: %s", can_id, data_hex)
        try:
            msg = can.Message(arbitration_id=can_id, data=data, is_extended_id=False)
            self.bus.send(msg)
            time.sleep(self.CAN_PACING_DELAY_S) # Critical pacing delay
        except Exception as e:
            logger.error(f"CAN Send Error: {e}")
            raise DDPCANError(f"CAN Send Error: {e}")

    def _recv(self, timeout_s: float = 0.01) -> Optional[List[int]]:
        """Receives and logs a single CAN message from the bus (ID 0x6C1)."""
        msg = self.bus.recv(timeout_s)
        if msg:
            if msg.arbitration_id == self.CAN_ID_RECV:
                data = list(msg.data)
                logger.debug("<- 0x%03X: %s", self.CAN_ID_RECV, ' '.join(f'{b:02X}' for b in data))
                time.sleep(self.CAN_PACING_DELAY_S)
                return data
        return None

    def send_ack(self, received_seq_num: int):
        """Sends a DDP ACK (0xB0 + seq+1) for a received packet."""
        ack_seq = (received_seq_num + 1) % 16
        ack_packet = [self.PKT_TYPE_ACK + ack_seq]
        logger.debug(f"Sending ACK {ack_packet[0]:02X}")
        self.send_can(self.CAN_ID_SEND, ack_packet)

    def _handle_incoming_packet(self, data: List[int]) -> bool:
        """
        Central handler for all "background" packets (Keep-Alives, ACKs, etc.).
        Returns True if the packet was handled, False if it's a data packet.
        """
        if not data:
            return False

        msg_type_prefix = data[0] & self.PKT_TYPE_MASK
        
        # --- Type 0xA_ (Session Control) ---
        if msg_type_prefix == 0xA0:
            if data == self.KA_CLOSE:
                logger.warning("Cluster sent A8 (Close) -> closing session")
                self._set_state(DDPState.DISCONNECTED)
                return True
            
            # Session drop detection
            if data == self.KA_RED_PRESENT and self.dis_mode == DisMode.RED and self.state == DDPState.READY:
                logger.warning("Red DIS broadcast detected while READY. Session dropped.")
                self._set_state(DDPState.DISCONNECTED)
                return True

            # Cluster Ping (0xA3 or 0xA3 00, etc.)
            if data[0] == self.KA_KEEP_PING[0]:
                logger.debug(f"Cluster sent Keep-Alive {data} -> replying A1")
                reply = self.KA_RED_ACCEPT if self.dis_mode == DisMode.RED else self.KA_WHITE_ACCEPT
                self.send_can(self.CAN_ID_SEND, reply)
                return True
            
            # Cluster Pong (to our Ping)
            if (data == self.KA_WHITE_ACCEPT or data == self.KA_RED_ACCEPT) and self.i_am_opener:
                logger.debug("Cluster replied A1 to our A3")
                return True
            
            # Ignore unhandled 0xA_ packets
            return True # Assume it was session-related

        # --- Type 0xB_ (ACK) ---
        if msg_type_prefix == self.PKT_TYPE_ACK:
            logger.debug(f"<- Received ACK {data[0]:02X}")
            self._last_received_ack = data # Store for _recv_specific
            return True

        # --- Type 0x0_, 0x1_, 0x2_ (Data) ---
        if msg_type_prefix in [0x00, self.PKT_TYPE_DATA_END, self.PKT_TYPE_DATA_BODY]:
            return False # Not handled, it's data for the caller

        logger.warning(f"Unknown unhandled packet type {data[0]:02X}")
        return True # Treat as handled to avoid breaking loops

    def _recv_specific(self, expected_data: List[int], timeout_ms: int) -> Optional[List[int]]:
        """
        Waits for a *specific* CAN packet (e.g., an ACK or KA packet).
        Uses _handle_incoming_packet to filter out background noise.
        """
        start = time.time()
        self._last_received_ack = None # Clear buffer

        while time.time() - start < (timeout_ms / 1000.0):
            data = self._recv(0.05) # Poll for 50ms
            if not data:
                continue
            
            # First, check if it's the packet we are waiting for
            if data == expected_data:
                logger.debug(f"<- Received expected {expected_data}")
                return data
            
            # If not, let the central handler process it (handles ACKs, Pings, etc.)
            self._handle_incoming_packet(data)

            # If we went to DISCONNECTED state, abort
            if self.state == DDPState.DISCONNECTED:
                logger.warning("Session closed while waiting for specific packet")
                return None
                    
        logger.error(f"Timeout waiting for {expected_data}")
        return None

    def _recv_and_ack_data(self, timeout_ms: int) -> Optional[List[int]]:
        """
        Waits for a data packet (0x0x, 0x1x, 0x2x), ACKs it if required,
        and returns the full packet.
        Uses _handle_incoming_packet to filter out background noise.
        """
        start = time.time()
        self._last_received_data = None # Clear buffer

        while time.time() - start < (timeout_ms / 1000.0):
            data = self._recv(0.05) # Poll for 50ms
            if not data:
                continue

            # Let the central handler process it first
            is_background_packet = self._handle_incoming_packet(data)
            
            if self.state == DDPState.DISCONNECTED:
                logger.warning("Session closed while waiting for data packet")
                return None

            if is_background_packet:
                continue # It was an ACK or KA, keep waiting for data

            # If it wasn't a background packet, it must be data.
            msg_type = data[0] & self.PKT_TYPE_MASK
            msg_seq = data[0] & self.PKT_SEQ_MASK
            
            if msg_type in [0x00, self.PKT_TYPE_DATA_END]:
                self.send_ack(msg_seq)
                return data
            elif msg_type == self.PKT_TYPE_DATA_BODY:
                return data
            else:
                logger.warning(f"Received non-data packet {data} when expecting data")
                
        logger.error(f"Timeout waiting for a data packet")
        return None

    def send_data_packet(self, data: List[int], is_multi_packet_frame_body: bool = False):
        """
        Sends a single DDP data packet.
        Handles sequence numbers and waits for ACK on 0x1x (end-of-frame) packets.
        Raises DDPAckTimeoutError on failure.
        """
        packet_type = self.PKT_TYPE_DATA_BODY if is_multi_packet_frame_body else self.PKT_TYPE_DATA_END
        first_byte = packet_type + self.send_seq_num
        packet = [first_byte] + data
        
        self.send_can(self.CAN_ID_SEND, packet)
        
        expected_ack_byte = self.PKT_TYPE_ACK + (self.send_seq_num + 1) % 16
        self.send_seq_num = (self.send_seq_num + 1) % 16
        
        if is_multi_packet_frame_body:
            return # 0x2x packets are not ACKed
        
        # Wait for the specific ACK
        if self._recv_specific([expected_ack_byte], 500):
            return
        else:
            logger.warning(f"Timeout waiting for ACK {expected_ack_byte:02X} after sending {packet[0]:02X}")
            raise DDPAckTimeoutError(f"Timeout waiting for ACK {expected_ack_byte:02X}")

    # --- Public API Methods ---

    def send_ddp_frame(self, payload: List[int]) -> bool:
        """
        Sends a full DDP data payload.
        CRITICAL: Splits large payloads into multiple 'Blocks' of max 42 bytes.
        AND enforces an inter-block delay to allow the cluster to process buffer.
        """
        if self.state != DDPState.READY:
            logger.warning("Attempted to send frame while not READY. Ignoring.")
            return False
        if not payload:
            return True
        
        # 1. Chunk the application payload into Protocol Blocks (Max 42 bytes)
        payload_blocks = [payload[i:i + self.MAX_BYTES_PER_BLOCK] for i in range(0, len(payload), self.MAX_BYTES_PER_BLOCK)]

        try:
            for block in payload_blocks:
                # 2. Split each block into 7-byte CAN segments
                chunks = [block[i:i + 7] for i in range(0, len(block), 7)]
                if not chunks: continue

                last_chunk = chunks.pop()

                # Send Body Frames (0x2x) - No ACK
                for chunk in chunks:
                    self.send_data_packet(chunk, is_multi_packet_frame_body=True)
                
                # Send End Frame (0x1x) - Waits for ACK
                self.send_data_packet(last_chunk, is_multi_packet_frame_body=False)
                
                # 3. INTER-BLOCK PACING
                # Critical for White DIS: Pause after ACK to let the cluster CPU catch up.
                # This creates the "piece by piece" transmission style of RNS-E.
                if self.dis_mode == DisMode.WHITE:
                    time.sleep(0.02) # 20ms delay between blocks
            
        except (DDPAckTimeoutError, DDPCANError) as e:
            logger.error(f"Failed to send DDP frame: {e}. Session closing.")
            self._set_state(DDPState.DISCONNECTED)
            return False
            
        return True

    def _white_dis_passive_open(self) -> bool:
        """(Private) Waits for the White DIS Cluster to initiate (sends A0)."""
        logger.info("PASSIVE WHITE: Waiting for cluster A0...")
        data = self._recv_specific(self.KA_WHITE_OPEN, 1000)
        if data == self.KA_WHITE_OPEN:
            logger.info("Cluster opened -> sending A1")
            self.send_can(self.CAN_ID_SEND, self.KA_WHITE_ACCEPT)
            self.i_am_opener = False
            self._set_state(DDPState.SESSION_ACTIVE)
            self.dis_mode = DisMode.WHITE
            return True
        return False

    def _white_dis_active_open(self) -> bool:
        """(Private) Actively initiates the White DIS session by sending A0."""
        logger.info("ACTIVE WHITE: Sending A0...")
        self.send_can(self.CAN_ID_SEND, self.KA_WHITE_OPEN)
        if self._recv_specific(self.KA_WHITE_ACCEPT, 500):
            logger.info("A1 received")
            self.i_am_opener = True
            self._set_state(DDPState.SESSION_ACTIVE)
            self.dis_mode = DisMode.WHITE
            return True
        return False

    def _red_dis_open(self) -> bool:
        """(Private) Performs the handshake for an Old Red DIS cluster."""
        logger.info("RED DIS: Detected cluster broadcast. Starting Red DIS handshake.")
        
        try:
            # Step 1: Send A1 0F
            logger.info("RED DIS: Sending A1 0F...")
            self.send_can(self.CAN_ID_SEND, self.KA_RED_OPEN)
            
            # Step 2: Send A3 right after
            logger.info("RED DIS: Sending A3...")
            self.send_can(self.CAN_ID_SEND, self.KA_KEEP_PING)
            
            # Step 3: Wait for cluster's A1 0F reply
            if not self._recv_specific(self.KA_RED_ACCEPT, 500):
                raise DDPHandshakeError("Cluster did not reply with A1 0F")
            logger.info("RED DIS: Received A1 0F reply from cluster.")
            
            # Step 4: Exchange A3 / A1 0F four times
            for i in range(4):
                logger.info(f"RED DIS: Sending A3 (Loop {i+1}/4)...")
                self.send_can(self.CAN_ID_SEND, self.KA_KEEP_PING)
                if not self._recv_specific(self.KA_RED_ACCEPT, 500):
                    raise DDPHandshakeError(f"Cluster did not reply on loop {i+1}")
                logger.info(f"RED DIS: Received A1 0F (Loop {i+1}/4).")
            
            logger.info("RED DIS: Handshake complete. Session is active.")
            self.i_am_opener = True
            self._set_state(DDPState.SESSION_ACTIVE)
            self.dis_mode = DisMode.RED
            return True

        except Exception as e:
            logger.error(f"RED DIS: Handshake failed with error: {e}")
            return False

    def detect_and_open_session(self) -> bool:
        """
        Detects cluster type (Red or White) and establishes a Keep-Alive session.
        This is Step 1 of the connection.
        """
        if self.state != DDPState.DISCONNECTED:
            logger.warning("Session already open.")
            return True
            
        logger.info("Detecting cluster type (Red or White)...")
        
        # Listen for 1.5 seconds to see what's on the bus
        start = time.time()
        while time.time() - start < 1.5:
            data = self._recv(0.1) # Poll every 100ms
            if not data:
                continue
            
            # --- Red DIS Detection ---
            if data == self.KA_RED_PRESENT:
                logger.info("Found Red DIS broadcast (A0 07 00).")
                return self._red_dis_open()
                
            # --- White DIS (Passive) Detection ---
            if data == self.KA_WHITE_OPEN:
                logger.info("Found White DIS passive open (A0 0F...).")
                self.send_can(self.CAN_ID_SEND, self.KA_WHITE_ACCEPT)
                self.i_am_opener = False
                self._set_state(DDPState.SESSION_ACTIVE)
                self.dis_mode = DisMode.WHITE
                return True
        
        # --- No broadcast detected ---
        # Assume White DIS, try Active Open
        logger.info("No Red DIS broadcast. Assuming White DIS, attempting Active Open.")
        return self._white_dis_active_open()

    def close_session(self):
        """Actively closes the DDP session by sending A8 (Hard Close)."""
        if self.state != DDPState.DISCONNECTED:
            logger.info("Actively closing session (sending A8)...")
            self.send_can(self.CAN_ID_SEND, self.KA_CLOSE)
            self._set_state(DDPState.DISCONNECTED)

    def release_screen(self) -> bool:
        """
        Sends a 'Release Screen' command (0x33) to the cluster.
        """
        if self.state != DDPState.READY:
            logger.warning("Cannot release screen, session not READY.")
            return False
        
        logger.info("Releasing DIS screen to Bordcomputer (sending 0x33)...")
        payload = [0x33]
        try:
            self.send_data_packet(payload, is_multi_packet_frame_body=False)
            logger.info("Screen released. Session remains open.")
            return True
        except (DDPAckTimeoutError, DDPCANError) as e:
            logger.error(f"Failed to send release screen packet: {e}. Session may be dead.")
            self._set_state(DDPState.DISCONNECTED)
            return False

    # --- Initialization (Step 2) ---

    def _get_init_payloads(self) -> dict:
        """Returns the correct set of payloads based on self.dis_mode."""
        PL_LOG_3 = [0x00, 0x01]
        PL_LOG_5 = [0x00, 0x01]
        PL_LOG_23_COMMON = [0x21, 0x3B, 0xA0, 0x00]

        if self.dis_mode == DisMode.WHITE:
            logger.debug("Using WHITE DIS payload set.")
            return {
                "PL_LOG_3": PL_LOG_3,
                "PL_LOG_5": PL_LOG_5,
                "PL_LOG_11": [0x09, 0x20, 0x0B, 0x50, 0x0A, 0x24, 0x50],
                "PL_LOG_14": [0x30, 0x39, 0x00, 0x30, 0x00],
                "PL_LOG_18": [0x09, 0x20, 0x0B, 0x50, 0x0A, 0x24, 0x50],
                "PL_LOG_21": [0x30, 0x39, 0x00, 0x30, 0x00],
                "PL_LOG_23": PL_LOG_23_COMMON,
                "PL_LOG_27": [0x21, 0x3B, 0xA0, 0x00]
            }
        else: # DisMode.RED
            logger.debug("Using RED DIS payload set.")
            return {
                "PL_LOG_3": PL_LOG_3,
                "PL_LOG_5": PL_LOG_5,
                "PL_LOG_11": [0x09, 0x20, 0x0B, 0x50, 0x00, 0x32, 0x44],
                "PL_LOG_14": [0x30, 0x33, 0x00, 0x31, 0x00],
                "PL_LOG_23": PL_LOG_23_COMMON,
                # Other payloads not needed for the shorter Red path
                "PL_LOG_18": [],
                "PL_LOG_21": [],
                "PL_LOG_27": []
            }

    def _init_common_start(self):
        """Sends the first 4 packets common to all handshakes."""
        self.send_data_packet([0x15, 0x01, 0x01, 0x02, 0x00, 0x00]) # Step 1
        logger.info("Init 1/x passed!")

        data = self._recv_and_ack_data(1000) # Step 2
        if not self.payload_is(data, self.PL["PL_LOG_3"]):
            raise DDPHandshakeError(f"Step 2 failed: wait PL {self.PL['PL_LOG_3']}, got {data}")
        logger.info("Init 2/x passed!")

        self.send_data_packet([0x01, 0x01, 0x00]) # Step 3
        logger.info("Init 3/x passed!")

        self.send_data_packet([0x08]) # Step 4
        logger.info("Init 4/x passed!")

    def _init_path_b_white(self):
        """Handles the short White DIS handshake path."""
        logger.info("Following Path B (White Short)...")
        self.send_data_packet([0x20, 0x3B, 0xA0, 0x00]) # Step 5
        logger.info("Init 5/x (Path B) passed!")

    def _init_path_c_white(self):
        """Handles the long White DIS handshake path."""
        logger.info("Following Path C (White Long)...")
        self.send_data_packet([0x01, 0x01, 0x00]) # Step 5
        logger.info("Init 5/x passed!")
        
        data = self._recv_and_ack_data(1000) # Step 6
        if not self.payload_is(data, self.PL["PL_LOG_14"]):
            raise DDPHandshakeError(f"Step 6 failed: wait PL {self.PL['PL_LOG_14']}, got {data}")
        logger.info("Init 6/x passed!")
        
        self.send_data_packet([0x08]) # Step 7
        logger.info("Init 7/x passed!")

        data = self._recv_and_ack_data(1000) # Step 8
        if not self.payload_is(data, self.PL["PL_LOG_18"]):
            raise DDPHandshakeError(f"Step 8 failed: wait PL {self.PL['PL_LOG_18']}, got {data}")
        logger.info("Init 8/x passed!")

        self.send_data_packet([0x20, 0x3B, 0xA0, 0x00]) # Step 9
        logger.info("Init 9/x passed!")

        data = self._recv_and_ack_data(1000) # Step 10
        if not self.payload_is(data, self.PL["PL_LOG_21"]):
            raise DDPHandshakeError(f"Step 10 failed: wait PL {self.PL['PL_LOG_21']}, got {data}")
        logger.info("Init 10/x passed!")

        data = self._recv_and_ack_data(1000) # Step 11
        if not self.payload_is(data, self.PL["PL_LOG_23"]):
            raise DDPHandshakeError(f"Step 11 failed: wait PL {self.PL['PL_LOG_23']}, got {data}")
        logger.info("Init 11/x passed!")

        self.send_data_packet([0x20, 0x3B, 0xA0, 0x00]) # Step 12
        logger.info("Init 12/x passed!")

        data = self._recv_and_ack_data(1000) # Step 13
        if not self.payload_is(data, self.PL["PL_LOG_27"]):
            raise DDPHandshakeError(f"Step 13 failed: wait PL {self.PL['PL_LOG_27']}, got {data}")
        logger.info("Init 13/x passed!")

        self.send_data_packet([0x33]) # Step 14
        logger.info("Init 14/x passed!")

        self.send_data_packet([0x33]) # Step 15
        logger.info("Init 15/x passed!")

    def _init_path_red(self):
        """Handles the Red DIS handshake path."""
        logger.info("Following RED DIS Short Path...")
        data = self._recv_and_ack_data(1000) # Wait for PL_LOG_14
        if not self.payload_is(data, self.PL["PL_LOG_14"]):
            raise DDPHandshakeError(f"RED Path failed (Step 2): wait PL {self.PL['PL_LOG_14']}, got {data}")
        logger.info("Init 2/x (Red) passed!")
        
        self.send_data_packet([0x20, 0x3B, 0xA0, 0x00]) # Send 13 20...
        logger.info("Init 3/x (Red) passed!")

        data = self._recv_and_ack_data(1000) # Wait for PL_LOG_23
        if not self.payload_is(data, self.PL["PL_LOG_23"]):
            raise DDPHandshakeError(f"RED Path failed (Step 4): wait PL {self.PL['PL_LOG_23']}, got {data}")
        logger.info("Init 4/x (Red) passed!")
        
        self.send_data_packet([0x33]) # Send 14 33
        logger.info("Init 5/x (Red) passed!")

    def perform_initialization(self) -> bool:
        """
        Performs the complex DDP initialization handshake (Step 2).
        This must be called after a session is active (Step 1).
        """
        logger.info(f"Starting DDP Step 2 Initialization for {self.dis_mode.name} DIS...")
        self._set_state(DDPState.INITIALIZING)
        self.send_seq_num = 0

        if not self.i_am_opener:
            logger.error("This handshake is for ACTIVE (Pi opens) mode only.")
            self._set_state(DDPState.DISCONNECTED)
            return False
            
        if self.dis_mode == DisMode.UNKNOWN:
             logger.error("DIS mode is unknown. Cannot perform initialization.")
             self._set_state(DDPState.DISCONNECTED)
             return False

        # Get correct payloads for our DIS type
        self.PL = self._get_init_payloads()

        try:
            # --- Common Start ---
            self._init_common_start()

            # --- Handshake Fork ---
            # Wait for the packet that determines which path to take
            data = self._recv_and_ack_data(1000)
            if data is None: raise DDPHandshakeError("Timed out waiting for handshake fork packet.")
            
            # Handle out-of-order PL_LOG_5 (seen in some logs)
            if self.payload_is(data, self.PL["PL_LOG_5"]):
                logger.info("Handshake Fork: Got out-of-order packet (PL 00 01). Accepting.")
                data = self._recv_and_ack_data(1000)
                if data is None: raise DDPHandshakeError("Timed out after out-of-order packet.")

            # --- Path B (White Short) ---
            if self.payload_is(data, self.PL["PL_LOG_14"]) and self.dis_mode == DisMode.WHITE:
                self._init_path_b_white()
            
            # --- Path C (White Long) or Path Red ---
            elif self.payload_is(data, self.PL["PL_LOG_11"]):
                if self.dis_mode == DisMode.RED:
                    self._init_path_red()
                else:
                    self._init_path_c_white()
            
            else:
                raise DDPHandshakeError(f"Handshake fork failed. Got unhandled packet {data}")

            # --- Final Keep-Alive Exchange ---
            logger.info("Sending final A3 Keep-Alive to complete handshake...")
            self.send_can(self.CAN_ID_SEND, self.KA_KEEP_PING)
            
            reply = self.KA_RED_ACCEPT if self.dis_mode == DisMode.RED else self.KA_WHITE_ACCEPT
            if not self._recv_specific(reply, 1000):
                raise DDPHandshakeError(f"Did not receive final {reply} ACK")
            
            logger.info(f"DDP Initialization COMPLETE")
            self._set_state(DDPState.READY)
            self.last_ka_sent = time.time()
            return True

        except (DDPHandshakeError, DDPAckTimeoutError, DDPCANError) as e:
            logger.error(f"Handshake Error: {e}")
            self._set_state(DDPState.DISCONNECTED)
            return False
        finally:
            # Clean up payload dict
            if hasattr(self, 'PL'):
                del self.PL

    # --- Main Loop Functions ---

    def send_keepalive_if_needed(self):
        """Sends an A3 Keep-Alive ping if we are the opener and 2s have passed."""
        # We must allow Keep-Alives even when PAUSED, to prevent session drop
        if self.state not in [DDPState.READY, DDPState.PAUSED]:
            return
        
        if self.i_am_opener and time.time() - self.last_ka_sent > 2.0:
            logger.debug("Sending A3 Keep-Alive")
            self.send_can(self.CAN_ID_SEND, self.KA_KEEP_PING)
            self.last_ka_sent = time.time()

    def poll_bus_events(self):
        """
        Main polling function. This must be called continuously.
        Handles background traffic, status interrupts (Busy/Free), and Re-Init requests.
        """
        if self.state == DDPState.DISCONNECTED:
            return

        # Non-blocking read
        data = self._recv(0) 
        if not data:
            return
            
        # 1. Handle Keep-Alives / Background ACKs / Session Logic
        is_background_packet = self._handle_incoming_packet(data)

        # 2. Process Data Packets (Status Updates / Re-Init Requests)
        if not is_background_packet:
            msg_type = data[0] & self.PKT_TYPE_MASK
            msg_seq = data[0] & self.PKT_SEQ_MASK
            payload = data[1:]

            # ALWAYS ACK data packets (Type 0x00 or 0x10) immediately,
            # regardless of whether we handle the content.
            if msg_type in [0x00, self.PKT_TYPE_DATA_END]:
                self.send_ack(msg_seq)

            # --- DETECT PAUSE (Cluster Claims Screen) ---
            if payload in [DDPMessages.STAT_BUSY_WARN_HALF, DDPMessages.STAT_BUSY_HALF,
                           DDPMessages.STAT_BUSY_WARN_FULL, DDPMessages.STAT_BUSY_FULL]:
                
                if self.state != DDPState.PAUSED:
                    logger.warning(f"Cluster INTERRUPT (Status {payload}). Pausing...")
                    self._set_state(DDPState.PAUSED)
                    # Urgent Ping to keep session alive during warning
                    self.send_can(self.CAN_ID_SEND, self.KA_KEEP_PING)

            # --- DETECT FREE (Cluster Releases Screen) ---
            elif payload in [DDPMessages.STAT_FREE_HALF, DDPMessages.STAT_FREE_FULL]:
                logger.info(f"Cluster Status FREE ({payload}). Waiting for Re-Init Request (2E)...")
                # Do not resume yet. Protocol dictates we wait for 0x2E.

            # --- HANDLE RE-INIT (Resume Sequence) ---
            elif payload == DDPMessages.CMD_REINIT_REQ:
                logger.info("Received Re-Init Request (2E). Sending Confirm (2F).")
                
                # 1. Reply with 2F (Confirmation)
                first_byte = self.PKT_TYPE_DATA_END + self.send_seq_num
                pkt = [first_byte] + DDPMessages.CMD_REINIT_CONF
                self.send_can(self.CAN_ID_SEND, pkt)
                self.send_seq_num = (self.send_seq_num + 1) % 16

                # 2. Switch state directly to READY.
                # CRITICAL FIX: Do NOT go to SESSION_ACTIVE. We are technically still
                # initialized, we just need to claim the screen again.
                self._set_state(DDPState.READY)

            else:
                logger.warning(f"Received unexpected data packet: {data}. (ACK sent).")
