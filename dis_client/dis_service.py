#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# Unified Audi DIS Service (Color & Monochrome Support)
# V4.0 - Visual Polish (Anti-Ghosting, Smart Caching, Traffic Reduction)
#
import zmq
import json
import time
import logging
from typing import List, Dict
try:
    from ddp_protocol import DDPProtocol, DDPState, DisMode, DDPError, DDPHandshakeError
except ImportError:
    print("Error: Could not import DDPProtocol. Ensure ddp_protocol.py is present.")
    exit(1)
try:
    from icons import audscii_trans, BITMAPS
except ImportError:
    print("Error: Could not import icons.py.")
    exit(1)
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] (DIS Svc) %(message)s')
logger = logging.getLogger(__name__)
class DisService:
    def __init__(self, config_path='/home/pi/config.json'):
        try:
            with open(config_path) as f: self.config = json.load(f)
        except Exception as e:
            logger.critical(f"Config Load Error: {e}")
            exit(1)
        try:
            self.ddp = DDPProtocol(self.config)
        except Exception as e:
            logger.critical(f"DDP Driver Init Error: {e}")
            exit(1)
        self.context = zmq.Context()
        self.draw_socket = self.context.socket(zmq.PULL)
        try:
            self.draw_socket.bind(self.config['zmq']['dis_draw'])
            logger.info(f"ZMQ listening on {self.config['zmq']['dis_draw']}")
        except Exception as e:
            logger.critical(f"ZMQ Bind Error: {e}")
            exit(1)
       
        self.poller = zmq.Poller()
        self.poller.register(self.draw_socket, zmq.POLLIN)
        self.screen_is_active = False
        self.command_cache = {}
        self.last_draw_time = 0.0
        self.ENABLE_AUTO_RELEASE = False
        self.INACTIVITY_TIMEOUT = 30.0
       
        # --- V4.0: Visual State Caching ---
        # Stores the hash/content of each line to prevent redundant writes
        # Now stores hash, text length, inverted for better artifact handling
        self.line_cache: Dict[int, tuple[int, int, bool]] = {}  # y: (hash, len(text), is_inverted)
        self.SCROLL_DELAY = 0.5  # Slower scrolling
        self.CHAR_WIDTH_MONO = 5  # Assumed character width in pixels for mono displays
        self.LINE_WIDTH_MONO = 64  # Line width in pixels for mono
        self.LINE_HEIGHT_MONO = 9  # Line height in pixels for mono
        self.CHAR_WIDTH_COLOR = 10  # Assumed for color
        self.LINE_WIDTH_COLOR = 220  # For color
    # --- Helpers ---
    def _pack_u16(self, val: int) -> List[int]:
        return [val & 0xFF, (val >> 8) & 0xFF]
    def _translate_audscii(self, text: str) -> List[int]:
        return [audscii_trans[ord(c) % 256] for c in text]
    def _is_color_mode(self):
        return self.ddp.dis_mode in [DisMode.COLOR_TYPE1, DisMode.COLOR_TYPE2]
    # --- Core DDP Commands ---
    def claim_nav_screen(self) -> bool:
        if self.ddp.state != DDPState.READY: return False
        if self._is_color_mode():
            try:
                op_win = 0x7A if self.ddp.dis_mode == DisMode.COLOR_TYPE1 else 0x52
                payload = [op_win, 0x09, 0x82] + \
                          self._pack_u16(0) + self._pack_u16(120) + \
                          self._pack_u16(220) + self._pack_u16(240)
                self.ddp.send_ddp_frame(payload)
               
                data = self.ddp._recv_and_ack_data(1000)
                if data and len(data) >= 2 and data[0] == 0x7B:
                    status = data[1]
                    if status in [0x85, 0x05, 0x8A]:
                        self.screen_is_active = True
                        return True
                    elif status in [0x04, 0x84, 0x08, 0x88]:
                        logger.info(f"Color Claim BUSY (7B {status:02X}), waiting...")
                        data = self.ddp._recv_and_ack_data(2000)
                        if data and len(data)>=2 and data[1] in [0x05, 0x0A]:
                            self.screen_is_active = True
                            return True
                        return False
                    elif status == 0xC0:
                        logger.error("Color Claim FAILED: Invalid Coords (7B C0)")
                        return False
                self.screen_is_active = True
                return True
            except Exception as e:
                logger.error(f"Color Claim Failed: {e}")
                return False
        else:
            payload_claim = [0x52, 0x05, 0x82, 0x00, 0x1B, 0x40, 0x30]
            payload_ok    = [0x53, 0x85]
            payload_busy  = [0x53, 0x84]
            payload_free  = [0x53, 0x05]
            payload_ready = [0x2E]
            payload_clear_conf = [0x2F]
            try:
                self.ddp.send_data_packet(payload_claim)
                data = self.ddp._recv_and_ack_data(1000)
               
                if self.ddp.payload_is(data, payload_ok):
                    self.screen_is_active = True
                    return True
               
                if self.ddp.payload_is(data, payload_busy):
                    data = self.ddp._recv_and_ack_data(2000)
                    if not self.ddp.payload_is(data, payload_free):
                        raise DDPHandshakeError(f"Wait Free failed, got {data}")
                    data = self.ddp._recv_and_ack_data(1000)
                    if not self.ddp.payload_is(data, payload_ready):
                        raise DDPHandshakeError(f"Wait Ready failed, got {data}")
                    self.ddp.send_data_packet(payload_clear_conf)
                    self.ddp.send_data_packet(payload_claim)
                    data = self.ddp._recv_and_ack_data(1000)
                    if self.ddp.payload_is(data, payload_ok):
                        self.screen_is_active = True
                        return True
                       
                logger.warning(f"Claim gave unexpected response {data}, assuming active.")
                self.screen_is_active = True
                return True
            except DDPError as e:
                logger.error(f"Mono Claim Failed: {e}")
                return False
    def clear_screen(self):
        if not self.screen_is_active: self.claim_nav_screen()
        # Invalidate cache on full clear
        self.line_cache = {}
        if self._is_color_mode():
            op = 0x83 if self.ddp.dis_mode == DisMode.COLOR_TYPE1 else 0x52
            if op == 0x83:
                payload = [op, 0x09, 0x00] + self._pack_u16(0) + self._pack_u16(0) + self._pack_u16(220) + self._pack_u16(240)
                self.ddp.send_ddp_frame(payload)
            else:
                self.ddp.send_ddp_frame([0x52 + self.ddp.opcode_offset, 0x05, 0x02, 0x00, 0x00, 0xFF, 0xFF])
        else:
            payload = [0x52 + self.ddp.opcode_offset, 0x05, 0x02, 0x00, 0x1B, 0x40, 0x30]
            self.ddp.send_ddp_frame(payload)
            self.ddp.send_ddp_frame([0x52 + self.ddp.opcode_offset, 0x05, 0x00, 0x00, 0x1B, 0x40, 0x30])
    def write_text(self, text: str, x: int, y: int, font: int = 0x28, color: int = 0x07, flags: int = 0x06, opcode: int = None, force_update: bool = False):
        cache_key = hash((x, y, text, font, color, flags))
        text_len = len(text)
        is_inverted = (flags & 0x80) != 0
        changed = force_update
        old_len = 0
        old_is_inverted = False
        if y in self.line_cache:
            old_key, old_len, old_is_inverted = self.line_cache[y]
            if cache_key == old_key and not force_update:
                return  # Skip redundant update
            changed = True
        else:
            changed = True
        
        self.line_cache[y] = (cache_key, text_len, is_inverted)
        chars = self._translate_audscii(text)
        
        if self._is_color_mode():
            op = opcode if opcode else 0x5F
            final_color = 0x07 if color == 0x70 else color
            font_height = 16  # Assumed; adjust based on font if known
            if changed:
                if text_len < old_len:
                    # Clear full line if length decreased
                    self.draw_rectangle(0, y, self.LINE_WIDTH_COLOR, font_height, 0)
                elif force_update:
                    self.draw_rectangle(0, y, self.LINE_WIDTH_COLOR, font_height, 0)
            if op == 0x5F:
                payload = [op, len(chars)+5, font, final_color, x & 0xFF, y & 0xFF] + chars
            else:
                payload = [op, len(chars)+7, font, final_color] + self._pack_u16(x) + self._pack_u16(y) + chars
            self.ddp.send_ddp_frame(payload)
        else:
            abs_y = y + 0x1B
            protocol_flags = flags & 0x7C
            if changed or force_update:
                if is_inverted:
                    # For inverted, always clear the area to red
                    width = self.LINE_WIDTH_MONO  # Full line for simplicity
                    height = self.LINE_HEIGHT_MONO
                    payload_bg = [0x52, 0x05, 0x03, x, abs_y, width, height]
                    self.ddp.send_ddp_frame(payload_bg)
                    text_mode_bits = 0x00  # XOR for inverted
                    final_flags = protocol_flags | text_mode_bits
                    payload_text = [0x57 + self.ddp.opcode_offset, len(chars) + 3, final_flags, 0, 0] + chars
                    self.ddp.send_ddp_frame(payload_text)
                    payload_reset = [0x52, 0x05, 0x00, 0x00, 0x1B, 0x40, 0x30]
                    self.ddp.send_ddp_frame(payload_reset)
                else:
                    # For normal, clear only if transition from inverted or length decreased
                    if old_is_inverted:
                        # Clear full line to black
                        self.ddp.send_ddp_frame([0x52, 0x05, 0x02, 0, abs_y, self.LINE_WIDTH_MONO, self.LINE_HEIGHT_MONO])
                        self.ddp.send_ddp_frame([0x52, 0x05, 0x00, 0x00, 0x1B, 0x40, 0x30])
                    if text_len < old_len:
                        # Clear trailing area
                        trail_x = x + text_len * self.CHAR_WIDTH_MONO
                        trail_w = old_len * self.CHAR_WIDTH_MONO - text_len * self.CHAR_WIDTH_MONO
                        if trail_w > 0:
                            self.ddp.send_ddp_frame([0x52, 0x05, 0x02, trail_x, abs_y, trail_w, self.LINE_HEIGHT_MONO])
                            self.ddp.send_ddp_frame([0x52, 0x05, 0x00, 0x00, 0x1B, 0x40, 0x30])
                    # Draw text opaque
                    text_mode_bits = 0x02
                    final_flags = protocol_flags | text_mode_bits
                    payload = [0x57 + self.ddp.opcode_offset, len(chars) + 3, final_flags, x, y] + chars
                    self.ddp.send_ddp_frame(payload)
    def draw_bitmap(self, x: int, y: int, icon_name: str):
        if icon_name not in BITMAPS: return
        icon = BITMAPS[icon_name]
        w, h, data = icon['w'], icon['h'], icon['data']
       
        if self._is_color_mode():
            pass  # Implement if supported
        else:
            abs_y = y + 0x1B
            self.ddp.send_ddp_frame([0x52 + self.ddp.opcode_offset, 0x05, 0x00, x, abs_y, w, h])
            bytes_per_row = (w + 7) // 8
            rows_per_chunk = 30 // bytes_per_row
            if rows_per_chunk < 1: rows_per_chunk = 1
            op_bmp = 0x55 + self.ddp.opcode_offset
            for i in range(0, h, rows_per_chunk):
                start = i * bytes_per_row
                chunk_rows = min(rows_per_chunk, h - i)
                end = start + (chunk_rows * bytes_per_row)
                chunk = data[start:end]
                payload = [op_bmp, len(chunk)+3, 0x02, 0x00, i] + chunk
                self.ddp.send_ddp_frame(payload)
            self.ddp.send_ddp_frame([0x52 + self.ddp.opcode_offset, 0x05, 0x00, 0x00, 0x1B, 0x40, 0x30])
    def draw_rectangle(self, x, y, w, h, color_idx):
        if self._is_color_mode():
            payload = [0x83, 0x09, color_idx] + self._pack_u16(x) + self._pack_u16(y) + self._pack_u16(w) + self._pack_u16(h)
            self.ddp.send_ddp_frame(payload)
        else:
            abs_y = y + 0x1B
            if color_idx == 0:
                self.ddp.send_ddp_frame([0x52 + self.ddp.opcode_offset, 0x05, 0x02, x, abs_y, w, h])
                self.ddp.send_ddp_frame([0x52 + self.ddp.opcode_offset, 0x05, 0x00, 0x00, 0x1B, 0x40, 0x30])
            else:
                orient = 0x20 if w > h else 0x10
                length = w if w > h else h
                self.ddp.send_ddp_frame([0x63 + self.ddp.opcode_offset, 0x04, orient, x, abs_y, length])
    def draw_line(self, x: int, y: int, length: int, vertical: bool = True):
        if self._is_color_mode():
            # Implement if supported
            pass
        else:
            orientation = 0x10 if vertical else 0x20
            abs_y = y + 0x1B if not vertical else y
            payload = [0x63 + self.ddp.opcode_offset, 0x04, orientation, x, abs_y, length]
            self.ddp.send_ddp_frame(payload)
    def clear_area(self, x: int, y: int, w: int, h: int):
        self.draw_rectangle(x, y, w, h, 0)
    def commit_frame(self):
        self.ddp.send_ddp_frame([0x39])
    # --- Main Loop ---
    def restore_screen(self):
        if not self.command_cache: return
        logger.info("Restoring screen content...")
       
        self.clear_screen()
       
        sorted_cmds = sorted(self.command_cache.values(), key=lambda item: (item.get('y',0), item.get('x',0)))
       
        for cmd in sorted_cmds:
            self._execute_command(cmd, cache=False, force_update=True, dry_run=False)
       
        self.commit_frame()
    def _execute_command(self, cmd: dict, cache: bool = True, force_update: bool = False, dry_run: bool = False):
        c = cmd.get('command')
        if cache and c in ['draw_text', 'draw_bitmap', 'draw_rect', 'draw_line']:
            key = (c, cmd.get('y', 0), cmd.get('x', 0))
            self.command_cache[key] = cmd
        if c == 'clear':
            self.command_cache = {}
            self.line_cache = {}
            if not dry_run:
                self.clear_screen()
        elif c == 'commit':
            if not dry_run:
                self.commit_frame()
        elif c == 'clear_area':
            if not dry_run:
                self.clear_area(cmd.get('x',0), cmd.get('y',0), cmd.get('w', self.LINE_WIDTH_COLOR if self._is_color_mode() else self.LINE_WIDTH_MONO), cmd.get('h', self.LINE_HEIGHT_MONO))
        elif c == 'draw_text':
            if not dry_run:
                self.write_text(
                    cmd.get('text', ''), cmd.get('x', 0), cmd.get('y', 0),
                    font=cmd.get('font', 0x28), color=cmd.get('color', 0x07),
                    flags=cmd.get('flags', 0x06), opcode=cmd.get('opcode', None),
                    force_update=force_update
                )
        elif c == 'draw_bitmap':
            if not dry_run:
                self.draw_bitmap(cmd.get('x', 0), cmd.get('y', 0), cmd.get('icon_name'))
        elif c == 'draw_rect':
            if not dry_run:
                self.draw_rectangle(
                    cmd.get('x', 0), cmd.get('y', 0),
                    cmd.get('w', 10), cmd.get('h', 10), cmd.get('color', 0x04)
                )
        elif c == 'draw_line':
            if not dry_run:
                self.draw_line(
                    cmd.get('x', 0), cmd.get('y', 0),
                    cmd.get('length', 0), cmd.get('vertical', True)
                )
    def run(self):
        logger.info("DIS Service Started")
        try:
            while True:
                try:
                    if self.ddp.state == DDPState.DISCONNECTED:
                        self.screen_is_active = False
                        if self.ddp.detect_and_open_session():
                            logger.info(f"Connected: {self.ddp.dis_mode.name}")
                        else:
                            time.sleep(2)
                   
                    elif self.ddp.state == DDPState.SESSION_ACTIVE:
                        if self.ddp.perform_initialization():
                            self.ddp.send_can(0x661, [0]*8)
                            self.screen_is_active = False
                        else:
                            time.sleep(1)
                    elif self.ddp.state == DDPState.PAUSED:
                        self.screen_is_active = False
                        self.ddp.poll_bus_events()
                        self.ddp.send_keepalive_if_needed()
                        socks = dict(self.poller.poll(10))
                        if self.draw_socket in socks:
                            try:
                                while True:
                                    cmd = self.draw_socket.recv_json(flags=zmq.NOBLOCK)
                                    self._execute_command(cmd, cache=True, dry_run=True)
                            except zmq.Again:
                                pass
                        time.sleep(0.1)
                    elif self.ddp.state == DDPState.READY:
                        self.ddp.poll_bus_events()
                        self.ddp.send_keepalive_if_needed()
                       
                        if not self.screen_is_active and self.command_cache:
                            if self.claim_nav_screen():
                                self.restore_screen()
                        socks = dict(self.poller.poll(10))
                        if self.draw_socket in socks:
                            try:
                                while True:
                                    cmd = self.draw_socket.recv_json(flags=zmq.NOBLOCK)
                                    if not self.screen_is_active:
                                        if not self.claim_nav_screen(): continue
                                    self.last_draw_time = time.time()
                                    self._execute_command(cmd, cache=True, dry_run=False)
                            except zmq.Again:
                                pass
                       
                        if self.ENABLE_AUTO_RELEASE and self.screen_is_active:
                             if (time.time() - self.last_draw_time) > self.INACTIVITY_TIMEOUT:
                                 if self.ddp.release_screen(): self.screen_is_active = False
                except Exception as e:
                    logger.error(f"Main Loop Error: {e}", exc_info=True)
                    if hasattr(self, 'ddp'): self.ddp._set_state(DDPState.DISCONNECTED)
                    time.sleep(3)
        except KeyboardInterrupt:
            logger.info("Shutting down service (User Interrupt).")
            if hasattr(self, 'ddp'):
                self.ddp.close_session()
if __name__ == "__main__":
    DisService().run()
