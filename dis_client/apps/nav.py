from .base import BaseApp
from typing import List, Dict, Any
import logging

logger = logging.getLogger(__name__)

class NavApp(BaseApp):
    def __init__(self):
        super().__init__()
        self.maneuver_type = 0      # NavigationManeuverType
        self.maneuver_side = 3      # UNSPECIFIED
        self.description = ""       # "Turn left onto Main St"
        self.distance_label = ""    # "500 m" or "2.3 km"
        self.icon_data = b""        # Raw PNG from HUDIY (not used)
        
        # Cache previous state to prevent flickering logic if needed
        self.last_maneuver = -1

    def update_hudiy(self, topic: bytes, data: Dict[str, Any]):
        if topic == b'HUDIY_NAV':
            # Full maneuver update
            self.description = data.get('description', '')
            self.maneuver_type = data.get('maneuver_type', 0)
            self.maneuver_side = data.get('maneuver_side', 3)
            # self.icon_data = data.get('icon', b"") 

        elif topic == b'HUDIY_NAV_DISTANCE':
            self.distance_label = data.get('label', '')

    def handle_input(self, action):
        if action in ['hold_up', 'hold_down']:
            return 'BACK'
        return None

    def _get_icon_name(self) -> str:
        """
        Map HUDIY maneuver type + side -> icon key.
        FALLBACK: Maps complex turns to basic LEFT/RIGHT/STRAIGHT 
        until specific bitmaps are added to icons.py.
        """
        t = self.maneuver_type
        # side is 1 for Left, 2 for Right (usually)
        side_str = "LEFT" if self.maneuver_side == 1 else "RIGHT"

        # --- SAFE MAPPING (Uses only LEFT, RIGHT, STRAIGHT) ---
        # Modify 'icons.py' to add specific icons (UTURN, FORK, etc) 
        # then update this mapping to use specific names.
        mapping = {
            0:  "STRAIGHT",         # UNKNOWN
            1:  "DEPART",           # DEPART
            3:  f"SLIGHT_{side_str}", # SLIGHT_TURN
            4:  f"TURN_{side_str}",   # TURN
            5:  f"SHARP_{side_str}",  # SHARP_TURN
            6:  "UTURN",            # UTURN (Standard is left)
            7:  f"RAMP_{side_str}",   # ON_RAMP
            8:  f"RAMP_{side_str}",   # OFF_RAMP (Use Ramp icon for now)
            9:  f"FORK_{side_str}",   # FORK
            10: "MERGE",            # MERGE
            11: "ROUNDABOUT_ENTER", # ROUNDABOUT_ENTER (Usually right/CCW)
            12: "ROUNDABOUT_EXIT",  # ROUNDABOUT_EXIT
            13: "ROUNDABOUT_FULL",  # ROUNDABOUT_FULL
            14: "STRAIGHT",         # STRAIGHT
            19: "DESTINATION",      # DESTINATION
        }
        return mapping.get(t, "STRAIGHT")

    def _get_progress_height(self) -> int:
        """Convert distance string to bar height (0..36 px, 300 m = full)"""
        if not self.distance_label:
            return 0
        try:
            s = self.distance_label.lower().replace(',', '.')
            if 'now' in s or 'arrived' in s:
                return 36
            
            val = 0.0
            if 'km' in s:
                val = float(s.split('km')[0].strip()) * 1000
            elif 'm' in s:
                val = float(s.split('m')[0].strip())
            else:
                return 36 # Unknown unit, show full bar
            
            # "Approach Bar" Logic:
            # >300m: Empty (0px)
            # 300m -> 0m: Fills up (0px -> 36px)
            if val > 300: return 0
            
            # Calculate fill ratio
            ratio = (300.0 - val) / 300.0
            return int(ratio * 36)
            
        except:
            return 36

    def get_view(self) -> List[Dict]:
        # If no route, show text fallback
        if not self.description and not self.distance_label:
            return {
                'line3': ("No Route".center(11), self.FLAG_WIPE),
                'line4': ("" .ljust(16), self.FLAG_ITEM)
            }

        icon_name = self._get_icon_name()
        bar_h = self._get_progress_height()

        # Clean distance: "500 m" -> "500m", but ONLY if we actually have a label
        dist_clean = ""
        if self.distance_label:
            dist_clean = self.distance_label.replace(" ", "").replace("km", "km").replace("m", "m")

        # Build graphical command list
        # The 'type' key is used by the engine for caching signatures
        commands = [{'type': 'nav_graphic_v2'}]

        # 1. Big arrow — moved UP to Y=1
        commands.append({
            'cmd': 'draw_bitmap',
            'icon': icon_name,
            'x': 0,
            'y': 1   # Moved up to maximize vertical space
        })

        # 2. Distance (top-right) — only draw if we have real data
        if dist_clean:
            commands.append({
                'cmd': 'draw_text',
                'text': dist_clean,
                'x': 34,
                'y': 10,
                'flags': 0x06 # Compact Font
            })

        # 3. Street name (bottom, centered)
        # Extract just the street name if possible
        street = self.description
        prefixes = [
            "Turn left onto ", "Turn right onto ", "Turn left into ", "Turn right into ",
            "Keep left onto ", "Keep right onto ", "Head onto ", "Continue onto ",
            "Take the ", " toward ", " towards "
        ]
        for p in prefixes:
            if p.lower() in street.lower():
                street = street.lower().split(p.lower(), 1)[-1]
                break
        
        street = street.strip(" .,;").strip()
        if len(street) > 18:
            street = street[:15] + "..."
            
        commands.append({
            'cmd': 'draw_text',
            'text': street.center(18), # Center align manually for 0x06 font
            'x': 2,
            'y': 39, # Moved down to 39 to hug the bottom edge
            'flags': 0x06
        })

        # 4. Red: Progress bar (Right Edge)
        # Draws an "Approach Bar" that fills up from bottom-to-top
        if bar_h > 0:
            start_y = 48 - bar_h # Anchor to bottom (Y=48)
            
            # Draw 3 vertical lines for a thick bar
            commands += [
                {'cmd': 'draw_line', 'x': 61, 'y': start_y, 'len': bar_h, 'vert': True},
                {'cmd': 'draw_line', 'x': 62, 'y': start_y, 'len': bar_h, 'vert': True},
                {'cmd': 'draw_line', 'x': 63, 'y': start_y, 'len': bar_h, 'vert': True},
            ]

        return commands
