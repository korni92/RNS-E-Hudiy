from .base import BaseApp

class MediaApp(BaseApp):
    def __init__(self):
        super().__init__()
        self.title = ""
        self.artist = ""
        self.album = ""
        self.time_str = ""

    def update_hudiy(self, topic, data):
        if topic == b'HUDIY_MEDIA':
            # 1. Extract Metadata
            self.title  = data.get('title', '')
            self.artist = data.get('artist', '')
            self.album  = data.get('album', '')
            
            # 2. Format Time: "1:23 / 4:56"
            pos = data.get('position', '0:00')
            dur = data.get('duration', '0:00')
            
            # Only show time if valid
            if dur == '0:00' and pos == '0:00':
                self.time_str = ""
            else:
                self.time_str = f"{pos} / {dur}"

    def handle_input(self, action):
        # Return to Media Menu
        if action in ['hold_up', 'hold_down']: return 'BACK'
        return None

    def get_view(self):
        lines = {}
        
        # Helper to format text: Ensure it is string, pad to 16 to wipe old text
        def fmt(text):
            return (str(text) if text else "").ljust(16)[:16]

        # Line 1: Title (Compact Font 0x06)
        lines['line1'] = (fmt(self.title), self.FLAG_ITEM)

        # Line 2: Artist (Compact Font 0x06)
        lines['line2'] = (fmt(self.artist), self.FLAG_ITEM)

        # Line 3: Album (Compact Font 0x06)
        lines['line3'] = (fmt(self.album), self.FLAG_ITEM)

        # Line 4: Time (Compact Font 0x06)
        lines['line4'] = (fmt(self.time_str), self.FLAG_ITEM)

        # Line 5: Back Label (Compact Font 0x06)
        # Visual cue that holding button goes back
        lines['line5'] = (fmt("Back"), self.FLAG_ITEM)

        return lines