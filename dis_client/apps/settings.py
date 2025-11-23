# apps/settings.py
from .base import BaseApp
import time
import os

class SettingsApp(BaseApp):
    def __init__(self):
        super().__init__()
        self.header = "Settings"
        self.items = [
            {'label': 'System Info', 'action': 'sys_info'},
            {'label': 'Reboot Pi',   'action': 'reboot'},
            {'label': 'Back',        'action': 'back'}
        ]
        self.sel = 0
        self.scroll = 0
        
        # View State
        self.view_mode = 'list' # 'list' or 'info'
        self.info_page = 0      # 0=Page 1, 1=Page 2
        
        # Pi Stats Data
        self.pi_data = {'cpu': '0%', 'ram': '0%', 'temp': '0C'}
        self.last_stats_update = 0
        self.last_cpu_time = 0
        self.last_cpu_idle = 0

    def _read_pi_stats(self):
        """Reads Linux system stats (CPU/RAM/Temp)."""
        now = time.time()
        if now - self.last_stats_update < 1.0: return # Rate limit 1s

        try:
            # Temp
            with open('/sys/class/thermal/thermal_zone0/temp', 'r') as f:
                self.pi_data['temp'] = f"{int(f.read()) / 1000:.0f}C"
            
            # RAM
            with open('/proc/meminfo', 'r') as f:
                lines = f.readlines()
            total = int(lines[0].split()[1])
            avail = int(lines[2].split()[1])
            percent = 100 * (1 - avail / total)
            self.pi_data['ram'] = f"{percent:.0f}%"

            # CPU
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

    def handle_input(self, action):
        # --- INFO MODE NAVIGATION ---
        if self.view_mode == 'info':
            # Short Press: Switch Pages
            if action == 'tap_down':
                self.info_page = 1 # Go to Page 2
            elif action == 'tap_up':
                self.info_page = 0 # Go to Page 1
            
            # Long Press: Exit to Menu
            elif action in ['hold_up', 'hold_down']:
                self.view_mode = 'list'
                self.info_page = 0
            return None

        # --- LIST MODE NAVIGATION ---
        count = len(self.items)
        if action == 'tap_up':   self.sel = (self.sel - 1) % count
        if action == 'tap_down': self.sel = (self.sel + 1) % count
        
        if action == 'hold_down': # Select
            act = self.items[self.sel]['action']
            if act == 'back': return 'BACK'
            elif act == 'sys_info':
                self.view_mode = 'info'
                self.info_page = 0
            elif act == 'reboot':
                # Trigger reboot command here if needed
                pass
        
        if action == 'hold_up': return 'BACK'
        return None

    def get_view(self):
        lines = {}
        
        # --- SYSTEM INFO VIEW ---
        if self.view_mode == 'info':
            lines['line1'] = ("System Info", self.FLAG_HEADER)
            
            if self.info_page == 0:
                # Page 1: Static Info
                lines['line2'] = ("DIS V4.3".center(16)[:16], self.FLAG_ITEM)
                lines['line3'] = ("Audi A2/TT".center(16)[:16], self.FLAG_ITEM)
                lines['line4'] = (" ".center(16), self.FLAG_ITEM)
                lines['line5'] = ("1/2".center(16)[:16], self.FLAG_ITEM)
            else:
                # Page 2: Live Stats
                self._read_pi_stats()
                cpu = f"CPU: {self.pi_data['cpu']}"
                ram = f"RAM: {self.pi_data['ram']}"
                tmp = f"Tmp: {self.pi_data['temp']}"
                
                lines['line2'] = (cpu.ljust(16)[:16], self.FLAG_ITEM)
                lines['line3'] = (ram.ljust(16)[:16], self.FLAG_ITEM)
                lines['line4'] = (tmp.ljust(16)[:16], self.FLAG_ITEM)
                lines['line5'] = ("2/2".center(16)[:16], self.FLAG_ITEM)

        # --- LIST VIEW ---
        else:
            lines['line1'] = (self.header, self.FLAG_HEADER)
            visible = 4
            if self.sel < self.scroll: self.scroll = self.sel
            elif self.sel >= self.scroll + visible: self.scroll = self.sel - visible + 1
            
            for i in range(visible):
                idx = self.scroll + i
                key = f'line{i+2}'
                if idx < len(self.items):
                    prefix = ">" if idx == self.sel else " "
                    txt = f"{prefix}{self.items[idx]['label']}".ljust(16)[:16]
                    lines[key] = (txt, self.FLAG_ITEM)
                else:
                    lines[key] = (" " * 16, self.FLAG_ITEM)
                    
        return lines