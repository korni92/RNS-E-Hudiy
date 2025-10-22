#!/usr/bin/env python3
#
#  Copyright (C) Hudiy Project - All Rights Reserved
#

import socket
import struct
import threading
import time
import logging
import websocket

# FIX: Direct import (no relative)
from Api_pb2 import *

class ClientEventHandler:
    """Base class for handling events from the Hudiy API."""
    
    def on_hello_response(self, client, message): pass
    def on_media_status(self, client, message): pass
    def on_media_metadata(self, client, message): pass
    def on_navigation_status(self, client, message): pass
    def on_navigation_maneuver_details(self, client, message): pass
    def on_navigation_maneuver_distance(self, client, message): pass
    def on_phone_voice_call_status(self, client, message): pass

class Client:
    def __init__(self, name):
        self.name = name
        self.event_handler = None
        self.socket = None
        self.use_websocket = False
        self.running = False
        self.next_id = 1
        
    def set_event_handler(self, event_handler):
        self.event_handler = event_handler
        
    def connect(self, host, port, use_websocket=False):
        self.use_websocket = use_websocket
        self.host = host
        self.port = port
        
        if use_websocket:
            self.socket = websocket.create_connection(f"ws://{host}:{port}")
        else:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.connect((host, port))
        
        self.running = True
        self.thread = threading.Thread(target=self._receive_loop)
        self.thread.daemon = True
        self.thread.start()
        
        self.send_hello()
        
    def send_hello(self):
        hello = HelloRequest()
        hello.name = self.name
        hello.api_version.major = 1
        hello.api_version.minor = 0
        self.send(MESSAGE_HELLO_REQUEST, 0, hello.SerializeToString())
        
    def send(self, msg_type, msg_id, data):
        if not self.running: return
        try:
            frame = struct.pack('<III', len(data), msg_type, msg_id) + data
            if self.use_websocket:
                self.socket.send(frame, websocket.ABNF.OPCODE_BINARY)
            else:
                self.socket.sendall(frame)
        except Exception as e:
            logging.error(f"Send error: {e}")
            self.running = False
            
    def wait_for_message(self):
        time.sleep(0.1)
        return self.running
        
    def _receive_loop(self):
        while self.running:
            try:
                if self.use_websocket:
                    frame = self.socket.recv()
                    if len(frame) < 12: continue
                    size, msg_type, msg_id = struct.unpack('<III', frame[:12])
                    data = frame[12:12+size]
                else:
                    header = self.socket.recv(12)
                    if len(header) < 12: 
                        self.running = False
                        break
                    size, msg_type, msg_id = struct.unpack('<III', header)
                    data = b''
                    while len(data) < size:
                        chunk = self.socket.recv(size - len(data))
                        if not chunk:
                            self.running = False
                            break
                        data += chunk
                
                self._handle_message(msg_type, msg_id, data)
                
            except Exception as e:
                logging.error(f"Receive error: {e}")
                self.running = False
                break
        
    def _handle_message(self, msg_type, msg_id, data):
        try:
            if msg_type == MESSAGE_HELLO_RESPONSE and self.event_handler:
                response = HelloResponse()
                response.ParseFromString(data)
                self.event_handler.on_hello_response(self, response)
                
            elif msg_type == MESSAGE_MEDIA_METADATA and self.event_handler:
                metadata = MediaMetadata()
                metadata.ParseFromString(data)
                self.event_handler.on_media_metadata(self, metadata)
                
            elif msg_type == MESSAGE_MEDIA_STATUS and self.event_handler:
                status = MediaStatus()
                status.ParseFromString(data)
                self.event_handler.on_media_status(self, status)
                
            elif msg_type == MESSAGE_NAVIGATION_MANEUVER_DETAILS and self.event_handler:
                maneuver = NavigationManeuverDetails()
                maneuver.ParseFromString(data)
                self.event_handler.on_navigation_maneuver_details(self, maneuver)
                
            elif msg_type == MESSAGE_PHONE_VOICE_CALL_STATUS and self.event_handler:
                call_status = PhoneVoiceCallStatus()
                call_status.ParseFromString(data)
                self.event_handler.on_phone_voice_call_status(self, call_status)
                    
        except Exception as e:
            logging.error(f"Handle message error: {e}")
            
    def disconnect(self):
        self.running = False
        if self.socket:
            self.socket.close()
