#!/usr/bin/env python3
"""
📊 Hudiy Data Extractor (THREADING - ULTRA STABLE)
✅ AUTO-RECONNECT EVERY 5s
✅ SEPARATE THREADS: Media + Nav/Phone
✅ ONLY LOG CHANGES
✅ /tmp/now_playing.json
"""

import json
import time
import logging
import sys
import os
import threading

sys.path.insert(0, '/home/pi/hudiy_client/api_files/common')

from Client import Client
from ClientEventHandler import ClientEventHandler
from Api_pb2 import *

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

class HudiyEventHandler(ClientEventHandler):
    def __init__(self):
        self.last_media = None
        self.last_status = None
        self.last_nav_details = None
        self.last_call = None
        
    def on_hello_response(self, client, message):
        logger.info(f"✅ {client.name} Connected - API v{message.api_version.major}.{message.api_version.minor}")
        
        subs = SetStatusSubscriptions()
        
        if client.name == "MEDIA":
            subs.subscriptions.append(SetStatusSubscriptions.Subscription.MEDIA)
            client.send(MESSAGE_SET_STATUS_SUBSCRIPTIONS, 0, subs.SerializeToString())
            logger.info("📡 MEDIA Subscribed")
            
        elif client.name == "NAV_PHONE":
            subs.subscriptions.extend([
                SetStatusSubscriptions.Subscription.NAVIGATION,
                SetStatusSubscriptions.Subscription.PHONE
            ])
            client.send(MESSAGE_SET_STATUS_SUBSCRIPTIONS, 0, subs.SerializeToString())
            logger.info("📡 NAV+PHONE Subscribed")
    
    # === MEDIA ===
    def on_media_metadata(self, client, message):
        new_meta = f"{message.artist}|{message.title}|{message.album}"
        if new_meta != self.last_media:
            self.last_media = new_meta
            info = f"🎵 {message.artist} - {message.title}"
            if message.album: info += f" ({message.album})"
            logger.info(info)
            
            data = {
                'artist': message.artist or '',
                'title': message.title or '',
                'album': message.album or '',
                'playing': True,
                'duration': getattr(message, 'duration_label', ''),
                'timestamp': time.time()
            }
            with open('/tmp/now_playing.json', 'w') as f:
                json.dump(data, f, indent=2)
    
    def on_media_status(self, client, message):
        # ONLY LOG SIGNIFICANT CHANGES (play/pause + every 30s)
        pos = getattr(message, 'position_label', 'N/A')
        if message.is_playing and ':' in pos:
            minutes = int(pos.split(':')[0])
            if minutes % 1 == 0:  # Every minute
                status = 'PLAYING'
                logger.info(f"⏯️ {status} - {pos}")
        elif not message.is_playing:
            logger.info(f"⏯️ PAUSED - {pos}")
            
        # Always update JSON
        try:
            with open('/tmp/now_playing.json', 'r') as f:
                current = json.load(f)
            current['playing'] = message.is_playing
            current['duration'] = pos
            with open('/tmp/now_playing.json', 'w') as f:
                json.dump(current, f, indent=2)
        except:
            pass
    
    # === NAVIGATION ===
    def on_navigation_maneuver_details(self, client, message):
        new_details = getattr(message, 'description', 'N/A')
        if new_details != self.last_nav_details:
            self.last_nav_details = new_details
            logger.info(f"🧭 {new_details}")
    
    # === PHONE ===
    def on_phone_voice_call_status(self, client, message):
        new_call = f"{getattr(message, 'state', 0)}|{getattr(message, 'caller_name', '') or getattr(message, 'caller_id', '')}"
        if new_call != self.last_call:
            self.last_call = new_call
            state_map = {0: 'IDLE', 1: 'RINGING', 2: 'ALERTING', 3: 'ACTIVE'}
            state = state_map.get(getattr(message, 'state', 0), 'IDLE')
            caller = getattr(message, 'caller_name', '') or getattr(message, 'caller_id', '') or 'Unknown'
            logger.info(f"📞 {state}: {caller}")

class HudiyData:
    def __init__(self):
        self.handler = HudiyEventHandler()
        self.media_client = None
        self.nav_client = None
        self.running = True
        
    def connect_media(self):
        """Thread: Media WebSocket"""
        while self.running:
            try:
                self.media_client = Client("MEDIA")
                self.media_client.set_event_handler(self.handler)
                self.media_client.connect('127.0.0.1', 44406, use_websocket=True)
                logger.info("✅ MEDIA Thread ACTIVE")
                while self.media_client.running and self.running:
                    time.sleep(1)
            except Exception as e:
                logger.error(f"❌ MEDIA Thread: {e}")
            if self.running:
                logger.info("🔄 MEDIA Reconnecting in 5s...")
                time.sleep(5)
    
    def connect_nav(self):
        """Thread: Nav+Phone TCP"""
        while self.running:
            try:
                self.nav_client = Client("NAV_PHONE")
                self.nav_client.set_event_handler(self.handler)
                self.nav_client.connect('127.0.0.1', 44405)
                logger.info("✅ NAV_THREAD ACTIVE")
                while self.nav_client.running and self.running:
                    time.sleep(1)
            except Exception as e:
                logger.error(f"❌ NAV Thread: {e}")
            if self.running:
                logger.info("🔄 NAV Reconnecting in 5s...")
                time.sleep(5)
    
    def run(self):
        logger.info("🚀 THREADING Hudiy Data ACTIVE!")
        logger.info("🎵 Play music → Titles!")
        logger.info("🧭 Start nav → Directions!")
        
        # Start threads
        media_thread = threading.Thread(target=self.connect_media, daemon=True)
        nav_thread = threading.Thread(target=self.connect_nav, daemon=True)
        
        media_thread.start()
        nav_thread.start()
        
        try:
            # Main thread: Keep alive
            while self.running:
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("🛑 Stopped")
            self.running = False
            
        if self.media_client: self.media_client.disconnect()
        if self.nav_client: self.nav_client.disconnect()

if __name__ == '__main__':
    HudiyData().run()
