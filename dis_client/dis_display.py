#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import zmq, json, time, logging, sys, os
from typing import Set, List, Dict, Union

# --- SAFE APP IMPORTS ---
try:
    from apps.menu import MenuApp
    from apps.radio import RadioApp
    from apps.media import MediaApp
    from apps.nav import NavApp
    from apps.phone import PhoneApp
    from apps.settings import SettingsApp
    from apps.car_info import CarInfoApp
except ImportError as e:
    print(f"Warning: Some apps could not be loaded: {e}")

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

SETTINGS_FILE = '/home/pi/dis_settings.json'

class DisplayEngine:
    # Screen Profiles for Color Clusters
    LAYOUTS_COLOR = {
        'menu': {
            'text_x': 20,
            'lines': {
                'line1': {'y': 0,   'h': 60,  'font': 0x20, 'color': 0x07}, # Header
                'line2': {'y': 70,  'h': 40,  'font': 0x08, 'color': 0x07}, # Items
                'line3': {'y': 110, 'h': 40,  'font': 0x08, 'color': 0x07},
                'line4': {'y': 150, 'h': 40,  'font': 0x08, 'color': 0x07},
                'line5': {'y': 190, 'h': 40,  'font': 0x08, 'color': 0x07}
            },
            'sep': {'y': 62, 'h': 3, 'color': 0x07}
        },
        'flat': {
            'text_x': 20,
            'lines': {
                'line1': {'y': 10,  'h': 40,  'font': 0x08, 'color': 0x07},
                'line2': {'y': 55,  'h': 40,  'font': 0x08, 'color': 0x07},
                'line3': {'y': 100, 'h': 40,  'font': 0x08, 'color': 0x07},
                'line4': {'y': 145, 'h': 40,  'font': 0x08, 'color': 0x07},
                'line5': {'y': 190, 'h': 40,  'font': 0x08, 'color': 0x07}
            },
            'sep': None
        }
    }

    # Line mapping for Mono (White/Red) Clusters
    Y_MONO = {'line1': 1, 'line2': 11, 'line3': 21, 'line4': 31, 'line5': 41}

    def __init__(self, config_path='/home/pi/config.json'):
        with open(config_path) as f: self.cfg = json.load(f)
        self.settings = self.load_settings()
        
        # 1. Hardware Detection
        self.hw_mode = self.cfg.get('dis_type', 'mono') # Default to mono if not set
        logger.info(f"Display Engine initializing in [{self.hw_mode.upper()}] mode.")

        # 2. App Initialization (Unified)
        self.apps = {}
        self._init_apps()

        # 3. ZMQ Setup
        self.zmq_ctx = zmq.Context()
        self._setup_zmq()

        # 4. State Management
        self.stack = ['main']
        self._init_state()

        # 5. Drawing Cache
        self.last_drawn = {}      # Shared cache
        self.last_flags = {k: 0 for k in self.Y_MONO} # Specifically for Mono logic
        self.last_layout = None   # Specifically for Color logic

        self.btn = {'up': {'p':False, 's':0, 'l':0}, 'down': {'p':False, 's':0, 'l':0}}

    def _init_apps(self):
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

    def _setup_zmq(self):
        # Subscriber for CAN data
        self.sub = self.zmq_ctx.socket(zmq.SUB)
        self.sub.connect(self.cfg['zmq']['publish_address'])
        
        # Subscriber for HUDIY (Phone/Nav/Media)
        self.sub_hudiy = self.zmq_ctx.socket(zmq.SUB)
        self.sub_hudiy.connect(self.cfg['zmq']['hudiy_publish_address'])
        for t in [b'HUDIY_MEDIA', b'HUDIY_NAV', b'HUDIY_PHONE']: 
            self.sub_hudiy.subscribe(t)

        # Output to display driver
        self.draw = self.zmq_ctx.socket(zmq.PUSH)
        self.draw.connect(self.cfg['zmq']['dis_draw'])
        
        self.poller = zmq.Poller()
        self.poller.register(self.sub, zmq.POLLIN)
        self.poller.register(self.sub_hudiy, zmq.POLLIN)

        # Dynamic CAN Subscriptions
        self.t_btn = self._topics('steering_module', '0x2C1')
        self.t_car = set()
        for key in ['oil_temp', 'battery', 'fuel_level']:
             self.t_car.update(self._topics(key, '0x000'))
        
        # Radio specific lines (Legacy support)
        if 'app_radio' in self.apps:
            self.apps['app_radio'].set_topics(self._topics('fis_line1', '0x363'), self._topics('fis_line2', '0x365'))

        sub_list = self.t_btn | self.t_car
        if 'app_radio' in self.apps: sub_list |= self.apps['app_radio'].topics
        for t in sub_list: self.sub.subscribe(t.encode())

    def _init_state(self):
        start_app = self.settings.get('last_app', 'main') if self.settings.get('remember_last') else self.settings.get('startup_app', 'main')
        if start_app not in self.apps: start_app = 'main'
        if start_app != 'main':
            self.stack.append(start_app)
            self.current_app = self.apps[start_app]
        else:
            self.current_app = self.apps['main']
        self.current_app.on_enter()

    def load_settings(self):
        default = {'startup_app': 'main', 'remember_last': False, 'last_app': 'main'}
        try:
            if os.path.exists(SETTINGS_FILE):
                with open(SETTINGS_FILE, 'r') as f: default.update(json.load(f))
        except: pass
        return default

    def save_settings(self):
        try:
            with open(SETTINGS_FILE, 'w') as f: json.dump(self.settings, f, indent=4)
        except: pass

    def _topics(self, key, default) -> Set[str]:
        v = set()
        val = str(self.cfg['can_ids'].get(key, default))
        if val == '0x000': return v 
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
        
        if self.settings.get('remember_last', False):
            if target in ['main', 'app_media_player', 'app_radio', 'app_nav', 'app_phone', 'app_car']:
                self.settings['last_app'] = target
                self.save_settings()
        
        self.force_clear()

    def force_clear(self):
        self.last_drawn = {}
        self.last_flags = {k: 0 for k in self.Y_MONO}
        self.draw.send_json({'command': 'clear'})
        self.draw.send_json({'command': 'commit'})

    def process_input(self, action):
        result = self.current_app.handle_input(action)
        if result: self.switch_app(result)

    def run(self):
        time.sleep(1.0) 
        self.force_clear()
        
        while True:
            try:
                socks = dict(self.poller.poll(30))
                
                # Handle HUDIY data
                if self.sub_hudiy in socks:
                    try:
                        while True:
                            parts = self.sub_hudiy.recv_multipart(flags=zmq.NOBLOCK)
                            if len(parts) == 2:
                                topic, msg = parts
                                data = json.loads(msg)
                                self.current_app.update_hudiy(topic, data)
                                if topic == b'HUDIY_MEDIA' and 'source_label' in data:
                                    self.apps['menu_media'].set_item_label('app_media_player', data['source_label'])
                    except zmq.Again: pass

                # Handle CAN data
                if self.sub in socks: 
                    self._handle_can()
                
                self._check_buttons()
                
                # Dynamic Drawing Dispatcher
                if self.hw_mode == 'color':
                    self._draw_color_smart()
                else:
                    self._draw_mono_smart()

                time.sleep(0.01)
            except KeyboardInterrupt: break
            except Exception as e: 
                logger.error(f"Loop Error: {e}")
                time.sleep(1)

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

    # --- COLOR DRAWING ENGINE ---
    def _draw_color_smart(self):
        app_title = getattr(self.current_app, 'title', '')
        layout_name = 'menu' if app_title in ['Main Menu', 'Settings'] else 'flat'

        if self.last_layout != layout_name:
            self.draw.send_json({'command': 'clear'})
            self.last_drawn = {}
            self.last_layout = layout_name

        layout = self.LAYOUTS_COLOR[layout_name]
        view = self.current_app.get_view()
        
        # Handle Custom Raw Views
        if isinstance(view, list):
            sig = str(view)
            if self.last_drawn.get('raw_sig') == sig: return
            self.draw.send_json({'command': 'clear'})
            for item in view:
                if 'opcode' not in item: item['opcode'] = 0x5F
                self.draw.send_json(item)
            self.draw.send_json({'command': 'commit'})
            self.last_drawn = {'raw_sig': sig}
            return

        something_changed = False
        if layout['sep'] and 'sep_drawn' not in self.last_drawn:
            s = layout['sep']
            self.draw.send_json({'command': 'draw_rect', 'x': 0, 'y': s['y'], 'w': 220, 'h': s['h'], 'color': s['color']})
            self.last_drawn['sep_drawn'] = True
            something_changed = True

        for key, profile in layout['lines'].items():
            raw_val = view.get(key, ("", 0))
            txt, flags = raw_val if isinstance(raw_val, tuple) else (raw_val, 0)

            cache_key = f"{key}_state"
            if self.last_drawn.get(cache_key) == (txt, flags): continue

            # Clear line area
            self.draw.send_json({'command': 'draw_rect', 'x': 0, 'y': profile['y'], 'w': 220, 'h': profile['h'], 'color': 0x00})

            # Selection Highlight
            font_color = profile['color']
            if flags & 0x80:
                self.draw.send_json({'command': 'draw_rect', 'x': 0, 'y': profile['y'], 'w': 220, 'h': profile['h'], 'color': 0x04}) # Red
                font_color = 0x00 # Black text on red

            if txt:
                self.draw.send_json({'command': 'draw_text', 'text': txt, 'x': layout['text_x'], 'y': profile['y'], 'opcode': 0x5F, 'font': profile['font'], 'color': font_color})

            self.last_drawn[cache_key] = (txt, flags)
            something_changed = True

        if something_changed: self.draw.send_json({'command': 'commit'})

    # --- MONO (WHITE/RED) DRAWING ENGINE ---
    def _draw_mono_smart(self):
        view = self.current_app.get_view()
        
        # Handle Custom Raw Views
        if isinstance(view, list):
            sig = str(view)
            if self.last_drawn.get('custom_sig') != sig:
                self.draw.send_json({'command': 'clear'})
                for item in view:
                    cmd = item.get('cmd')
                    if cmd == 'draw_bitmap':
                        self.draw.send_json({'command': 'draw_bitmap', 'icon_name': item.get('icon'), 'x': item.get('x', 0), 'y': item.get('y', 0)})
                    elif cmd == 'draw_text':
                        self.draw.send_json({'command': 'draw_text', 'text': item.get('text', ''), 'x': item.get('x', 0), 'y': item.get('y', 0), 'flags': item.get('flags', 0x06)})
                self.draw.send_json({'command': 'commit'})
                self.last_drawn['custom_sig'] = sig
            return

        changed = False
        for k, y_pos in self.Y_MONO.items():
            raw_val = view.get(k, ("", 0))
            txt, flag = raw_val if isinstance(raw_val, tuple) else (raw_val, 0)
            
            if self.last_drawn.get(k) != txt or self.last_flags.get(k) != flag:
                # Transition logic to avoid ghosting
                if (self.last_flags.get(k, 0) & 0x80) and not (flag & 0x80):
                    self.draw.send_json({'command': 'clear_area', 'x': 0, 'y': y_pos, 'w': 64, 'h': 9})
                
                # Shrink protection
                prev_txt = self.last_drawn.get(k, "")
                if prev_txt and len(txt.rstrip()) < len(prev_txt.rstrip()):
                     self.draw.send_json({'command': 'clear_area', 'x': 0, 'y': y_pos, 'w': 64, 'h': 9})

                self.draw.send_json({'command':'draw_text', 'text':txt, 'y': y_pos, 'flags':flag})
                self.last_drawn[k] = txt
                self.last_flags[k] = flag
                changed = True
        
        if changed: self.draw.send_json({'command':'commit'})

if __name__ == "__main__":
    DisplayEngine().run()
