#!/usr/bin/env python3
"""
Hudiy Data Extractor
ONLY LOG CHANGES
AUTO-RECONNECT
MUSIC TITLES + NAV + PHONE
/tmp/now_playing.json
"""

import json
import time
import logging
import sys
import os

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
        self.last_nav_distance = None
        self.last_nav_status = None
        self.last_call = None
        
    def on_hello_response(self, client, message):
        logger.info(f"? {client.name} Connected - API v{message.api_version.major}.{message.api_version.minor}")
        
        subs = SetStatusSubscriptions()
        
        if client.name == "MEDIA":
            subs.subscriptions.append(SetStatusSubscriptions.Subscription.MEDIA)
            client.send(MESSAGE_SET_STATUS_SUBSCRIPTIONS, 0, subs.SerializeToString())
            logger.info("?? MEDIA Subscribed")
            
        elif client.name == "NAV_PHONE":
            subs.subscriptions.extend([
                SetStatusSubscriptions.Subscription.NAVIGATION,
                SetStatusSubscriptions.Subscription.PHONE
            ])
            client.send(MESSAGE_SET_STATUS_SUBSCRIPTIONS, 0, subs.SerializeToString())
            logger.info("?? NAV+PHONE Subscribed")
    
    # === MEDIA ===
    def on_media_metadata(self, client, message):
        new_meta = f"{message.artist}|{message.title}|{message.album}"
        if new_meta != self.last_media:
            self.last_media = new_meta
            info = f"?? {message.artist} - {message.title}"
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
        new_status = f"{message.is_playing}|{getattr(message, 'position_label', 'N/A')}"
        if new_status != self.last_status:
            self.last_status = new_status
            status = 'PLAYING' if message.is_playing else 'PAUSED'
            logger.info(f"?? {status} - {getattr(message, 'position_label', 'N/A')}")
            
            # Update JSON
            try:
                with open('/tmp/now_playing.json', 'r') as f:
                    current = json.load(f)
                current['playing'] = message.is_playing
                current['duration'] = getattr(message, 'position_label', current['duration'])
                with open('/tmp/now_playing.json', 'w') as f:
                    json.dump(current, f, indent=2)
            except:
                pass
    
    # === NAVIGATION ===
    def on_navigation_maneuver_details(self, client, message):
        new_details = getattr(message, 'description', 'N/A')
        if new_details != self.last_nav_details:
            self.last_nav_details = new_details
            logger.info(f"?? {new_details}")
    
    def on_navigation_maneuver_distance(self, client, message):
        new_distance = getattr(message, 'label', 'N/A')
        if new_distance != self.last_nav_distance:
            self.last_nav_distance = new_distance
            logger.info(f"?? {new_distance}")
    
    def on_navigation_status(self, client, message):
        new_status = getattr(message, 'state', 'N/A')
        if new_status != self.last_nav_status:
            self.last_nav_status = new_status
            logger.info(f"??? Navigation: {new_status}")
    
    # === PHONE ===
    def on_phone_voice_call_status(self, client, message):
        new_call = f"{getattr(message, 'state', 0)}|{getattr(message, 'caller_name', '') or getattr(message, 'caller_id', '')}"
        if new_call != self.last_call:
            self.last_call = new_call
            state_map = {0: 'IDLE', 1: 'RINGING', 2: 'ALERTING', 3: 'ACTIVE'}
            state = state_map.get(getattr(message, 'state', 0), 'IDLE')
            caller = getattr(message, 'caller_name', '') or getattr(message, 'caller_id', '') or 'Unknown'
            logger.info(f"?? {state}: {caller}")

def main():
    handler = HudiyEventHandler()
    
    # Media (WebSocket)
    media_client = Client("MEDIA")
    media_client.set_event_handler(handler)
    media_client.connect('127.0.0.1', 44406, use_websocket=True)
    
    # Nav+Phone (TCP)
    nav_client = Client("NAV_PHONE")
    nav_client.set_event_handler(handler)
    nav_client.connect('127.0.0.1', 44405)
    
    logger.info("?? ACTIVE!")
    
    try:
        while True:
            if not media_client.running: media_client.connect('127.0.0.1', 44406, use_websocket=True)
            if not nav_client.running: nav_client.connect('127.0.0.1', 44405)
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("?? Stopped")
        media_client.disconnect()
        nav_client.disconnect()

if __name__ == '__main__':
    main()
