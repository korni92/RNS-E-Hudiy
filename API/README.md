# Hudiy API Python Client Installation Tutorial

This tutorial guides you through setting up a Python client for the Hudiy API on Raspberry Pi OS (Bookworm). It uses TCP on port 44405 (as configured). The client allows controlling features like media playback, volume, and monitoring status.

## Prerequisites
- API enabled in `~/.hudiy/share/config/main_configuration.json`:
  ```json
  {
    "api": {
      "tcpEndpointPort": 44405,
      "webSocketEndpointPort": 44406
    }
  }
  ```
- Restart Hudiy after config changes (e.g., `pkill hudiy` and relaunch).

## Step 1: Install System Dependencies
Update packages and install required tools:
```bash
sudo apt update
sudo apt install python3-full python3-venv protobuf-compiler
```

## Step 2: Create Project Directory and Virtual Environment
```bash
mkdir ~/hudiy_client
cd ~/hudiy_client
python3 -m venv venv
source venv/bin/activate
```

## Step 3: Install Python Packages
Inside the virtual environment:
```bash
pip install protobuf websocket-client
```

## Step 4: Create API Files Directory
```bash
mkdir api_files
cd api_files
```

## Step 5: Download and Generate Protobuf
Download the protobuf definition (from Hudiy GitHub):
```bash
wget https://raw.githubusercontent.com/wiboma/hudiy/main/api/hudiy_api.proto
protoc --python_out=. hudiy_api.proto
```
This generates `hudiy_api_pb2.py`.

## Step 6: Create the Client Script
Go back to the main directory:
```bash
cd ..
```
Create `run_client.py` (copy-paste the code from previous responses, or use this command to create it):
```bash
cat > run_client.py << 'EOF'
#!/usr/bin/env python3
import socket
import struct
import time
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + '/api_files')
from hudiy_api_pb2 import *

class HudiyClient:
    def __init__(self, host='localhost', port=44405):
        self.host = host
        self.port = port
        self.sock = None
        
    def connect(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.connect((self.host, self.port))
        print(f"✅ Connected: {self.host}:{self.port}")
        self.send_hello()
        
    def send_hello(self):
        hello = HelloRequest()
        hello.name = "QuickClient"
        hello.api_version.major = 1
        hello.api_version.minor = 0
        self.send_message(MESSAGE_HELLO_REQUEST, hello)
        
    def send_message(self, msg_type, message):
        data = message.SerializeToString()
        frame = struct.pack('<III', len(data), msg_type, 0) + data
        self.sock.sendall(frame)
        
    def receive_message(self):
        size_data = self.sock.recv(4)
        size = struct.unpack('<I', size_data)[0]
        header_data = self.sock.recv(8)
        msg_id = struct.unpack('<I', header_data[:4])[0]
        data = b''
        while len(data) < size:
            data += self.sock.recv(size - len(data))
        return msg_id, data
        
    def subscribe_all(self):
        subs = SetStatusSubscriptions()
        subs.subscriptions.extend([
            SetStatusSubscriptions.MEDIA,
            SetStatusSubscriptions.PHONE,
            SetStatusSubscriptions.OBD
        ])
        self.send_message(MESSAGE_SET_STATUS_SUBSCRIPTIONS, subs)
    
    def play_pause(self):
        action = DispatchAction()
        action.action = "now_playing_toggle_play"
        self.send_message(MESSAGE_DISPATCH_ACTION, action)
        print("⏯️ Play/Pause")
    
    def volume_up(self):
        action = DispatchAction()
        action.action = "output_volume_up"
        self.send_message(MESSAGE_DISPATCH_ACTION, action)
        print("🔊 Volume UP")
    
    def listen(self):
        print("👀 Monitoring... (Ctrl+C to stop)")
        try:
            while True:
                msg_id, data = self.receive_message()
                if msg_id == MESSAGE_MEDIA_STATUS:
                    status = MediaStatus()
                    status.ParseFromString(data)
                    print(f"🎵 {'▶️' if status.is_playing else '⏸️'} {status.position_label}")
                time.sleep(0.1)
        except KeyboardInterrupt:
            print("\n👋 Stopped monitoring")

def main():
    print("🚗 Hudiy Client Starting...")
    client = HudiyClient(port=44405)
    
    try:
        client.connect()
        client.subscribe_all()
        
        print("\n🎛️ COMMANDS:")
        print("1 = Play/Pause | 2 = Volume Up | 3 = Monitor | 0 = Quit")
        
        while True:
            cmd = input("\n> ").strip()
            if cmd == "1": client.play_pause()
            elif cmd == "2": client.volume_up()
            elif cmd == "3": client.listen()
            elif cmd == "0": break
                    
    except KeyboardInterrupt:
        pass
    finally:
        client.sock.close()

if __name__ == "__main__":
    main()
EOF
```
Make it executable:
```bash
chmod +x run_client.py
```

## Step 7: Run the Client
```bash
python3 run_client.py
```
- Follow on-screen commands (e.g., "1" for play/pause).
- Use Ctrl+C to stop monitoring or exit.

## Troubleshooting
- **Broken Pipe Error**: Connection closed unexpectedly—restart Hudiy and retry.
- **struct.error**: Empty/invalid response—check Hudiy logs for API issues.
- **Connection Refused**: Verify Hudiy is running and ports match.

This setup works as shown in your output. Expand the script for more features like OBD queries!
