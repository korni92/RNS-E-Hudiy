#!/usr/bin/env python3
"""
Hudiy Dark Mode Service V1.1

This service listens for CAN bus messages (via ZMQ from can_handler.py)
to automatically toggle the Hudiy day/night mode and optionally Android Auto.

It reads /home/pi/config.json to check if the feature is enabled.
"""

import socket
import struct
import sys
import os
import time
import zmq
import json
import logging

# --- Setup Logging ---
LOG_FILE = '/var/log/rnse_control/dark_mode_service.log'

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# --- Add API Files Path ---
api_path = os.path.dirname(os.path.abspath(__file__)) + '/api_files/common'
sys.path.insert(0, api_path)
try:
    #  assume Api_pb2 1.1 definitions
    from Api_pb2 import *
except ImportError:
    logger.critical(f"FATAL: Could not import Api_pb2.")
    logger.critical(f"Looked in: {api_path}")
    sys.exit(1)


# --- Hudiy API Function ---
def send_dark_mode(enabled, sync_android_auto=True, max_retries=3):
    """
    Connects to Hudiy and sends the dark mode command.
    
    Args:
        enabled (bool): True for Night, False for Day.
        sync_android_auto (bool): If True, also sends the specific Android Auto command.
        max_retries (int): Number of connection attempts.
        
    Returns:
        bool: True if successful, False if failed.
    """
    mode_str = '🌙 Dark (night)' if enabled else '☀️ Light (day)'
    
    for attempt in range(max_retries):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2.0)
            sock.connect(('localhost', 44405))
            
            # 1. Hello (Updated to API Version 1.1)
            hello = HelloRequest()
            hello.name = "DarkModeService"
            hello.api_version.major = 1
            hello.api_version.minor = 1  # BUMPED TO 1.1
            data = hello.SerializeToString()
            frame = struct.pack('<III', len(data), MESSAGE_HELLO_REQUEST, 0) + data
            sock.sendall(frame)
            
            # 2. Set System Dark Mode (Existing Logic)
            dark = SetDarkMode()
            dark.enabled = enabled
            data = dark.SerializeToString()
            frame = struct.pack('<III', len(data), MESSAGE_SET_DARK_MODE, 0) + data
            sock.sendall(frame)
            
            # 3. Set Android Auto Mode (New Logic)
            if sync_android_auto:
                try:
                    aa_msg = SetAndroidAutoDayNightMode()
                    # Map boolean to Enum: NIGHT=1, DAY=2 (Based on typical Proto definitions)
                    # Using the class attributes ensures we match the generated code
                    aa_msg.mode = SetAndroidAutoDayNightMode.NIGHT if enabled else SetAndroidAutoDayNightMode.DAY
                    
                    data_aa = aa_msg.SerializeToString()
                    # Ensure MESSAGE_SET_ANDROID_AUTO_DAY_NIGHT_MODE is defined in your updated Api_pb2
                    frame_aa = struct.pack('<III', len(data_aa), MESSAGE_SET_ANDROID_AUTO_DAY_NIGHT_MODE, 0) + data_aa
                    sock.sendall(frame_aa)
                    logger.debug(f"Sent Android Auto mode command: {mode_str}")
                except NameError:
                    logger.error("API 1.1 symbols missing in Api_pb2. Cannot set Android Auto mode.")
                except Exception as e_aa:
                    logger.warning(f"Sent System mode, but failed to set Android Auto: {e_aa}")

            sock.close()
            logger.info(f"API call successful: Set System (and AA={sync_android_auto}) to {mode_str}.")
            return True
            
        except Exception as e:
            # Only log detailed warning on the last retry to keep logs clean during startup
            if attempt == max_retries - 1:
                logger.warning(f"Failed to set {mode_str} mode: {e}")
            if attempt < max_retries - 1:
                time.sleep(0.5)
                continue
                
    return False

# --- Config Loading Function ---
def load_config(config_path='/home/pi/config.json'):
    """Loads the main config file to get all required settings."""
    logger.info(f"Loading configuration from {config_path}...")
    try:
        with open(config_path, 'r') as f:
            config_data = json.load(f)
        
        config = {
            'zmq_publish_address': config_data.get('zmq', {}).get('publish_address'),
            'light_status_can_id': config_data.get('can_ids', {}).get('light_status'),
            'day_night_mode': config_data.get('features', {}).get('day_night_mode', False),
            'initial_mode': config_data.get('features', {}).get('initial_mode', 'night'),
            # NEW: Toggle to control if AA follows the headlights independently
            'sync_android_auto': config_data.get('features', {}).get('sync_android_auto', True) 
        }

        if not config['zmq_publish_address']:
            logger.critical("FATAL: 'zmq_publish_address' not found in config.json.")
            return None
        if not config['light_status_can_id']:
            logger.critical("FATAL: 'light_status_can_id' not found in config.json.")
            return None

        logger.info("Configuration loaded successfully.")
        return config
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logger.critical(f"FATAL: Could not load or parse config.json: {e}")
        return None

# --- Main Service Loop ---
def main():
    """Main service loop."""
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
            
    config = load_config()
    if not config:
        sys.exit(1)

    if not config.get('day_night_mode'):
        logger.info("Day/Night mode feature is disabled in config.json. Exiting.")
        sys.exit(0)
    
    # --- 1. Handle Initial Mode ---
    initial_mode_str = config.get('initial_mode', 'night').lower()
    is_initial_night = (initial_mode_str == 'night')
    sync_aa = config.get('sync_android_auto', True)
    
    logger.info(f"Day/Night feature enabled. Default: {initial_mode_str}. Sync AA: {sync_aa}")

    # Try to set the initial mode immediately (Best Effort)
    send_dark_mode(enabled=is_initial_night, sync_android_auto=sync_aa, max_retries=1)

    # Initialize internal state
    # 1 = Night, 0 = Day
    light_status = 1 if is_initial_night else 0
    last_msg_data = None

    # --- 2. ZMQ Connection ---
    zmq_address = config['zmq_publish_address']
    can_id_str = config['light_status_can_id'].replace('0x', '').upper()
    can_topic = f"CAN_{can_id_str}"
    
    context = zmq.Context()
    socket = context.socket(zmq.SUB)
    
    logger.info(f"Connecting to ZMQ publisher at {zmq_address}...")
    try:
        socket.connect(zmq_address)
    except zmq.ZMQError as e:
        logger.critical(f"Failed to bind ZMQ socket: {e}. Is can_handler.py running?")
        sys.exit(1)
        
    socket.setsockopt_string(zmq.SUBSCRIBE, can_topic)
    logger.info(f"Subscribed to ZMQ topic: {can_topic}")
    logger.info("Day/Night service started. Waiting for CAN messages...")

    while True:
        try:
            [topic, payload] = socket.recv_multipart()
            msg_data = json.loads(payload.decode('utf-8'))
            data_hex = msg_data.get('data_hex')

            if not data_hex:
                continue

            # --- Logic Change: How we detect if we need to send ---
            first_message = last_msg_data is None

            if first_message or data_hex != last_msg_data:
                
                try:
                    light_byte_hex = data_hex[2:4]
                    light_value = int(light_byte_hex, 16)
                except (IndexError, ValueError) as e:
                    logger.error(f"Could not parse light value from data_hex '{data_hex}'. Error: {e}")
                    continue

                # 1 = night (lights on), 0 = day (lights off)
                new_light_status = 1 if light_value > 0 else 0

                if first_message or (new_light_status != light_status):
                    
                    is_dark_mode_enabled = (new_light_status == 1) 
                    mode_str = 'night' if is_dark_mode_enabled else 'day'
                    
                    logger.info(f"State change required (CAN Value: {light_value}). Target: {mode_str}.")
                    
                    # Update API Call with new Sync AA flag
                    if send_dark_mode(is_dark_mode_enabled, sync_android_auto=sync_aa):
                        light_status = new_light_status
                        last_msg_data = data_hex
                        logger.info("State updated successfully.")
                    else:
                        logger.warning("API call failed. Will retry on next CAN message.")
                        # Do NOT update last_msg_data to force retry
                else:
                    last_msg_data = data_hex

        except zmq.ZMQError as e:
            if e.errno == zmq.ETERM:
                logger.info("ZMQ context terminated. Shutting down.")
                break
            logger.error(f"ZMQ Error: {e}. Reconnecting...")
            socket.close()
            time.sleep(5)
            socket = context.socket(zmq.SUB)
            socket.connect(zmq_address)
            socket.setsockopt_string(zmq.SUBSCRIBE, can_topic)
        except KeyboardInterrupt:
            logger.info("Shutdown signal received. Exiting...")
            break
        except Exception as e:
            logger.critical(f"An unexpected error occurred in main loop: {e}", exc_info=True)
            time.sleep(10)

    socket.close()
    context.term()
    logger.info("Day/Night service stopped.")

if __name__ == '__main__':
    main()
