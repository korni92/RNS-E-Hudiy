import sys
from .base import BaseApp

class RadioApp(BaseApp):
    def __init__(self):
        super().__init__()
        self.top = "Radio"
        self.bot = ""
        self.topics_top = set()
        self.topics_bot = set()
        self.topics = set()
        print("[RadioApp] Initialized")

    def set_topics(self, t_top, t_bot):
        self.topics_top = t_top
        self.topics_bot = t_bot
        self.topics = self.topics_top.union(self.topics_bot)
        print(f"[RadioApp] Topics set: {self.topics}")

    def update_can(self, topic, payload):
        """
        Receives RAW BYTES from DisplayEngine.
        """
        is_top = topic in self.topics_top
        is_bot = topic in self.topics_bot

        if not (is_top or is_bot):
            return

        try:
            if isinstance(payload, bytes):
                # 1. Decode Audi ISO-8859-1 (Latin-1)
                decoded = payload.decode('iso-8859-1', errors='replace')
                
                # 2. Handle Control Characters (0x1C)
                # The radio sends 0x1C as a spacer block. We map it to SPACE (0x20).
                # This turns "\x1c\x1cFM" into "  FM", preserving the indentation.
                clean_text = decoded.replace('\x1c', ' ')
                
                # 3. Clean Nulls
                clean_text = clean_text.replace('\x00', '')
                
                # Debug: Verify we have leading spaces
                # print(f"[RadioApp] '{topic}' -> '{clean_text}'")

                if is_top:
                    self.top = clean_text
                elif is_bot:
                    self.bot = clean_text
                    
        except Exception as e:
            print(f"[RadioApp] Error decoding {topic}: {e}")

    def handle_input(self, action):
        if action in ['hold_up', 'hold_down']: 
            return 'BACK'
        return None

    def get_view(self):
        lines = {}
        lines['line1'] = ("Radio", self.FLAG_HEADER)

        t_top = str(self.top) if self.top else ""
        t_bot = str(self.bot) if self.bot else ""

        # --- FIX FOR ARTIFACTS AND CENTERING ---
        # We use .ljust(LENGTH) to pad the string with spaces.
        # This ensures "FM" becomes "FM        ", overwriting any old text like "TV/VIDEO".
        
        # Line 3 (Top Station): Limit 10 chars, Pad to 10
        # If t_top is "  FM" (4 chars), it becomes "  FM      " (10 chars).
        # This preserves the leading spaces (centering) AND clears the end (artifacts).
        if not t_top.strip():
             # If completely empty, send full blank line to wipe
            lines['line3'] = (" " * 10, self.FLAG_WIPE)
        else:
            lines['line3'] = (t_top[:10].ljust(10), self.FLAG_WIPE)

        # Line 4 (Info): Limit 16 chars, Pad to 16
        if not t_bot.strip():
            lines['line4'] = (" " * 16, self.FLAG_ITEM)
        else:
            lines['line4'] = (t_bot[:16].ljust(16), self.FLAG_ITEM)

        lines['line2'] = (" " * 16, self.FLAG_ITEM)
        lines['line5'] = (" " * 16, self.FLAG_ITEM)
        
        return lines
