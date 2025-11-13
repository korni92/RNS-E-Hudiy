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
pip install protobuf websocket-client pyzmq
```

## Step 4: Create API Files Directory
```bash
mkdir api_files
```

## Step 5: Download and Generate Protobuf
Download the protobuf definition Api.proto(from Hudiy GitHub):
```bash
mkdir -p api/files/common
cd api_files/common
wget https://raw.githubusercontent.com/wiboma/hudiy/main/api/Api.proto
protoc --python_out=. Api.proto
cd ..
```
This generates `Api_pb2.py`.

## Step 5.1: Add Official Client Files
```
mkdir -p api_files/common
```
Add Client.py and Message.py to the directory from Hudiy GitHub


## Troubleshooting
- **Broken Pipe Error**: Connection closed unexpectedly—restart Hudiy and retry.
- **struct.error**: Empty/invalid response—check Hudiy logs for API issues.
- **Connection Refused**: Verify Hudiy is running and ports match.

This setup works as shown in your output. Expand the script for more features like OBD queries!

## Adding Servive to start scripts

```
sudo nano /etc/systemd/system/dark_mode.service
```

```
[Unit]
Description=Hudiy Dark Mode CAN Bus Service
Requires=configure-can0.service
Requires=can-handler.service
After=network.target configure-can0.service can_handler.service

[Service]
User=pi
Group=pi

WorkingDirectory=/home/pi/hudiy_client

# It runs the python3 *from inside* your venv
ExecStart=/home/pi/hudiy_client/venv/bin/python3 /home/pi/hudiy_client/dark_mode_api.py

Restart=on-failure
RestartSec=5s

[Install]
WantedBy=multi-user.target
```

```
sudo systemctl daemon-reload
```

```
sudo systemctl enable dark_mode.service
```

```
sudo systemctl start dark_mode.service
```

## Troubleshooting service

```
systemctl status dark_mode.service
```

```
journalctl -u dark_mode.service -f
```

```
sudo systemctl restart dark_mode.service
```
