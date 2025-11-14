#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# Audi DIS Media Player Display Alpha 1
#
# Buffer only 49 Bytes!!!
#
import zmq
import json
import time
import logging
import sys
from typing import List, Optional, Dict
from enum import Enum

# ----------------------------------------------------------------------
# Logging
# ----------------------------------------------------------------------
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] (DIS App) %(message)s')
logger = logging.getLogger(__name__)

# ----------------------------------------------------------------------
# DisplayManager
# ----------------------------------------------------------------------
class DisplayMode(Enum):
    MUSIC = 0
    NAVIGATION = 1
    PHONE = 2

class DisplayManager:
    
    # --- !!! YOU MUST CHANGE THESE !!! ---
    # These are placeholders. Use `candump can0` to find your real values. (Mostly 5C3)
    MFSW_CAN_ID = 'CAN_799'     # Your test ID
    BUTTON_UP_DATA = [16]   # [0x10]
    BUTTON_DOWN_DATA = [32] # [0x20]
    # --- !!! YOU MUST CHANGE THESE !!! ---
    
    def __init__(self, 
                 music_json: str = '/tmp/now_playing.json',
                 nav_json: str = '/tmp/current_nav.json',
                 call_json: str = '/tmp/current_call.json',
                 config_path='/home/pi/config.json'):
        
        # 1. Load Config
        try:
            with open(config_path, 'r') as f:
                self.config = json.load(f)
            self.zmq_conf = self.config['zmq']
            logger.info("Config loaded.")
        except Exception as e:
            logger.critical(f"FATAL: Could not load config.json: {e}")
            sys.exit(1)

        self.context = zmq.Context()

        # --- Save the json paths as class attributes ---
        self.music_json = music_json
        self.nav_json = nav_json
        self.call_json = call_json
        
        # --- ZMQ Sockets (Using correct keys from config.json) ---
        # (Using IPC paths from your full config)
        self.sub_input = self.context.socket(zmq.SUB)
        self.sub_input.connect(self.zmq_conf['publish_address']) 
        self.sub_input.subscribe(self.MFSW_CAN_ID.encode('utf-8'))
        
        self.sub_hudiy = self.context.socket(zmq.SUB)
        self.sub_hudiy.connect(self.zmq_conf['hudiy_publish_address'])
        self.sub_hudiy.subscribe(b'HUDIY_MEDIA')
        self.sub_hudiy.subscribe(b'HUDIY_NAV')
        self.sub_hudiy.subscribe(b'HUDIY_PHONE')

        self.draw_socket = self.context.socket(zmq.PUSH)
        self.draw_socket.connect(self.zmq_conf['dis_draw'])
        
        # --- Unified Poller ---
        self.poller = zmq.Poller()
        self.poller.register(self.sub_input, zmq.POLLIN)
        self.poller.register(self.sub_hudiy, zmq.POLLIN)
        
        # --- Display Layout ---
        self.Y = {'line1': 0, 'line2': 10, 'line3': 20, 'line4': 30}
        self.FLAGS = 0x06 # Proportional, Red
        
        # --- FIX: Max Chars set to 11 ---
        self.MAX_CHARS = 11 
        self.PAD_CHAR = " "

        # --- State Management ---
        self.modes = [DisplayMode.MUSIC, DisplayMode.NAVIGATION, DisplayMode.PHONE]
        self.current_mode = DisplayMode.MUSIC
        self.music_data = {'title': 'Hudiy Offline', 'artist': '', 'album': '', 'duration': '', 'position': ''}
        self.nav_data = {'description': 'No Route Active', 'distance': ''}
        self.phone_data = {'state': 'IDLE', 'caller': 'No Active Call'}
        self.last_data: Dict[str, str] = {}
        self.last_sent: Dict[str, str] = {k: self.PAD_CHAR * self.MAX_CHARS for k in self.Y}
        self.scroll_ofs: Dict[str, int] = {k: 0 for k in self.Y}
        self.scroll_pause: Dict[str, float] = {k: 0.0 for k in self.Y}

    def initialize(self):
        logger.info("Reading initial state from JSON files...")
        self.music_data = self._read_json(self.music_json) or self.music_data
        self.nav_data = self._read_json(self.nav_json) or self.nav_data
        self.phone_data = self._read_json(self.call_json) or self.phone_data
        
        logger.info(f"DisplayManager Ready. Listening for buttons on '{self.MFSW_CAN_ID}' "
                    f"and Hudiy on '{self.zmq_conf['hudiy_publish_address']}'")

    def _read_json(self, path: str) -> dict:
        """Reads a JSON file, returning {} on error."""
        try:
            with open(path, 'r') as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {} # Return empty dict, not None

    def _scrolled_and_padded(self, key: str, txt: str) -> str:
        if len(txt) <= self.MAX_CHARS:
            self.scroll_ofs[key] = 0
            return txt.ljust(self.MAX_CHARS, self.PAD_CHAR)
        if time.time() < self.scroll_pause[key]:
            return (txt + self.PAD_CHAR * 3)[0:self.MAX_CHARS]
        padded = txt + self.PAD_CHAR * 3
        ofs = self.scroll_ofs[key]
        slice_ = padded[ofs:ofs + self.MAX_CHARS]
        new_ofs = (ofs + 1) % (len(padded) - self.MAX_CHARS + 1)
        self.scroll_ofs[key] = new_ofs
        if new_ofs == 0:
            self.scroll_pause[key] = time.time() + 1.0
        return slice_

    def _clear_and_reset_state(self):
        self.draw_socket.send_json({'command': 'clear'})
        self.scroll_ofs = {k: 0 for k in self.Y}
        self.scroll_pause = {k: 0.0 for k in self.Y}
        self.last_sent = {k: self.PAD_CHAR * self.MAX_CHARS for k in self.Y}
        self.last_data = {}

    def _draw_line(self, key: str, text: str, y: int):
        """Sends a simple text-draw command to the handler."""
        visible_text = self._scrolled_and_padded(key, text)
        
        if visible_text == self.last_sent[key]:
            return
        
        self.draw_socket.send_json({
            'command': 'draw_text',
            'text': visible_text,
            'y': y,
            'flags': self.FLAGS
        })
        self.last_sent[key] = visible_text

    def _commit(self):
        """Sendet den Commit-Befehl."""
        self.draw_socket.send_json({'command': 'commit'})

    def draw_music_screen(self):
        data = self.music_data
        cur = (data.get('title'), data.get('artist'))
        prev = (self.last_data.get('title'), self.last_data.get('artist'))
        if cur != prev:
            logger.info("New track: %s - %s", data.get('artist', '?'), data.get('title', '?'))
            self._clear_and_reset_state()
            self.last_data = data.copy()

        # smaller frames to prevent overflow
        
        # Frame 1: Line 1 2 (17 + 17 = 34 Bytes < 49)
        self._draw_line('line1', data.get('title', ''), self.Y['line1'])
        self._draw_line('line2', data.get('artist', ''), self.Y['line2'])
        self._commit() # Frame 1 anzeigen

        # Frame 2: Line 3 4 (17 + 17 = 34 Bytes < 49)
        self._draw_line('line3', data.get('album', ''), self.Y['line3'])
        
        pos = data.get('position', '0:00')
        dur = data.get('duration', '0:00')
        if not pos or ':' not in pos:
             pos = "0:00"
        
        time_str = f"{pos} / {dur}"
        padded_time_str = time_str.ljust(self.MAX_CHARS, self.PAD_CHAR)
        
        if padded_time_str != self.last_sent['line4']:
            self.draw_socket.send_json({
                'command': 'draw_text',
                'text': padded_time_str,
                'y': self.Y['line4'],
                'flags': self.FLAGS
            })
            self.last_sent['line4'] = padded_time_str
        
        self._commit()

    def draw_nav_screen(self):
        data = self.nav_data
        cur = (data.get('description'))
        prev = (self.last_data.get('description'))
        if cur != prev:
            logger.info("New Nav: %s", cur)
            self._clear_and_reset_state()
            self.last_data = data.copy()
        
        # frame 1
        self._draw_line('line1', "Navigation", self.Y['line1'])
        self._draw_line('line2', data.get('description', ''), self.Y['line2'])
        self._commit()

        #frame 2
        self._draw_line('line3', data.get('distance', ''), self.Y['line3'])
        self._draw_line('line4', "", self.Y['line4'])
        self._commit()


    def draw_phone_screen(self):
        data = self.phone_data
        cur = (data.get('state'))
        prev = (self.last_data.get('state'))

        if cur != prev:
            logger.info("New Call State: %s", cur)
            self._clear_and_reset_state()
            self.last_data = data.copy()
        
        state = data.get('state', 'IDLE')
        if state == 'ACTIVE':
            display_state = "Call"
        elif state == 'IDLE':
            display_state = "Idle"
        else:
            display_state = state 
        
        # frame 1
        self._draw_line('line1', f"Phone: {display_state}", self.Y['line1'])
        self._draw_line('line2', data.get('caller', ''), self.Y['line2'])
        self._commit()
        
        # frame 2
        self._draw_line('line3', "", self.Y['line3'])
        self._draw_line('line4', "", self.Y['line4'])
        self._commit()

    def _handle_hudiy_input(self):
        try:
            topic_bytes, msg_json = self.sub_hudiy.recv_multipart(flags=zmq.NOBLOCK)
            topic = topic_bytes.decode('utf-8')
            data = json.loads(msg_json.decode('utf-8'))
            if topic == 'HUDIY_MEDIA':
                self.music_data = data
            elif topic == 'HUDIY_NAV':
                self.nav_data = data
            elif topic == 'HUDIY_PHONE':
                self.phone_data = data
        except zmq.Again: pass
        except Exception as e: logger.error(f"Hudiy input error: {e}")

    def _handle_button_input(self):
        try:
            topic_bytes, msg_json = self.sub_input.recv_multipart(flags=zmq.NOBLOCK)
            msg = json.loads(msg_json)
            data = list(bytes.fromhex(msg['data_hex']))
            logger.info(f"Input Detected: ID={topic_bytes.decode()} Data={data}")
            current_index = self.modes.index(self.current_mode)
            if data == self.BUTTON_DOWN_DATA:
                logger.info("Menu DOWN")
                new_index = (current_index + 1) % len(self.modes)
                self.current_mode = self.modes[new_index]
                self._clear_and_reset_state()
            elif data == self.BUTTON_UP_DATA:
                logger.info("Menu UP")
                new_index = (current_index - 1) % len(self.modes)
                self.current_mode = self.modes[new_index]
                self._clear_and_reset_state()
            logger.info(f"New Mode: {self.current_mode.name}")
        except zmq.Again: pass
        except Exception as e: logger.error(f"Button input error: {e}")

    def run(self):
        while True:
            try:
                # 1. Check for ALL inputs
                # Slower scroll speed (500ms)
                socks = dict(self.poller.poll(timeout=500)) 
                
                if self.sub_hudiy in socks:
                    self._handle_hudiy_input()
                if self.sub_input in socks:
                    self._handle_button_input()
                
                # 2. Draw the screen for the current mode
                
                if self.current_mode == DisplayMode.MUSIC:
                    self.draw_music_screen()
                elif self.current_mode == DisplayMode.NAVIGATION:
                    self.draw_nav_screen()
                elif self.current_mode == DisplayMode.PHONE:
                    self.draw_phone_screen()
                
                # self.draw_socket.send_json({'command': 'commit'})

            except (TimeoutError, ValueError) as e:
                logger.error("CAN error: %s", e)
                time.sleep(1)
            except KeyboardInterrupt:
                logger.info("Shutdown")
                break
            except Exception as e:
                logger.exception("Unexpected error")
                break
        
        self.draw_socket.close()
        self.sub_input.close()
        self.sub_hudiy.close()
        self.context.term()
        logger.info("DisplayManager shut down.")

# ----------------------------------------------------------------------
if __name__ == "__main__":
    
    dm = DisplayManager(
        music_json='/tmp/now_playing.json',
        nav_json='/tmp/current_nav.json',
        call_json='/tmp/current_call.json',
        config_path='/home/pi/config.json'
    )
    dm.initialize()
    dm.run()
