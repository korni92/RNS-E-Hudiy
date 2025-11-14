Audi RNS-E DIS (Cluster) Controller

This project allows you to take control of the Audi A3 8P / RNS-E navigation display (the red DIS screen in the instrument cluster) and show custom information, such as media player tracks, navigation instructions, or phone status.

This project is split into two main components:

dis_handler.py: The low-level driver/server that speaks the DDP protocol to the car.

hudiy_dis.py: The high-level client/application that decides what to draw.

Project Status

This is an early release. The components are in different stages of development:

dis_handler.py (Beta 1): This script is stable. It successfully handles the complex DDP handshake, claims the navigation screen, and reliably draws text sent via ZMQ.

hudiy_dis.py (Alpha): This is an example client to show how to use the handler. The logic for scrolling, menus, and data handling is still experimental and has bugs. This script requires a lot of work before it is fully functional.

How it Works

The project uses a client-server architecture to separate the complex CAN protocol from the application logic.

1. dis_handler.py (The Server / Driver)

This is the core of the project. It runs as a background service.

Connects directly to your car's CAN bus (e.g., can0) using python-can.

Monitors 0x6C1 (cluster) and 0x6C0 (nav) IDs.

Performs the full DDP handshake to open a session.

Sends the "Region Claim" handshake to force the DIS to show the navigation screen.

Listens on a ZMQ PULL socket for simple JSON commands (like draw_text, commit, clear).

Translates these simple JSON commands into the complex, multi-packet DDP CAN frames that the cluster understands.

2. hudiy_dis.py (The Client / Application)

This is the "front-end" application.

It does not touch the CAN bus.

It handles all application logic: what to display, scrolling text, managing menus, etc.

It reads data from your other car computer systems (e.g., Hudiy, Navit, custom media players).

It connects to the dis_handler.py via a ZMQ PUSH socket.

It sends simple JSON commands (e.g., {"command": "draw_text", "text": "Hello World"}) to the handler to be drawn.

Requirements

A CAN interface (e.g., a PiCAN hat) configured as can0.

Python 3

Python libraries: python-can and pyzmq

You can install the libraries with:

```
sudo apt update
sudo apt install python3-can python3-zmq
```

Configuration

You must edit your config.json file for the scripts to work.

For dis_handler.py:
This script needs to know which CAN interface to use and where to listen for ZMQ commands.

```
{
  "can_channel": "can0",
  "zmq": {
    "dis_draw": "tcp://127.0.0.1:5557"
  }
}
```


For hudiy_dis.py:
This script needs to know where to send its draw commands (must match dis_draw) and where to get its data from your API.

```
{
  "zmq": {
    "dis_draw": "tcp://127.0.0.1:5557",

    "publish_address": "ipc:///run/rnse_control/can_stream.ipc",
    "hudiy_publish_address": "ipc:///run/rnse_control/hudiy_stream.ipc"
  }
}
```


Note: hudiy_dis.py will only show data if your system is correctly publishing data to the publish_address / hudiy_publish_address sockets or writing to the /tmp/now_playing.json files.

How to Run

In Terminal 1, run the handler:

```
python3 home/pi/dis_client/dis_handler.py
```

Wait for it to log DIS READY — FULLY REACTIVE. Your cluster screen should now be claimed (it may be blank).

In Terminal 2, run the client:

```
python3 home/pi/dis_client/hudiy_dis.py
```

This will connect to the handler and start sending draw commands based on the data it receives from your APIs.

Future Plans (Roadmap)

This project is still in early development. The main goals are:

Improve hudiy_dis.py:

Properly fix text scrolling logic.

Implement a menu system to switch between Music, Nav, and Phone modes (e.g., using MFSW buttons).

Improve dis_handler.py:

Add logic to gracefully release the screen (send A8) when hudiy_dis.py has no information to display for a set period.

Contributions are welcome.
