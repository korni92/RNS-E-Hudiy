#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# - FIX: Added logic to clear line background only when transition requires it
#   (Red -> Black) to prevent ghosting without causing flicker.
# - FIX: Added 'clear_area' command for precise cleanup.
#
import zmq
import json
import time
import logging
from typing import List, Optional

try:
    from ddp_protocol import DDPProtocol, DDPState, DisMode, DDPError, DDPHandshakeError
except ImportError:
    print("Error: Could not import DDPProtocol. Make sure ddp_protocol.py is in the same directory.")
    exit(1)

try:
    from icons import audscii_trans, ICONS, BITMAPS 
except ImportError:
    print("Error: Could not import icons.py. Make sure it is in the same directory.")
    exit(1)

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] (DIS Svc) %(message)s')
logger = logging.getLogger(__name__)

class DisService:
    def __init__(self, config_path='/home/pi/config.json'):
        try:
            with open(config_path) as f:
                self.config = json.load(f)
        except FileNotFoundError:
            logger.critical(f"FATAL: config.json not found at {config_path}")
            exit(1)
        except Exception as e:
            logger.critical(f"FATAL: Could not load config.json: {e}")
            exit(1)
            
        try:
            self.ddp = DDPProtocol(self.config)
        except Exception as e:
            logger.critical(f"FATAL: Could not initialize DDPProtocol driver: {e}")
            exit(1)

        self.context = zmq.Context()
        self.draw_socket = self.context.socket(zmq.PULL)
        try:
            self.draw_socket.bind(self.config['zmq']['dis_draw'])
            logger.info(f"ZMQ command socket bound to {self.config['zmq']['dis_draw']}")
        except Exception as e:
            logger.critical(f"FATAL: Could not bind ZMQ socket: {e}")
            logger.critical("This often means the service is already running (Address already in use).")
            exit(1)
            
        self.poller = zmq.Poller()
        self.poller.register(self.draw_socket, zmq.POLLIN)

        self.last_draw_time = 0.0
        self.screen_is_active = False
        self.inactivity_timeout_sec = 30.0 
        self.command_cache = {} 
        self.ENABLE_INACTIVITY_RELEASE = False

        if not self.ENABLE_INACTIVITY_RELEASE:
            logger.info("Inactivity auto-release is DISABLED (screen will stay claimed forever)")

    def parse_time(self, t: str) -> int:
        if not t: return 0
        parts = t.split(':')
        return sum(int(p) * (60 ** i) for i, p in enumerate(reversed(parts)))

    def translate_to_audscii(self, text: str) -> List[int]:
        return [audscii_trans[ord(c) % 256] for c in text]

    def claim_nav_screen(self):
        if self.ddp.state != DDPState.READY:
            logger.warning("Cannot claim screen, session not READY.")
            return False
        
        payload_claim = [0x52, 0x05, 0x82, 0x00, 0x1B, 0x40, 0x30]
        payload_busy  = [0x53, 0x84]
        payload_free  = [0x53, 0x05]
        payload_ready = [0x2E]
        payload_clear = [0x2F]
        payload_ok    = [0x53, 0x85]
            
        if self.ddp.dis_mode == DisMode.RED:
            try:
                self.ddp.send_data_packet(payload_claim)
                data = self.ddp._recv_and_ack_data(1000)
                if not self.ddp.payload_is(data, payload_ok):
                    raise DDPHandshakeError(f"Claim Handshake 2/2 failed (wait 1x 53 85), got {data}")
            except DDPError as e:
                logger.error(f"Failed to claim screen (RED path): {e}")
                return False
        else:
            try:
                self.ddp.send_data_packet(payload_claim)
                data = self.ddp._recv_and_ack_data(1000)
                if self.ddp.payload_is(data, payload_ok):
                    self.screen_is_active = True
                    self.last_draw_time = time.time()
                    return True
                if not self.ddp.payload_is(data, payload_busy):
                    raise DDPHandshakeError(f"Claim Handshake 2/7 failed (wait 1x 53 84), got {data}")
                data = self.ddp._recv_and_ack_data(1000)
                if not self.ddp.payload_is(data, payload_free):
                    raise DDPHandshakeError(f"Claim Handshake 3/7 failed (wait 1x 53 05), got {data}")
                data = self.ddp._recv_and_ack_data(1000)
                if not self.ddp.payload_is(data, payload_ready):
                    raise DDPHandshakeError(f"Claim HandShak 4/7 failed (wait 1x 2E), got {data}")
                self.ddp.send_data_packet(payload_clear)
                self.ddp.send_data_packet(payload_claim)
                data = self.ddp._recv_and_ack_data(1000)
                if not self.ddp.payload_is(data, payload_ok):
                    logger.warning(f"Got non-standard status {data} after 2nd claim, but proceeding.")
            except DDPError as e:
                logger.error(f"Failed to claim screen (WHITE path): {e}")
                return False
            
        logger.info("Region Claim handshake successful. Screen is active.")
        self.screen_is_active = True
        self.last_draw_time = time.time()
        return True

    def clear_screen_payload(self):
        logger.info("Queueing Region Clear")
        payload = [0x52, 0x05, 0x02, 0x00, 0x1B, 0x40, 0x30]
        if not self.ddp.send_ddp_frame(payload):
            logger.error("Failed to send clear payload.")

    def clear_area(self, x, y, w, h):
        """
        Explicitly clears a specific rectangle to BLACK.
        Used to erase artifacts or Red Highlights.
        """
        abs_y = y + 0x1B
        # Flag 0x02: Clear(Bit 7=0), Clear(Bit 1=1), Black(Bit 0=0)
        payload = [0x52, 0x05, 0x02, x, abs_y, w, h]
        self.ddp.send_ddp_frame(payload)
        
        # Reset Window
        payload_reset = [0x52, 0x05, 0x00, 0x00, 0x1B, 0x40, 0x30]
        self.ddp.send_ddp_frame(payload_reset)

    def write_text(self, text: str, x: int, y: int, flags: int = 0x06):
        chars = self.translate_to_audscii(text) 
        
        is_inverted = (flags & 0x80) != 0
        protocol_flags = flags & 0x7C 
        
        if is_inverted:
            # INVERTED MODE (Red Background)
            abs_y = y + 0x1B
            width = 64
            height = 9
            
            # 1. Clear Red
            payload_bg = [0x52, 0x05, 0x03, x, abs_y, width, height]
            self.ddp.send_ddp_frame(payload_bg)
            
            # 2. Draw Text (XOR)
            text_mode_bits = 0x00 
            final_text_flags = protocol_flags | text_mode_bits
            
            payload_text = [0x57, len(chars) + 3, final_text_flags, 0, 0] + chars
            self.ddp.send_ddp_frame(payload_text)
            
            # 3. Reset Window
            payload_reset = [0x52, 0x05, 0x00, 0x00, 0x1B, 0x40, 0x30]
            self.ddp.send_ddp_frame(payload_reset)
            
        else:
            # NORMAL MODE (Black Background)
            # Optimization: We rely on DisplayEngine to call 'clear_area' if we are
            # transitioning from Red->Black to remove ghosting. 
            # Otherwise, we just draw over existing pixels.
            
            text_mode_bits = 0x02 # Opaque + Normal
            final_text_flags = protocol_flags | text_mode_bits
            payload = [0x57, len(chars) + 3, final_text_flags, x, y] + chars
            self.ddp.send_ddp_frame(payload)

    def draw_bitmap(self, x: int, y: int, icon_name: str):
        if not icon_name or icon_name not in BITMAPS:
            logger.error(f"Bitmap icon '{icon_name}' not found.")
            return

        icon = BITMAPS[icon_name]
        w = icon['w']
        h = icon['h']
        data = icon['data']
        abs_y = y + 0x1B

        payload_clip = [0x52, 0x05, 0x00, x, abs_y, w, h]
        if not self.ddp.send_ddp_frame(payload_clip): return

        bytes_per_row = (w + 7) // 8
        rows_per_chunk = 37 // bytes_per_row
        if rows_per_chunk < 1: rows_per_chunk = 1
        
        for i in range(0, h, rows_per_chunk):
            start_byte = i * bytes_per_row
            rows_to_send = min(rows_per_chunk, h - i)
            end_byte = start_byte + (rows_to_send * bytes_per_row)
            chunk_data = data[start_byte:end_byte]
            chunk_y = i 
            payload_bmp = [0x55, len(chunk_data) + 3, 0x02, 0x00, chunk_y] + chunk_data
            if not self.ddp.send_ddp_frame(payload_bmp): return

        payload_reset = [0x52, 0x05, 0x00, 0x00, 0x1B, 0x40, 0x30]
        self.ddp.send_ddp_frame(payload_reset)
        logger.info(f"Bitmap '{icon_name}' drawn at Abs({x},{abs_y})")

    def draw_line(self, x: int, y: int, length: int, vertical: bool = True):
        orientation = 0x10 if vertical else 0x20
        payload = [0x63, 0x04, orientation, x, y, length]
        if not self.ddp.send_ddp_frame(payload):
            logger.error("Failed to send line payload.")

    def commit_frame(self):
        payload = [0x39]
        if not self.ddp.send_ddp_frame(payload):
             logger.error("Failed to send commit packet.")
        time.sleep(0.10)

    def clear_screen(self):
        logger.info("Executing full clear_screen command...")
        payload_clear = [0x52, 0x05, 0x02, 0x00, 0x1B, 0x40, 0x30]
        payload_commit = [0x39]
        if not self.ddp.send_ddp_frame(payload_clear + payload_commit):
            logger.error("clear_screen: Failed to send frame.")
            
    def set_source_radio(self):
        self.ddp.send_can(0x661, [0x00] * 8)
        logger.info("Source: Radio")

    def handle_redraw(self):
        if not self.command_cache: return
        logger.info("Restoring screen content after interruption...")
        self.clear_screen_payload() 
        sorted_cmds = sorted(self.command_cache.values(), key=lambda item: (item.get('y',0), item.get('x',0)))
        
        for cmd in sorted_cmds:
            c = cmd.get('command')
            if c == 'draw_text':
                self.write_text(cmd.get('text',''), cmd.get('x',0), cmd.get('y',0), cmd.get('flags', 0x06))
            elif c == 'draw_bitmap':
                self.draw_bitmap(cmd.get('x',0), cmd.get('y',0), cmd.get('icon_name'))
            elif c == 'draw_line':
                self.draw_line(cmd.get('x',0), cmd.get('y',0), cmd.get('length',0), cmd.get('vertical', True))
        
        self.commit_frame()

    def run(self):
        logger.info("DIS Service Started. Entering main loop.")
        while True:
            try:
                if self.ddp.state == DDPState.DISCONNECTED:
                    self.screen_is_active = False
                    if self.ddp.detect_and_open_session():
                        logger.info(f"Session established (Mode: {self.ddp.dis_mode.name}).")
                    else:
                        time.sleep(3)
                elif self.ddp.state == DDPState.SESSION_ACTIVE:
                    if not self.ddp.perform_initialization():
                        logger.error("DDP Initialization failed. Retrying.")
                        time.sleep(3)
                    else:
                        self.set_source_radio()
                        logger.info("DDP READY.")
                        self.last_draw_time = time.time()
                        self.screen_is_active = False
                elif self.ddp.state == DDPState.PAUSED:
                    if self.screen_is_active:
                        logger.info("Service PAUSED by Cluster. Waiting for release...")
                        self.screen_is_active = False
                    self.ddp.send_keepalive_if_needed()
                    self.ddp.poll_bus_events()
                    try:
                        while True:
                            self.draw_socket.recv_json(flags=zmq.NOBLOCK)
                    except zmq.Again:
                        pass
                    time.sleep(0.05)
                    continue
                elif self.ddp.state == DDPState.READY:
                    self.ddp.send_keepalive_if_needed()
                    self.ddp.poll_bus_events()
                    if self.ddp.state != DDPState.READY:
                        continue 
                    if not self.screen_is_active and self.command_cache:
                         logger.info("Auto-Restore triggered.")
                         if self.claim_nav_screen():
                             self.handle_redraw()
                    socks = dict(self.poller.poll(5))
                    if self.draw_socket in socks:
                        while True:
                            try:
                                cmd = self.draw_socket.recv_json(flags=zmq.NOBLOCK)
                                if not self.screen_is_active:
                                    if not self.claim_nav_screen():
                                        logger.error("Failed to claim screen.")
                                        break 
                                self.last_draw_time = time.time()
                                c = cmd.get('command')
                                if c == 'clear':
                                    self.command_cache = {}
                                    self.clear_screen()
                                elif c == 'clear_area':
                                    self.clear_area(cmd.get('x',0), cmd.get('y',0), cmd.get('w',64), cmd.get('h',9))
                                elif c == 'clear_payload':
                                    self.command_cache = {}
                                    self.clear_screen_payload()
                                elif c == 'draw_text':
                                    k = ('draw_text', cmd.get('y', 0), cmd.get('x', 0))
                                    self.command_cache[k] = cmd
                                    self.write_text(cmd.get('text', ''), cmd.get('x', 0), cmd.get('y', 0), cmd.get('flags', 0x06))
                                elif c == 'draw_bitmap':
                                    k = ('draw_bitmap', cmd.get('y', 0), cmd.get('x', 0))
                                    self.command_cache[k] = cmd
                                    self.draw_bitmap(cmd.get('x', 0), cmd.get('y', 0), cmd.get('icon_name'))
                                elif c == 'draw_line':
                                    k = ('draw_line', cmd.get('y', 0), cmd.get('x', 0))
                                    self.command_cache[k] = cmd
                                    self.draw_line(cmd.get('x', 0), cmd.get('y', 0), cmd.get('length', 0), cmd.get('vertical', True))
                                elif c == 'commit':
                                    self.commit_frame()
                            except zmq.Again:
                                break 
                    if (self.ENABLE_INACTIVITY_RELEASE
                        and self.screen_is_active
                        and (time.time() - self.last_draw_time > self.inactivity_timeout_sec)):
                        logger.info("Inactivity timeout. Releasing screen.")
                        if self.ddp.release_screen():
                            self.screen_is_active = False
                        else:
                            self.screen_is_active = False
                time.sleep(0.01)
            except Exception as e:
                logger.error(f"Main loop error: {e}", exc_info=True)
                if hasattr(self, 'ddp'):
                    self.ddp._set_state(DDPState.DISCONNECTED)
                time.sleep(3)

if __name__ == "__main__":
    try:
        DisService(config_path='/home/pi/config.json').run()
    except KeyboardInterrupt:
        logger.info("Shutting down service.")
