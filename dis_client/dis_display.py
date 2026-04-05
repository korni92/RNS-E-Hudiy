#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
DIS Display Engine V5.8
An driver for Driver Information Systems (DIS).
Handles CAN bus inputs, app switching, and ZeroMQ-based rendering.
"""

import zmq
import json
import time
import logging
import sys
import os
from typing import Set, List, Dict, Union

# Application Imports
from apps.menu import MenuApp
from apps.radio import RadioApp
from apps.media import MediaApp
from apps.nav import NavApp
from apps.phone import PhoneApp
from apps.settings import SettingsApp
from apps.car_info import CarInfoApp

# Logging Configuration
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

SETTINGS_FILE = '/home/pi/dis_settings.json'

class DisplayEngine:
    """
    Main engine responsible for orchestrating apps, handling CAN signals,
    and pushing draw commands to the display hardware via ZeroMQ.
    """
    
    # Vertical pixel offsets for the 5-line display
    Y_OFFSETS = {'line1': 1, 'line2': 11, 'line3': 21, 'line4': 31, 'line5': 41}

    def __init__(self, config_path='/home/pi/config.json'):
        """Initializes the engine, connects sockets, and loads applications."""
        with open(config_path) as f: 
            self.cfg = json.load(f)
            
        self.settings = self.load_settings()
        
        # Application Registry
        self.apps = {}
        self._init_apps()

        # ZeroMQ Communication Setup
        self.zmq_ctx = zmq.Context()
        
        # Subscriber for CAN Bus data
        self.sub = self.zmq_ctx.socket(zmq.SUB)
        self.sub.connect(self.cfg['zmq']['publish_address'])
        
        # Subscriber for HUDIY (Heads Up Display) metadata
        self.sub_hudiy = self.zmq_ctx.socket(zmq.SUB)
        self.sub_hudiy.connect(self.cfg['zmq']['hudiy_publish_address'])
        
        # PUSH socket for drawing commands
        self.draw = self.zmq_ctx.socket(zmq.PUSH)
        self.draw.connect(self.cfg['zmq']['dis_draw'])
        
        # Poller for non-blocking multi-socket handling
        self.poller = zmq.Poller()
        self.poller.register(self.sub, zmq.POLLIN)
        self.poller.register(self.sub_hudiy, zmq.POLLIN)

        self._setup_subscriptions()
        self._init_state()

    def _init_apps(self):
        """Initializes the app structure and navigation menus."""
        self.apps['main'] = MenuApp("Main Menu", [
            {'label': 'Media',      'target': 'menu_media'},
            {'label': 'Car Info',   'target': 'app_car'},
            {'label': 'Navigation', 'target': 'app_nav'},
            {'label': 'Phone',      'target': 'app_phone'},
            {'label': 'Settings',   'target': 'app_settings'}
        ])
        self.apps['menu_media'] = MenuApp("Media", [
            {'label': 'Now Playing', 'target': 'app_media_player'},
            {'label': 'Radio',       'target': 'app_radio'},
            {'label': 'Back',        'target': 'BACK'}
        ])
        self.apps['app_radio']        = RadioApp()
        self.apps['app_media_player'] = MediaApp()
        self.apps['app_nav']          = NavApp()
        self.apps['app_phone']        = PhoneApp()
        self.apps['app_settings']     = SettingsApp(self) 
        self.apps['app_car']          = CarInfoApp()

    def _setup_subscriptions(self):
        """Subscribes to relevant CAN IDs and HUDIY topics."""
        self.t_btn = self._resolve_topics('steering_module', '0x2C1')
        self.apps['app_radio'].set_topics(
            self._resolve_topics('fis_line1', '0x363'), 
            self._resolve_topics('fis_line2', '0x365')
        )
        
        self.t_car = set()
        for key in ['oil_temp', 'battery', 'fuel_level']:
             self.t_car.update(self._resolve_topics(key, '0x000'))
             
        # Combined subscription to all required CAN topics
        for t in self.t_btn | self.t_car | self.apps['app_radio'].topics: 
            self.sub.subscribe(t.encode())
        
        # HUDIY Metadata subscriptions
        for t in [b'HUDIY_MEDIA', b'HUDIY_NAV', b'HUDIY_PHONE']: 
            self.sub_hudiy.subscribe(t)

    def _init_state(self):
        """Initializes internal state, app stack, and last sent cache."""
        self.stack = ['main']
        
        # Determine starting application
        if self.settings.get('remember_last', False):
            start_app = self.settings.get('last_app', 'main')
        else:
            start_app = self.settings.get('startup_app', 'main')
            
        if start_app not in self.apps: start_app = 'main'
        
        if start_app != 'main':
            self.stack.append(start_app)
        
        self.current_app = self.apps[start_app]
        logger.info(f"Starting Engine with App: {start_app}")
        
        self.current_app.on_enter()
        
        # Render Cache to avoid redundant draw calls
        self.last_sent = {k: None for k in self.Y_OFFSETS}
        self.last_sent['custom_sig'] = None 
        self.last_sent_flags = {k: 0 for k in self.Y_OFFSETS} 
        
        # Button state tracking
        self.btn = {'up': {'p':False, 's':0, 'l':0}, 'down': {'p':False, 's':0, 'l':0}}

    def load_settings(self) -> dict:
        """Loads user settings from the JSON file."""
        default = {'startup_app': 'main', 'remember_last': False, 'last_app': 'main'}
        try:
            if os.path.exists(SETTINGS_FILE):
                with open(SETTINGS_FILE, 'r') as f:
                    data = json.load(f)
                    default.update(data)
        except Exception as e: 
            logger.error(f"Failed to load settings: {e}")
        return default

    def save_settings(self):
        """Persists current user settings to disk."""
        try:
            with open(SETTINGS_FILE, 'w') as f: 
                json.dump(self.settings, f, indent=4)
        except Exception as e: 
            logger.error(f"Failed to save settings: {e}")

    def _resolve_topics(self, key: str, default: str) -> Set[str]:
        """Resolves configuration keys to a set of possible CAN topic formats."""
        v = set()
        val = str(self.cfg['can_ids'].get(key, default))
        if val == '0x000': return v 
        v.add(f"CAN_{val}"); v.add(f"CAN_{val.strip()}")
        try: 
            n = int(val, 16)
            v.add(f"CAN_{n:X}"); v.add(f"CAN_0x{n:X}"); v.add(f"CAN_{n}")
        except: pass
        return v

    def switch_app(self, target: str):
        """Handles navigation between apps using a stack-based approach."""
        if target == 'BACK':
            if len(self.stack) > 1: 
                self.stack.pop()
                target = self.stack[-1]
            else: return 
        else:
            self.stack.append(target)
        
        self.current_app.on_leave()
        self.current_app = self.apps.get(target, self.apps['main'])
        self.current_app.on_enter()
        
        if self.settings.get('remember_last', False):
            if target in ['main', 'app_media_player', 'app_radio', 'app_nav', 'app_phone', 'app_car']:
                self.settings['last_app'] = target
                self.save_settings()
                
        self.force_redraw(send_clear=True)

    def process_input(self, action: str):
        """Passes user input to the current app and handles returned nav commands."""
        result = self.current_app.handle_input(action)
        if result: self.switch_app(result)

    def force_redraw(self, send_clear=False):
        """Invalidates the render cache and optionally clears the screen."""
        self.last_sent = {k: None for k in self.Y_OFFSETS}
        self.last_sent['custom_sig'] = None
        if send_clear:
            self.draw.send_json({'command': 'clear'})
            self.draw.send_json({'command': 'commit'})
            self.last_sent_flags = {k: 0 for k in self.Y_OFFSETS}

    def run(self):
        """Main execution loop for the Display Engine."""
        logger.info("DIS Engine V5.8 Operational")
        time.sleep(1.0) 
        self.force_redraw(send_clear=True)
        
        while True:
            try:
                socks = dict(self.poller.poll(30))
                
                # Handle HUDIY (Metadata) Updates
                if self.sub_hudiy in socks:
                    try:
                        while True:
                            parts = self.sub_hudiy.recv_multipart(flags=zmq.NOBLOCK)
                            if len(parts) == 2:
                                topic, msg = parts
                                try:
                                    data = json.loads(msg)
                                    self.current_app.update_hudiy(topic, data)
                                    if topic == b'HUDIY_MEDIA':
                                        label = data.get('source_label', 'Now Playing')
                                        self.apps['menu_media'].set_item_label('app_media_player', label)
                                except json.JSONDecodeError: pass
                    except zmq.Again: pass

                # Handle CAN Bus and System Updates
                if self.sub in socks: 
                    self._handle_can()
                    
                self._check_buttons()
                self._draw()
                time.sleep(0.01)
                
            except KeyboardInterrupt: 
                break
            except Exception as e: 
                logger.error(f"Execution Error: {e}", exc_info=True)
                time.sleep(1)

    def _handle_can(self):
        """Internal handler for incoming CAN bus messages."""
        try:
            while True:
                parts = self.sub.recv_multipart(flags=zmq.NOBLOCK)
                if len(parts) == 2:
                    topic, msg = parts
                    t_str = topic.decode()
                    payload = bytes.fromhex(json.loads(msg)['data_hex'])
                    self.current_app.update_can(t_str, payload)
                    
                    # Steering Wheel Button Logic
                    if t_str in self.t_btn and len(payload) > 2:
                        b = payload[2]
                        now = time.time()
                        if b & 0x20: self._btn_event('up', True, now)
                        elif self.btn['up']['p']: self._btn_event('up', False, now)
                        if b & 0x10: self._btn_event('down', True, now)
                        elif self.btn['down']['p']: self._btn_event('down', False, now)
        except zmq.Again: pass

    def _btn_event(self, name: str, pressed: bool, now: float):
        """Processes button press and release events."""
        b = self.btn[name]
        if pressed:
            if not b['p']: 
                b.update(p=True, s=now, l=False)
                self.force_redraw(send_clear=False)
        else:
            if b['p'] and not b['l']: self.process_input(f"tap_{name}")
            b['p'] = b['l'] = False

    def _check_buttons(self):
        """Checks for long-press (hold) events on buttons."""
        now = time.time()
        for name, b in self.btn.items():
            if b['p']:
                if not b['l'] and (now - b['s'] > 2.0):
                    b['l'] = True
                    self.process_input(f"hold_{name}")
                elif (now - b['s'] > 5.0): b['p'] = False

    def _draw(self):
        """
        Orchestrates the rendering logic. Supports standard 5-line text 
        rendering and custom list-based drawing (bitmaps/lines).
        """
        view = self.current_app.get_view()
        
        # Optimization: If app returns no data, skip frame
        if not view: 
            return
        
        # Handle Custom Drawing (List of commands)
        if isinstance(view, list):
            current_sig = str(view)
            if self.last_sent.get('custom_sig') != current_sig:
                is_partial = False
                if len(view) > 0 and view[0].get('partial'):
                    is_partial = True

                if not is_partial:
                    self.draw.send_json({'command': 'clear'})
                
                for item in view:
                    if 'type' in item: continue
                    cmd = item.get('cmd')
                    if cmd == 'draw_bitmap':
                        self.draw.send_json({'command': 'draw_bitmap', 'icon_name': item.get('icon'), 'x': item.get('x', 0), 'y': item.get('y', 0)})
                    elif cmd == 'draw_text':
                        self.draw.send_json({'command': 'draw_text', 'text': item.get('text', ''), 'x': item.get('x', 0), 'y': item.get('y', 0), 'flags': item.get('flags', 0x06)})
                    elif cmd == 'draw_line':
                        self.draw.send_json({'command': 'draw_line', 'x': item.get('x', 0), 'y': item.get('y', 0), 'length': item.get('len', 0), 'vertical': item.get('vert', True)})
                    elif cmd == 'clear_area':
                        self.draw.send_json({'command': 'clear_area', 'x': item.get('x', 0), 'y': item.get('y', 0), 'w': item.get('w', 64), 'h': 9})
                
                self.draw.send_json({'command': 'commit'})
                self.last_sent['custom_sig'] = current_sig
                for k in self.Y_OFFSETS: self.last_sent[k] = None
            return

        # Handle Standard Text-Line Drawing
        if self.last_sent.get('custom_sig'):
             self.draw.send_json({'command': 'clear'})
             self.last_sent['custom_sig'] = None
             for k in self.Y_OFFSETS: self.last_sent[k] = None

        changed = False
        for k, (txt, flag) in view.items():
            if k == 'type': continue
            
            prev_txt = self.last_sent.get(k)
            prev_flag = self.last_sent_flags.get(k, 0)
            
            if prev_txt != txt or prev_flag != flag:
                must_clear = False
                
                # Case 1: Transition from inverted to normal text requires line clear
                if (prev_flag & 0x80) and not (flag & 0x80):
                    must_clear = True
                
                # Case 2: Partial clear for shrinking text to prevent ghosting
                if prev_txt:
                    prev_eff = len(prev_txt.rstrip())
                    curr_eff = len(txt.rstrip())
                    if curr_eff < prev_eff:
                        char_width = 4 
                        clear_x = curr_eff * char_width
                        clear_w = 64 - clear_x
                        if clear_w > 0:
                            self.draw.send_json({'command': 'clear_area', 'x': clear_x, 'y': self.Y_OFFSETS[k], 'w': clear_w, 'h': 9})
                
                if must_clear:
                    self.draw.send_json({'command': 'clear_area', 'x': 0, 'y': self.Y_OFFSETS[k], 'w': 64, 'h': 9})
                
                self.draw.send_json({'command':'draw_text', 'text':txt, 'y':self.Y_OFFSETS[k], 'flags':flag})
                self.last_sent[k] = txt
                self.last_sent_flags[k] = flag
                changed = True
        
        if changed: 
            self.draw.send_json({'command':'commit'})

if __name__ == "__main__":
    DisplayEngine().run()
