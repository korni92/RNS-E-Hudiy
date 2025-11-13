#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Hudiy Data Extractor (V2.8)
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
    print(f"Looked for 'common' module in: {api_path}")
    print("Please ensure Client.py and Api_pb2.py are in a 'common' subfolder (e.g., /home/pi/hudiy_client/api_files/common/)")
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

class HudiyEventHandler(ClientEventHandler):
    def __init__(self, zmq_publisher):
        super().__init__() # Call the parent constructor
        self.zmq_pub = zmq_publisher
        self.last_media = None
        self.last_nav_details = None
        self.last_call = None
        
        self.current_media_data = {}
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
            subs.subscriptions.append(hudiy_api.SetStatusSubscriptions.Subscription.MEDIA)
            client.send(hudiy_api.MESSAGE_SET_STATUS_SUBSCRIPTIONS, 0, subs.SerializeToString())
            logger.info(f"Client '{client._name}': Subscribed to MEDIA")
            
        elif client._name == "NAV_PHONE":
            subs.subscriptions.extend([
                hudiy_api.SetStatusSubscriptions.Subscription.NAVIGATION,
                hudiy_api.SetStatusSubscriptions.Subscription.PHONE
            ])
            client.send(hudiy_api.MESSAGE_SET_STATUS_SUBSCRIPTIONS, 0, subs.SerializeToString())
            logger.info(f"Client '{client._name}': Subscribed to NAV and PHONE")
    
    # --- Media Callbacks (Port 44406) ---
    
    def on_media_metadata(self, client, message):
        new_meta = f"{message.artist}|{message.title}|{message.album}"
        if new_meta == self.last_media:
            return
        self.last_media = new_meta
        logger.info(f"?? {message.artist} - {message.title} ({message.album})")
        
        self.current_media_data = {
            'artist': message.artist or '',
            'title': message.title or '',
            'album': message.album or '',
            'playing': True,
            'duration': getattr(message, 'duration_label', '0:00'),
            'position': '0:00',
            'timestamp': time.time()
        }
        self.publish_and_write_media(self.current_media_data)

    def on_media_status(self, client, message):
        pos = getattr(message, 'position_label', 'N/A')
        if not message.is_playing:
            logger.info(f"?? PAUSED - {pos}")
            
        # Ensure media data exists before updating
        if not self.current_media_data:
             self.current_media_data = {'artist': '', 'title': '', 'album': '', 'duration': '', 'position': ''}
             
        self.current_media_data['playing'] = message.is_playing
        self.current_media_data['position'] = pos
        self.current_media_data['timestamp'] = time.time()
        
        self.publish_and_write_media(self.current_media_data)

    def publish_and_write_media(self, data: dict):
        try:
            self.zmq_pub.send_multipart([
                b'HUDIY_MEDIA',
                json.dumps(data).encode('utf-8')
            ])
        except Exception as e:
            logger.error(f"Failed to publish ZMQ media data: {e}")
        try:
            with open('/tmp/now_playing.json', 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to write /tmp/now_playing.json: {e}")

    # --- Nav/Phone Callbacks (Port 44405) ---
    
    def on_navigation_maneuver_details(self, client, message):
        desc = getattr(message, 'description', '')
        type_num = getattr(message, 'maneuver_type', 0)
        side_num = getattr(message, 'maneuver_side', 3)
        
        maneuver_text = MANEUVER_TYPE_MAP.get(type_num, 'N/A')
        side_text = MANEUVER_SIDE_MAP.get(side_num, 'N/A')
        full_maneuver_text = f"{maneuver_text} {side_text}".strip()
        
        logger.info(f"NAV Update: desc='{desc}', type='{maneuver_text}', side='{side_text}'")

        if not self.current_nav_data:
            self.current_nav_data = {}

        self.current_nav_data['description'] = desc
        self.current_nav_data['maneuver_text'] = full_maneuver_text
        self.current_nav_data['timestamp'] = time.time()
        self.publish_and_write_nav(self.current_nav_data)

    def on_navigation_maneuver_distance(self, client, message):
        dist = getattr(message, 'label', '')
        
        if not self.current_nav_data:
            self.current_nav_data = {}
            
        self.current_nav_data['distance'] = dist
        self.current_nav_data['timestamp'] = time.time()
        logger.info(f"NAV DIST: {dist}")
        self.publish_and_write_nav(self.current_nav_data)

    def publish_and_write_nav(self, data: dict):
        try:
            self.zmq_pub.send_multipart([
                b'HUDIY_NAV',
                json.dumps(data).encode('utf-8')
            ])
        except Exception as e:
            logger.error(f"Failed to publish ZMQ nav data: {e}")
        try:
            with open('/tmp/current_nav.json', 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to write /tmp/current_nav.json: {e}")
    
    # --- Phone Handlers ---
    
    def on_phone_connection_status(self, client, message):
        state = CONN_STATE_MAP.get(message.state, 'DISCONNECTED')
        name = getattr(message, 'name', '')
        logger.info(f"?? PHONE CONN: {state}: {name}")
        
        self.current_phone_data['connection_state'] = state
        self.current_phone_data['name'] = name
        self.current_phone_data['timestamp'] = time.time()
        self.publish_and_write_phone(self.current_phone_data)

    def on_phone_levels_status(self, client, message):
        battery = getattr(message, 'bettery_level', 0)
        signal = getattr(message, 'signal_level', 0)
        logger.info(f"?? PHONE LVL: Bat={battery}/5, Sig={signal}%")
        
        self.current_phone_data['battery'] = battery
        self.current_phone_data['signal'] = signal
        self.current_phone_data['timestamp'] = time.time()
        self.publish_and_write_phone(self.current_phone_data)

    def on_phone_voice_call_status(self, client, message):
        state = CALL_STATE_MAP.get(message.state, 'IDLE')
        caller_name = getattr(message, 'caller_name', '')
        caller_id = getattr(message, 'caller_id', '')
        caller = caller_name or caller_id or 'Unknown'

        new_call = f"{state}|{caller}"
        if new_call == self.last_call:
            return
        self.last_call = new_call
        logger.info(f"?? PHONE CALL: {state}: {caller}")

        self.current_phone_data['state'] = state
        self.current_phone_data['caller_name'] = caller_name
        self.current_phone_data['caller_id'] = caller_id
        self.current_phone_data['timestamp'] = time.time()
        self.publish_and_write_phone(self.current_phone_data)

    def publish_and_write_phone(self, data: dict):
        try:
            self.zmq_pub.send_multipart([
                b'HUDIY_PHONE',
                json.dumps(data).encode('utf-8')
            ])
        except Exception as e:
            logger.error(f"Failed to publish ZMQ phone data: {e}")
        try:
            with open('/tmp/current_call.json', 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to write /tmp/current_call.json: {e}")

class HudiyData:
    def __init__(self, config_path='/home/pi/config.json'):
        # --- Load ZMQ Config ---
        try:
            with open(config_path, 'r') as f:
                config = json.load(f)
            zmq_addr = config['zmq']['hudiy_publish_address']
        except Exception as e:
            logger.warning(f"Could not load config.json: {e}")
            logger.warning("Using default address: ipc:///run/rnse_control/hudiy_stream.ipc")
            zmq_addr = "ipc:///run/rnse_control/hudiy_stream.ipc"
        
        # --- ZMQ PUB SOCKET ---
        logger.info(f"Binding Hudiy ZMQ publisher to {zmq_addr}")
        self.zmq_publisher = ZMQ_CONTEXT.socket(zmq.PUB)
        try:
            self.zmq_publisher.bind(zmq_addr)
        except zmq.ZMQError as e:
            logger.critical(f"FATAL: Could not bind ZMQ PUB socket: {e}")
            logger.critical("Check if another service is using the address.")
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
                        break # Connection closed
            except Exception as e:
                logger.error(f"MEDIA Thread: {e}")
            
            if self.media_client:
                self.media_client.disconnect()
                
            if self.running:
                logger.info("MEDIA Reconnecting in 5s...")
                time.sleep(5)
        logger.info("MEDIA thread finished.")
    
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
                        break # Connection closed
            except Exception as e:
                logger.error(f"NAV Thread: {e}")
                
            if self.nav_client:
                self.nav_client.disconnect()
                
            if self.running:
                logger.info("NAV Reconnecting in 5s...")
                time.sleep(5)
        logger.info("NAV_PHONE thread finished.")
    
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
