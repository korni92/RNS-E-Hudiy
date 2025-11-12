#!/usr/bin/env python3
"""
Hudiy Dark Mode Service

This service listens for CAN bus messages (via ZMQ from can_handler.py)
to automatically toggle the Hudiy day/night mode.

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
api_path = os.path.dirname(os.path.abspath(__file__)) + '/api_files'
sys.path.insert(0, api_path)
try:
    from hudiy_api_pb2 import *
except ImportError:
    logger.critical(f"FATAL: Could not import hudiy_api_pb2.")
    logger.critical(f"Looked in: {api_path}")
    sys.exit(1)


# --- Hudiy API Function ---
def send_dark_mode(enabled, max_retries=3):
    """
    Connects to Hudiy and sends the dark mode command.
    'enabled=True' means Dark Mode (Night).
    'enabled=False' means Light Mode (Day).
    """
    mode_str = '🌙 Dark (night)' if enabled else '☀️ Light (day)'
    for attempt in range(max_retries):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2.0)
            sock.connect(('localhost', 44405))
            
            # 1. Hello
            hello = HelloRequest()
            hello.name = "DarkModeService"
            hello.api_version.major = 1
            hello.api_version.minor = 0
            data = hello.SerializeToString()
            frame = struct.pack('<III', len(data), MESSAGE_HELLO_REQUEST, 0) + data
            sock.sendall(frame)
            
            # 2. Set Dark Mode
            dark = SetDarkMode()
            dark.enabled = enabled
            data = dark.SerializeToString()
            frame = struct.pack('<III', len(data), MESSAGE_SET_DARK_MODE, 0) + data
            sock.sendall(frame)
            
            sock.close()
            logger.info(f"API call successful: Set {mode_str} mode.")
            return True
            
        except Exception as e:
            logger.warning(f"Failed to set {mode_str} mode (Attempt {attempt + 1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                time.sleep(0.5)
                continue
    logger.error(f"API call failed after {max_retries} retries.")
    return False

# --- Config Loading Function ---
def load_config(config_path='/home/pi/config.json'):
    """Loads the main config file to get all required settings."""
    logger.info(f"Loading configuration from {config_path}...")
    try:
        with open(config_path, 'r') as f:
            config_data = json.load(f)
        
        # Use .get() to avoid crashes if keys are missing
        config = {
            'zmq_publish_address': config_data.get('zmq', {}).get('publish_address'),
            'light_status_can_id': config_data.get('can_ids', {}).get('light_status'),
            'day_night_mode': config_data.get('features', {}).get('day_night_mode', False),
        }

        # Check for critical missing values
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
    # Create log directory if it doesn't exist
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
            
    config = load_config()
    if not config:
        sys.exit(1)

    # Check for feature flag
    if not config.get('day_night_mode'):
        logger.info("Day/Night mode feature is disabled in config.json. Exiting.")
        sys.exit(0)
    
    logger.info("Day/Night mode feature is enabled.") 

    # --- ZMQ Connection ---
    zmq_address = config['zmq_publish_address']
    # Convert "0x635" string from config to "635"
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
    light_status = 1 
    last_msg_data = None

    logger.info("Day/Night service started. Waiting for CAN messages...")

    while True:
        try:
            [topic, payload] = socket.recv_multipart()
            msg_data = json.loads(payload.decode('utf-8'))
            data_hex = msg_data.get('data_hex')

            if not data_hex:
                logger.warning("Received message with no data_hex.")
                continue

            first_message = last_msg_data is None

            # Check 1: Only process if the CAN message data has changed
            if first_message or data_hex != last_msg_data:
                
                try:
                    light_byte_hex = data_hex[2:4]
                    light_value = int(light_byte_hex, 16)
                except (IndexError, ValueError) as e:
                    logger.error(f"Could not parse light value from data_hex '{data_hex}'. Error: {e}")
                    continue

                # 1 = night (dark mode on), 0 = day (dark mode off)
                new_light_status = 1 if light_value > 0 else 0

                # Check 2: Only send API call if the *calculated state* has changed
                if first_message or (new_light_status != light_status):
                    
                    is_dark_mode_enabled = (new_light_status == 1) 
                    mode_str = 'night' if is_dark_mode_enabled else 'day'
                    logger.info(f"State change detected (CAN Value: {light_value}). Desired mode: {mode_str}.")
                    
                    logger.info("Sending API command.")
                    send_dark_mode(is_dark_mode_enabled)

                # Update state of whether the API was called
                light_status = new_light_status
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
