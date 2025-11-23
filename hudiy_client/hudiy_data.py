#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Hudiy Data Extractor (V2.9)
- FIX: Adapted to latest Hudiy API structure (MediaSource inside MediaStatus).
- FEATURE: Extracts Media Source (AA, CarPlay, BT) for dynamic headers.
- FEATURE: Handles Projection Status.
"""

import json
import time
import logging
import sys
import os
import threading
import zmq

# --- Add hudiy_client to Python path ---
try:
    script_dir = os.path.dirname(os.path.abspath(__file__))
    api_path = os.path.join(script_dir, 'api_files')
    sys.path.insert(0, api_path)
    
    from common.Client import Client, ClientEventHandler
    import common.Api_pb2 as hudiy_api
except ImportError as e:
    print(f"FATAL: Could not import Hudiy client libraries: {e}")
    sys.exit(1)

# --- Logging Setup ---
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s [%(levelname)s] (Hudiy) %(message)s')
logger = logging.getLogger(__name__)

# --- ZMQ Publishing Setup ---
ZMQ_CONTEXT = zmq.Context()

# --- Translation Maps ---
MANEUVER_TYPE_MAP = {
    0: "Unknown", 1: "Depart", 2: "Name Change", 3: "Slight turn",
    4: "Turn", 5: "Sharp turn", 6: "U-Turn", 7: "On Ramp",
    8: "Off Ramp", 9: "Fork", 10: "Merge", 11: "Roundabout",
    12: "Roundabout Exit", 13: "Roundabout", 14: "Straight",
    16: "Ferry Boat", 17: "Ferry Train", 19: "Destination"
}
MANEUVER_SIDE_MAP = { 1: "left", 2: "right", 3: "" }

CALL_STATE_MAP = {
    0: 'IDLE',      # PHONE_VOICE_CALL_STATE_NONE
    1: 'INCOMING',  # PHONE_VOICE_CALL_STATE_INCOMING
    2: 'ALERTING',  # PHONE_VOICE_CALL_STATE_ALERTING
    3: 'ACTIVE'     # PHONE_VOICE_CALL_STATE_ACTIVE
}
CONN_STATE_MAP = {
    1: 'CONNECTED',
    2: 'DISCONNECTED'
}

# --- NEW: Media Source Map ---
MEDIA_SOURCE_MAP = {
    0: "Paused",        # MEDIA_SOURCE_NONE
    1: "Android",       # MEDIA_SOURCE_ANDROID_AUTO (Shortened from AndroidAuto)
    2: "CarPlay",       # MEDIA_SOURCE_AUTOBOX
    3: "Bluetooth",     # MEDIA_SOURCE_A2DP
    4: "Storage",       # MEDIA_SOURCE_STORAGE
    5: "FM-Radio",      # MEDIA_SOURCE_FM_RADIO
    6: "Web"            # MEDIA_SOURCE_WEB
}

class HudiyEventHandler(ClientEventHandler):
    def __init__(self, zmq_publisher):
        super().__init__() 
        self.zmq_pub = zmq_publisher
        self.last_media = None
        
        # Initialize Data Objects
        self.current_media_data = {
            'artist': '', 'title': '', 'album': '', 
            'playing': False, 'duration': '0:00', 'position': '0:00',
            'source_id': 0, 
            'source_label': 'Now Playing', # Default
            'projection_active': False,
            'timestamp': 0
        }
        self.current_nav_data = {}
        self.current_phone_data = {
            'connection_state': 'DISCONNECTED', 'name': '', 'state': 'IDLE', 
            'caller_name': '', 'caller_id': '', 'battery': 0, 'signal': 0,
            'timestamp': 0
        }

    def on_hello_response(self, client, message):
        logger.info(f"Client '{client._name}' Connected - API v{message.api_version.major}.{message.api_version.minor}")
        subs = hudiy_api.SetStatusSubscriptions()
        
        if client._name == "MEDIA":
            # Subscribe to MEDIA and PROJECTION
            subs.subscriptions.extend([
                hudiy_api.SetStatusSubscriptions.Subscription.MEDIA,
                hudiy_api.SetStatusSubscriptions.Subscription.PROJECTION
            ])
            client.send(hudiy_api.MESSAGE_SET_STATUS_SUBSCRIPTIONS, 0, subs.SerializeToString())
            logger.info(f"Client '{client._name}': Subscribed to MEDIA + PROJECTION")
            
        elif client._name == "NAV_PHONE":
            subs.subscriptions.extend([
                hudiy_api.SetStatusSubscriptions.Subscription.NAVIGATION,
                hudiy_api.SetStatusSubscriptions.Subscription.PHONE
            ])
            client.send(hudiy_api.MESSAGE_SET_STATUS_SUBSCRIPTIONS, 0, subs.SerializeToString())
            logger.info(f"Client '{client._name}': Subscribed to NAV and PHONE")
    
    # --- Media Callbacks (Port 44406) ---
    
    def on_media_metadata(self, client, message):
        # Handle Metadata (Artist, Title, Album)
        new_meta = f"{message.artist}|{message.title}|{message.album}"
        
        self.current_media_data.update({
            'artist': message.artist or '',
            'title': message.title or '',
            'album': message.album or '',
            'duration': getattr(message, 'duration_label', '0:00'),
            'timestamp': time.time()
        })
        
        # Log only if changed
        if new_meta != self.last_media:
            self.last_media = new_meta
            logger.info(f"?? {message.artist} - {message.title}")
            
        self.publish_and_write_media(self.current_media_data)

    def on_media_status(self, client, message):
        # Handle Status (Playing, Position, Source)
        pos = getattr(message, 'position_label', 'N/A')
        
        # Extract Source (New Feature)
        src_id = getattr(message, 'source', 0)
        src_label = MEDIA_SOURCE_MAP.get(src_id, "Now Playing")
        
        if src_id != self.current_media_data.get('source_id'):
            logger.info(f"SOURCE CHANGED: {src_label} ({src_id})")

        self.current_media_data.update({
            'playing': message.is_playing,
            'position': pos,
            'source_id': src_id,
            'source_label': src_label,
            'timestamp': time.time()
        })
        
        self.publish_and_write_media(self.current_media_data)

    # --- Projection Callback ---
    def on_projection_status(self, client, message):
        active = getattr(message, 'active', False)
        logger.info(f"PROJECTION STATUS: {'Active' if active else 'Inactive'}")
        self.current_media_data['projection_active'] = active
        self.publish_and_write_media(self.current_media_data)

    def publish_and_write_media(self, data: dict):
        try:
            self.zmq_pub.send_multipart([
                b'HUDIY_MEDIA',
                json.dumps(data).encode('utf-8')
            ])
        except Exception as e:
            logger.error(f"Failed to publish ZMQ media: {e}")
        try:
            with open('/tmp/now_playing.json', 'w') as f:
                json.dump(data, f, indent=2)
        except Exception: pass

    # --- Nav/Phone Callbacks (Port 44405) ---
    
    def on_navigation_maneuver_details(self, client, message):
        desc = getattr(message, 'description', '')
        type_num = getattr(message, 'maneuver_type', 0)
        side_num = getattr(message, 'maneuver_side', 3)
        
        maneuver_text = MANEUVER_TYPE_MAP.get(type_num, 'N/A')
        side_text = MANEUVER_SIDE_MAP.get(side_num, 'N/A')
        full_maneuver_text = f"{maneuver_text} {side_text}".strip()
        
        logger.info(f"NAV: {full_maneuver_text} - {desc}")

        self.current_nav_data.update({
            'description': desc,
            'maneuver_text': full_maneuver_text,
            'timestamp': time.time()
        })
        self.publish_and_write_nav(self.current_nav_data)

    def on_navigation_maneuver_distance(self, client, message):
        dist = getattr(message, 'label', '')
        self.current_nav_data['distance'] = dist
        self.current_nav_data['timestamp'] = time.time()
        self.publish_and_write_nav(self.current_nav_data)

    def on_navigation_status(self, client, message):
        # Handle active/inactive state
        # 1=Active, 2=Inactive
        state = getattr(message, 'state', 2) 
        logger.info(f"NAV STATE: {'Active' if state == 1 else 'Inactive'}")
        self.current_nav_data['active'] = (state == 1)
        self.publish_and_write_nav(self.current_nav_data)

    def publish_and_write_nav(self, data: dict):
        try:
            self.zmq_pub.send_multipart([
                b'HUDIY_NAV',
                json.dumps(data).encode('utf-8')
            ])
        except Exception: pass
        try:
            with open('/tmp/current_nav.json', 'w') as f:
                json.dump(data, f, indent=2)
        except Exception: pass
    
    # --- Phone Handlers ---
    
    def on_phone_connection_status(self, client, message):
        state = CONN_STATE_MAP.get(message.state, 'DISCONNECTED')
        name = getattr(message, 'name', '')
        logger.info(f"PHONE CONN: {state}: {name}")
        
        self.current_phone_data.update({
            'connection_state': state,
            'name': name,
            'timestamp': time.time()
        })
        self.publish_and_write_phone(self.current_phone_data)

    def on_phone_levels_status(self, client, message):
        battery = getattr(message, 'bettery_level', 0)
        signal = getattr(message, 'signal_level', 0)
        
        self.current_phone_data.update({
            'battery': battery,
            'signal': signal,
            'timestamp': time.time()
        })
        self.publish_and_write_phone(self.current_phone_data)

    def on_phone_voice_call_status(self, client, message):
        state = CALL_STATE_MAP.get(message.state, 'IDLE')
        caller = getattr(message, 'caller_name', '') or getattr(message, 'caller_id', '') or 'Unknown'
        
        logger.info(f"PHONE CALL: {state}: {caller}")

        self.current_phone_data.update({
            'state': state,
            'caller_name': getattr(message, 'caller_name', ''),
            'caller_id': getattr(message, 'caller_id', ''),
            'timestamp': time.time()
        })
        self.publish_and_write_phone(self.current_phone_data)

    def publish_and_write_phone(self, data: dict):
        try:
            self.zmq_pub.send_multipart([
                b'HUDIY_PHONE',
                json.dumps(data).encode('utf-8')
            ])
        except Exception: pass
        try:
            with open('/tmp/current_call.json', 'w') as f:
                json.dump(data, f, indent=2)
        except Exception: pass

class HudiyData:
    def __init__(self, config_path='/home/pi/config.json'):
        # --- Load ZMQ Config ---
        try:
            with open(config_path, 'r') as f:
                config = json.load(f)
            zmq_addr = config['zmq']['hudiy_publish_address']
        except Exception as e:
            logger.warning(f"Config Error: {e}. Using default ZMQ address.")
            zmq_addr = "ipc:///run/rnse_control/hudiy_stream.ipc"
        
        # --- ZMQ PUB SOCKET ---
        logger.info(f"Binding Hudiy ZMQ publisher to {zmq_addr}")
        self.zmq_publisher = ZMQ_CONTEXT.socket(zmq.PUB)
        try:
            self.zmq_publisher.bind(zmq_addr)
        except zmq.ZMQError as e:
            logger.critical(f"FATAL: Could not bind ZMQ PUB socket: {e}")
            sys.exit(1)
        
        self.handler = HudiyEventHandler(self.zmq_publisher)
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
                logger.info("MEDIA Thread ACTIVE")
                while self.media_client._connected and self.running:
                    if not self.media_client.wait_for_message():
                        break 
            except Exception as e:
                logger.error(f"MEDIA Thread: {e}")
            
            if self.media_client: self.media_client.disconnect()
            if self.running:
                logger.info("MEDIA Reconnecting in 5s...")
                time.sleep(5)
    
    def connect_nav(self):
        """Thread: Nav+Phone TCP"""
        while self.running:
            try:
                self.nav_client = Client("NAV_PHONE")
                self.nav_client.set_event_handler(self.handler)
                self.nav_client.connect('127.0.0.1', 44405) 
                logger.info("NAV_THREAD ACTIVE")
                while self.nav_client._connected and self.running:
                    if not self.nav_client.wait_for_message():
                        break 
            except Exception as e:
                logger.error(f"NAV Thread: {e}")
                
            if self.nav_client: self.nav_client.disconnect()
            if self.running:
                logger.info("NAV Reconnecting in 5s...")
                time.sleep(5)
    
    def run(self):
        logger.info("THREADING Hudiy Data ACTIVE!")
        media_thread = threading.Thread(target=self.connect_media, daemon=True)
        nav_thread = threading.Thread(target=self.connect_nav, daemon=True)
        media_thread.start()
        nav_thread.start()
        
        try:
            while self.running:
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("Stopped by user (KeyboardInterrupt)")
            self.running = False
            
        if self.media_client: self.media_client.disconnect()
        if self.nav_client: self.nav_client.disconnect()
        media_thread.join(timeout=2.0)
        nav_thread.join(timeout=2.0)
        
        self.zmq_publisher.close()
        logger.info("ZMQ publisher closed.")

if __name__ == '__main__':
    try:
        # Assumes config.json is in /home/pi/
        HudiyData(config_path='/home/pi/config.json').run()
    except Exception as e:
        logger.critical(f"Unhandled exception in main: {e}", exc_info=True)
    finally:
        ZMQ_CONTEXT.term()
        logger.info("HudiyData service has shut down.")
