#!/usr/bin/env python3
"""
Dark Mode Toggle via API
Usage: python3 dark_mode.py [on|off]
"""

import socket
import struct
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + '/api_files')
from hudiy_api_pb2 import *

def send_dark_mode(enabled):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
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
    print(f"{'ðŸŒ™ Dark' if enabled else 'â˜€ï¸ Light'} mode set")

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python3 dark_mode.py [on|off]")
        sys.exit(1)
    send_dark_mode(sys.argv[1].lower() == 'on')
