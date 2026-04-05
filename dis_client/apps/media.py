#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# Audi DIS (Cluster) DDP Protocol Driver - Media App
#
# Handles rendering media playback information (Title, Artist, Album, Time)
# to the instrument cluster display, utilizing the scrolling text engine.
#

from .base import BaseApp


class MediaApp(BaseApp):
    """
    Application responsible for displaying current media playback details.
    Listens to HUDIY_MEDIA topics and formats data for screen lines.
    """

    def __init__(self):
        super().__init__()
        self.title = ""
        self.artist = ""
        self.album = ""
        self.time_str = ""

    def update_hudiy(self, topic, data):
        """
        Updates the internal media state when new playback data arrives over ZMQ.
        """
        if topic == b'HUDIY_MEDIA':
            self.title  = data.get('title', '')
            self.artist = data.get('artist', '')
            self.album  = data.get('album', '')
            
            pos = data.get('position', '0:00')
            dur = data.get('duration', '0:00')
            
            # Hide the timer line entirely if no valid times are reported
            if dur == '0:00' and pos == '0:00':
                self.time_str = ""
            else:
                self.time_str = f"{pos} / {dur}"

    def handle_input(self, action):
        """
        Processes button inputs. Holding up or down exits the media view.
        """
        if action in ['hold_up', 'hold_down']: 
            return 'BACK'
            
        return None

    def get_view(self):
        """
        Builds the display line output, applying text scrolling where necessary.
        """
        lines = {}
        
        # Use _scroll_text helper from BaseApp (Speed is now governed by config.json)
        title_scroll = self._scroll_text(self.title, 'media_title', 16)
        artist_scroll = self._scroll_text(self.artist, 'media_artist', 16)
        album_scroll = self._scroll_text(self.album, 'media_album', 16)

        lines['line1'] = (title_scroll, self.FLAG_ITEM)
        lines['line2'] = (artist_scroll, self.FLAG_ITEM)
        lines['line3'] = (album_scroll, self.FLAG_ITEM)
        
        # Standard static fields formatter
        def fmt(text): 
            return (str(text) if text else "").ljust(16)[:16]
            
        lines['line4'] = (fmt(self.time_str), self.FLAG_ITEM)

        return lines