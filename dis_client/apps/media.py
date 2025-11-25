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
            self.title  = data.get('title', '')
            self.artist = data.get('artist', '')
            self.album  = data.get('album', '')
            
            pos = data.get('position', '0:00')
            dur = data.get('duration', '0:00')
            
            if dur == '0:00' and pos == '0:00':
                self.time_str = ""
            else:
                self.time_str = f"{pos} / {dur}"

    def handle_input(self, action):
        if action in ['hold_up', 'hold_down']: return 'BACK'
        return None

    def get_view(self):
        lines = {}
        
        # Use _scroll_text helper from BaseApp
        # Speed: 200ms per character update
        # Key: Unique identifier for the scroll state (e.g. 'media_title')
        
        title_scroll = self._scroll_text(self.title, 'media_title', 16, 200)
        artist_scroll = self._scroll_text(self.artist, 'media_artist', 16, 200)
        album_scroll = self._scroll_text(self.album, 'media_album', 16, 200)

        lines['line1'] = (title_scroll, self.FLAG_ITEM)
        lines['line2'] = (artist_scroll, self.FLAG_ITEM)
        lines['line3'] = (album_scroll, self.FLAG_ITEM)
        
        # Standard static fields
        def fmt(text): return (str(text) if text else "").ljust(16)[:16]
        lines['line4'] = (fmt(self.time_str), self.FLAG_ITEM)

        return lines
