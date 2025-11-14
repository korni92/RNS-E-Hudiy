#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# Audi DIS (Cluster) DDP Protocol Handler - Beta 1.0
#
# This script connects directly to a SocketCAN interface (e.g., can0)
# to communicate with an Audi RNS-E (0x6C0) and DIS Cluster (0x6C1).
# It performs the DDP handshake, claims the navigation screen, and then
# listens on a ZMQ PULL socket for draw commands from a client application.
#
# Protocol: Audi DDP (proprietary)
# Target: RNS-E / A3 8P Cluster
#
import zmq
import json
import time
import logging
from typing import List, Optional
import can 

# ----------------------------------------------------------------------
# Logging Configuration
# (Set level to logging.DEBUG for verbose handshake tracing)
# ----------------------------------------------------------------------
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] (DIS) %(message)s')
logger = logging.getLogger(__name__)

# ----------------------------------------------------------------------
# DDP Protocol Constants
# ----------------------------------------------------------------------
KA_OPEN   = [0xA0, 0x0F, 0x8A, 0xFF, 0x4A, 0xFF] # Session Open Request
KA_ACCEPT = [0xA1, 0x0F, 0x8A, 0xFF, 0x4A, 0xFF] # Session Accept / Keep-Alive Pong
KA_KEEP   = [0xA3]                               # Keep-Alive Ping
KA_CLOSE  = [0xA8]                               # Session Close

# ----------------------------------------------------------------------
# DDPProtocol Class
# Handles the low-level CAN communication and DDP state machine.
# ----------------------------------------------------------------------
class DDPProtocol:
    def __init__(self, config: dict):
        # Internal state
        self.cfg = config
        self.state = 'DISCONNECTED' # Current state: DISCONNECTED, SESSION_ACTIVE, INITIALIZING, READY
        self.i_am_opener = False    # True if we initiated the session (A0)
        self.last_ka_sent = 0.0     # Timestamp of the last keep-alive we sent
        self.send_seq_num = 0       # Our outgoing DDP packet sequence number (0x0-0xF)

        # CAN bus configuration
        self.channel = config.get('can_channel', 'can0')
        self.bitrate = config.get('can_bitrate', 100000) 
        
        logger.debug(f"CAN config: {{'bitrate': {self.bitrate}, 'interface': 'socketcan', 'channel': '{self.channel}'}}")
        
        try:
            # Connect to the CAN bus using python-can
            self.bus = can.Bus(
                interface='socketcan',
                channel=self.channel,
                bitrate=self.bitrate
            )
            # Set a hardware filter to only receive 0x6C1 (Cluster -> Navi)
            self.bus.set_filters([
                {"can_id": 0x6C1, "can_mask": 0x7FF, "extended": False}
            ])
        except Exception as e:
            logger.error(f"Failed to open CAN-Bus '{self.channel}': {e}")
            logger.error(f"Make sure '{self.channel}' is up (e.g., sudo ip link set {self.channel} up type can bitrate {self.bitrate})")
            raise
        
        # AUDSCII Translation Table (Standard ASCII to Cluster-Specific)
        # This converts a string like "Hello" into bytes the cluster understands.
        self.audscii_trans = [
            0x00,0x20,0x20,0x20,0x20,0x20,0x20,0x20,0x20,0x2F,0x20,0x20,0x20,0x20,0x20,0x20,
            0x20,0x20,0x20,0x20,0x20,0x20,0x20,0x20,0x20,0x20,0x20,0x20,0x1C,0x20,0x20,0x20,
            0x20,0x21,0x22,0x23,0x24,0x25,0x26,0x27,0x28,0x29,0x2A,0x2B,0x2C,0x2D,0x2E,0x2F,
            0x30,0x31,0x32,0x33,0x34,0x35,0x36,0x37,0x38,0x39,0x3A,0x3B,0x3C,0x3D,0x3E,0x3F,
            0x40,0x41,0x42,0x43,0x44,0x45,0x46,0x47,0x48,0x49,0x4A,0x4B,0x4C,0x4D,0x4E,0x4F,
            0x50,0x51,0x52,0x53,0x54,0x55,0x56,0x57,0x58,0x59,0x5A,0x5B,0x5C,0x5D,0x5E,0x66,
            0x20,0x01,0x02,0x03,0x04,0x05,0x06,0x07,0x08,0x09,0x0A,0x0B,0x0C,0x0D,0x0E,0x0F,
            0x10,0x71,0x72,0x73,0x74,0x75,0x76,0x77,0x78,0x79,0x7A,0x7B,0x7C,0x7D,0x7E,0x20,
            0x20,0x20,0x20,0x20,0x20,0x20,0x20,0x20,0x20,0x20,0x20,0x20,0x20,0x20,0x20,0x20,
            0x20,0x20,0x20,0x20,0x20,0x20,0x20,0x20,0x20,0x20,0x20,0x20,0x20,0x20,0x20,0x20,
            0x20,0x20,0x20,0x20,0x20,0x20,0x20,0x20,0x20,0xA2,0xA0,0x20,0x20,0x2D,0x20,0x7E,
            0x6B,0xB4,0xB2,0xB3,0x20,0xB8,0x20,0x20,0x20,0xB1,0xB0,0x20,0x20,0x20,0x20,0xB9,
            0xC1,0xC0,0xD0,0xE0,0x5F,0xE1,0xE2,0x8B,0xC3,0xC2,0xD2,0xD3,0xC5,0xC4,0xD4,0xD5,
            0xCE,0x8A,0xC7,0xC6,0xD6,0xE6,0x60,0x20,0xE7,0xC9,0xC8,0xD8,0x61,0xE5,0xE8,0x8D,
            0x81,0x80,0x90,0xF0,0x91,0xF1,0xF2,0x9B,0x83,0x82,0x92,0x93,0x85,0x84,0x94,0x95,
            0xEF,0x9A,0x87,0x86,0x96,0xF6,0x97,0xBA,0xF7,0x89,0x88,0x98,0x99,0xF5,0xF8,0x20
        ]

    def __del__(self):
        """Shuts down the CAN bus connection on exit."""
        if hasattr(self, 'bus'):
            self.bus.shutdown()

    def payload_is(self, data: List[int], expected_payload: List[int]) -> bool:
        """
        Helper to check payload regardless of the sequence number (first byte).
        """
        if not data or len(data) < 1: return False
        # Compare everything except the first byte (the sequence number)
        return data[1:] == expected_payload

    def send_can(self, can_id: int, data: List[int]):
        """Sends a raw CAN message to the bus."""
        data_hex = ' '.join(f'{b:02X}' for b in data)
        logger.debug("-> 0x%03X: %s", can_id, data_hex)
        try:
            msg = can.Message(arbitration_id=can_id, data=data, is_extended_id=False)
            self.bus.send(msg)
            # This 2ms delay is critical for protocol timing/pacing
            time.sleep(0.002) 
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
        Handles our own sequence numbers and waits for ACK on 0x1x (end-of-frame) packets.
        """
        packet_type = 0x20 if is_multi_packet_frame_body else 0x10
        first_byte = packet_type + self.send_seq_num
        packet = [first_byte] + data
        self.send_can(0x6C0, packet)
        
        # Increment our sequence number for the next packet
        expected_ack_byte = 0xB0 + (self.send_seq_num + 1) % 16
        self.send_seq_num = (self.send_seq_num + 1) % 16
        
        if is_multi_packet_frame_body:
            return True # 0x2x packets are not ACKed
        
        # 0x1x packets (end-of-frame) MUST be ACKed
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
        if not payload:
            return True
        
        # Split payload into 7-byte chunks
        chunks = [payload[i:i + 7] for i in range(0, len(payload), 7)]
        
        if not chunks:
            return True

        last_chunk = chunks.pop()

        # Send all 0x2x "body" packets
        for chunk in chunks:
            if not self.send_data_packet(chunk, is_multi_packet_frame_body=True):
                return False
        
        # Send the final 0x1x "end" packet (which gets ACKed)
        if not self.send_data_packet(last_chunk, is_multi_packet_frame_body=False):
            return False
            
        return True

    def _recv(self, timeout_ms: int = 100) -> Optional[List[int]]:
        """Receives a single CAN message from the bus (ID 0x6C1)."""
        msg = self.bus.recv(timeout_ms / 1000.0) # timeout in seconds
        if msg:
            if msg.arbitration_id == 0x6C1:
                logger.debug("<- 0x6C1: %s", ' '.join(f'{b:02X}' for b in msg.data))
                # This 2ms delay is critical for protocol timing/pacing
                time.sleep(0.002) 
                return list(msg.data)
        return None # Timeout

    def _recv_specific(self, expected_data: List[int], timeout_ms: int) -> Optional[List[int]]:
        """Waits for a specific CAN packet (e.g., an ACK or handshake step)."""
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
            elif data and data[0] >> 4 == 0xA:
                # Handle other session packets (like A3) while waiting
                self.handle_incoming(data)
                if self.state == 'DISCONNECTED':
                    return None
        logger.error(f"Timeout waiting for {expected_data}")
        return None

    def _recv_and_ack_data(self, timeout_ms: int) -> Optional[List[int]]:
        """
        Waits for a data packet (0x0-0x2), ACKs it *immediately* if required,
        and then returns the full data packet.
        """
        start = time.time()
        while time.time() - start < (timeout_ms / 1000.0):
            data = self._recv(50) 
            if not data:
                continue
            
            msg_type = data[0] >> 4
            msg_seq = data[0] & 0x0F
            
            # 0x0x and 0x1x packets require an ACK
            if msg_type in [0x0, 0x1]:
                self.send_ack(msg_seq)
                return data
            # 0x2x packets do NOT get an ACK
            elif msg_type == 0x2:
                return data
            # 0xA_ packets are session control
            elif msg_type == 0xA:
                self.handle_incoming(data)
                if self.state != 'INITIALIZING': 
                    return None
                continue
            # 0xB_ packets are ACKs, ignore them while waiting for data
            elif msg_type == 0xB:
                logger.debug("Ignoring stray ACK packet while waiting for data")
                continue
                
        logger.error(f"Timeout waiting for a data packet")
        return None

    def passive_open(self) -> bool:
        """Waits for the Cluster to initiate the session (sends A0)."""
        logger.info("PASSIVE: Waiting for cluster A0...")
        start = time.time()
        while time.time() - start < 10.0:
            data = self._recv(500)
            if not data: continue
            if data == KA_OPEN:
                logger.info("Cluster opened -> sending A1")
                self.send_can(0x6C0, KA_ACCEPT)
                self.i_am_opener = False
                self.state = 'SESSION_ACTIVE'
                return True
            if data == KA_CLOSE:
                return False
        return False

    def active_open(self) -> bool:
        """Actively initiates the session by sending A0."""
        logger.info("ACTIVE: Sending A0...")
        self.send_can(0x6C0, KA_OPEN)
        if self._recv_specific(KA_ACCEPT, 500):
            logger.info("A1 received")
            self.i_am_opener = True
            self.state = 'SESSION_ACTIVE'
            return True
        return False

    def perform_initialization(self) -> bool:
        """
        Performs the complex DDP initialization handshake (Step 2).
        This logic is flexible and handles multiple known handshake paths
        by checking payloads rather than strict sequence numbers.
        """
        logger.info("Starting DDP Step 2 Initialization (Flexible Payload-Logic)...")
        self.state = 'INITIALIZING'
        self.send_seq_num = 0 
        if not self.i_am_opener:
            logger.error("This handshake is for ACTIVE (Pi opens) mode only.")
            self.state = 'DISCONNECTED'
            return False

        # --- Expected Payloads ---
        # We check payloads (data[1:]) because the cluster's sequence
        # number (data[0]) can be unpredictable.
        PL_LOG_3  = [0x00, 0x01]
        PL_LOG_5  = [0x00, 0x01] 
        PL_LOG_11 = [0x09, 0x20, 0x0B, 0x50, 0x0A, 0x24, 0x50]
        PL_LOG_14 = [0x30, 0x39, 0x00, 0x30, 0x00]
        PL_LOG_18 = [0x09, 0x20, 0x0B, 0x50, 0x0A, 0x24, 0x50]
        PL_LOG_21 = [0x30, 0x39, 0x00, 0x30, 0x00]
        PL_LOG_23 = [0x21, 0x3B, 0xA0, 0x00]
        PL_LOG_27 = [0x21, 0x3B, 0xA0, 0x00]

        try:
            # --- Handshake Sequence ---
            
            # 1. Pi sends its first data packet
            if not self.send_data_packet([0x15, 0x01, 0x01, 0x02, 0x00, 0x00]): 
                raise Exception("Init failed (Step 1: send 10 15...)")
            logger.info("Init 1/x passed!") 

            # 2. Cluster responds (PL 00 01)
            data = self._recv_and_ack_data(1000) 
            if not self.payload_is(data, PL_LOG_3):
                raise Exception(f"Init failed (Step 2: wait PL {PL_LOG_3}), got {data}")
            logger.info("Init 2/x passed!")

            # 3. Pi sends its second data packet
            if not self.send_data_packet([0x01, 0x01, 0x00]):
                raise Exception("Init failed (Step 3: send 11 01...)")
            logger.info("Init 3/x passed!") 

            # 4. Pi sends its third data packet
            if not self.send_data_packet([0x08]):
                raise Exception("Init failed (Step 4: send 12 08)")
            logger.info("Init 4/x passed!") 

            # --- LOGIC FORK ---
            # The cluster's response at this point can vary.
            data = self._recv_and_ack_data(1000)
            
            # Handle out-of-order packet (seen in Log7)
            if self.payload_is(data, PL_LOG_5):
                logger.info("Handshake Fork: Got out-of-order packet (PL 00 01). Accepting.")
                data = self._recv_and_ack_data(1000)
            
            # Path B (seen in Log6) - short handshake
            if self.payload_is(data, PL_LOG_14):
                logger.info("Following Path B (short): Got PL 30 39...")
                if not self.send_data_packet([0x20, 0x3B, 0xA0, 0x00]): 
                    raise Exception("Init failed (Path B: send 13 20...)")
                logger.info("Init 5/x (Path B) passed!") 
                
                logger.info("Sending initial A3 Keep-Alive (Path B)")
                self.send_can(0x6C0, KA_KEEP)
                if not self._recv_specific(KA_ACCEPT, 1000):
                    raise Exception("Init Step 3 failed (wait for A1 on Path B)")
                
                logger.info("DDP Initialization COMPLETE (Path B)")
                self.state = 'READY'
                self.last_ka_sent = time.time()
                return True # Handshake complete

            # Path C (RNS-E Log) - long handshake
            elif self.payload_is(data, PL_LOG_11):
                 logger.info("Following Path C (long): Got PL 09 20...")
                 # Continue to common path
                 pass
            
            else:
                raise Exception(f"Handshake fork failed. Got unhandled packet {data}")

            # --- COMMON PATH (C) ---
            # (This is the rest of the RNS-E log sequence)
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
            
            # --- End of DDP Step 2 ---
            
            # --- DDP Step 3 (Keep-Alive) ---
            logger.info("Sending initial A3 Keep-Alive (Path C)")
            self.send_can(0x6C0, KA_KEEP)
            if not self._recv_specific(KA_ACCEPT, 1000):
                raise Exception("Init Step 3 failed (wait for A1)")
            logger.info("Init Step 3 passed!")

        except Exception as e:
            logger.error(f"Handshake Error: {e}")
            self.state = 'DISCONNECTED'
            return False

        logger.info("DDP Initialization COMPLETE (Path C)")
        self.state = 'READY'
        self.last_ka_sent = time.time()
        return True

    def send_keepalive_if_needed(self):
        """Sends an A3 Keep-Alive ping if we are the opener and 2s have passed."""
        if self.state != 'READY': return
        if self.i_am_opener and time.time() - self.last_ka_sent > 2.0:
            logger.debug("Sending A3 Keep-Alive")
            self.send_can(0x6C0, KA_KEEP)
            self.last_ka_sent = time.time()

    def handle_incoming(self, data: List[int]) -> Optional[List[int]]:
        """
        Main packet router for the 'READY' state.
        Handles Keep-Alives (A3) and ACKs data (0x0, 0x1) from the cluster.
        """
        if not data: return None
        msg_type = data[0] >> 4
        msg_seq = data[0] & 0x0F
        
        # Session packets
        if msg_type == 0xA:
            if data == KA_CLOSE:
                logger.warning("Cluster sent A8 -> closing session")
                self.state = 'DISCONNECTED'
            elif data == KA_KEEP:
                # Cluster is pinging us, we must pong
                logger.debug("Cluster sent A3 -> replying A1")
                self.send_can(0x6C0, KA_ACCEPT)
            elif data == KA_ACCEPT and self.i_am_opener:
                # Cluster is ponging our ping
                logger.debug("Cluster replied A1 to our A3")
            return None
        
        # ACK packets (from cluster, for our draw commands)
        elif msg_type == 0xB:
            logger.debug(f"<- 0x6C1: Received ACK {data[0]:02X}")
            return None
        
        # Data packets (from cluster)
        elif msg_type in [0x0, 0x1]:
            logger.debug(f"<- 0x6C1: Data packet (0x{msg_type:X}x) {data}")
            self.send_ack(msg_seq) 
            return data # Return data for potential future processing
        
        # Multi-frame data packets (from cluster)
        elif msg_type == 0x2:
            logger.debug(f"<- 0x6C1: Data packet BODY (0x{msg_type:X}x) {data}")
            return data # No ACK needed
        
        logger.warning(f"Unknown packet type {data[0]:02X}")
        return None

    def translate_to_audscii(self, text: str) -> List[int]:
        """Translates a standard Python string into a list of AUDSCII bytes."""
        return [self.audscii_trans[ord(c) % 256] for c in text]

    def claim_nav_screen(self):
        """
        Performs the full "Claim Screen" handshake.
        This forces the DIS to switch to the Navigation screen.
        Based on "RNS-E compass on.csv" log.
        """
        if self.state != 'READY':
            logger.warning("Cannot claim screen, session not READY.")
            return False
        
        logger.info("Sending Region Claim (0x52) to switch to NAV screen...")
        
        try:
            # Payloads for the claim handshake
            payload_claim = [0x52, 0x05, 0x82, 0x00, 0x1B, 0x40, 0x30] # 'R'egion, Len 5, Flag 82 (Claim+Clear), Coords
            payload_busy  = [0x53, 0x84] # 'S'tatus, Busy
            payload_free  = [0x53, 0x05] # 'S'tatus, Free
            payload_ready = [0x2E]       # 'Ready'
            payload_clear = [0x2F]       # 'Clear'
            payload_ok    = [0x53, 0x85] # 'S'tatus, OK
            
            # 1. Pi sends: 1x 52 ... (Claim)
            if not self.send_data_packet(payload_claim):
                raise Exception("Claim Handshake 1/7 failed (send 1x 52)")
            
            # 2. Cluster sends: 1x 53 84 (Status Busy)
            data = self._recv_and_ack_data(1000)
            if not self.payload_is(data, payload_busy):
                raise Exception(f"Claim Handshake 2/7 failed (wait 1x 53 84), got {data}")

            # 3. Cluster sends: 1x 53 05 (Status Free)
            data = self._recv_and_ack_data(1000)
            if not self.payload_is(data, payload_free):
                raise Exception(f"Claim Handshake 3/7 failed (wait 1x 53 05), got {data}")
            
            # 4. Cluster sends: 1x 2E (Ready?)
            data = self._recv_and_ack_data(1000)
            if not self.payload_is(data, payload_ready):
                raise Exception(f"Claim Handshake 4/7 failed (wait 1x 2E), got {data}")
            
            # 5. Pi sends: 1x 2F (Clear)
            if not self.send_data_packet(payload_clear):
                raise Exception("Claim Handshake 5/7 failed (send 1x 2F)")

            # 6. Pi sends: 1x 52 ... (Second Claim)
            if not self.send_data_packet(payload_claim):
                raise Exception("Claim Handshake 6/7 failed (send 1x 52 again)")

            # 7. Cluster sends: 1x 53 85 (Status OK)
            data = self._recv_and_ack_data(1000)
            if not self.payload_is(data, payload_ok):
                logger.warning(f"Got non-standard status {data} after 2nd claim, but proceeding.")

        except Exception as e:
            logger.error(f"Failed to claim screen: {e}")
            self.state = 'DISCONNECTED' # Force re-init
            return False
            
        logger.info("Region Claim handshake successful. Screen is active.")
        return True

    # --- Draw Functions (V5.9) ---
    # These functions queue commands. They are executed by 'commit_frame()'.

    def clear_screen_payload(self):
        """
        Queues a 'Region Clear' command (0x52).
        This does NOT display anything until 'commit_frame()' is called.
        """
        if self.state != 'READY': return False
        
        # Payload: 0x52 (Region), 0x05 (Len), 0x02 (Flag: Clear), Coords...
        payload = [0x52, 0x05, 0x02, 0x00, 0x1B, 0x40, 0x30]
        
        logger.info("Queueing Region Clear")
        # Use send_ddp_frame as this payload could be > 7 bytes
        if not self.send_ddp_frame(payload):
            logger.error("Failed to send clear payload.")
            return False
        return True

    def write_text(self, text: str, x: int = 0, y: int = 0):
        """
        Queues a 'Write Text' command (0x57).
        This does NOT display anything until 'commit_frame()' is called.
        """
        if self.state != 'READY': return False
        
        chars = self.translate_to_audscii(text) + [0xD7] # 0xD7 = Clear to end of line
        
        # Payload: 0x57 (Write), Len, Flags (0x06=Red), X, Y, Chars...
        payload = [0x57, len(chars) + 3, 0x06, x, y] + chars
        
        logger.info(f"Queueing text '{text}'")
        if not self.send_ddp_frame(payload):
            logger.error("Failed to send text payload.")
            return False
        return True

    def commit_frame(self):
        """
        Queues a 'Commit' command (0x39).
        This makes all previously queued commands (like write_text)
        appear on the display.
        """
        if self.state != 'READY': return False
        
        logger.info("Committing Frame (0x39)")
        payload = [0x39]
        # A commit is a single packet, so we use send_data_packet
        if not self.send_data_packet(payload, is_multi_packet_frame_body=False):
            logger.error("Failed to send commit packet.")
            return False
        return True

    def clear_screen(self):
        """
        Executes a full clear screen command (Clear + Commit).
        Used by the 'clear' ZMQ command.
        """
        logger.info("Executing full clear_screen command...")
        if not self.clear_screen_payload():
            logger.error("clear_screen: Failed to queue clear payload.")
            return
        if not self.commit_frame():
            logger.error("clear_screen: Failed to commit clear payload.")
            return

    def set_source_radio(self):
        """Sends the 0x661 broadcast to set the audio source to Radio."""
        self.send_can(0x661, [0x00] * 8)
        logger.info("Source: Radio")

# ----------------------------------------------------------------------
# DisHandler Class
#
# This is the main "server" class. It wraps DDPProtocol and adds
# a ZMQ PULL socket to listen for commands from client applications
# (like display_manager.py or test_draw.py).
# ----------------------------------------------------------------------
class DisHandler:
    def __init__(self, config_path='/home/pi/config.json'):
        """Initializes the DDP handler and the ZMQ command socket."""
        with open(config_path) as f:
            self.config = json.load(f)
        
        # Initialize the DDP protocol handler (passes full config)
        self.ddp = DDPProtocol(self.config)
        
        # Initialize ZMQ PULL socket to listen for draw commands
        self.context = zmq.Context()
        self.draw_socket = self.context.socket(zmq.PULL)
        self.draw_socket.bind(self.config['zmq']['dis_draw'])
        
        # Use a poller to listen for ZMQ commands
        self.poller = zmq.Poller()
        self.poller.register(self.draw_socket, zmq.POLLIN)

        # Icon definitions for progress bar
        self.ICONS = {
            'filled': [0x90], 'empty': [0xB7],
            'l_bracket': [0x5B], 'r_bracket': [0x5D]
        }

    def parse_time(self, t: str) -> int:
        """Helper to parse "M:SS" time strings to seconds."""
        if not t: return 0
        parts = t.split(':')
        return sum(int(p) * (60 ** i) for i, p in enumerate(reversed(parts)))

    def run(self):
        """Main loop for the DIS handler."""
        while True:
            try:
                # --- STEP 1: Session Management ---
                # Keep trying to establish a connection
                if self.ddp.state == 'DISCONNECTED':
                    if self.ddp.passive_open():
                        logger.info("PASSIVE MODE: Cluster opened")
                    elif self.ddp.active_open():
                        logger.info("ACTIVE MODE: We opened")
                    else:
                        logger.warning("No session — retrying...")
                        time.sleep(3)
                        continue

                # --- STEP 2 & 3: DDP Handshake ---
                if self.ddp.state == 'SESSION_ACTIVE':
                    if not self.ddp.perform_initialization():
                        logger.error("DDP Initialization failed. Retrying session.")
                        self.ddp.state = 'DISCONNECTED' # Force retry
                        time.sleep(3)
                        continue
                    else:
                        # Init is successful, set audio source
                        self.ddp.set_source_radio()
                        
                        # Force the DIS to switch to our screen
                        if not self.ddp.claim_nav_screen():
                            logger.error("Failed to claim screen. Retrying session.")
                            self.ddp.state = 'DISCONNECTED'
                            time.sleep(3)
                            continue
                        
                        logger.info("DIS READY — FULLY REACTIVE")

                # --- STEP 4: Main Loop (Ready State) ---
                while self.ddp.state == 'READY':
                    # Poll ZMQ socket for draw commands (5ms timeout)
                    socks = dict(self.poller.poll(5)) 

                    # Service the DDP session (send keep-alives)
                    self.ddp.send_keepalive_if_needed()

                    # Service the DDP session (handle incoming pings/data)
                    data = self.ddp._recv(10)
                    if data:
                        self.ddp.handle_incoming(data)
                    
                    # If handle_incoming got an A8, break to re-initialize
                    if self.ddp.state != 'READY':
                        logger.warning("Session closed by cluster. Re-initializing.")
                        break

                    # --- ZMQ Command Parser ---
                    if self.draw_socket in socks:
                        while True:
                            try:
                                cmd = self.draw_socket.recv_json(flags=zmq.NOBLOCK)
                                c = cmd.get('command')
                                
                                if c == 'clear':
                                    # Executes clear_screen_payload + commit_frame
                                    self.ddp.clear_screen() 
                                
                                elif c == 'clear_payload':
                                    # Queues a clear command
                                    self.ddp.clear_screen_payload()
                                
                                elif c == 'draw_text':
                                    # Queues a write command
                                    self.ddp.write_text(
                                        cmd.get('text', ''),
                                        cmd.get('x', 0),
                                        cmd.get('y', 0)
                                    )
                                
                                elif c == 'commit':
                                    # Executes the commit command
                                    self.ddp.commit_frame()
                                
                                elif c == 'draw_progress_bar':
                                    # (Example of a compound command)
                                    pos = self.parse_time(cmd.get('position', '0:00'))
                                    dur = self.parse_time(cmd.get('duration', '0:00'))
                                    w = cmd.get('width', 10)
                                    filled = min(int(pos / dur * w) if dur else 0, w)
                                    bar = (
                                        self.ICONS['l_bracket'] +
                                        self.ICONS['filled'] * filled +
                                        self.ICONS['empty'] * (w - filled) +
                                        self.ICONS['r_bracket']
                                    )
                                    bar_text = "".join([chr(b[0]) for b in bar])
                                    self.ddp.write_text(
                                        bar_text,
                                        y=cmd.get('y', 0)
                                    )
                            except zmq.Again:
                                # No more commands in queue
                                break
                
                logger.info("Session no longer READY. Restarting loop.")
                time.sleep(1) 

            except Exception as e:
                logger.error(f"Main loop error: {e}", exc_info=True)
                self.ddp.state = 'DISCONNECTED'
                time.sleep(3)


if __name__ == "__main__":
    try:
        DisHandler(config_path='/home/pi/config.json').run()
    except KeyboardInterrupt:
        logger.info("Shutting down.")
