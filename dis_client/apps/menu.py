# /home/pi/dis_manager/apps/menu.py
from .base import BaseApp

class MenuApp(BaseApp):
    def __init__(self, title, items):
        super().__init__()
        self.title = title
        self.items = items
        self.sel = 0
        self.scroll = 0
        self.max_lines = 4

    def set_item_label(self, target, new_label):
        """Updates the label of a specific menu item identified by its target."""
        for item in self.items:
            if item.get('target') == target:
                # Only trigger update if text actually changed
                if item['label'] != new_label:
                    item['label'] = new_label
                return

    def handle_input(self, action):
        count = len(self.items)
        
        if action == 'tap_up':
            self.sel = (self.sel - 1) % count
        elif action == 'tap_down':
            self.sel = (self.sel + 1) % count
        
        elif action == 'hold_down': # Select
            item = self.items[self.sel]
            target = item.get('target')
            if target: return target
            
        elif action == 'hold_up': # Back
            return 'BACK'
            
        return None

    def get_view(self):
        lines = {}
        # Line 1: Wide Centered Title
        lines['line1'] = (self.title, self.FLAG_HEADER)

        # Scroll Logic
        if self.sel < self.scroll: self.scroll = self.sel
        elif self.sel >= self.scroll + self.max_lines: self.scroll = self.sel - self.max_lines + 1
        self.scroll = max(0, self.scroll)
        
        # Render Items
        for i in range(self.max_lines):
            idx = self.scroll + i
            key = f'line{i+2}'
            if idx < len(self.items):
                prefix = ">" if idx == self.sel else " "
                # Pad to 16 to wipe old text
                txt = f"{prefix}{self.items[idx]['label']}".ljust(16)[:16]
                lines[key] = (txt, self.FLAG_ITEM)
            else:
                lines[key] = (" " * 16, self.FLAG_ITEM)
        return lines