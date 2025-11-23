#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# DIS Menu System V4.3 - Car Info & Settings
import zmq, json, time, logging, sys
from typing import Set

# Import Modular Apps
from apps.menu import MenuApp
from apps.radio import RadioApp
from apps.media import MediaApp
from apps.nav import NavApp
from apps.phone import PhoneApp
from apps.settings import SettingsApp # NEW
from apps.car_info import CarInfoApp  # NEW

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

class DisplayEngine:
    Y = {'line1': 1, 'line2': 11, 'line3': 21, 'line4': 31, 'line5': 41}

    def __init__(self, config_path='/home/pi/config.json'):
        with open(config_path) as f: self.cfg = json.load(f)
        
        # --- APP REGISTRY ---
        self.apps = {}
        
        # Main Menu
        self.apps['main'] = MenuApp("Main Menu", [
            {'label': 'Media',      'target': 'menu_media'},
            {'label': 'Car Info',   'target': 'app_car'},
            {'label': 'Navigation', 'target': 'app_nav'},
            {'label': 'Phone',      'target': 'app_phone'},
            {'label': 'Settings',   'target': 'app_settings'} # Points to new App
        ])
        
        # Media Submenu
        self.apps['menu_media'] = MenuApp("Media", [
            {'label': 'Now Playing', 'target': 'app_media_player'},
            {'label': 'Radio',       'target': 'app_radio'},
            {'label': 'Back',        'target': 'BACK'}
        ])

        # Initialize Functional Apps
        self.apps['app_radio']        = RadioApp()
        self.apps['app_media_player'] = MediaApp()
        self.apps['app_nav']          = NavApp()
        self.apps['app_phone']        = PhoneApp()
        self.apps['app_settings']     = SettingsApp() # NEW
        self.apps['app_car']          = CarInfoApp()  # NEW

        # --- ZMQ & TOPICS ---
        self.zmq_ctx = zmq.Context()
        
        # 1. CAN Input
        self.sub = self.zmq_ctx.socket(zmq.SUB)
        self.sub.connect(self.cfg['zmq']['publish_address'])
        
        # Radio Topics
        self.t_top = self._topics('fis_line1', '0x363')
        self.t_bot = self._topics('fis_line2', '0x365')
        self.apps['app_radio'].set_topics(self.t_top, self.t_bot)

        # Button Topics
        self.t_btn = self._topics('steering_module', '0x2C1')

        # Car Info Topics (Add these IDs to your config.json 'can_ids' section!)
        # If they aren't in config, these will just be empty sets and ignored safely
        self.t_car = set()
        for key in ['oil_temp', 'battery', 'fuel_level']: # Example config keys
             self.t_car.update(self._topics(key, '0x000'))

        for t in self.t_top | self.t_bot | self.t_btn | self.t_car: 
            self.sub.subscribe(t.encode())
        
        # 2. HUDIY Input
        self.sub_hudiy = self.zmq_ctx.socket(zmq.SUB)
        self.sub_hudiy.connect(self.cfg['zmq']['hudiy_publish_address'])
        for t in [b'HUDIY_MEDIA', b'HUDIY_NAV', b'HUDIY_PHONE']: 
            self.sub_hudiy.subscribe(t)

        # 3. Draw Output
        self.draw = self.zmq_ctx.socket(zmq.PUSH)
        self.draw.connect(self.cfg['zmq']['dis_draw'])
        
        self.poller = zmq.Poller()
        self.poller.register(self.sub, zmq.POLLIN)
        self.poller.register(self.sub_hudiy, zmq.POLLIN)

        # Runtime
        self.stack = ['main']
        self.current_app = self.apps['main']
        self.current_app.on_enter()
        self.last_sent = {k: None for k in self.Y}
        self.btn = {'up': {'p':False, 's':0, 'l':0}, 'down': {'p':False, 's':0, 'l':0}}

    def _topics(self, key, default) -> Set[str]:
        v = set()
        val = str(self.cfg['can_ids'].get(key, default))
        if val == '0x000': return v # Skip missing config
        v.add(f"CAN_{val}"); v.add(f"CAN_{val.strip()}")
        try: 
            n = int(val, 16)
            v.add(f"CAN_{n:X}"); v.add(f"CAN_0x{n:X}"); v.add(f"CAN_{n}")
        except: pass
        return v

    def switch_app(self, target):
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
        
        self.last_sent = {k: None for k in self.Y}
        self.draw.send_json({'command': 'clear'})
        self.draw.send_json({'command': 'commit'})

    def process_input(self, action):
        result = self.current_app.handle_input(action)
        if result: self.switch_app(result)

    def run(self):
        logger.info("DIS Engine V4.3 Running")
        while True:
            try:
                socks = dict(self.poller.poll(30))
                
                # Handle HUDIY Data
                if self.sub_hudiy in socks:
                    try:
                        while True:
                            topic, msg = self.sub_hudiy.recv_multipart(flags=zmq.NOBLOCK)
                            data = json.loads(msg)
                            self.current_app.update_hudiy(topic, data)
                            # Dynamic Label Update
                            if topic == b'HUDIY_MEDIA':
                                label = data.get('source_label', 'Now Playing')
                                self.apps['menu_media'].set_item_label('app_media_player', label)
                    except zmq.Again: pass

                # Handle CAN Data
                if self.sub in socks:
                    self._handle_can()

                self._check_buttons()
                self._draw()
                time.sleep(0.01)
            except KeyboardInterrupt: break
            except Exception as e: logger.error(f"Err: {e}", exc_info=True); time.sleep(1)

    def _handle_can(self):
        try:
            while True:
                topic, msg = self.sub.recv_multipart(flags=zmq.NOBLOCK)
                t_str = topic.decode()
                payload = bytes.fromhex(json.loads(msg)['data_hex'])
                
                self.current_app.update_can(t_str, payload)

                if t_str in self.t_btn and len(payload) > 2:
                    b = payload[2]
                    now = time.time()
                    if b & 0x20: self._btn_event('up', True, now)
                    elif self.btn['up']['p']: self._btn_event('up', False, now)
                    
                    if b & 0x10: self._btn_event('down', True, now)
                    elif self.btn['down']['p']: self._btn_event('down', False, now)
        except zmq.Again: pass

    def _btn_event(self, name, pressed, now):
        b = self.btn[name]
        if pressed:
            if not b['p']: b.update(p=True, s=now, l=False)
        else:
            if b['p'] and not b['l']: self.process_input(f"tap_{name}")
            b['p'] = b['l'] = False

    def _check_buttons(self):
        now = time.time()
        for name, b in self.btn.items():
            if b['p']:
                if not b['l'] and (now - b['s'] > 2.0):
                    b['l'] = True
                    self.process_input(f"hold_{name}")
                elif (now - b['s'] > 5.0): b['p'] = False

    def _draw(self):
        view = self.current_app.get_view()
        changed = False
        for k, (txt, flag) in view.items():
            if self.last_sent.get(k) != txt:
                self.draw.send_json({'command':'draw_text', 'text':txt, 'y':self.Y[k], 'flags':flag})
                self.last_sent[k] = txt
                changed = True
        if changed: self.draw.send_json({'command':'commit'})

if __name__ == "__main__":
    DisplayEngine().run()