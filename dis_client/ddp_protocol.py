#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# Audi DIS (Cluster) DDP Protocol Driver - V1.2
#
# - Added 'release_screen()' method (sends 0x33) for stable screen release.
#
import time
import logging
import can
from typing import List, Optional

# Get the logger for this module
logger = logging.getLogger(__name__)

# --- DDP Protocol Constants ---
KA_OPEN   = [0xA0, 0x0F, 0x8A, 0xFF, 0x4A, 0xFF] # Session Open Request
KA_ACCEPT = [0xA1, 0x0F, 0x8A, 0xFF, 0x4A, 0xFF] # Session Accept / Keep-Alive Pong
KA_KEEP   = [0xA3]                               # Keep-Alive Ping
KA_CLOSE  = [0xA8]                               # Session Close

class DDPProtocol:
    """
    Handles the low-level DDP protocol state machine and CAN bus communication.
    """
    def __init__(self, config: dict):
        self.cfg = config
        self.state = 'DISCONNECTED' # DISCONNECTED, SESSION_ACTIVE, INITIALIZING, READY
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
                logger.debug("<- 0x6C1: %s", ' '.join(f'{b:02X}' for b in msg.data))
                time.sleep(0.002) 
                return list(msg.data)
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
            elif data == KA_KEEP:
                logger.debug("Cluster sent A3 -> replying A1")
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
            
            if msg_type in [0x0, 0x1]:
                self.send_ack(msg_seq)
                return data
            elif msg_type == 0x2:
                return data
            elif data == KA_CLOSE:
                logger.warning("Cluster sent A8 during handshake!")
                self.state = 'DISCONNECTED'
                return None
            elif data == KA_KEEP:
                logger.debug("Cluster sent A3 -> replying A1")
                self.send_can(0x6C0, KA_ACCEPT)
                continue
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

    def close_session(self):
        """Actively closes the DDP session by sending A8 (Hard Close)."""
        if self.state != 'DISCONNECTED':
            logger.info("Actively closing session (sending A8)...")
            self.send_can(0x6C0, KA_CLOSE)
            self.state = 'DISCONNECTED'

    # Screen release back to cluster
    def release_screen(self):
        """
        Sends a 'Release Screen' command (0x33) to the cluster.
        This returns the DIS to the on-board computer but keeps
        the DDP session open and READY.
        """
        if self.state != 'READY':
            logger.warning("Cannot release screen, session not READY.")
            return False
        
        logger.info("Releasing DIS screen to Bordcomputer (sending 0x33)...")
        payload = [0x33] # 0x33 = Release Screen command
        if not self.send_data_packet(payload, is_multi_packet_frame_body=False):
            logger.error("Failed to send release screen packet. Session may be dead.")
            # self.state set by send_data_packet to DISCONNECTED
            return False
        
        logger.info("Screen released. Session remains open.")
        return True

    def perform_initialization(self) -> bool:
        """
        Performs the complex DDP initialization handshake (Step 2).
        """
        logger.info("Starting DDP Step 2 Initialization (Flexible Payload-Logic)...")
        self.state = 'INITIALIZING'
        self.send_seq_num = 0 
        if not self.i_am_opener:
            logger.error("This handshake is for ACTIVE (Pi opens) mode only.")
            self.state = 'DISCONNECTED'
            return False

        PL_LOG_3  = [0x00, 0x01]
        PL_LOG_5  = [0x00, 0x01] 
        PL_LOG_11 = [0x09, 0x20, 0x0B, 0x50, 0x0A, 0x24, 0x50]
        PL_LOG_14 = [0x30, 0x39, 0x00, 0x30, 0x00]
        PL_LOG_18 = [0x09, 0x20, 0x0B, 0x50, 0x0A, 0x24, 0x50]
        PL_LOG_21 = [0x30, 0x39, 0x00, 0x30, 0x00]
        PL_LOG_23 = [0x21, 0x3B, 0xA0, 0x00]
        PL_LOG_27 = [0x21, 0x3B, 0xA0, 0x00]

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
                return True 

            elif self.payload_is(data, PL_LOG_11):
                 logger.info("Following Path C (long): Got PL 09 20...")
                 pass
            
            else:
                raise Exception(f"Handshake fork failed. Got unhandled packet {data}")

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
        msg_seq = data[0] & 0x0F
        
        if msg_type == 0xA:
            if data == KA_CLOSE:
                logger.warning("Cluster sent A8 -> closing session")
                self.state = 'DISCONNECTED'
            elif data == KA_KEEP:
                logger.debug("Cluster sent A3 -> replying A1")
                self.send_can(0x6C0, KA_ACCEPT)
            elif data == KA_ACCEPT and self.i_am_opener:
                logger.debug("Cluster replied A1 to our A3")
            return
        
        elif msg_type == 0xB:
            logger.debug(f"<- 0x6C1: Received ACK {data[0]:02X}")
            return
        
        elif msg_type in [0x0, 0x1]:
            logger.debug(f"<- 0x6C1: Data packet (0x{msg_type:X}x) {data}")
            self.send_ack(msg_seq) 
            return
        
        elif msg_type == 0x2:
            logger.debug(f"<- 0x6C1: Data packet BODY (0x{msg_type:X}x) {data}")
            return
        
        logger.warning(f"Unknown packet type {data[0]:02X}")
        return