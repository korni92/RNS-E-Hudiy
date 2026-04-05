from .base import BaseApp
from typing import List, Dict, Any, Tuple
import logging
import time

logger = logging.getLogger(__name__)

class NavApp(BaseApp):
    """
    Navigation Application for HUDIY.
    Handles maneuver icons, distance labels, and scrolling street names
    with an progress bar.
    """
    def __init__(self):
        super().__init__()
        self.maneuver_type = 0
        self.maneuver_side = 3
        self.description = ""
        self.distance_label = ""
        
        # State tracking to prevent flickering and ghosting
        self.force_redraw = True
        self._last_icon = None
        self._last_dist = None
        self._last_street = None
        self._last_state_no_route = False
        
        # Progress bar optimization
        self._last_bar_active = False
        self._drawn_dashed_bar = False
        self._filled_gaps = 0
        
        # Custom scroller tracking (Traffic-Optimized)
        self._scroll_idx = 0
        self._scroll_last_time = 0
        self._scroll_phase = 'START'  # Phases: 'START', 'SCROLL', 'CLEAR'
        self._scroll_current_street = ""

    def on_enter(self):
        """Reset redraw flag when entering the app."""
        self.force_redraw = True

    def update_hudiy(self, topic: bytes, data: Dict[str, Any]):
        """Update navigation data from system topics."""
        if topic == b'HUDIY_NAV':
            self.description = data.get('description', '')
            self.maneuver_type = data.get('maneuver_type', 0)
            self.maneuver_side = data.get('maneuver_side', 3)
            self.distance_label = data.get('distance', self.distance_label) 

        elif topic == b'HUDIY_NAV_DISTANCE':
            self.distance_label = data.get('label', '')

    def handle_input(self, action: str) -> str:
        """Handle user input; long-press returns to the previous menu."""
        if action in ['hold_up', 'hold_down']:
            return 'BACK'
        return None

    def _get_icon_name(self) -> str:
        """Map maneuver types and sides to specific icon asset names."""
        t = self.maneuver_type
        side_str = "LEFT" if self.maneuver_side == 1 else "RIGHT"
        mapping = {
            0:  "STRAIGHT", 1:  "DEPART", 3:  f"SLIGHT_{side_str}",
            4:  f"TURN_{side_str}", 5:  f"SHARP_{side_str}", 6:  "UTURN",
            7:  f"RAMP_{side_str}", 8:  f"RAMP_{side_str}", 9:  f"FORK_{side_str}",
            10: "MERGE", 11: "ROUNDABOUT_ENTER", 12: "ROUNDABOUT_EXIT",
            13: "ROUNDABOUT_FULL", 14: "STRAIGHT", 19: "DESTINATION",
        }
        return mapping.get(t, "STRAIGHT")

    def _get_progress_info(self) -> Tuple[int, bool]:
        """Calculate how many segments of the progress bar should be filled."""
        if not self.distance_label: 
            return 0, False
        try:
            s = self.distance_label.lower().replace(',', '.')
            if 'now' in s or 'arrived' in s: 
                return 9, True
            
            val = 0.0
            if 'km' in s: 
                val = float(s.split('km')[0].strip()) * 1000
            elif 'm' in s: 
                val = float(s.split('m')[0].strip())
            else: 
                return 9, True
            
            if val > 300: 
                return 0, False
            
            # Scale 300m -> 0m to 0-9 segments
            ratio = (300.0 - val) / 300.0
            gaps = min(9, max(0, int(round(ratio * 9))))
            return gaps, True
        except Exception:
            return 9, True

    def _nav_scroll_text(self, text: str, max_chars: int) -> str:
        """Logic for scrolling long street names in the display area."""
        if self._scroll_current_street != text:
            self._scroll_current_street = text
            self._scroll_idx = 0
            self._scroll_last_time = time.time()
            self._scroll_phase = 'START'
            
        now = time.time()
        
        if self._scroll_phase == 'START':
            # Hold at the start for 3 seconds
            if now - self._scroll_last_time > 3.0:
                self._scroll_phase = 'SCROLL'
                self._scroll_last_time = now
            return text[:max_chars]
            
        elif self._scroll_phase == 'SCROLL':
            # Shift characters every 1 second
            if now - self._scroll_last_time > 1.0:
                self._scroll_idx += 1
                self._scroll_last_time = now
                
            if self._scroll_idx > len(text) - max_chars:
                self._scroll_phase = 'CLEAR'
                self._scroll_last_time = now
                return ""
                
            return text[self._scroll_idx : self._scroll_idx + max_chars]
            
        elif self._scroll_phase == 'CLEAR':
            # Brief clear phase before restarting
            if now - self._scroll_last_time > 1.0: 
                self._scroll_phase = 'START'
                self._scroll_idx = 0
                self._scroll_last_time = now
                return text[:max_chars]
            return ""

        return text[:max_chars]

    def get_view(self) -> List[Dict]:
        """Main rendering logic. Returns a list of draw commands for the HUD."""
        has_route = bool(self.description or self.distance_label)

        # Handle "No Route" state
        if not has_route:
            if not self._last_state_no_route:
                self._last_state_no_route = True
                self.force_redraw = True 
            return {
                'line3': ("No Route".center(11), self.FLAG_WIPE),
                'line4': ("" .ljust(16), self.FLAG_ITEM)
            }

        if self._last_state_no_route:
            self._last_state_no_route = False
            self.force_redraw = True 

        icon_name = self._get_icon_name()
        dist_clean = self.distance_label.replace(" ", "") if self.distance_label else ""

        # Cleanup street name by removing common direction prefixes
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

        target_gaps, bar_active = self._get_progress_info()
        
        # STRICT ZONING: Prevent text from bleeding into the progress bar area
        # 12 chars fit safely into the 60px available.
        max_chars = 12 if bar_active else 16
        
        if len(street) <= max_chars:
            display_street = street.center(max_chars)
        else:
            display_street = self._nav_scroll_text(street, max_chars)

        is_partial = not self.force_redraw
        commands = [{'type': 'nav_graphic_v2', 'partial': is_partial}]
        has_changes = False

        # STRICT ZONING: Text covers pixels 0-59. Bar starts exactly at 60.
        text_width = 60 if bar_active else 64

        # Clear street area if bar state changed
        if self.force_redraw or self._last_bar_active != bar_active:
            if is_partial:
                commands.append({'cmd': 'clear_area', 'x': 0, 'y': 40, 'w': text_width, 'h': 8})
            self._last_street = None 
            self._last_bar_active = bar_active

        # Update maneuver icon
        if self.force_redraw or icon_name != self._last_icon:
            if is_partial: 
                commands.append({'cmd': 'clear_area', 'x': 0, 'y': 1, 'w': 31, 'h': 37})
            commands.append({'cmd': 'draw_bitmap', 'icon': icon_name, 'x': 0, 'y': 1})
            self._last_icon = icon_name
            has_changes = True

        # Update distance label
        if self.force_redraw or dist_clean != self._last_dist:
            if is_partial:
                # Clear up to column 59
                commands.append({'cmd': 'clear_area', 'x': 32, 'y': 1, 'w': 28, 'h': 8})
            commands.append({'cmd': 'draw_text', 'text': dist_clean, 'x': 34, 'y': 1, 'flags': 0x06})
            self._last_dist = dist_clean
            has_changes = True

        # Update street name
        if self.force_redraw or display_street != self._last_street:
            if is_partial:
                commands.append({'cmd': 'clear_area', 'x': 0, 'y': 40, 'w': text_width, 'h': 8})
            
            if display_street:
                commands.append({'cmd': 'draw_text', 'text': display_street, 'x': 0, 'y': 40, 'flags': 0x06})
                
            self._last_street = display_street
            has_changes = True

        # Render Progress Bar
        if bar_active:
            needs_skeleton = not self._drawn_dashed_bar or target_gaps < self._filled_gaps
            
            if self.force_redraw or needs_skeleton:
                if is_partial: 
                    commands.append({'cmd': 'clear_area', 'x': 60, 'y': 1, 'w': 4, 'h': 47})
                
                # Draw background dashes
                for dash_y in [45, 40, 35, 30, 25, 20, 15, 10, 5]:
                    commands += [
                        {'cmd': 'draw_line', 'x': 61, 'y': dash_y, 'len': 2, 'vert': True},
                        {'cmd': 'draw_line', 'x': 62, 'y': dash_y, 'len': 2, 'vert': True},
                        {'cmd': 'draw_line', 'x': 63, 'y': dash_y, 'len': 2, 'vert': True},
                    ]
                self._drawn_dashed_bar = True
                self._filled_gaps = 0
                has_changes = True

            # Fill segments based on distance
            if target_gaps > self._filled_gaps:
                gaps_y = [42, 37, 32, 27, 22, 17, 12, 7, 2]
                for i in range(self._filled_gaps, target_gaps):
                    gy = gaps_y[i]
                    commands += [
                        {'cmd': 'draw_line', 'x': 61, 'y': gy, 'len': 3, 'vert': True},
                        {'cmd': 'draw_line', 'x': 62, 'y': gy, 'len': 3, 'vert': True},
                        {'cmd': 'draw_line', 'x': 63, 'y': gy, 'len': 3, 'vert': True},
                    ]
                self._filled_gaps = target_gaps
                has_changes = True
        else:
            # Clear bar if it was previously drawn
            if self._drawn_dashed_bar:
                if is_partial:
                    commands.append({'cmd': 'clear_area', 'x': 60, 'y': 1, 'w': 4, 'h': 47})
                self._drawn_dashed_bar = False
                self._filled_gaps = 0
                has_changes = True

        # If nothing changed in partial mode, return an empty list to save bandwidth
        if not has_changes and is_partial:
            return []

        self.force_redraw = False
        return commands
