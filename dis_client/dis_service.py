#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# Audi DIS (Cluster) DDP Service - V1.3
#
# - Uses new 'release_screen()' (0x33) for inactivity timeout.
#   This keeps the session open for stable reconnects.
#
import zmq
import json
import time
import logging
from typing import List, Optional

try:
    from ddp_protocol import DDPProtocol
except ImportError:
    print("Error: Could not import DDPProtocol. Make sure ddp_protocol.py is in the same directory.")
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

        self.ICONS = {
            'filled': [0x90], 'empty': [0xB7],
            'l_bracket': [0x5B], 'r_bracket': [0x5D]
        }
        
        self.audscii_trans = [
            0x00,0x20,0x20,0x20,0x20,0x20,0x20,0x20,0x20,0x2F,0x20,0x20,0x20,0x20,0x20,0x20,
            0x20,0x20,0x20,0x20,0x20,0x20,0x20,0x20,0x20,0x20,0x20,0x20,0x1C,0x20,0x20,0x20,
            0x20,0x21,0x22,0x23,0x24,0x25,0x26,0x27,0x28,0x29,0x2A,0x2B,0x2C,0x2D,0x2E,0x2F,
            0x30,0x31,0x32,0x33,0x34,0x35,0x36,0x37,0x38,0x39,0x3A,0x3B,0x3C,0x3D,0x3E,0x3F,
            0x40,0x41,0x42,0x43,0x44,0x45,0x46,0x47,0x48,0x49,0x4A,0x4B,0x4C,0x4D,0x4E,0x4F,
            0x50,0x51,0x52,0x53,0x54,0x55,0x56,0x57,0x58,0x59,0x5A,0x5B,0x5C,0x5D,0x5E,0x66,
            0x20,0x01,0x02,0x03,0x04,0x05,0x06,0x07,0x08,0x09,0x0A,0x0B,0x0C,0x0D,0x0E,0x0F,
            0x10,0x71,0x72,0x73,0x74,0x75,0x76,0x77,0x78,0x79,0x7A,0x7B,0x7C,0x7D,0x7E,0x20,
            0x20,0x20,0x20,0x20,0x20,0x20,0x20,0x20,0x20,0x20,0x20,0x20,0x20,0x20,0x20,0x20,
            0x20,0x20,0x20,0x20,0x20,0x20,0x20,0x20,0x20,0x20,0x20,0x20,0x20,0x20,0x20,0x20,
            0x20,0x20,0x20,0x20,0x20,0x20,0x20,0x20,0x20,0xA2,0xA0,0x20,0x20,0x2D,0x20,0x7E,
            0x6B,0xB4,0xB2,0xB3,0x20,0xB8,0x20,0x20,0x20,0xB1,0xB0,0x20,0x20,0x20,0x20,0xB9,
            0xC1,0xC0,0xD0,0xE0,0x5F,0xE1,0xE2,0x8B,0xC3,0xC2,0xD2,0xD3,0xC5,0xC4,0xD4,0xD5,
            0xCE,0x8A,0xC7,0xC6,0xD6,0xE6,0x60,0x20,0xE7,0xC9,0xC8,0xD8,0x61,0xE5,0xE8,0x8D,
            0x81,0x80,0x90,0xF0,0x91,0xF1,0xF2,0x9B,0x83,0x82,0x92,0x93,0x85,0x84,0x94,0x95,
            0xEF,0x9A,0x87,0x86,0x96,0xF6,0x97,0xBA,0xF7,0x89,0x88,0x98,0x99,0xF5,0xF8,0x20
        ]

    def parse_time(self, t: str) -> int:
        """Helper to parse "M:SS" time strings to seconds."""
        if not t: return 0
        parts = t.split(':')
        return sum(int(p) * (60 ** i) for i, p in enumerate(reversed(parts)))

    def translate_to_audscii(self, text: str) -> List[int]:
        """Translates a standard Python string into a list of AUDSCII bytes."""
        return [self.audscii_trans[ord(c) % 256] for c in text]

    # Application Logic Functions (Payload Generation)

    def claim_nav_screen(self):
        """Performs the full "Claim Screen" handshake."""
        if self.ddp.state != 'READY':
            logger.warning("Cannot claim screen, session not READY.")
            return False
        
        logger.info("Sending Region Claim (0x52) to switch to NAV screen...")
        
        try:
            payload_claim = [0x52, 0x05, 0x82, 0x00, 0x1B, 0x40, 0x30]
            payload_busy  = [0x53, 0x84]
            payload_free  = [0x53, 0x05]
            payload_ready = [0x2E]
            payload_clear = [0x2F]
            payload_ok    = [0x53, 0x85]
            
            if not self.ddp.send_data_packet(payload_claim):
                raise Exception("Claim Handshake 1/7 failed (send 1x 52)")
            
            data = self.ddp._recv_and_ack_data(1000)
            if not self.ddp.payload_is(data, payload_busy):
                raise Exception(f"Claim Handshake 2/7 failed (wait 1x 53 84), got {data}")

            data = self.ddp._recv_and_ack_data(1000)
            if not self.ddp.payload_is(data, payload_free):
                raise Exception(f"Claim Handshake 3/7 failed (wait 1x 53 05), got {data}")
            
            data = self.ddp._recv_and_ack_data(1000)
            if not self.ddp.payload_is(data, payload_ready):
                raise Exception(f"Claim Handshake 4/7 failed (wait 1x 2E), got {data}")
            
            if not self.ddp.send_data_packet(payload_clear):
                raise Exception("Claim Handshake 5/7 failed (send 1x 2F)")

            if not self.ddp.send_data_packet(payload_claim):
                raise Exception("Claim Handshake 6/7 failed (send 1x 52 again)")

            data = self.ddp._recv_and_ack_data(1000)
            if not self.ddp.payload_is(data, payload_ok):
                logger.warning(f"Got non-standard status {data} after 2nd claim, but proceeding.")

        except Exception as e:
            logger.error(f"Failed to claim screen: {e}")
            self.ddp.state = 'DISCONNECTED' # Force re-init
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

    def write_text(self, text: str, x: int = 0, y: int = 0):
        """Queues a 'Write Text' command (0x57)."""
        logger.info(f"Queueing text '{text}'")
        chars = self.translate_to_audscii(text) + [0xD7] # 0xD7 = Clear to end of line
        payload = [0x57, len(chars) + 3, 0x06, x, y] + chars
        if not self.ddp.send_ddp_frame(payload):
            logger.error("Failed to send text payload.")

    def commit_frame(self):
        """Queues a 'Commit' command (0x39) to draw the frame."""
        logger.info("Committing Frame (0x39)")
        payload = [0x39]
        if not self.ddp.send_data_packet(payload, is_multi_packet_frame_body=False):
            logger.error("Failed to send commit packet.")

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
        """
        Main loop for the DIS Service.
        This loop is now a persistent state machine.
        """
        while True:
            try:
                # STATE: DISCONNECTED
                if self.ddp.state == 'DISCONNECTED':
                    self.screen_is_active = False 
                    if self.ddp.passive_open():
                        logger.info("PASSIVE MODE: Cluster opened")
                    elif self.ddp.active_open():
                        logger.info("ACTIVE MODE: We opened")
                    else:
                        logger.warning("No session — retrying in 3s...")
                        time.sleep(3)
                    
                # STATE: SESSION_ACTIVE
                elif self.ddp.state == 'SESSION_ACTIVE':
                    if not self.ddp.perform_initialization():
                        logger.error("DDP Initialization failed. Retrying session.")
                        # Driver state is set to DISCONNECTED on fail
                        time.sleep(3)
                    else:
                        # Success!
                        self.set_source_radio()
                        logger.info("DDP READY. Waiting for first client command to claim screen.")
                        self.last_draw_time = time.time() 
                        self.screen_is_active = False 
                
                # STATE: READY
                elif self.ddp.state == 'READY':
                    # This is the "inner loop"
                    self.ddp.send_keepalive_if_needed()
                    self.ddp.poll_bus_events()
                    
                    if self.ddp.state != 'READY':
                        logger.warning("Session closed by cluster. Re-initializing.")
                        continue # Go back to top of loop

                    # ZMQ Command Parser
                    socks = dict(self.poller.poll(5)) 
                    if self.draw_socket in socks:
                        while True:
                            try:
                                cmd = self.draw_socket.recv_json(flags=zmq.NOBLOCK)
                                
                                # Auto-Claim Feature
                                if not self.screen_is_active:
                                    if not self.claim_nav_screen():
                                        logger.error("Failed to claim screen. Will retry on next command.")
                                        break 
                                    self.screen_is_active = True

                                self.last_draw_time = time.time()
                                c = cmd.get('command')
                                
                                if c == 'clear':
                                    self.clear_screen() 
                                
                                elif c == 'clear_payload':
                                    self.clear_screen_payload()
                                
                                elif c == 'draw_text':
                                    self.write_text(
                                        cmd.get('text', ''),
                                        cmd.get('x', 0),
                                        cmd.get('y', 0)
                                    )
                                
                                elif c == 'commit':
                                    self.commit_frame()
                                
                            except zmq.Again:
                                break # No more commands in queue
                    
                    # Auto-Release Feature (V1.3)
                    if self.screen_is_active and (time.time() - self.last_draw_time > self.inactivity_timeout_sec):
                        logger.info(f"Inactivity timeout ({self.inactivity_timeout_sec}s). Releasing screen to Bordcomputer.")
                        if self.ddp.release_screen(): # This sends 0x33
                            self.screen_is_active = False
                        else:
                            # Release failed, session is dead.
                            # The driver set its state to DISCONNECTED.
                            logger.error("Failed to send release packet, forcing reconnect.")
                            self.screen_is_active = False 
                
                time.sleep(0.01)

            except Exception as e:
                logger.error(f"Main loop error: {e}", exc_info=True)
                if hasattr(self, 'ddp'):
                    self.ddp.state = 'DISCONNECTED'
                time.sleep(3)


if __name__ == "__main__":
    try:
        DisService(config_path='/home/pi/config.json').run()
    except KeyboardInterrupt:
        logger.info("Shutting down service.")