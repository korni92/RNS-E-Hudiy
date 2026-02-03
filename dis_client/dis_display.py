#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import zmq, json, time, logging, sys, os
from typing import Set, List, Dict, Union

# Import Apps (Safe Import)
try:
    from apps.menu import MenuApp
    from apps.radio import RadioApp
    from apps.media import MediaApp
    from apps.nav import NavApp
    from apps.phone import PhoneApp
    from apps.settings import SettingsApp
    from apps.car_info import CarInfoApp
except ImportError:
    pass

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

SETTINGS_FILE = '/home/pi/dis_settings.json'

class DisplayEngine:
    # --- Screen Layout Profiles ---
    LAYOUTS = {
        'mono': {
            'width': 64,
            'y_map': {'line1': 1, 'line2': 11, 'line3': 21, 'line4': 31, 'line5': 41}
        },
        # Layout 1: Menus (Header + Line + Items)
        'color_menu': {
            'text_x': 20,
            'lines': {
                'line1': {'y': 0,   'h': 60, 'font': 0x20, 'color': 0x07}, # Header: Big Center White
                'line2': {'y': 70,  'h': 40, 'font': 0x08, 'color': 0x07}, # Items: Small Left White
                'line3': {'y': 110, 'h': 40, 'font': 0x08, 'color': 0x07},
                'line4': {'y': 150, 'h': 40, 'font': 0x08, 'color': 0x07},
                'line5': {'y': 190, 'h': 40, 'font': 0x08, 'color': 0x07}
            },
            'sep': {'y': 62, 'h': 3, 'color': 0x07} # Separator Line
        },
        # Layout 2: Flat Apps (No Header, Maximize Space)
        'color_flat': {
            'text_x': 20,
            'lines': {
                'line1': {'y': 10,  'h': 40, 'font': 0x08, 'color': 0x07}, # Top line usage
                'line2': {'y': 55,  'h': 40, 'font': 0x08, 'color': 0x07},
                'line3': {'y': 100, 'h': 40, 'font': 0x08, 'color': 0x07},
                'line4': {'y': 145, 'h': 40, 'font': 0x08, 'color': 0x07},
                'line5': {'y': 190, 'h': 40, 'font': 0x08, 'color': 0x07}
            },
            'sep': None # No separator
        }
    }

    def __init__(self, config_path='/home/pi/config.json'):
        with open(config_path) as f: self.cfg = json.load(f)
        self.settings = self.load_settings()
        
        # Detect Hardware Mode
        self.hw_mode = self.cfg.get('dis_type', 'color') 
        if self.hw_mode not in ['mono', 'color']: self.hw_mode = 'color'
        
        # Helper for legacy Mono lookups
        if self.hw_mode == 'mono':
            self.Y = self.LAYOUTS['mono']['y_map']

        logger.info(f"Display Engine initialized in [{self.hw_mode.upper()}] mode.")
        
        # --- INIT APPS ---
        self.apps = {}
        try:
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
        except NameError:
            pass

        # --- ZMQ SETUP ---
        self.zmq_ctx = zmq.Context()
        self.sub = self.zmq_ctx.socket(zmq.SUB)
        self.sub.connect(self.cfg['zmq']['publish_address'])
        
        self.sub_hudiy = self.zmq_ctx.socket(zmq.SUB)
        self.sub_hudiy.connect(self.cfg['zmq']['hudiy_publish_address'])
        for t in [b'HUDIY_MEDIA', b'HUDIY_NAV', b'HUDIY_PHONE']: 
            self.sub_hudiy.subscribe(t)

        self.draw = self.zmq_ctx.socket(zmq.PUSH)
        self.draw.connect(self.cfg['zmq']['dis_draw'])
        
        self.poller = zmq.Poller()
        self.poller.register(self.sub, zmq.POLLIN)
        self.poller.register(self.sub_hudiy, zmq.POLLIN)

        # --- STATE ---
        self.stack = ['main']
        start_app = self.settings.get('last_app', 'main') if self.settings.get('remember_last') else self.settings.get('startup_app', 'main')
        if start_app not in self.apps: start_app = 'main'
        if start_app != 'main':
            self.stack.append(start_app)
            self.current_app = self.apps[start_app]
        else:
            self.current_app = self.apps['main']

        # CAN Filters
        self.t_btn = self._topics('steering_module', '0x2C1')
        self.t_car = set()
        for key in ['oil_temp', 'battery', 'fuel_level']:
             self.t_car.update(self._topics(key, '0x000'))
        
        sub_list = self.t_btn | self.t_car
        if 'app_radio' in self.apps: sub_list |= self.apps['app_radio'].topics
        for t in sub_list: self.sub.subscribe(t.encode())

        # Smart Redraw Cache
        self.last_drawn = {} # Stores {line_key: (text, flags)}
        self.last_layout = None

        self.btn = {'up': {'p':False, 's':0, 'l':0}, 'down': {'p':False, 's':0, 'l':0}}
        
        logger.info(f"Starting App: {start_app}")
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
        
        # Clear cache on app switch to force immediate redraw of new layout
        self.last_drawn = {}
        self.draw.send_json({'command': 'clear'})
        # We don't commit here, we let the loop handle it

    def process_input(self, action):
        result = self.current_app.handle_input(action)
        if result: self.switch_app(result)

    def run(self):
        time.sleep(1.0) 
        self.draw.send_json({'command': 'clear'})
        self.draw.send_json({'command': 'commit'})
        
        while True:
            try:
                socks = dict(self.poller.poll(30))
                
                if self.sub_hudiy in socks:
                    while True:
                        try:
                            parts = self.sub_hudiy.recv_multipart(flags=zmq.NOBLOCK)
                            if len(parts) == 2:
                                topic, msg = parts
                                data = json.loads(msg)
                                self.current_app.update_hudiy(topic, data)
                                if topic == b'HUDIY_MEDIA' and 'source_label' in data:
                                    self.apps['menu_media'].set_item_label('app_media_player', data['source_label'])
                        except zmq.Again: break

                if self.sub in socks: 
                    self._handle_can()
                
                self._check_buttons()
                
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
                # No forced redraw needed; loop picks up change instantly
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

    #  SMART DRAWING (No Flicker)
    def _draw_color_smart(self):
        # 1. Determine Layout based on App
        # Only show Header for Main Menu and Settings. Others get Flat layout.
        app_title = getattr(self.current_app, 'title', '')
        if app_title in ['Main Menu', 'Settings']:
            layout_name = 'color_menu'
        else:
            layout_name = 'color_flat'

        # Check if layout changed completely
        if self.last_layout != layout_name:
            self.draw.send_json({'command': 'clear'})
            self.last_drawn = {} # Invalidate cache
            self.last_layout = layout_name

        layout = self.LAYOUTS[layout_name]
        lines = layout['lines']
        sep = layout['sep']
        text_x = layout.get('text_x', 20)

        view = self.current_app.get_view()
        
        # Handle Raw Views (e.g. Startup Logo)
        if isinstance(view, list):
            sig = str(view)
            if self.last_drawn.get('raw_sig') == sig: return
            self.draw.send_json({'command': 'clear'})
            for item in view:
                if item.get('command') == 'draw_text' and 'opcode' not in item:
                     item['opcode'] = 0x5F
                self.draw.send_json(item)
            self.draw.send_json({'command': 'commit'})
            self.last_drawn = {'raw_sig': sig}
            return

        something_changed = False

        # 2. Draw Separator (Only if cache empty or layout changed)
        if sep and 'sep_drawn' not in self.last_drawn:
            self.draw.send_json({
                'command': 'draw_rect',
                'x': 0, 'y': sep['y'], 
                'w': 220, 'h': sep['h'], 
                'color': sep['color']
            })
            self.last_drawn['sep_drawn'] = True
            something_changed = True

        # 3. Iterate Lines with Diffing
        for key, profile in lines.items():
            # Extract Data
            if key in view:
                raw_val = view[key]
                txt = raw_val[0] if isinstance(raw_val, tuple) else raw_val
                flags = raw_val[1] if isinstance(raw_val, tuple) else 0
            else:
                txt = ""; flags = 0

            # Compare with Cache
            cache_key = f"{key}_state"
            prev_state = self.last_drawn.get(cache_key)
            current_state = (txt, flags)

            if prev_state == current_state:
                continue # No change, skip!

            # -- REDRAW THIS LINE --
            # A. Clear the line area (Black Rect)
            # This is crucial for "erasing" old text without a full screen clear
            self.draw.send_json({
                'command': 'draw_rect',
                'x': 0, 'y': profile['y'],
                'w': 220, 'h': profile['h'],
                'color': 0x00 # Black
            })

            # B. Check Selection Highlight
            if flags & 0x80:
                # Selected: Red Background + Black Text
                self.draw.send_json({
                    'command': 'draw_rect',
                    'x': 0, 'y': profile['y'], 
                    'w': 220, 'h': profile['h'],
                    'color': 0x04 # Red
                })
                font_color = 0x00 # Black
            else:
                # Normal: Profile Color
                font_color = profile['color']

            # C. Draw Text
            if txt:
                self.draw.send_json({
                    'command': 'draw_text',
                    'text': txt,
                    'x': text_x, # Indent
                    'y': profile['y'],
                    'opcode': 0x5F,
                    'font': profile['font'],
                    'color': font_color
                })

            # D. Update Cache
            self.last_drawn[cache_key] = current_state
            something_changed = True

        if something_changed:
            self.draw.send_json({'command': 'commit'})

    def _draw_mono_smart(self):
        # ... (Mono implementation unchanged for brevity, use previous if needed) ...
        pass

if __name__ == "__main__":
    DisplayEngine().run()
