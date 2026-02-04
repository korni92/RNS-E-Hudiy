#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# Audi DIS (Cluster) DDP Protocol Driver - V4.0
# Changes:
# - NEW: "Breathing" Logic. On ACK Timeout, send A3 (Ping) loop 
#   to give cluster time to process data (fixes timeouts on slow clusters).
# - FIX: Reverted ID 20 03 to use Standard 8-bit commands (matches RNS-E log).
#
import time
import logging
import can
from typing import List, Optional
from enum import Enum, auto

logger = logging.getLogger(__name__)

class DDPError(Exception):
    pass

class DDPCANError(DDPError):
    pass

class DDPAckTimeoutError(DDPError):
    pass

class DDPHandshakeError(DDPError):
    pass

class DDPState(Enum):
    DISCONNECTED = auto()
    SESSION_ACTIVE = auto()
    INITIALIZING = auto()
    READY = auto()
    PAUSED = auto()

class DisMode(Enum):
    UNKNOWN = auto()
    WHITE = auto()
    RED = auto()
    COLOR_TYPE1 = auto()
    COLOR_TYPE2 = auto()
    MONO_HYBRID = auto() # ID 20 03

class DDPMessages:
    # Mono/Red Status (Prefix 0x53)
    STAT_BUSY_HALF        = [0x53, 0x84]
    STAT_BUSY_WARN_HALF   = [0x53, 0x04]
    STAT_BUSY_FULL        = [0x53, 0x88]
    STAT_BUSY_WARN_FULL   = [0x53, 0x08]
    STAT_FREE_HALF        = [0x53, 0x05]
    STAT_FREE_FULL        = [0x53, 0x0A]
    
    # Color Status (Prefix 0x7B)
    STAT_COLOR_BUSY_HALF       = [0x7B, 0x84]
    STAT_COLOR_BUSY_WARN_HALF  = [0x7B, 0x04]
    STAT_COLOR_BUSY_FULL       = [0x7B, 0x88]
    STAT_COLOR_BUSY_WARN_FULL  = [0x7B, 0x08]
    STAT_COLOR_FREE_HALF       = [0x7B, 0x05]
    STAT_COLOR_FREE_FULL       = [0x7B, 0x0A]
    STAT_COLOR_OK              = [0x7B, 0x85]

    STAT_GRAPHIC_ACK_WHITE = [0x0B, 0x03, 0x57]
    STAT_GRAPHIC_ACK_RED   = [0x0B, 0x01, 0x00]
    CMD_REINIT_REQ        = [0x2E] 
    CMD_REINIT_CONF       = [0x2F]

class DDPProtocol:
    CAN_ID_SEND = 0x6C0
    CAN_ID_RECV = 0x6C1
    CAN_MASK_RECV = 0x7FF

    DEFAULT_BS = 0x0F 
    DEFAULT_T1_MS = 100 
    DEFAULT_T3_MS = 5 
    DEFAULT_ACK_TIMEOUT_MS = 500
    DEFAULT_PACING_DELAY_S = 0.005

    KA_WHITE_OPEN = [0xA0, 0x0F, 0x8A, 0xFF, 0x4A, 0xFF]
    KA_WHITE_ACCEPT = [0xA1, 0x0F, 0x8A, 0xFF, 0x4A, 0xFF]
    KA_KEEP_PING = [0xA3]
    KA_CLOSE = [0xA8]
    KA_RED_PRESENT = [0xA0, 0x07, 0x00]
    KA_COLOR_PRESENT = [0xA0, 0x0F, 0x00]
    KA_RED_OPEN = [0xA1, 0x0F]
    KA_RED_ACCEPT = [0xA1, 0x0F]
    KA_SIMPLE_ACCEPT = [0xA1, 0x0F]

    PKT_TYPE_MASK = 0xF0
    PKT_SEQ_MASK = 0x0F
    PKT_TYPE_DATA_END = 0x10
    PKT_TYPE_DATA_BODY = 0x20
    PKT_TYPE_ACK = 0xB0
    PKT_TYPE_DATA_CONTINUE = 0x00

    def __init__(self, config: dict):
        self.cfg = config
        self.state = DDPState.DISCONNECTED
        self.dis_mode = DisMode.UNKNOWN
        self.i_am_opener = False
        self.last_ka_sent = 0.0
        self.send_seq_num = 0
        self.opcode_offset = 0x00
        self.coord_bytes = 1
        self._rx_buffer_ack = None
        self.current_payload = []
        self.pending_payload = None
        self.region = None
        self.tp_version = None 
        self.bs = self.DEFAULT_BS
        self.t1_ms = self.DEFAULT_T1_MS
        self.t3_ms = self.DEFAULT_T3_MS
        self.ack_timeout_ms = self.DEFAULT_ACK_TIMEOUT_MS
        self.pacing_delay_s = self.DEFAULT_PACING_DELAY_S
        self.ka_format_long = False
        
        self.channel = config.get('can_channel', 'can0')
        self.bitrate = config.get('can_bitrate', 100000)
        
        try:
            self.bus = can.Bus(
                interface='socketcan',
                channel=self.channel,
                bitrate=self.bitrate,
                timeout=0.01 
            )
            self.bus.set_filters([
                {"can_id": self.CAN_ID_RECV, "can_mask": self.CAN_MASK_RECV, "extended": False}
            ])
        except Exception as e:
            logger.error(f"Failed to open CAN-Bus: {e}")
            raise DDPCANError(f"Failed to open CAN bus: {e}")

    def __del__(self):
        if hasattr(self, 'bus'):
            try:
                self.bus.shutdown()
            except: pass

    def _set_state(self, new_state: DDPState):
        if self.state == new_state: return
        logger.info(f"State transition: {self.state.name} -> {new_state.name}")
        old_state = self.state
        self.state = new_state
        if new_state == DDPState.DISCONNECTED:
            self.dis_mode = DisMode.UNKNOWN
            self.i_am_opener = False
            self.send_seq_num = 0
            self.opcode_offset = 0
            self.coord_bytes = 1
            self.current_payload = []
            self.pending_payload = None
            self.region = None
        if new_state == DDPState.READY and old_state == DDPState.PAUSED:
            self._restore_screen()

    def _restore_screen(self):
        if self.pending_payload is not None:
            logger.info("Restoring screen with pending payload.")
            self.send_ddp_frame(self.pending_payload)
        elif self.current_payload:
            logger.info("Restoring screen with current payload.")
            self.send_ddp_frame(self.current_payload)
        else:
            logger.debug("No payload to restore.")

    def payload_is(self, data: List[int], expected_payload: List[int]) -> bool:
        if not data or len(data) < 1: return False
        return data[1:] == expected_payload

    def decode_time(self, byte: int) -> float:
        units = byte >> 6
        scale = byte & 0x3F
        base_ms = 0.1 * (10 ** units)
        return base_ms * scale

    def encode_time(self, ms: float) -> int:
        for units in range(4):
            base_ms = 0.1 * (10 ** units)
            scale = ms / base_ms
            if scale == int(scale) and 0 <= scale <= 63:
                return (units << 6) | int(scale)
        raise DDPError(f"Cannot encode time {ms} ms")

    def parse_params(self, data: List[int]):
        if data[0] not in [0xA0, 0xA1]:
            return
        self.bs = min(self.bs, data[1])
        self.MAX_BYTES_PER_BLOCK = (self.bs - 1) * 7
        if len(data) == 6:
            self.tp_version = 2.0
            self.t1_ms = self.decode_time(data[2])
            self.t3_ms = self.decode_time(data[4])
            self.ack_timeout_ms = int(self.t1_ms)
            self.pacing_delay_s = self.t3_ms / 1000.0
            self.ka_format_long = True
            logger.debug(f"Parsed TP2.0 params: BS={self.bs:02X}, T1={self.t1_ms}ms, T3={self.t3_ms}ms")
        elif len(data) in [2, 3]:
            self.tp_version = 1.6
            self.ka_format_long = False
            logger.debug(f"Parsed TP1.6 params: BS={self.bs:02X}")
        else:
            logger.warning(f"Invalid params length {len(data)}")

    def build_a1(self) -> List[int]:
        if self.ka_format_long or self.tp_version == 2.0:
            t1_b = self.encode_time(self.t1_ms)
            t3_b = self.encode_time(self.t3_ms)
            return [0xA1, self.bs, t1_b, 0xFF, t3_b, 0xFF]
        else:
            return [0xA1, self.bs]

    def send_can(self, can_id: int, data: List[int]):
        try:
            msg = can.Message(arbitration_id=can_id, data=data, is_extended_id=False)
            self.bus.send(msg)
            time.sleep(self.pacing_delay_s)
        except Exception as e:
            logger.error(f"CAN Send Error: {e}")
            raise DDPCANError(f"CAN Send Error: {e}")

    def _recv(self, timeout_s: float = 0.01) -> Optional[List[int]]:
        msg = self.bus.recv(timeout_s)
        if msg and msg.arbitration_id == self.CAN_ID_RECV:
            data = list(msg.data)
            time.sleep(self.pacing_delay_s)
            return data
        return None

    def send_ack(self, received_seq_num: int):
        ack_seq = (received_seq_num + 1) % 16
        ack_packet = [self.PKT_TYPE_ACK + ack_seq]
        self.send_can(self.CAN_ID_SEND, ack_packet)

    def _handle_incoming_packet(self, data: List[int]) -> bool:
        if not data: return False
        msg_type_prefix = data[0] & self.PKT_TYPE_MASK
        
        if msg_type_prefix == 0xA0:
            if data == self.KA_CLOSE:
                self._set_state(DDPState.DISCONNECTED)
                return True
            if (data == self.KA_RED_PRESENT or data == self.KA_COLOR_PRESENT) and self.state == DDPState.READY:
                logger.warning("Broadcast detected while READY. Session dropped.")
                self._set_state(DDPState.DISCONNECTED)
                return True
            if data[0] == self.KA_KEEP_PING[0]:
                reply = self.build_a1()
                self.send_can(self.CAN_ID_SEND, reply)
                return True
            if (data == self.KA_WHITE_ACCEPT or data == self.KA_RED_ACCEPT) and self.i_am_opener:
                return True
            return True 

        if msg_type_prefix == self.PKT_TYPE_ACK:
            self._rx_buffer_ack = data 
            return True

        if msg_type_prefix in [0x00, self.PKT_TYPE_DATA_END, self.PKT_TYPE_DATA_BODY]:
            return False 

        if data[0] == 0x53 or data[0] == 0x7B:
            return False

        logger.warning(f"Unknown packet type {data[0]:02X}")
        return True 

    def _recv_specific(self, expected_data: List[int], timeout_ms: int) -> Optional[List[int]]:
        start = time.time()
        self._rx_buffer_ack = None
        while time.time() - start < (timeout_ms / 1000.0):
            data = self._recv(0.05)
            if not data: continue
            if data == expected_data: return data
            
            is_bg = self._handle_incoming_packet(data)
            
            if not is_bg:
                msg_type = data[0] & self.PKT_TYPE_MASK
                msg_seq = data[0] & self.PKT_SEQ_MASK
                if msg_type in [0x00, self.PKT_TYPE_DATA_END]:
                    self.send_ack(msg_seq)
            
            if self.state == DDPState.DISCONNECTED: return None
        return None
        
    def _recv_message_chain(self, timeout_ms: int) -> Optional[List[int]]:
        start = time.time()
        payload_buffer = []

        while time.time() - start < (timeout_ms / 1000.0):
            data = self._recv(0.05)
            if not data: continue

            is_bg = self._handle_incoming_packet(data)
            if is_bg: 
                if self.state == DDPState.DISCONNECTED: return None
                continue

            msg_type = data[0] & self.PKT_TYPE_MASK
            msg_seq = data[0] & self.PKT_SEQ_MASK

            if msg_type == self.PKT_TYPE_DATA_BODY:
                payload_buffer.extend(data[1:])
            elif msg_type in [0x00, self.PKT_TYPE_DATA_END]:
                payload_buffer.extend(data[1:])
                self.send_ack(msg_seq)
                return payload_buffer

        return None

    def _recv_and_ack_data(self, timeout_ms: int) -> Optional[List[int]]:
        data = self._recv_message_chain(timeout_ms)
        if data: return [0x00] + data
        return None

    def resync(self, original_packet: List[int], original_seq: int, next_expected: int):
        num_dummies = (original_seq - next_expected) % 16
        if num_dummies == 0:
            logger.warning("Wrong ACK but num_dummies=0, skipping resync.")
            return
        logger.info(f"Performing sequence resync: {num_dummies} dummies, from seq {next_expected:02X} to {original_seq:02X}")
        self.send_seq_num = next_expected
        dummy_data = [0x00] * 7
        block_count = 0
        while num_dummies > 0:
            if block_count == self.bs - 1:
                pkt_type = self.PKT_TYPE_DATA_CONTINUE 
                first_byte = pkt_type + self.send_seq_num
                packet = [first_byte] + dummy_data
                self.send_can(self.CAN_ID_SEND, packet)
                expected_ack_byte = self.PKT_TYPE_ACK + (self.send_seq_num + 1) % 16
                if not self._recv_specific([expected_ack_byte], self.ack_timeout_ms):
                    raise DDPAckTimeoutError("Resync ACK timeout")
                self.send_seq_num = (self.send_seq_num + 1) % 16
                num_dummies -= 1
                block_count = 0
            else:
                pkt_type = self.PKT_TYPE_DATA_BODY
                first_byte = pkt_type + self.send_seq_num
                packet = [first_byte] + dummy_data
                self.send_can(self.CAN_ID_SEND, packet)
                self.send_seq_num = (self.send_seq_num + 1) % 16
                num_dummies -= 1
                block_count += 1

    # --- NEW: Breathing Logic Helper ---
    def _wait_for_ack_with_breathing(self, expected_ack_byte: int) -> bool:
        """
        Waits for ACK. If timeout, enters 'Breathing Loop' (Sending A3)
        to allow slow clusters to catch up.
        """
        # 1. Try normal wait first
        if self._recv_specific([expected_ack_byte], self.ack_timeout_ms):
            return True
        
        # 2. Check if we got it in the buffer
        if self._rx_buffer_ack and self._rx_buffer_ack[0] == expected_ack_byte:
            return True
        
        # 3. Enter Breathing Loop
        logger.warning(f"ACK {expected_ack_byte:02X} Timeout. Entering Breathing Loop (sending A3).")
        
        for i in range(10): # Try 10 times (~2-3 seconds total)
            self.send_can(self.CAN_ID_SEND, self.KA_KEEP_PING) # Send A3
            
            start = time.time()
            got_keepalive_response = False
            
            # Wait for response (either the missing ACK or A1)
            while time.time() - start < 0.2: 
                data = self._recv(0.05)
                if not data: continue

                # Check for the missing ACK
                if data[0] == expected_ack_byte:
                    logger.info("Recovered: Delayed ACK received during breathing.")
                    return True
                
                # Check for Keep-Alive Response (A1)
                if data[0] == 0xA1:
                    got_keepalive_response = True
                    # Do not return yet, we still need the ACK, but at least cluster is alive.
                
                # Handle background packets
                self._handle_incoming_packet(data)
                
                # Check buffer again
                if self._rx_buffer_ack and self._rx_buffer_ack[0] == expected_ack_byte:
                    logger.info("Recovered: Delayed ACK found in buffer.")
                    return True

            if not got_keepalive_response:
                logger.warning(f"Breathing: No A1 response in attempt {i+1}.")
            else:
                logger.debug(f"Breathing: Got A1, waiting for ACK...")

        logger.error("Breathing Loop Failed: Cluster did not ACK.")
        return False
    # -----------------------------------

    def send_data_packet(self, data: List[int], is_multi_packet_frame_body: bool = False):
        packet_type = self.PKT_TYPE_DATA_BODY if is_multi_packet_frame_body else self.PKT_TYPE_DATA_END
        first_byte = packet_type + self.send_seq_num
        packet = [first_byte] + data
        original_seq = self.send_seq_num
        self.send_seq_num = (self.send_seq_num + 1) % 16
        
        self.send_can(self.CAN_ID_SEND, packet)
        
        if is_multi_packet_frame_body:
            return 
        
        expected_ack_byte = self.PKT_TYPE_ACK + self.send_seq_num 
        
        # Use new Breathing Logic
        if self._wait_for_ack_with_breathing(expected_ack_byte):
            return

        # If we are here, we failed hard. Try resync logic as last resort?
        if self._rx_buffer_ack:
            received_ack_byte = self._rx_buffer_ack[0]
            if received_ack_byte != expected_ack_byte:
                next_expected = received_ack_byte & 0x0F
                self.resync(packet, original_seq, next_expected)
                if self._recv_specific([expected_ack_byte], self.ack_timeout_ms):
                    return

        raise DDPAckTimeoutError(f"Timeout waiting for ACK {expected_ack_byte:02X}")

    def send_ddp_frame(self, payload: List[int]) -> bool:
        if not payload: return True
        if self.state != DDPState.READY:
            logger.warning("Not READY, storing as pending payload.")
            self.pending_payload = payload[:]
            return False
        
        payload_blocks = [payload[i:i + self.MAX_BYTES_PER_BLOCK] for i in range(0, len(payload), self.MAX_BYTES_PER_BLOCK)]

        try:
            for block in payload_blocks:
                chunks = [block[i:i + 7] for i in range(0, len(block), 7)]
                if not chunks: continue
                last_chunk = chunks.pop()
                for chunk in chunks:
                    self.send_data_packet(chunk, is_multi_packet_frame_body=True)
                self.send_data_packet(last_chunk, is_multi_packet_frame_body=False)
                if self.dis_mode == DisMode.WHITE: time.sleep(0.02)
            self.current_payload = payload[:]
            self.pending_payload = None
            return True
        except (DDPAckTimeoutError, DDPCANError) as e:
            logger.error(f"Failed to send DDP frame: {e}")
            self._set_state(DDPState.DISCONNECTED)
            return False

    def detect_and_open_session(self) -> bool:
        if self.state != DDPState.DISCONNECTED: return True
        logger.info("Detecting cluster type...")
        
        start = time.time()
        while time.time() - start < 1.0: 
            data = self._recv(0.1)
            if not data: continue
            if data[0] == 0xA0:
                logger.info("Detected A0 open request.")
                self.parse_params(data)
                reply = self.build_a1()
                self.send_can(self.CAN_ID_SEND, reply)
                self.i_am_opener = False
                self._set_state(DDPState.SESSION_ACTIVE)
                if len(data) == 6:
                    self.dis_mode = DisMode.WHITE
                    self.ka_format_long = True
                else:
                    self.dis_mode = DisMode.UNKNOWN  
                    self.ka_format_long = False
                return True
        
        logger.info("No broadcast. Attempting Active Open with TP2.0.")
        our_a0 = self.KA_WHITE_OPEN
        self.send_can(self.CAN_ID_SEND, our_a0)
        
        start = time.time()
        while time.time() - start < 0.5:
            data = self._recv(0.05)
            if not data: continue
            if data[0] == 0xA1:
                self.parse_params(data)
                if len(data) == 6:
                    logger.info("A1 (Long) received - White DIS detected.")
                    self.ka_format_long = True
                    self.dis_mode = DisMode.WHITE
                else:
                    logger.info("A1 (Short) received - Color or Red DIS detected.")
                    self.ka_format_long = False
                    self.dis_mode = DisMode.UNKNOWN
                self.i_am_opener = True
                self._set_state(DDPState.SESSION_ACTIVE)
                return True
            self._handle_incoming_packet(data)
        
        logger.info("No TP2.0 response. Attempting TP1.6 Active Open.")
        our_a0_short = [0xA0, self.bs, 0x00]
        self.send_can(self.CAN_ID_SEND, our_a0_short)
        
        start = time.time()
        while time.time() - start < 0.5:
            data = self._recv(0.05)
            if not data: continue
            if data[0] == 0xA1:
                self.parse_params(data)
                logger.info("A1 (TP1.6) received - Red DIS detected.")
                self.ka_format_long = False
                self.dis_mode = DisMode.RED
                self.i_am_opener = True
                self._set_state(DDPState.SESSION_ACTIVE)
                return True
            self._handle_incoming_packet(data)
        return False

    def close_session(self):
        if self.state != DDPState.DISCONNECTED:
            logger.info("Closing session (A8).")
            self.send_can(self.CAN_ID_SEND, self.KA_CLOSE)
            self._set_state(DDPState.DISCONNECTED)

    def release_screen(self) -> bool:
        if self.state != DDPState.READY: return False
        try:
            self.send_data_packet([0x33])
            return True
        except Exception:
            self._set_state(DDPState.DISCONNECTED)
            return False

    def perform_initialization(self) -> bool:
        logger.info(f"Starting Init Step 2 (Mode: {self.dis_mode.name})...")
        self._set_state(DDPState.INITIALIZING)
        self.send_seq_num = 0
        try:
            if self.dis_mode == DisMode.RED:
                data = self._recv_and_ack_data(1000)
                if data is None: return False
                self.send_data_packet([0x20, 0x3B, 0xA0, 0x00])
                data = self._recv_and_ack_data(1000)
                if data is None: return False
                self.send_data_packet([0x33])
            else:
                self.send_data_packet([0x15, 0x01, 0x01, 0x02, 0x00, 0x00])
                data = self._recv_message_chain(1000)
                if not data: 
                    logger.warning("Cluster not ready, will retry later.")
                    return False
                self.send_data_packet([0x01, 0x01, 0x00])
                self.send_data_packet([0x08]) 
                data = self._recv_message_chain(1000) 
                if not data: raise DDPHandshakeError("Init Step 5 Timeout")
                
                if data and len(data) > 1 and data[0] == 0x09:
                    cl = data[1]
                    if cl == 0x10:  # Color
                        type_byte = data[2]
                        if type_byte == 0x03:
                            logger.info("Detected COLOR DIS TYPE 1")
                            self.dis_mode = DisMode.COLOR_TYPE1
                            self.opcode_offset = 0x28
                            self.coord_bytes = 2
                        else:
                            logger.info("Detected COLOR DIS TYPE 2")
                            self.dis_mode = DisMode.COLOR_TYPE2
                            self.opcode_offset = 0x08
                            self.coord_bytes = 1
                    elif cl == 0x20:  # Mono
                        type_byte = data[2] if len(data) > 2 else 0x00
                        if type_byte == 0x03:
                            logger.info("Detected MONO HYBRID (ID 20 03) - Using 8-bit commands")
                            # RNS-E LOG CONFIRMS: It uses 8-bit coords/standard opcodes
                            self.dis_mode = DisMode.MONO_HYBRID
                            self.coord_bytes = 1 # Back to 1 byte
                            self.opcode_offset = 0x00 # Standard opcodes
                        else:
                            self.dis_mode = DisMode.WHITE if self.ka_format_long else DisMode.RED
                            self.opcode_offset = 0x00
                            self.coord_bytes = 1

                    try:
                        idx = data.index(0x30)
                        if idx + 3 < len(data):
                            self.region = data[idx + 3]
                            logger.info(f"Parsed region: {self.region:02X}")
                        else:
                            self.region = 0x31
                    except ValueError:
                        self.region = 0x31
                
                self.send_data_packet([0x20, 0x3B, 0xA0, 0x00])
                self._recv_message_chain(1000)
                self.send_data_packet([0x33])
            
            self.send_can(self.CAN_ID_SEND, self.KA_KEEP_PING)
            reply = self.build_a1()
            self._recv_specific(reply, 1000)
            logger.info("DDP Initialization COMPLETE")
            self._set_state(DDPState.READY)
            self.last_ka_sent = time.time()
            return True
        except Exception as e:
            logger.error(f"Handshake Error: {e}")
            self._set_state(DDPState.DISCONNECTED)
            return False

    def send_keepalive_if_needed(self):
        if self.state not in [DDPState.READY, DDPState.PAUSED]: return
        if self.i_am_opener and time.time() - self.last_ka_sent > 2.0:
            self.send_can(self.CAN_ID_SEND, self.KA_KEEP_PING)
            self.last_ka_sent = time.time()

    def poll_bus_events(self):
        if self.state == DDPState.DISCONNECTED: return
        while True:
            data = self._recv(0)
            if not data: break
            is_bg = self._handle_incoming_packet(data)
            if not is_bg:
                msg_type = data[0] & self.PKT_TYPE_MASK
                msg_seq = data[0] & self.PKT_SEQ_MASK
                payload = data[1:]
                
                if msg_type in [0x00, self.PKT_TYPE_DATA_END]:
                    self.send_ack(msg_seq)

                if payload in [DDPMessages.STAT_BUSY_WARN_HALF, DDPMessages.STAT_BUSY_HALF,
                               DDPMessages.STAT_BUSY_WARN_FULL, DDPMessages.STAT_BUSY_FULL]:
                    if self.state != DDPState.PAUSED:
                        logger.info(f"Cluster Busy (Mono) -> PAUSED. Payload: {payload}")
                        self._set_state(DDPState.PAUSED)
                        self.send_can(self.CAN_ID_SEND, self.KA_KEEP_PING)
                
                elif payload in [DDPMessages.STAT_COLOR_BUSY_HALF, DDPMessages.STAT_COLOR_BUSY_WARN_HALF,
                                 DDPMessages.STAT_COLOR_BUSY_FULL, DDPMessages.STAT_COLOR_BUSY_WARN_FULL]:
                    if self.state != DDPState.PAUSED:
                        logger.info(f"Cluster Busy (Color) -> PAUSED. Payload: {payload}")
                        self._set_state(DDPState.PAUSED)
                        self.send_can(self.CAN_ID_SEND, self.KA_KEEP_PING)

                elif payload in [DDPMessages.STAT_COLOR_FREE_HALF, DDPMessages.STAT_COLOR_FREE_FULL]:
                     if self.dis_mode in [DisMode.COLOR_TYPE1, DisMode.COLOR_TYPE2]:
                         if self.state == DDPState.PAUSED:
                             logger.info(f"Color Cluster FREE ({payload}) -> Resuming immediately (No Re-Init expected).")
                             self._set_state(DDPState.READY)
                     else:
                         logger.info(f"Cluster Status FREE ({payload}). Waiting for Re-Init...")

                elif payload in [DDPMessages.STAT_FREE_HALF, DDPMessages.STAT_FREE_FULL]:
                     logger.info(f"Cluster Status FREE ({payload}). Waiting for Re-Init...")

                elif payload == DDPMessages.CMD_REINIT_REQ:
                    logger.info("Re-Init Request. Sending Confirm.")
                    first_byte = self.PKT_TYPE_DATA_END + self.send_seq_num
                    self.send_can(self.CAN_ID_SEND, [first_byte] + DDPMessages.CMD_REINIT_CONF)
                    self.send_seq_num = (self.send_seq_num + 1) % 16
                    self._set_state(DDPState.READY)
