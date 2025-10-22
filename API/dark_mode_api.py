#!/usr/bin/env python3
"""
Dark Mode Toggle
Simple: python3 dark_mode_api.py [on|off]
Threading: Non-blocking + Auto-retry
1 Line Output
"""

import socket
import struct
import sys
import os
import time
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + '/api_files')
from hudiy_api_pb2 import *

def send_dark_mode(enabled, max_retries=3):
    """Thread-safe dark mode with auto-retry"""
    for attempt in range(max_retries):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2.0)
            sock.connect(('localhost', 44405))
            
            # Hello
            hello = HelloRequest()
            hello.name = "DarkMode"
            hello.api_version.major = 1
            hello.api_version.minor = 0
            data = hello.SerializeToString()
            frame = struct.pack('<III', len(data), MESSAGE_HELLO_REQUEST, 0) + data
            sock.sendall(frame)
            
            # Dark mode
            dark = SetDarkMode()
            dark.enabled = enabled
            data = dark.SerializeToString()
            frame = struct.pack('<III', len(data), MESSAGE_SET_DARK_MODE, 0) + data
            sock.sendall(frame)
            
            sock.close()
            return True
            
        except Exception as e:
            if attempt < max_retries - 1:
                time.sleep(0.5)
                continue
            return False

def main():
    if len(sys.argv) < 2:
        print("Usage: python3 dark_mode_api.py [on|off]")
        sys.exit(1)
    
    enabled = sys.argv[1].lower() == 'on'
    success = send_dark_mode(enabled)
    
    if success:
        print(f"{'🌙 Dark' if enabled else '☀️ Light'} mode set")
    else:
        print(f"❌ Failed to set {'dark' if enabled else 'light'} mode")

if __name__ == '__main__':
    main()
