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
cd api_files
```

## Step 5: Download and Generate Protobuf
Download the protobuf definition (from Hudiy GitHub):
```bash
mkdir api_files
cd api_files
# Download proto from GitHub
wget https://raw.githubusercontent.com/wiboma/hudiy/main/api/hudiy_api.proto
protoc --python_out=. hudiy_api.proto
mv hudiy_api_pb2.py Api_pb2.py  # For official client
cd ..
```
This generates `hudiy_api_pb2.py`.

## Step 5.1: Add Official Client Files
```
mkdir -p api_files/common
```
Add Client.py and ClientEventHandler.py to the directory


## Troubleshooting
- **Broken Pipe Error**: Connection closed unexpectedly—restart Hudiy and retry.
- **struct.error**: Empty/invalid response—check Hudiy logs for API issues.
- **Connection Refused**: Verify Hudiy is running and ports match.

This setup works as shown in your output. Expand the script for more features like OBD queries!
