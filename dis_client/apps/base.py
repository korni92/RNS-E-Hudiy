# /home/pi/dis_manager/apps/base.py
class BaseApp:
    # --- SHARED DISPLAY FLAGS ---
    # 0x22: Fixed Width + Protocol Center (Best for Static Titles like "Main Menu")
    FLAG_HEADER = 0x22  
    
    # 0x02: Fixed Width + Manual Center (Best for Dynamic Data like Radio to wipe ghosts)
    FLAG_WIPE   = 0x02  
    
    # 0x06: Compact Font + Left Align (Best for Lists/Items)
    FLAG_ITEM   = 0x06  

    def __init__(self):
        self.active = False

    def on_enter(self):
        """Called when the app becomes active."""
        self.active = True

    def on_leave(self):
        """Called when the app is put in background."""
        self.active = False

    def update_can(self, topic, payload):
        """Handle raw CAN data."""
        pass

    def update_hudiy(self, topic, data):
        """Handle JSON data from HUDIY scripts."""
        pass

    def handle_input(self, action):
        """
        Return: 
          - None (do nothing)
          - 'BACK' (go to previous menu)
          - 'app_name' (switch to specific app)
        """
        return None

    def get_view(self):
        """Return dict: {'lineX': (text, flag)}"""
        return {}
