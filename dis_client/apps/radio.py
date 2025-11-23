# /home/pi/dis_manager/apps/radio.py
from .base import BaseApp

class RadioApp(BaseApp):
    def __init__(self):
        super().__init__()
        self.top = "Radio"
        self.bot = ""
        self.topics_top = set()
        self.topics_bot = set()

    def set_topics(self, t_top, t_bot):
        """Called by main driver to inject configured topics."""
        self.topics_top = t_top
        self.topics_bot = t_bot

    def update_can(self, topic, payload):
        try:
            # No .strip() to preserve clearing spaces
            raw = payload.split(b'\x00')[0]
            text = raw.decode('ascii', errors='replace').rstrip('\x00')
            
            if topic in self.topics_top: self.top = text
            elif topic in self.topics_bot: self.bot = text
        except: pass

    def handle_input(self, action):
        # Long Press UP -> Go Back to previous menu (Media)
        if action == 'hold_up': 
            return 'BACK'
        # Long Press DOWN -> Also Back (or stay/exit)
        if action == 'hold_down':
            return 'BACK'
        return None

    def get_view(self):
        lines = {}
        # Line 1: "Radio" Headline (Centered Protocol)
        lines['line1'] = ("Radio", self.FLAG_HEADER)

        # Line 3: Station Info (Manual Wipe)
        # Using Fixed Width (0x02) + Manual Padding to erase ghosts
        txt_top = self.top
        if not txt_top.strip(): txt_top = " " * 10
        else: txt_top = txt_top[:10].center(10)
        lines['line3'] = (txt_top, self.FLAG_WIPE)

        # Line 4: Details (Manual Wipe Left Align)
        txt_bot = self.bot
        if not txt_bot.strip(): txt_bot = " " * 16
        else: txt_bot = txt_bot.ljust(16)[:16]
        lines['line4'] = (txt_bot, self.FLAG_ITEM) # Use ITEM flag for smaller text

        # Clear unused
        lines['line2'] = (" " * 16, self.FLAG_ITEM)
        lines['line5'] = (" " * 16, self.FLAG_ITEM)
        return lines