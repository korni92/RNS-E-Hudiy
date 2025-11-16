#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# Audi DIS (Cluster) DDP Protocol Driver - V1.4
#
# - Added 'release_screen()' method (sends 0x33) for stable screen release. (V1.2)
# - Added auto-detection and support for "Old Red DIS" clusters. (V1.3)
# - Fixed "Red DIS" handshake based on RNS-E log (V1.4)
# - Fixed stability issue by handling 0x6C1 A3 00 packets (V1.4)
# - Added session-drop detection for Red DIS (V1.4)
#
import time
import logging
import can
from typing import List, Optional

# Get the logger for this module
logger = logging.getLogger(__name__)

# --- DDP Protocol Constants ---

# -- White DIS (New) --
KA_OPEN = [0xA0, 0x0F, 0x8A, 0xFF, 0x4A, 0xFF] # Session Open Request
KA_ACCEPT = [0xA1, 0x0F, 0x8A, 0xFF, 0x4A, 0xFF] # Session Accept / Keep-Alive Pong
KA_KEEP = [0xA3] # Keep-Alive Ping (We send this)
KA_CLOSE = [0xA8] # Session Close

# -- Red DIS (Old) --
# Cluster constantly sends this when no session is open
KA_RED_PRESENT = [0xA0, 0x07, 0x00]
# Navigation replies to PRESENT with this
KA_RED_OPEN = [0xA1, 0x0F]
# Cluster replies to OPEN and subsequent KEEPs with this
KA_RED_ACCEPT = [0xA1, 0x0F]
# Note: The Red DIS uses the same KA_KEEP (0xA3) as the white DIS

class DDPProtocol:
    """
    Handles the low-level DDP protocol state machine and CAN bus communication.
    Supports both White and Red DIS clusters via auto-detection.
    """
    def __init__(self, config: dict):
        self.cfg = config
        self.state = 'DISCONNECTED' # DISCONNECTED, SESSION_ACTIVE, INITIALIZING, READY
        self.dis_mode = 'unknown' # 'unknown', 'white', 'red'
        self.i_am_opener = False
        self.last_ka_sent = 0.0
        self.send_seq_num = 0

        self.channel = config.get('can_channel', 'can0')
        self.bitrate = config.get('can_bitrate', 100000)
        
        logger.debug(f"CAN config: {{'bitrate': {self.bitrate}, 'interface': 'socketcan', 'channel': '{self.channel}'}}")
        
        try:
            self.bus = can.Bus(
                interface='socketcan',
                channel=self.channel,
                bitrate=self.bitrate,
                timeout=0.01
            )
            self.bus.set_filters([
                {"can_id": 0x6C1, "can_mask": 0x7FF, "extended": False}
            ])
        except Exception as e:
            logger.error(f"Failed to open CAN-Bus '{self.channel}': {e}")
            logger.error(f"Make sure '{self.channel}' is up (e.g., sudo ip link set {self.channel} up type can bitrate {self.bitrate})")
            raise

    def __del__(self):
        """Shuts down the CAN bus connection on exit."""
        if hasattr(self, 'bus'):
            logger.debug("Shutting down CAN bus.")
            self.bus.shutdown()

    def payload_is(self, data: List[int], expected_payload: List[int]) -> bool:
        """Helper to check payload regardless of the sequence number (first byte)."""
        if not data or len(data) < 1: return False
        return data[1:] == expected_payload

    def send_can(self, can_id: int, data: List[int]):
        """Sends a raw CAN message to the bus."""
        data_hex = ' '.join(f'{b:02X}' for b in data)
        logger.debug("-> 0x%03X: %s", can_id, data_hex)
        try:
            msg = can.Message(arbitration_id=can_id, data=data, is_extended_id=False)
            self.bus.send(msg)
            time.sleep(0.002) # Critical 2ms pacing delay
        except Exception as e:
            logger.error(f"CAN Send Error: {e}")

    def send_ack(self, received_seq_num: int):
        """Sends a DDP ACK (0xB0 + seq+1) for a received packet."""
        ack_seq = (received_seq_num + 1) % 16
        ack_packet = [0xB0 + ack_seq]
        logger.debug(f"Sending ACK {ack_packet[0]:02X}")
        self.send_can(0x6C0, ack_packet)

    def send_data_packet(self, data: List[int], is_multi_packet_frame_body: bool = False) -> bool:
        """
        Sends a single DDP data packet (0x1x or 0x2x).
        Handles sequence numbers and waits for ACK on 0x1x (end-of-frame) packets.
        """
        packet_type = 0x20 if is_multi_packet_frame_body else 0x10
        first_byte = packet_type + self.send_seq_num
        packet = [first_byte] + data
        self.send_can(0x6C0, packet)
        
        expected_ack_byte = 0xB0 + (self.send_seq_num + 1) % 16
        self.send_seq_num = (self.send_seq_num + 1) % 16
        
        if is_multi_packet_frame_body:
            return True # 0x2x packets are not ACKed
        
        if self._recv_specific([expected_ack_byte], 500):
            return True
        else:
            logger.warning(f"Timeout waiting for ACK {expected_ack_byte:02X} after sending {packet[0]:02X}")
            self.state = 'DISCONNECTED'
            return False

    def send_ddp_frame(self, payload: List[int]) -> bool:
        """
        Sends a full DDP data payload, splitting it into
        multiple 0x2x CAN packets (body) and one final 0x1x packet (end).
        """
        if self.state != 'READY':
            logger.warning("Attempted to send frame while not READY. Ignoring.")
            return False
        if not payload:
            return True
        
        chunks = [payload[i:i + 7] for i in range(0, len(payload), 7)]
        
        if not chunks:
            return True

        last_chunk = chunks.pop()

        for chunk in chunks:
            if not self.send_data_packet(chunk, is_multi_packet_frame_body=True):
                return False
        
        if not self.send_data_packet(last_chunk, is_multi_packet_frame_body=False):
            return False
            
        return True

    def _recv(self, timeout_ms: int = 100) -> Optional[List[int]]:
        """Receives a single CAN message from the bus (ID 0x6C1)."""
        msg = self.bus.recv(timeout_ms / 1000.0)
        if msg:
            if msg.arbitration_id == 0x6C1:
                data = list(msg.data)
                logger.debug("<- 0x6C1: %s", ' '.join(f'{b:02X}' for b in data))
                time.sleep(0.002)
                return data
        return None

    def _recv_specific(self, expected_data: List[int], timeout_ms: int) -> Optional[List[int]]:
        """Waits for a *specific* CAN packet (e.g., an ACK)."""
        start = time.time()
        while time.time() - start < (timeout_ms / 1000.0):
            data = self._recv(50)
            if data == expected_data:
                logger.debug(f"<- Received expected {expected_data}")
                return data
            elif data == KA_CLOSE:
                logger.warning("Cluster sent A8 during wait!")
                self.state = 'DISCONNECTED'
                return None
            # --- STABILITY FIX V1.4 ---
            # Check for 0xA3, ignoring extra bytes (e.g., A3 00)
            elif data and data[0] == KA_KEEP[0]: # 0xA3
                logger.debug(f"Cluster sent Keep-Alive {data} -> replying A1")
                # Use correct reply for the mode.
                if self.dis_mode == 'red':
                    self.send_can(0x6C0, KA_RED_ACCEPT)
                else:
                    self.send_can(0x6C0, KA_ACCEPT)
                    
        logger.error(f"Timeout waiting for {expected_data}")
        return None

    def _recv_and_ack_data(self, timeout_ms: int) -> Optional[List[int]]:
        """Waits for a data packet, ACKs it immediately if required, and returns it."""
        start = time.time()
        while time.time() - start < (timeout_ms / 1000.0):
            data = self._recv(50)
            if not data:
                continue
            
            msg_type = data[0] >> 4
            msg_seq = data[0] & 0x0F
            
            if msg_type in [0x0, 0x1]: # 0x0x or 0x1x
                self.send_ack(msg_seq)
                return data
            elif msg_type == 0x2: # 0x2x
                return data
            elif data == KA_CLOSE:
                logger.warning("Cluster sent A8 during handshake!")
                self.state = 'DISCONNECTED'
                return None
            # --- STABILITY FIX V1.4 ---
            # Check for 0xA3, ignoring extra bytes (e.g., A3 00)
            elif data and data[0] == KA_KEEP[0]: # 0xA3
                logger.debug(f"Cluster sent Keep-Alive {data} -> replying A1")
                if self.dis_mode == 'red':
                    self.send_can(0x6C0, KA_RED_ACCEPT)
                else:
                    self.send_can(0x6C0, KA_ACCEPT)
                continue
            elif msg_type == 0xB:
                logger.debug("Ignoring stray ACK packet while waiting for data")
                continue
                
        logger.error(f"Timeout waiting for a data packet")
        return None

    def _passive_white_open(self) -> bool:
        """(Private) Waits for the White DIS Cluster to initiate the session (sends A0)."""
        logger.info("PASSIVE WHITE: Waiting for cluster A0...")
        start = time.time()
        while time.time() - start < 1.0: # Short wait
            data = self._recv(100)
            if not data: continue
            if data == KA_OPEN:
                logger.info("Cluster opened -> sending A1")
                self.send_can(0x6C0, KA_ACCEPT)
                self.i_am_opener = False
                self.state = 'SESSION_ACTIVE'
                self.dis_mode = 'white'
                return True
            if data == KA_CLOSE:
                return False
        return False

    def _active_white_open(self) -> bool:
        """(Private) Actively initiates the White DIS session by sending A0."""
        logger.info("ACTIVE WHITE: Sending A0...")
        self.send_can(0x6C0, KA_OPEN)
        if self._recv_specific(KA_ACCEPT, 500):
            logger.info("A1 received")
            self.i_am_opener = True
            self.state = 'SESSION_ACTIVE'
            self.dis_mode = 'white'
            return True
        return False

    def _red_dis_open(self) -> bool:
        """(Private) Performs the handshake for an Old Red DIS cluster."""
        logger.info("RED DIS: Detected cluster broadcast. Starting Red DIS handshake.")
        
        try:
            # Step 1: Send A1 0F
            logger.info("RED DIS: Sending A1 0F...")
            self.send_can(0x6C0, KA_RED_OPEN)
            
            # Step 2: Send A3 right after
            logger.info("RED DIS: Sending A3...")
            self.send_can(0x6C0, KA_KEEP)
            
            # Step 3: Wait for cluster's A1 0F reply
            if not self._recv_specific(KA_RED_ACCEPT, 500):
                logger.error("RED DIS: Cluster did not reply with A1 0F. Aborting.")
                return False
            logger.info("RED DIS: Received A1 0F reply from cluster.")
            
            # Step 4: Exchange A3 / A1 0F four times
            for i in range(4):
                logger.info(f"RED DIS: Sending A3 (Loop {i+1}/4)...")
                self.send_can(0x6C0, KA_KEEP)
                if not self._recv_specific(KA_RED_ACCEPT, 500):
                    logger.error(f"RED DIS: Cluster did not reply on loop {i+1}. Aborting.")
                    return False
                logger.info(f"RED DIS: Received A1 0F (Loop {i+1}/4).")
            
            logger.info("RED DIS: Handshake complete. Session is active.")
            self.i_am_opener = True # We are initiating the data exchange
            self.state = 'SESSION_ACTIVE'
            self.dis_mode = 'red'
            return True

        except Exception as e:
            logger.error(f"RED DIS: Handshake failed with error: {e}")
            return False

    def detect_and_open_session(self) -> bool:
        """
        Replaces active/passive_open.
        Listens for Red DIS broadcast. If not found, attempts White DIS handshake.
        """
        if self.state != 'DISCONNECTED':
            logger.warning("Session already open.")
            return True
            
        logger.info("Detecting cluster type (Red or White)...")
        
        # Listen for 1.5 seconds to see what's on the bus
        start = time.time()
        while time.time() - start < 1.5:
            data = self._recv(100)
            if not data:
                continue
            
            # --- Red DIS Detection ---
            if data == KA_RED_PRESENT:
                logger.info("Found Red DIS broadcast (A0 07 00).")
                return self._red_dis_open()
                
            # --- White DIS (Passive) Detection ---
            if data == KA_OPEN:
                logger.info("Found White DIS passive open (A0 0F...).")
                self.send_can(0x6C0, KA_ACCEPT)
                self.i_am_opener = False
                self.state = 'SESSION_ACTIVE'
                self.dis_mode = 'white'
                return True
        
        # --- No broadcast detected ---
        # Assume White DIS, try Active Open
        logger.info("No Red DIS broadcast. Assuming White DIS, attempting Active Open.")
        return self._active_white_open()

    def close_session(self):
        """Actively closes the DDP session by sending A8 (Hard Close)."""
        if self.state != 'DISCONNECTED':
            logger.info("Actively closing session (sending A8)...")
            self.send_can(0x6C0, KA_CLOSE)
            self.state = 'DISCONNECTED'
            self.dis_mode = 'unknown' # Reset mode on close
            self.i_am_opener = False

    def release_screen(self):
        """
        Sends a 'Release Screen' command (0x33) to the cluster.
        """
        if self.state != 'READY':
            logger.warning("Cannot release screen, session not READY.")
            return False
        
        logger.info("Releasing DIS screen to Bordcomputer (sending 0x33)...")
        payload = [0x33]
        if not self.send_data_packet(payload, is_multi_packet_frame_body=False):
            logger.error("Failed to send release screen packet. Session may be dead.")
            return False
        
        logger.info("Screen released. Session remains open.")
        return True

    def perform_initialization(self) -> bool:
        """
        Performs the complex DDP initialization handshake (Step 2).
        Uses different payloads based on self.dis_mode (set during open_session).
        """
        logger.info(f"Starting DDP Step 2 Initialization for {self.dis_mode.upper()} DIS...")
        self.state = 'INITIALIZING'
        self.send_seq_num = 0
        if not self.i_am_opener:
            logger.error("This handshake is for ACTIVE (Pi opens) mode only.")
            self.state = 'DISCONNECTED'
            return False
            
        if self.dis_mode == 'unknown':
             logger.error("DIS mode is unknown. Cannot perform initialization.")
             self.state = 'DISCONNECTED'
             return False
        
        # --- Define Payloads for BOTH cluster types ---
        PL_LOG_3 = [0x00, 0x01] # Common
        PL_LOG_5 = [0x00, 0x01] # Common
        PL_LOG_23_COMMON = [0x21, 0x3B, 0xA0, 0x00] # Common payload, used in different places

        if self.dis_mode == 'white':
            logger.debug("Using WHITE DIS payload set.")
            PL_LOG_11 = [0x09, 0x20, 0x0B, 0x50, 0x0A, 0x24, 0x50]
            PL_LOG_14 = [0x30, 0x39, 0x00, 0x30, 0x00]
            PL_LOG_18 = [0x09, 0x20, 0x0B, 0x50, 0x0A, 0x24, 0x50]
            PL_LOG_21 = [0x30, 0x39, 0x00, 0x30, 0x00]
            PL_LOG_23 = PL_LOG_23_COMMON
            PL_LOG_27 = [0x21, 0x3B, 0xA0, 0x00]
        else: # 'red'
            logger.debug("Using RED DIS payload set.")
            PL_LOG_11 = [0x09, 0x20, 0x0B, 0x50, 0x00, 0x32, 0x44]
            PL_LOG_14 = [0x30, 0x33, 0x00, 0x31, 0x00]
            PL_LOG_23 = PL_LOG_23_COMMON # This is the 0x13 21 3B A0 00 packet
            # Other payloads for red path are not needed for the new short handshake
            PL_LOG_18 = [] 
            PL_LOG_21 = []
            PL_LOG_27 = []


        try:
            if not self.send_data_packet([0x15, 0x01, 0x01, 0x02, 0x00, 0x00]):
                raise Exception("Init failed (Step 1: send 10 15...)")
            logger.info("Init 1/x passed!")

            data = self._recv_and_ack_data(1000)
            if data is None: raise Exception("Init failed (Step 2: Session closed)")
            if not self.payload_is(data, PL_LOG_3):
                raise Exception(f"Init failed (Step 2: wait PL {PL_LOG_3}), got {data}")
            logger.info("Init 2/x passed!")

            if not self.send_data_packet([0x01, 0x01, 0x00]):
                raise Exception("Init failed (Step 3: send 11 01...)")
            logger.info("Init 3/x passed!")

            if not self.send_data_packet([0x08]):
                raise Exception("Init failed (Step 4: send 12 08)")
            logger.info("Init 4/x passed!")

            data = self._recv_and_ack_data(1000)
            if data is None: raise Exception("Handshake fork failed. Timed out or session closed.")
            
            if self.payload_is(data, PL_LOG_5):
                logger.info("Handshake Fork: Got out-of-order packet (PL 00 01). Accepting.")
                data = self._recv_and_ack_data(1000)
                if data is None: raise Exception("Handshake fork failed. Timed out or session closed.")
            
            # --- Handshake Fork ---
            
            if self.payload_is(data, PL_LOG_14) and self.dis_mode == 'white':
                # --- Path B (White DIS Only) ---
                logger.info(f"Following Path B (short): Got PL {PL_LOG_14}...")
                if not self.send_data_packet([0x20, 0x3B, 0xA0, 0x00]):
                    raise Exception("Init failed (Path B: send 13 20...)")
                logger.info("Init 5/x (Path B) passed!")
                
                logger.info("Sending initial A3 Keep-Alive (Path B)")
                self.send_can(0x6C0, KA_KEEP)
                
                reply = KA_ACCEPT # Path B is white-only
                
                if not self._recv_specific(reply, 1000):
                    raise Exception(f"Init Step 3 failed (wait for {reply} on Path B)")
                
                logger.info("DDP Initialization COMPLETE (Path B)")
                self.state = 'READY'
                self.last_ka_sent = time.time()
                return True

            elif self.payload_is(data, PL_LOG_11):
                # --- Path C (Long White) or Path Red ---
                logger.info(f"Following Path C/Red: Got PL {PL_LOG_11}...")
                
                # --- HANDSHAKE FIX V1.4 ---
                if self.dis_mode == 'red':
                    # --- RED DIS Short Path (Confirmed by log) ---
                    logger.info("Following RED DIS Short Path...")
                    
                    data = self._recv_and_ack_data(1000) # Wait for PL_LOG_14
                    if not self.payload_is(data, PL_LOG_14):
                        raise Exception(f"RED Path failed (Step 2: wait PL {PL_LOG_14}), got {data}")
                    logger.info("Init 2/x (Red) passed!")
                    
                    if not self.send_data_packet([0x20, 0x3B, 0xA0, 0x00]): # Send 13 20...
                        raise Exception("RED Path failed (Step 3: send 13 20...)")
                    logger.info("Init 3/x (Red) passed!")

                    data = self._recv_and_ack_data(1000) # Wait for PL_LOG_23
                    if not self.payload_is(data, PL_LOG_23):
                        raise Exception(f"RED Path failed (Step 4: wait PL {PL_LOG_23}), got {data}")
                    logger.info("Init 4/x (Red) passed!")
                    
                    if not self.send_data_packet([0x33]): # Send 14 33
                        raise Exception("RED Path failed (Step 5: send 14 33)")
                    logger.info("Init 5/x (Red) passed!")
                    
                    logger.info("Sending initial A3 Keep-Alive (Red Path)")
                    self.send_can(0x6C0, KA_KEEP)
                    if not self._recv_specific(KA_RED_ACCEPT, 1000):
                        raise Exception("Init Step 3 failed (wait for A1 0F)")
                    
                    logger.info("DDP Initialization COMPLETE (Red Path)")
                    self.state = 'READY'
                    self.last_ka_sent = time.time()
                    return True
                
                else:
                    # --- WHITE DIS Long Path C ---
                    logger.info("Following WHITE DIS Long Path...")
                    if not self.send_data_packet([0x01, 0x01, 0x00]):
                        raise Exception("Init failed (Step 5: send 13 01...)")
                    logger.info("Init 5/x passed!")
                    
                    data = self._recv_and_ack_data(1000)
                    if not self.payload_is(data, PL_LOG_14):
                        raise Exception(f"Init failed (Step 6: wait PL {PL_LOG_14}), got {data}")
                    logger.info("Init 6/x passed!")
                    
                    if not self.send_data_packet([0x08]):
                        raise Exception("Init failed (Step 7: send 14 08)")
                    logger.info("Init 7/x passed!")

                    data = self._recv_and_ack_data(1000)
                    if not self.payload_is(data, PL_LOG_18):
                        raise Exception(f"Init failed (Step 8: wait PL {PL_LOG_18}), got {data}")
                    logger.info("Init 8/x passed!")

                    if not self.send_data_packet([0x20, 0x3B, 0xA0, 0x00]):
                        raise Exception("Init failed (Step 9: send 15 20...)")
                    logger.info("Init 9/x passed!")

                    data = self._recv_and_ack_data(1000)
                    if not self.payload_is(data, PL_LOG_21):
                        raise Exception(f"Init failed (Step 10: wait PL {PL_LOG_21}), got {data}")
                    logger.info("Init 10/x passed!")

                    data = self._recv_and_ack_data(1000)
                    if not self.payload_is(data, PL_LOG_23):
                        raise Exception(f"Init failed (Step 11: wait PL {PL_LOG_23}), got {data}")
                    logger.info("Init 11/x passed!")

                    if not self.send_data_packet([0x20, 0x3B, 0xA0, 0x00]):
                        raise Exception("Init failed (Step 12: send 16 20...)")
                    logger.info("Init 12/x passed!")

                    data = self._recv_and_ack_data(1000)
                    if not self.payload_is(data, PL_LOG_27):
                        raise Exception(f"Init failed (Step 13: wait PL {PL_LOG_27}), got {data}")
                    logger.info("Init 13/x passed!")

                    if not self.send_data_packet([0x33]):
                        raise Exception("Init failed (Step 14: send 17 33)")
                    logger.info("Init 14/x passed!")

                    if not self.send_data_packet([0x33]):
                        raise Exception("Init failed (Step 15: send 18 33)")
                    logger.info("Init 15/x passed!")
                    
                    logger.info("Sending initial A3 Keep-Alive (Path C)")
                    self.send_can(0x6C0, KA_KEEP)
                    
                    if not self._recv_specific(KA_ACCEPT, 1000):
                        raise Exception("Init Step 3 failed (wait for A1)")
                    logger.info("Init Step 3 passed!")

                    logger.info("DDP Initialization COMPLETE (Path C)")
                    self.state = 'READY'
                    self.last_ka_sent = time.time()
                    return True
            
            else:
                raise Exception(f"Handshake fork failed. Got unhandled packet {data}")


        except Exception as e:
            logger.error(f"Handshake Error: {e}")
            self.state = 'DISCONNECTED'
            self.dis_mode = 'unknown' # Reset mode on failure
            return False

    def send_keepalive_if_needed(self):
        """Sends an A3 Keep-Alive ping if we are the opener and 2s have passed."""
        if self.state != 'READY': return
        if self.i_am_opener and time.time() - self.last_ka_sent > 2.0:
            logger.debug("Sending A3 Keep-Alive")
            self.send_can(0x6C0, KA_KEEP)
            self.last_ka_sent = time.time()

    def poll_bus_events(self):
        """
        Main polling function for the 'READY' state.
        This must be called continuously in the service loop.
        It handles incoming pings/acks/data from the cluster.
        """
        data = self._recv(0)
        if not data:
            return
            
        msg_type = data[0] >> 4
        
        if msg_type == 0xA:
            if data == KA_CLOSE:
                logger.warning("Cluster sent A8 -> closing session")
                self.state = 'DISCONNECTED'
                self.dis_mode = 'unknown'
                self.i_am_opener = False
            
            # --- STABILITY FIX V1.4 ---
            # Session drop detection
            elif data == KA_RED_PRESENT and self.dis_mode == 'red':
                logger.warning("Red DIS broadcast detected while READY. Session dropped, forcing reconnect.")
                self.state = 'DISCONNECTED'
                self.dis_mode = 'unknown'
                self.i_am_opener = False

            # --- STABILITY FIX V1.4 ---
            # Check for 0xA3, ignoring extra bytes (e.g., A3 00)
            elif data and data[0] == KA_KEEP[0]: # 0xA3
                logger.debug(f"Cluster sent Keep-Alive {data} -> replying A1")
                if self.dis_mode == 'red':
                    self.send_can(0x6C0, KA_RED_ACCEPT)
                else:
                    self.send_can(0x6C0, KA_ACCEPT)
            
            elif (data == KA_ACCEPT or data == KA_RED_ACCEPT) and self.i_am_opener:
                logger.debug("Cluster replied A1 to our A3")
            return
        
        elif msg_type == 0xB:
            logger.debug(f"<- 0x6C1: Received ACK {data[0]:02X}")
            return
        
        elif msg_type in [0x0, 0x1]:
            logger.debug(f"<- 0x6C1: Data packet (0x{msg_type:X}x) {data}")
            msg_seq = data[0] & 0x0F
            self.send_ack(msg_seq)
            return
        
        elif msg_type == 0x2:
            logger.debug(f"<- 0x6C1: Data packet BODY (0x{msg_type:X}x) {data}")
            return
        
        logger.warning(f"Unknown packet type {data[0]:02X}")
        return
