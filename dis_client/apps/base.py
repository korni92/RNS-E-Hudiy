#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# Audi DIS (Cluster) DDP Protocol Driver - Base App
#
# Provides the foundation for all display applications, including
# state management, configuration loading, and traffic-friendly text scrolling.
#

import time
import json
import os
import logging

logger = logging.getLogger(__name__)

class BaseApp:
    """
    Base class for all DIS applications. 
    Provides shared flags, lifecycle hooks, and common utilities like text scrolling.
    """
    
    # --- SHARED DISPLAY FLAGS ---
    FLAG_HEADER = 0x22  # Fixed Width + Protocol Center
    FLAG_WIPE   = 0x02  # Fixed Width + Manual Center (Wipes ghosts)
    FLAG_ITEM   = 0x06  # Compact Font + Left Align

    def __init__(self):
        self.active = False
        self.topics = set()
        
        # Scroll State: { 'key': {'text': '', 'offset': 0, 'last_tick': 0, 'phase': 'START'} }
        self._scroll_state = {}

        # Central scroll configuration (with fallbacks)
        self._scroll_cfg = {
            "enabled": True,
            "start_pause": 3.0,
            "interval": 1.0,
            "end_pause": 0.5
        }
        self._load_config()

    def _load_config(self):
        """Loads scrolling configuration from the global settings file."""
        try:
            if os.path.exists('/home/pi/config.json'):
                with open('/home/pi/config.json', 'r') as f:
                    cfg = json.load(f)
                    if 'fis_display' in cfg:
                        fis = cfg['fis_display']
                        self._scroll_cfg['enabled'] = fis.get('scrolling_enabled', True)
                        self._scroll_cfg['start_pause'] = fis.get('scroll_start_pause_sec', 3.0)
                        self._scroll_cfg['interval'] = fis.get('scroll_interval_sec', 1.0)
                        self._scroll_cfg['end_pause'] = fis.get('scroll_end_pause_sec', 0.5)
        except Exception as e:
            logger.error(f"BaseApp: Error loading scroll config: {e}")

    def set_topics(self, *args):
        """Registers CAN topics that this app needs to listen to."""
        for t_set in args: 
            self.topics.update(t_set)

    def on_enter(self):
        """Lifecycle hook: Called when the app becomes active."""
        self.active = True

    def on_leave(self):
        """Lifecycle hook: Called when the app goes to the background."""
        self.active = False
        self._scroll_state = {}  # Reset scroll states

    def update_can(self, topic, payload):
        """Hook to handle incoming CAN messages."""
        pass

    def update_hudiy(self, topic, data):
        """Hook to handle incoming HUDIY (Phone/Nav/Media) messages."""
        pass

    def handle_input(self, action):
        """Hook to handle user input (button presses)."""
        return None

    def get_view(self):
        """Hook to return the UI elements to be rendered."""
        return {}

    def _scroll_text(self, text, key, max_len=16, speed_ms=None):
        """
        Traffic-friendly text scroller with a state machine (START, SCROLL, CLEAR).
        Ignores legacy 'speed_ms' in favor of the global configuration.
        """
        if not text: 
            return ""
        
        text = str(text)

        # If scrolling is disabled or text fits within the limit
        if not self._scroll_cfg["enabled"] or len(text) <= max_len:
            if key in self._scroll_state: 
                del self._scroll_state[key]
            return text[:max_len]

        now = time.time()

        # New text = Reset the scroller
        if key not in self._scroll_state or self._scroll_state[key].get('text') != text:
            self._scroll_state[key] = {
                'text': text,
                'offset': 0,
                'last_tick': now,
                'phase': 'START'
            }

        state = self._scroll_state[key]
        phase = state['phase']

        if phase == 'START':
            if now - state['last_tick'] > self._scroll_cfg["start_pause"]:
                state['phase'] = 'SCROLL'
                state['last_tick'] = now
            return text[:max_len]

        elif phase == 'SCROLL':
            if now - state['last_tick'] > self._scroll_cfg["interval"]:
                state['offset'] += 1
                state['last_tick'] = now

            if state['offset'] > len(text) - max_len:
                state['phase'] = 'CLEAR'
                state['last_tick'] = now
                return ""  # Wipes the line clean for 'end_pause' seconds!

            return text[state['offset'] : state['offset'] + max_len]

        elif phase == 'CLEAR':
            if now - state['last_tick'] > self._scroll_cfg["end_pause"]:
                state['phase'] = 'START'
                state['offset'] = 0
                state['last_tick'] = now
                return text[:max_len]
            return ""

        return text[:max_len]