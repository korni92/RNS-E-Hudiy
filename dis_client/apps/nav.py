from .base import BaseApp

class NavApp(BaseApp):
    def __init__(self):
        super().__init__()
        self.maneuver = "No Route"
        self.distance = ""
        self.street = ""

    def update_hudiy(self, topic, data):
        if topic == b'HUDIY_NAV':
            # Extract data from V2.5 extractor
            self.maneuver = data.get('maneuver_text', '')
            self.distance = data.get('distance', '')
            self.street   = data.get('description', '')

    def handle_input(self, action):
        if action in ['hold_up', 'hold_down']: return 'BACK'
        return None

    def get_view(self):
        lines = {}
        lines['line1'] = ("Navigation", self.FLAG_HEADER)

        # Logic: If no active maneuver, show "No Route"
        if not self.distance and not self.maneuver:
            lines['line3'] = ("No Route".center(10), self.FLAG_WIPE)
            lines['line4'] = (" " * 16, self.FLAG_ITEM)
            return lines

        # Line 3: Maneuver (e.g. "Turn Right") - Wide Centered
        man = self.maneuver[:10].center(10)
        lines['line3'] = (man, self.FLAG_WIPE)

        # Line 4: Distance + Street (e.g. "500m Main St") - Compact
        # Combine if space allows, or priority to distance
        if self.distance:
            info = f"{self.distance} {self.street}"
        else:
            info = self.street
            
        lines['line4'] = (info.ljust(16)[:16], self.FLAG_ITEM)
        return lines