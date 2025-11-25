from .base import BaseApp
import time
import os

class SettingsApp(BaseApp):
    def __init__(self, engine):
        super().__init__()
        self.engine = engine
        self.header = "Settings"
        self.items = [
            {'label': 'Startup Opts', 'action': 'startup_menu'},
            {'label': 'System Info',  'action': 'sys_info'},
            {'label': 'Reboot Pi',    'action': 'reboot'},
            {'label': 'Back',         'action': 'back'}
        ]
        self.sel = 0
        self.scroll = 0
        
        self.view_mode = 'list' # 'list', 'info', 'startup'
        self.info_page = 0
        
        self.startup_items = []
        self.startup_sel = 0
        self.startup_scroll = 0
        
        self.app_names = {
            'main': 'Main Menu',
            'app_media_player': 'Media',
            'app_radio': 'Radio',
            'app_nav': 'Nav',
            'app_phone': 'Phone',
            'app_car': 'Car Info'
        }
        self.pi_data = {'cpu': '0%', 'ram': '0%', 'temp': '0C'}
        self.last_stats_update = 0
        self.last_cpu_time = 0
        self.last_cpu_idle = 0

    def _read_pi_stats(self):
        now = time.time()
        if now - self.last_stats_update < 1.0: return 
        try:
            with open('/sys/class/thermal/thermal_zone0/temp', 'r') as f:
                self.pi_data['temp'] = f"{int(f.read()) / 1000:.0f}C"
            with open('/proc/meminfo', 'r') as f:
                lines = f.readlines()
            total = int(lines[0].split()[1])
            avail = int(lines[2].split()[1])
            percent = 100 * (1 - avail / total)
            self.pi_data['ram'] = f"{percent:.0f}%"
            with open('/proc/stat', 'r') as f:
                fields = [float(x) for x in f.readline().split()[1:5]]
            total_time = sum(fields)
            idle_time = fields[3]
            delta_total = total_time - self.last_cpu_time
            delta_idle = idle_time - self.last_cpu_idle
            if delta_total > 0:
                usage = 100.0 * (1.0 - delta_idle / delta_total)
                self.pi_data['cpu'] = f"{usage:.0f}%"
            self.last_cpu_time = total_time
            self.last_cpu_idle = idle_time
        except: pass
        self.last_stats_update = now

    def _build_startup_menu(self):
        self.startup_items = []
        self.startup_items.append({'key': 'remember', 'label': 'Remember Last'})
        for app_id, name in self.app_names.items():
            self.startup_items.append({'key': 'app', 'id': app_id, 'label': name})
        self.startup_items.append({'key': 'back', 'label': 'Back'})

    def handle_input(self, action):
        # --- STARTUP MENU ---
        if self.view_mode == 'startup':
            count = len(self.startup_items)
            if action == 'hold_up': 
                self.view_mode = 'list'
                self.engine.force_redraw(send_clear=True)
                return None
            if action == 'tap_up':
                self.startup_sel = (self.startup_sel - 1) % count
            elif action == 'tap_down':
                self.startup_sel = (self.startup_sel + 1) % count
            elif action == 'hold_down': 
                self._execute_startup_action()
            return None

        # --- INFO MODE ---
        if self.view_mode == 'info':
            if action == 'tap_down': self.info_page = 1 
            elif action == 'tap_up': self.info_page = 0 
            elif action in ['hold_up', 'hold_down']:
                self.view_mode = 'list'
                self.info_page = 0
                self.engine.force_redraw(send_clear=True)
            return None

        # --- MAIN LIST ---
        count = len(self.items)
        if action == 'tap_up':   self.sel = (self.sel - 1) % count
        if action == 'tap_down': self.sel = (self.sel + 1) % count
        if action == 'hold_down':
            act = self.items[self.sel]['action']
            if act == 'back': return 'BACK'
            elif act == 'sys_info':
                self.view_mode = 'info'
                self.info_page = 0
                self.engine.force_redraw(send_clear=True)
            elif act == 'startup_menu':
                self._build_startup_menu()
                self.view_mode = 'startup'
                self.startup_sel = 0
                self.startup_scroll = 0
                self.engine.force_redraw(send_clear=True)
            elif act == 'reboot':
                os.system('sudo reboot')
        if action == 'hold_up': return 'BACK'
        return None

    def _execute_startup_action(self):
        item = self.startup_items[self.startup_sel]
        key = item['key']
        if key == 'back':
            self.view_mode = 'list'
            self.engine.force_redraw(send_clear=True)
        elif key == 'remember':
            curr = self.engine.settings.get('remember_last', False)
            self.engine.settings['remember_last'] = not curr
            self.engine.save_settings()
            self.engine.force_redraw(send_clear=False) 
        elif key == 'app':
            app_id = item['id']
            self.engine.settings['startup_app'] = app_id
            self.engine.save_settings()
            self.engine.force_redraw(send_clear=False) 

    def get_view(self):
        lines = {}
        
        # --- STARTUP CONFIG VIEW ---
        if self.view_mode == 'startup':
            lines['line1'] = ("Startup Opts", self.FLAG_HEADER)
            
            # 4 Lines visible (2,3,4,5)
            visible = 4
            if self.startup_sel < self.startup_scroll:
                self.startup_scroll = self.startup_sel
            elif self.startup_sel >= self.startup_scroll + visible:
                self.startup_scroll = self.startup_sel - visible + 1
            
            for i in range(visible):
                idx = self.startup_scroll + i
                line_key = f'line{i+2}'
                
                if idx >= len(self.startup_items): 
                    lines[line_key] = (" " * 16, self.FLAG_ITEM)
                    continue
                
                item = self.startup_items[idx]
                key = item['key']
                base_label = item['label']
                
                prefix = ">" if idx == self.startup_sel else " "
                
                is_active = False
                if key == 'remember':
                    is_active = self.engine.settings.get('remember_last', False)
                elif key == 'app':
                    if self.engine.settings.get('startup_app') == item['id']:
                        is_active = True
                
                display_text = f"{prefix}{base_label}"
                if is_active:
                    if key == 'remember': display_text += " [ON]"
                elif key == 'remember':
                    display_text += " [OFF]"
                
                flag = 0x86 if is_active else 0x06
                lines[line_key] = (display_text.ljust(16)[:16], flag)
            return lines

        # --- SYSTEM INFO ---
        if self.view_mode == 'info':
            lines['line1'] = ("System Info", self.FLAG_HEADER)
            if self.info_page == 0:
                lines['line2'] = ("DIS V5.6".center(16)[:16], self.FLAG_ITEM)
                lines['line3'] = ("Audi A2/TT".center(16)[:16], self.FLAG_ITEM)
                lines['line4'] = (" ".center(16), self.FLAG_ITEM)
                lines['line5'] = ("1/2".center(16)[:16], self.FLAG_ITEM)
            else:
                self._read_pi_stats()
                lines['line2'] = (f"CPU: {self.pi_data['cpu']}".ljust(16)[:16], self.FLAG_ITEM)
                lines['line3'] = (f"RAM: {self.pi_data['ram']}".ljust(16)[:16], self.FLAG_ITEM)
                lines['line4'] = (f"Tmp: {self.pi_data['temp']}".ljust(16)[:16], self.FLAG_ITEM)
                lines['line5'] = ("2/2".center(16)[:16], self.FLAG_ITEM)

        # --- MAIN LIST ---
        else:
            lines['line1'] = (self.header, self.FLAG_HEADER)
            visible = 4
            if self.sel < self.scroll: self.scroll = self.sel
            elif self.sel >= self.scroll + visible: self.scroll = self.sel - visible + 1
            
            for i in range(visible):
                idx = self.scroll + i
                key = f'line{i+2}'
                if idx < len(self.items):
                    flag = 0x06
                    prefix = ">" if idx == self.sel else " "
                    txt = f"{prefix}{self.items[idx]['label']}".ljust(16)[:16]
                    lines[key] = (txt, flag)
                else:
                    lines[key] = (" " * 16, self.FLAG_ITEM)
                    
        return lines
