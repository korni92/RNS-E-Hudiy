import time

class BaseApp:
    # --- SHARED DISPLAY FLAGS ---
    FLAG_HEADER = 0x22 # Fixed Width + Protocol Center
    FLAG_WIPE   = 0x02 # Fixed Width + Manual Center (Wipes ghosts)
    FLAG_ITEM   = 0x06 # Compact Font + Left Align

    def __init__(self):
        self.active = False
        self.topics = set()
        
        # Scroll State: { 'key': {'offset': 0, 'last_tick': 0, 'pause': 0} }
        self._scroll_state = {}

    def set_topics(self, *args):
        for t_set in args: self.topics.update(t_set)

    def on_enter(self):
        """Called when app becomes active"""
        self.active = True

    def on_leave(self):
        """Called when app goes to background"""
        self.active = False
        self._scroll_state = {} # Reset scroll states

    def update_can(self, topic, payload):
        pass

    def update_hudiy(self, topic, data):
        pass

    def handle_input(self, action):
        # Return: None, 'BACK', or 'app_name'
        return None

    def get_view(self):
        # Returns Dict (Text Lines) or List (Draw Commands)
        return {}

    def _scroll_text(self, text, key, max_len=16, speed_ms=200):
        """
        Returns a window of text that scrolls if longer than max_len.
        """
        if not text: return ""
        text = str(text)
        
        if len(text) <= max_len:
            # If it fits, remove state so it resets if it grows later
            if key in self._scroll_state: del self._scroll_state[key]
            return text.ljust(max_len)[:max_len]

        now = time.time() * 1000
        
        if key not in self._scroll_state:
            self._scroll_state[key] = {
                'offset': 0, 
                'last_tick': now, 
                'pause_until': now + 1000
            }
            
        state = self._scroll_state[key]
        
        if now < state['pause_until']:
            offset = state['offset']
            return text[offset : offset + max_len]

        if now - state['last_tick'] > speed_ms:
            state['last_tick'] = now
            state['offset'] += 1
            
            if state['offset'] + max_len > len(text):
                state['offset'] = 0
                state['pause_until'] = now + 1000
            
        offset = state['offset']
        return text[offset : offset + max_len]
