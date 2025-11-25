#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import zmq, json, time, logging, sys, os
from typing import Set, List, Dict, Union

# Import Apps
from apps.menu import MenuApp
from apps.radio import RadioApp
from apps.media import MediaApp
from apps.nav import NavApp
from apps.phone import PhoneApp
from apps.settings import SettingsApp
from apps.car_info import CarInfoApp

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

SETTINGS_FILE = '/home/pi/dis_settings.json'

class DisplayEngine:
    Y = {'line1': 1, 'line2': 11, 'line3': 21, 'line4': 31, 'line5': 41}

    def __init__(self, config_path='/home/pi/config.json'):
        with open(config_path) as f: self.cfg = json.load(f)
        self.settings = self.load_settings()
        
        self.apps = {}
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

        self.zmq_ctx = zmq.Context()
        self.sub = self.zmq_ctx.socket(zmq.SUB)
        self.sub.connect(self.cfg['zmq']['publish_address'])
        self.t_btn = self._topics('steering_module', '0x2C1')
        self.apps['app_radio'].set_topics(self._topics('fis_line1', '0x363'), self._topics('fis_line2', '0x365'))
        self.t_car = set()
        for key in ['oil_temp', 'battery', 'fuel_level']:
             self.t_car.update(self._topics(key, '0x000'))
        for t in self.t_btn | self.t_car | self.apps['app_radio'].topics: 
            self.sub.subscribe(t.encode())
        
        self.sub_hudiy = self.zmq_ctx.socket(zmq.SUB)
        self.sub_hudiy.connect(self.cfg['zmq']['hudiy_publish_address'])
        for t in [b'HUDIY_MEDIA', b'HUDIY_NAV', b'HUDIY_PHONE']: 
            self.sub_hudiy.subscribe(t)

        self.draw = self.zmq_ctx.socket(zmq.PUSH)
        self.draw.connect(self.cfg['zmq']['dis_draw'])
        self.poller = zmq.Poller()
        self.poller.register(self.sub, zmq.POLLIN)
        self.poller.register(self.sub_hudiy, zmq.POLLIN)

        self.stack = ['main']
        start_app = 'main'
        if self.settings.get('remember_last', False):
            start_app = self.settings.get('last_app', 'main')
        else:
            start_app = self.settings.get('startup_app', 'main')
        if start_app not in self.apps: start_app = 'main'
        if start_app != 'main':
            self.stack.append(start_app)
            self.current_app = self.apps[start_app]
        else:
            self.current_app = self.apps['main']

        logger.info(f"Starting in App: {start_app}")
        self.current_app.on_enter()
        self.last_sent = {k: None for k in self.Y}
        self.last_sent['custom_sig'] = None 
        self.last_sent_flags = {k: 0 for k in self.Y} 
        self.btn = {'up': {'p':False, 's':0, 'l':0}, 'down': {'p':False, 's':0, 'l':0}}

    def load_settings(self):
        default = {'startup_app': 'main', 'remember_last': False, 'last_app': 'main'}
        try:
            if os.path.exists(SETTINGS_FILE):
                with open(SETTINGS_FILE, 'r') as f:
                    data = json.load(f)
                    default.update(data)
        except Exception as e: logger.error(f"Failed to load settings: {e}")
        return default

    def save_settings(self):
        try:
            with open(SETTINGS_FILE, 'w') as f: json.dump(self.settings, f, indent=4)
        except Exception as e: logger.error(f"Failed to save settings: {e}")

    def _topics(self, key, default) -> Set[str]:
        v = set()
        val = str(self.cfg['can_ids'].get(key, default))
        if val == '0x000': return v 
        v.add(f"CAN_{val}"); v.add(f"CAN_{val.strip()}")
        try: n = int(val, 16); v.add(f"CAN_{n:X}"); v.add(f"CAN_0x{n:X}"); v.add(f"CAN_{n}")
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
        if self.settings.get('remember_last', False):
            if target in ['main', 'app_media_player', 'app_radio', 'app_nav', 'app_phone', 'app_car']:
                self.settings['last_app'] = target
                self.save_settings()
        self.force_redraw(send_clear=True)

    def process_input(self, action):
        result = self.current_app.handle_input(action)
        if result: self.switch_app(result)

    def force_redraw(self, send_clear=False):
        self.last_sent = {k: None for k in self.Y}
        self.last_sent['custom_sig'] = None
        if send_clear:
            self.draw.send_json({'command': 'clear'})
            self.draw.send_json({'command': 'commit'})
            self.last_sent_flags = {k: 0 for k in self.Y}

    def run(self):
        logger.info("DIS Engine V5.8 Running")
        time.sleep(1.0) 
        self.force_redraw(send_clear=True)
        
        while True:
            try:
                socks = dict(self.poller.poll(30))
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

                if self.sub in socks: self._handle_can()
                self._check_buttons()
                self._draw()
                time.sleep(0.01)
            except KeyboardInterrupt: break
            except Exception as e: logger.error(f"Err: {e}", exc_info=True); time.sleep(1)

    def _handle_can(self):
        try:
            while True:
                parts = self.sub.recv_multipart(flags=zmq.NOBLOCK)
                if len(parts) == 2:
                    topic, msg = parts
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
            if not b['p']: 
                b.update(p=True, s=now, l=False)
                self.force_redraw(send_clear=False)
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
        
        if isinstance(view, list):
            current_sig = str(view)
            if self.last_sent.get('custom_sig') != current_sig:
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
                self.draw.send_json({'command': 'commit'})
                self.last_sent['custom_sig'] = current_sig
                for k in self.Y: self.last_sent[k] = None
            return

        if self.last_sent.get('custom_sig'):
             self.draw.send_json({'command': 'clear'})
             self.last_sent['custom_sig'] = None
             for k in self.Y: self.last_sent[k] = None

        changed = False
        for k, (txt, flag) in view.items():
            if k == 'type': continue
            
            prev_txt = self.last_sent.get(k)
            prev_flag = self.last_sent_flags.get(k, 0)
            
            if prev_txt != txt or prev_flag != flag:
                
                must_clear = False
                
                # CASE 1: Invert -> Normal transition
                if (prev_flag & 0x80) and not (flag & 0x80):
                    must_clear = True
                
                # CASE 2: Partial clear for shrinking text to avoid ghosting
                if prev_txt:
                    prev_eff = len(prev_txt.rstrip())
                    curr_eff = len(txt.rstrip())
                    if curr_eff < prev_eff:
                        char_width = 4  # 64 pixels / 16 chars
                        clear_x = curr_eff * char_width
                        clear_w = 64 - clear_x
                        if clear_w > 0:
                            self.draw.send_json({'command': 'clear_area', 'x': clear_x, 'y': self.Y[k], 'w': clear_w, 'h': 9})
                
                if must_clear:
                    self.draw.send_json({'command': 'clear_area', 'x': 0, 'y': self.Y[k], 'w': 64, 'h': 9})
                
                self.draw.send_json({'command':'draw_text', 'text':txt, 'y':self.Y[k], 'flags':flag})
                self.last_sent[k] = txt
                self.last_sent_flags[k] = flag
                changed = True
        
        if changed: 
            self.draw.send_json({'command':'commit'})

if __name__ == "__main__":
    DisplayEngine().run()
