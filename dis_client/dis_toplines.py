#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Audi FIS Topline Shadowing Service
Provides flicker-free overwriting of the top two lines of the Audi FIS (Driver Information System)
during TV-Tuner mode, including a state-machine-based text scrolling feature for long strings.
"""

import zmq
import can
import json
import threading
import time
import logging
import os
import icons  # Custom AUDSCII translation table

# --- Logging Setup ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] (FIS-Shadow) %(message)s')
logger = logging.getLogger(__name__)

# --- Config Loader ---
def load_config_with_fallbacks(config_path='/home/pi/config.json'):
    """Loads configuration and applies standard fallbacks for missing or corrupted entries."""
    config = {
        "can_interface": "can0",
        "zmq_hudiy_address": "ipc:///run/rnse_control/hudiy_stream.ipc",
        "id_source": 0x661,
        "id_line1": 0x265,
        "id_line2": 0x267,
        "tv_mode_identifier": 0x37,
        "layout_line1": "{title}",
        "layout_line2": "{source_label}",
        "align_line1": "center",
        "align_line2": "center",
        "pad_char": 0x20,
        "scrolling_enabled": True,
        "scroll_start_pause_sec": 3.0,
        "scroll_interval_sec": 1.0,
        "scroll_end_pause_sec": 0.5
    }

    if not os.path.exists(config_path):
        logger.warning(f"Config '{config_path}' is missing! Using default values.")
        return config

    try:
        with open(config_path, 'r') as f:
            raw = json.load(f)

        config["can_interface"] = raw.get("can_interface", config["can_interface"])
        config["zmq_hudiy_address"] = raw.get("zmq", {}).get("hudiy_publish_address", config["zmq_hudiy_address"])
        
        can_ids = raw.get("can_ids", {})
        config["id_source"] = int(can_ids.get("source", hex(config["id_source"])), 16)
        config["id_line1"] = int(can_ids.get("fis_line1", hex(config["id_line1"])), 16)
        config["id_line2"] = int(can_ids.get("fis_line2", hex(config["id_line2"])), 16)
        
        config["tv_mode_identifier"] = int(raw.get("source_data", {}).get("tv_mode_identifier", hex(config["tv_mode_identifier"])), 16)
        
        fis_cfg = raw.get("features", {}).get("fis_display", {})
        config["layout_line1"] = fis_cfg.get("layout_line1", config["layout_line1"])
        config["layout_line2"] = fis_cfg.get("layout_line2", config["layout_line2"])
        config["align_line1"] = fis_cfg.get("align_line1", config["align_line1"])
        config["align_line2"] = fis_cfg.get("align_line2", config["align_line2"])
        config["pad_char"] = int(fis_cfg.get("pad_char", hex(config["pad_char"])), 16)
        
        # Scroll Configurations
        config["scrolling_enabled"] = fis_cfg.get("scrolling_enabled", config["scrolling_enabled"])
        config["scroll_start_pause_sec"] = float(fis_cfg.get("scroll_start_pause_sec", config["scroll_start_pause_sec"]))
        config["scroll_interval_sec"] = float(fis_cfg.get("scroll_interval_sec", config["scroll_interval_sec"]))
        config["scroll_end_pause_sec"] = float(fis_cfg.get("scroll_end_pause_sec", config["scroll_end_pause_sec"]))

    except Exception as e:
        logger.error(f"Config Error: {e}. Using defaults where necessary.")

    return config

CONFIG = load_config_with_fallbacks()

# --- Shared State ---
class AppState:
    def __init__(self):
        self.is_tv_mode = False
        self.media_data = {'artist': '', 'title': '', 'album': '', 'source_label': 'HUDIY'}
        self.lock = threading.Lock()
        
        # Scroll states for each line
        self.scroll_state_line1 = {'text': '', 'index': 0, 'phase': 'START', 'last_tick': 0}
        self.scroll_state_line2 = {'text': '', 'index': 0, 'phase': 'START', 'last_tick': 0}

state = AppState()

# --- Scroll Logic & Formatting ---
def get_scrolled_text(full_text: str, scroll_state: dict, align: str, pad_char: str) -> str:
    """Calculates the current 8-character window based on time and state."""
    # Basic ASCII cleanup for accurate length calculation
    text = full_text.replace('ä', 'ae').replace('ö', 'oe').replace('ü', 'ue').replace('ß', 'ss')
    text = ''.join([c for c in text if ord(c) < 128])

    if not text.strip():
        return pad_char * 8

    # If the text fits within 8 chars, or scrolling is disabled, apply static alignment
    if len(text) <= 8 or not CONFIG['scrolling_enabled']:
        if align == 'center': return text.center(8, pad_char)
        elif align == 'right': return text.rjust(8, pad_char)
        else: return text.ljust(8, pad_char)

    now = time.time()
    
    # Reset state if the text changes
    if scroll_state['text'] != text:
        scroll_state['text'] = text
        scroll_state['index'] = 0
        scroll_state['phase'] = 'START'
        scroll_state['last_tick'] = now

    phase = scroll_state['phase']
    
    if phase == 'START':
        if now - scroll_state['last_tick'] >= CONFIG['scroll_start_pause_sec']:
            scroll_state['phase'] = 'SCROLL'
            scroll_state['index'] = 1
            scroll_state['last_tick'] = now
        return text[0:8]
        
    elif phase == 'SCROLL':
        # Calculate how many intervals/ticks have passed
        intervals = int((now - scroll_state['last_tick']) / CONFIG['scroll_interval_sec'])
        if intervals > 0:
            scroll_state['index'] += intervals
            scroll_state['last_tick'] += intervals * CONFIG['scroll_interval_sec']
            
        # Have we reached the end? (Index + 8 characters > total text length)
        if scroll_state['index'] > len(text) - 8:
            scroll_state['phase'] = 'PAUSE'
            scroll_state['last_tick'] = now
            return pad_char * 8  # Show empty line (fade out effect)
        else:
            return text[scroll_state['index'] : scroll_state['index'] + 8]
            
    elif phase == 'PAUSE':
        if now - scroll_state['last_tick'] >= CONFIG['scroll_end_pause_sec']:
            scroll_state['phase'] = 'START'
            scroll_state['index'] = 0
            scroll_state['last_tick'] = now
            return text[0:8]
        return pad_char * 8
        
    return pad_char * 8

def format_fis_string(template: str, scroll_state: dict, align: str, pad_char_code: int) -> list:
    if template is None:
        return None
        
    try:
        full_text = template.format(**state.media_data)
    except KeyError:
        full_text = template

    pad_char = chr(pad_char_code)
    window_text = get_scrolled_text(full_text, scroll_state, align, pad_char)

    # Convert characters to bytes using the AUDSCII translation table
    payload = []
    for c in window_text:
        char_val = ord(c)
        if char_val < 256:
            payload.append(icons.audscii_trans[char_val])
        else:
            payload.append(0x20) # Fallback to space for unsupported chars
            
    return payload

# --- Thread: ZMQ Hudiy Listener ---
def hudiy_listener_thread():
    context = zmq.Context()
    socket = context.socket(zmq.SUB)
    socket.connect(CONFIG["zmq_hudiy_address"])
    socket.setsockopt_string(zmq.SUBSCRIBE, "HUDIY_MEDIA")
    logger.info("ZMQ Listener active.")
    
    while True:
        try:
            topic, payload = socket.recv_multipart()
            data = json.loads(payload.decode('utf-8'))
            with state.lock:
                for key in state.media_data.keys():
                    if key in data:
                        state.media_data[key] = data[key]
        except Exception:
            time.sleep(1)

# --- Main: CAN Shadowing Loop ---
def main():
    logger.info("Starting FIS Shadowing with scrolling support...")
    t = threading.Thread(target=hudiy_listener_thread, daemon=True)
    t.start()
    
    # 1. Initialize CAN Bus
    try:
        bus = can.interface.Bus(interface='socketcan', channel=CONFIG['can_interface'])
    except Exception as e:
        logger.critical(f"Could not open CAN bus: {e}")
        return

    # 2. Main loop (with try/finally for clean exit)
    try:
        while True:
            msg = bus.recv(timeout=1.0)
            if msg is None: continue
                
            if msg.arbitration_id == CONFIG['id_source'] and msg.dlc >= 4:
                is_active = (msg.data[3] == CONFIG['tv_mode_identifier'])
                if is_active != state.is_tv_mode:
                    state.is_tv_mode = is_active

            elif msg.arbitration_id == CONFIG['id_line1'] and state.is_tv_mode and CONFIG['layout_line1']:
                with state.lock:
                    payload = format_fis_string(CONFIG['layout_line1'], state.scroll_state_line1, CONFIG['align_line1'], CONFIG['pad_char'])
                if payload:
                    bus.send(can.Message(arbitration_id=CONFIG['id_line1'], data=payload, is_extended_id=False))

            elif msg.arbitration_id == CONFIG['id_line2'] and state.is_tv_mode and CONFIG['layout_line2']:
                with state.lock:
                    payload = format_fis_string(CONFIG['layout_line2'], state.scroll_state_line2, CONFIG['align_line2'], CONFIG['pad_char'])
                if payload:
                    bus.send(can.Message(arbitration_id=CONFIG['id_line2'], data=payload, is_extended_id=False))
                    
    finally:
        # Executed when the script terminates (e.g., via Service-Stop or CTRL+C)
        logger.info("Closing CAN Bus...")
        bus.shutdown()

# --- Entry Point ---
if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass