from .base import BaseApp

class PhoneApp(BaseApp):
    def __init__(self):
        super().__init__()
        self.state = "IDLE"
        self.caller = ""
        self.battery = 0
        self.signal = 0
        self.conn_state = "DISCONNECTED"

    def update_hudiy(self, topic, data):
        if topic == b'HUDIY_PHONE':
            self.state = data.get('state', 'IDLE')
            self.caller = data.get('caller_name') or data.get('caller_id') or "Unknown"
            self.battery = data.get('battery', 0)
            self.signal = data.get('signal', 0)
            self.conn_state = data.get('connection_state', 'DISCONNECTED')

    def handle_input(self, action):
        if action in ['hold_up', 'hold_down']: return 'BACK'
        return None

    def get_view(self):
        lines = {}
        lines['line1'] = ("Phone", self.FLAG_HEADER)

        # Scenario A: Active Call / Incoming / Dialing
        if self.state in ['INCOMING', 'ACTIVE', 'ALERTING', 'DIALING']:
            # Line 3: State (e.g. "INCOMING")
            lbl = self.state[:10].center(10)
            lines['line3'] = (lbl, self.FLAG_WIPE)
            
            # Line 4: Caller Name
            name = self.caller.ljust(16)[:16]
            lines['line4'] = (name, self.FLAG_ITEM)

        # Scenario B: Idle but Connected
        elif self.conn_state == 'CONNECTED':
            # Line 3: "Connected"
            lines['line3'] = ("Connected".center(10), self.FLAG_WIPE)
            
            # Line 4: Battery & Signal
            # e.g. "Bat:4 Sig:100%"
            stats = f"Bat:{self.battery} Sig:{self.signal}%"
            lines['line4'] = (stats.ljust(16)[:16], self.FLAG_ITEM)

        # Scenario C: Disconnected
        else:
            lines['line3'] = ("No Phone".center(10), self.FLAG_WIPE)
            lines['line4'] = (" " * 16, self.FLAG_ITEM)

        return lines