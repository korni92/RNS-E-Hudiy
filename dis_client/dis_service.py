#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# Audi DIS (Cluster) DDP Service - V2.20 (Dumb Pipe Fix)
#
# - FIX: Removed "Aggressive Padding". Now trusts the upper layer for length.
# - FIX: write_text now accepts and uses the 'flags' parameter.
# - RESULT: Upper layer (dis_display) can now control Fixed Width (0x02) vs Compact (0x06).
#
import zmq
import json
import time
import logging
from typing import List, Optional

try:
    # Use ddp_protocol V2.6
    from ddp_protocol import DDPProtocol, DDPState, DisMode, DDPError, DDPHandshakeError
except ImportError:
    print("Error: Could not import DDPProtocol. Make sure ddp_protocol.py is in the same directory.")
    exit(1)

try:
    # Import assets from the new icons.py file
    from icons import audscii_trans, ICONS, BITMAPS 
except ImportError:
    print("Error: Could not import icons.py. Make sure it is in the same directory.")
    exit(1)

# Logging Configuration
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] (DIS Svc) %(message)s')
logger = logging.getLogger(__name__)

# DisService Class
class DisService:
    def __init__(self, config_path='/home/pi/config.json'):
        """Initializes the DDP driver and the ZMQ command socket."""
        try:
            with open(config_path) as f:
                self.config = json.load(f)
        except FileNotFoundError:
            logger.critical(f"FATAL: config.json not found at {config_path}")
            exit(1)
        except Exception as e:
            logger.critical(f"FATAL: Could not load config.json: {e}")
            exit(1)
            
        try:
            self.ddp = DDPProtocol(self.config)
        except Exception as e:
            logger.critical(f"FATAL: Could not initialize DDPProtocol driver: {e}")
            exit(1)

        self.context = zmq.Context()
        self.draw_socket = self.context.socket(zmq.PULL)
        try:
            self.draw_socket.bind(self.config['zmq']['dis_draw'])
            logger.info(f"ZMQ command socket bound to {self.config['zmq']['dis_draw']}")
        except Exception as e:
            logger.critical(f"FATAL: Could not bind ZMQ socket: {e}")
            logger.critical("This often means the service is already running (Address already in use).")
            exit(1)
            
        self.poller = zmq.Poller()
        self.poller.register(self.draw_socket, zmq.POLLIN)

        self.last_draw_time = 0.0
        self.screen_is_active = False
        self.inactivity_timeout_sec = 30.0 

        self.ENABLE_INACTIVITY_RELEASE = False   # True = auto-release after 30s, False = never release (recommended for nav)

        if not self.ENABLE_INACTIVITY_RELEASE:
            logger.info("Inactivity auto-release is DISABLED (screen will stay claimed forever)")

    def parse_time(self, t: str) -> int:
        """Helper to parse "M:SS" time strings to seconds."""
        if not t: return 0
        parts = t.split(':')
        return sum(int(p) * (60 ** i) for i, p in enumerate(reversed(parts)))

    def translate_to_audscii(self, text: str) -> List[int]:
        """Translates a standard Python string into a list of AUDSCII bytes."""
        return [audscii_trans[ord(c) % 256] for c in text]

    # Application Logic Functions (Payload Generation)
    def claim_nav_screen(self):
        """Performs the full "Claim Screen" handshake."""
        if self.ddp.state != DDPState.READY:
            logger.warning("Cannot claim screen, session not READY.")
            return False
        
        # Common Payloads
        payload_claim = [0x52, 0x05, 0x82, 0x00, 0x1B, 0x40, 0x30]
        payload_busy  = [0x53, 0x84]
        payload_free  = [0x53, 0x05]
        payload_ready = [0x2E]
        payload_clear = [0x2F]
        payload_ok    = [0x53, 0x85]
            
        if self.ddp.dis_mode == DisMode.RED:
            #Red DIS Claim Handshake (2-Step)
            try:
                self.ddp.send_data_packet(payload_claim)
                data = self.ddp._recv_and_ack_data(1000)
                if not self.ddp.payload_is(data, payload_ok):
                    raise DDPHandshakeError(f"Claim Handshake 2/2 failed (wait 1x 53 85), got {data}")
            except DDPError as e:
                logger.error(f"Failed to claim screen (RED path): {e}")
                return False
        
        else:
            # White DIS Claim Handshake
            try:
                self.ddp.send_data_packet(payload_claim)
                data = self.ddp._recv_and_ack_data(1000)

                # Fast Path
                if self.ddp.payload_is(data, payload_ok):
                    self.screen_is_active = True
                    self.last_draw_time = time.time()
                    return True

                # Standard Path
                if not self.ddp.payload_is(data, payload_busy):
                    raise DDPHandshakeError(f"Claim Handshake 2/7 failed (wait 1x 53 84), got {data}")

                data = self.ddp._recv_and_ack_data(1000)
                if not self.ddp.payload_is(data, payload_free):
                    raise DDPHandshakeError(f"Claim Handshake 3/7 failed (wait 1x 53 05), got {data}")
                
                data = self.ddp._recv_and_ack_data(1000)
                if not self.ddp.payload_is(data, payload_ready):
                    raise DDPHandshakeError(f"Claim HandShak 4/7 failed (wait 1x 2E), got {data}")
                
                self.ddp.send_data_packet(payload_clear)
                self.ddp.send_data_packet(payload_claim)

                data = self.ddp._recv_and_ack_data(1000)
                if not self.ddp.payload_is(data, payload_ok):
                    logger.warning(f"Got non-standard status {data} after 2nd claim, but proceeding.")

            except DDPError as e:
                logger.error(f"Failed to claim screen (WHITE path): {e}")
                return False
            
        logger.info("Region Claim handshake successful. Screen is active.")
        self.screen_is_active = True
        self.last_draw_time = time.time()
        return True

    def clear_screen_payload(self):
        """Queues a 'Region Clear' command (0x52)."""
        logger.info("Queueing Region Clear")
        payload = [0x52, 0x05, 0x02, 0x00, 0x1B, 0x40, 0x30]
        if not self.ddp.send_ddp_frame(payload):
            logger.error("Failed to send clear payload.")

    def write_text(self, text: str, x: int, y: int, flags: int = 0x06):
        """
        Queues a 'Write Text' command (0x57).
        
        CRITICAL UPDATE:
        - Removed 'Aggressive Padding'. We trust the upper layer to handle length.
        - Now applies the 'flags' argument passed from the display manager.
        """
        chars = self.translate_to_audscii(text) 
        
        # 0x57 = Text Opcode
        # Len = chars + 3 (Flags, X, Y)
        payload = [0x57, len(chars) + 3, flags, x, y] + chars
        
        if not self.ddp.send_ddp_frame(payload):
            logger.error("Failed to send text payload.")

    def draw_bitmap(self, x: int, y: int, icon_name: str):
        """Queues a 'Write Bitmap' command (0x55)."""
        if not icon_name or icon_name not in BITMAPS:
            logger.error(f"Bitmap icon '{icon_name}' not found.")
            return

        icon = BITMAPS[icon_name]
        w = icon['w']
        h = icon['h']
        data = icon['data']
        
        ln = 5 + len(data)
        payload = [0x55, ln, 0x02, x, y, w, h] + data
        
        logger.info(f"Queueing bitmap '{icon_name}' at ({x},{y})")
        if not self.ddp.send_ddp_frame(payload):
            logger.error("Failed to send bitmap payload.")

    def commit_frame(self):
        """Queues a 'Commit' command (0x39) to draw the frame."""
        payload = [0x39]
        if not self.ddp.send_ddp_frame(payload):
             logger.error("Failed to send commit packet.")
        
        # Safety Pacing (0.1s) - Critical for Red & White DIS stability
        time.sleep(0.10)

    def clear_screen(self):
        """Executes a full clear screen command (Clear + Commit)."""
        logger.info("Executing full clear_screen command...")
        payload_clear = [0x52, 0x05, 0x02, 0x00, 0x1B, 0x40, 0x30]
        payload_commit = [0x39]
        if not self.ddp.send_ddp_frame(payload_clear + payload_commit):
            logger.error("clear_screen: Failed to send frame.")
            
    def set_source_radio(self):
        """Sends the 0x661 broadcast to set the audio source to Radio."""
        self.ddp.send_can(0x661, [0x00] * 8)
        logger.info("Source: Radio")

    def run(self):
        """Main loop for the DIS Service."""
        logger.info("DIS Service Started. Entering main loop.")
        
        while True:
            try:
                # --- STATE: DISCONNECTED ---
                if self.ddp.state == DDPState.DISCONNECTED:
                    self.screen_is_active = False
                    if self.ddp.detect_and_open_session():
                        logger.info(f"Session established (Mode: {self.ddp.dis_mode.name}).")
                    else:
                        time.sleep(3)
                
                # --- STATE: SESSION_ACTIVE ---
                elif self.ddp.state == DDPState.SESSION_ACTIVE:
                    if not self.ddp.perform_initialization():
                        logger.error("DDP Initialization failed. Retrying.")
                        time.sleep(3)
                    else:
                        self.set_source_radio()
                        logger.info("DDP READY.")
                        self.last_draw_time = time.time()
                        self.screen_is_active = False
                
                # --- STATE: PAUSED ---
                elif self.ddp.state == DDPState.PAUSED:
                    if self.screen_is_active:
                        logger.info("Service PAUSED by Cluster. Waiting for release...")
                        self.screen_is_active = False
                    
                    self.ddp.send_keepalive_if_needed()
                    self.ddp.poll_bus_events()
                    
                    # Just read ZMQ to clear buffer
                    try:
                        while True:
                            self.draw_socket.recv_json(flags=zmq.NOBLOCK)
                    except zmq.Again:
                        pass
                        
                    time.sleep(0.05)
                    continue

                # --- STATE: READY ---
                elif self.ddp.state == DDPState.READY:
                    self.ddp.send_keepalive_if_needed()
                    self.ddp.poll_bus_events()
                    
                    if self.ddp.state != DDPState.READY:
                        continue 

                    # Standard Queue Processing
                    socks = dict(self.poller.poll(5))
                    if self.draw_socket in socks:
                        while True:
                            try:
                                cmd = self.draw_socket.recv_json(flags=zmq.NOBLOCK)
                                
                                if not self.screen_is_active:
                                    if not self.claim_nav_screen():
                                        logger.error("Failed to claim screen.")
                                        break 
                                    
                                self.last_draw_time = time.time()
                                c = cmd.get('command')
                                
                                if c == 'clear':
                                    self.clear_screen()
                                elif c == 'clear_payload':
                                    self.clear_screen_payload()
                                elif c == 'draw_text':
                                    # FIX: Pass the 'flags' parameter from the ZMQ command
                                    self.write_text(
                                        cmd.get('text', ''), 
                                        cmd.get('x', 0), 
                                        cmd.get('y', 0),
                                        cmd.get('flags', 0x06) # Default to 0x06 if missing
                                    )
                                elif c == 'draw_bitmap':
                                    self.draw_bitmap(cmd.get('x', 0), cmd.get('y', 0), cmd.get('icon_name'))
                                elif c == 'commit':
                                    self.commit_frame()
                                
                            except zmq.Again:
                                break 
                        
                    if (self.ENABLE_INACTIVITY_RELEASE
                        and self.screen_is_active
                        and (time.time() - self.last_draw_time > self.inactivity_timeout_sec)):
                        
                        # Auto-Release logic
                        logger.info("Inactivity timeout. Releasing screen.")
                        
                        # Ensure this 'if' is aligned vertically with 'logger' above it
                        if self.ddp.release_screen():
                            self.screen_is_active = False
                        else:
                            self.screen_is_active = False
                
                time.sleep(0.01)

            except Exception as e:
                logger.error(f"Main loop error: {e}", exc_info=True)
                if hasattr(self, 'ddp'):
                    self.ddp._set_state(DDPState.DISCONNECTED)
                time.sleep(3)

if __name__ == "__main__":
    try:
        DisService(config_path='/home/pi/config.json').run()
    except KeyboardInterrupt:
        logger.info("Shutting down service.")
