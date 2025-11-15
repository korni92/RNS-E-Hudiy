# Audi RNS-E DIS (Cluster) Controller

This project allows you to take control of the Audi A3 8P / RNS-E navigation display (the red DIS screen in the instrument cluster) and show custom information, such as media player tracks, navigation instructions, or phone status.

## Project Status

This is an early release. The components are in different stages of development:

  * **`dis_service.py` (Beta 1):** This is the main service/driver. It is stable, handles the complex DDP handshake, manages the CAN bus connection, and auto-releases the screen when idle.
  * **`dis_service.py` ** needs to be in the same folder as dis_service.py it contains the DDP
  * **`hudiy_dis.py` (Alpha):** This is an *example client* to show how to use the service. The logic for scrolling, menus, and data handling is still experimental and has bugs.

## How it Works

This project uses a robust driver/service architecture to separate the complex CAN protocol from the application logic.

### 1\. `ddp_protocol.py` (The Driver)

This file contains the `DDPProtocol` class. It is a low-level driver responsible **only** for the CAN bus communication.

  * Connects directly to SocketCAN (e.g., `can0`).
  * Manages the DDP state machine (Connecting, Handshake, Ready, Disconnected).
  * Handles packet sequencing, ACKs, and keep-alives.
  * Provides simple methods like `send_ddp_frame(payload)`.
  * It does **not** know what `0x52` (Region) or `0x57` (Write) means.

### 2\. `dis_service.py` (The Service)

This is the **main script you must run**. It acts as the "graphics card" for the DIS.

  * It imports and uses the `DDPProtocol` driver, which **runs persistently**.
  * It listens on a ZMQ PULL socket (e.g., `tcp://127.0.0.1:5557`) for high-level JSON commands from clients.
  * It translates simple commands (like `draw_text`) into DDP payloads (`[0x52, ..., 0x57, ...]`).
  * It tells the driver to send these payloads.
  * **Auto-Claim:** It automatically claims the screen on the *first* draw command received from a client.
  * **Auto-Release:** It automatically sends the `0x33` command to release the screen back to the on-board computer after a 30-second inactivity period. This keeps the DDP session **open** for stable, instant reconnection.
  * **Auto-Reconnect:** If the cluster closes the session (e.g., ignition off), the service automatically detects this and will attempt a new handshake when ready.

### 3\. `hudiy_dis.py` (The Client / Application)

This is your "front-end" application (e.g., your media player display).

  * It **does not** touch the CAN bus.
  * It connects to the `dis_service.py` via a ZMQ PUSH socket.
  * It sends simple JSON commands (e.g., `{"command": "draw_text", "text": "Hello World"}`) to the service to be drawn.

## Requirements

  * Python libraries: `python-can` and `pyzmq`

You can install the libraries on a Debian-based system (like Raspberry Pi OS) with:

```bash
sudo apt update
sudo apt install python3-can python3-zmq
```

## Configuration

Your `config.json` file is used by both the service and the client.

```json
{
  "can_channel": "can0",
  
  "zmq": {
    "dis_draw": "tcp://127.0.0.1:5557",
    
    "publish_address": "ipc:///run/rnse_control/can_stream.ipc",
    "hudiy_publish_address": "ipc:///run/rnse_control/hudiy_stream.ipc"
  }
}
```

  * `dis_service.py` uses `can_channel`, and `zmq.dis_draw`.
  * `hudiy_dis.py` (your client) uses `zmq.dis_draw`, `zmq.publish_address`, and `zmq.hudiy_publish_address`.

## How to Run

1.  **In Terminal 1, run the service:**

    ```bash
    python3 dis_service.py
    ```

    Wait for it to log `DDP READY. Waiting for first client command...`. The cluster screen will *not* change yet.

2.  **In Terminal 2, run your client:**

    ```bash
    python3 hudiy_dis.py
    ```

    As soon as this script sends its first draw command, the `dis_service` will claim the screen and display the text.

## Future Plans (Roadmap)

This project is still in early development. The main goals are:

  * **Improve `hudiy_dis.py`:**
      * Properly fix text scrolling logic.
      * Implement a menu system (e.g., using MFSW buttons).
  * **Improve `dis_service.py`:**
      * (Potentially) Add a ZMQ-based CAN gateway back in, so the `ddp_protocol.py` could run on a different machine from the `dis_service.py`.

Contributions are welcome.
