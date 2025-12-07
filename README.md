

````markdown
# Hudiy / RNS-E CAN Bus Integration

This project integrates a Raspberry Pi running Hudiy (or similar OS like Crankshaft NG) with an Audi RNS-E navigation system via the CAN bus. It creates a seamless infotainment experience by enabling DIS (Driver Information System) display, steering wheel controls, and time synchronization.

## 🚀 Architecture Overview

The system uses a stable microservice architecture where each script runs as a separate, managed systemd service to ensure reliability in an automotive environment.

You need at least
Raspberry Pi 4 or 5
5A DC/DC Bucket converter
SRGB HDMI or composite video converter
CAN Hat like MCP2515
DAC Hat

* **`can-handler.service`**: The central gateway to the CAN bus hardware.
* **`can-base-function.service`**: Provides core functions like TV tuner simulation and time synchronization.
* **`can-keyboard-control.service`**: Translates car steering wheel/button presses into virtual keyboard commands (uinput).
* **`dis_service`**: The driver used to control the instrument cluster screen.
* **`dis_display`**: Manages the content rendered on the cluster screen.

---

## 🚀 Quick Installation (Recommended)

The easiest way to install this project is using the automated installer script.

**1. Download the Installer**

wget https://raw.githubusercontent.com/korni92/RNS-E-Hudiy/main/install.sh


````

**2. Make it Executable and Run**

```bash
chmod +x install.sh
sudo ./install.sh
```

**3. Follow the Prompts**
The script will ask for:

  * **CAN HAT Frequency:** Usually 8, 12, or 16 MHz (Check your hardware specs).
  * **Interrupt Pin:** Default is 25.

  * ignore the error :)

**4. Reboot**
Once finished, reboot your Pi to apply all changes.

```bash
sudo reboot
```

-----

## 🛠️ Manual Installation (Advanced)

If you prefer to install everything manually or need to debug a specific step, follow this guide.

### 1\. Update System & Install Dependencies

```bash
sudo apt-get update
sudo apt-get install -y git python3-pip can-utils python3-can python3-serial \
    python3-tz python3-unidecode python3-zmq python3-aiozmq python3-uinput \
    python3-protobuf python3-full python3-venv protobuf-compiler wget
```

> **Note:** On newer Debian versions, if `python3-websocket-client` is not found in apt, install it via pip:
> `pip3 install websocket-client --break-system-packages`

### 2\. Set Up Project Directories

Clone the repository and organize the file structure.

```bash
cd /home/pi
git clone [https://github.com/korni92/RNS-E-Hudiy.git](https://github.com/korni92/RNS-E-Hudiy.git) temp_repo
cp -r temp_repo/rns-e_can .
cp -r temp_repo/hudiy_client .
cp -r temp_repo/dis_client .
cp temp_repo/config.json .
rm -rf temp_repo
```

### 3\. Configure Permissions (Crucial)

Allow the `pi` user to access the virtual keyboard (`uinput`).

```bash
sudo usermod -a -G input pi
echo 'uinput' | sudo tee /etc/modules-load.d/uinput.conf
echo 'KERNEL=="uinput", MODE="0660", GROUP="input", OPTIONS+="static_node=uinput"' | sudo tee /etc/udev/rules.d/99-uinput.rules

# Reload rules
sudo udevadm control --reload-rules
sudo udevadm trigger
```

### 4\. Setup Protobuf

Download the required API definitions and compile them.

```bash
cd /home/pi/hudiy_client/api_files/common
wget [https://raw.githubusercontent.com/wiboma/hudiy/main/api/Api.proto](https://raw.githubusercontent.com/wiboma/hudiy/main/api/Api.proto)
wget [https://raw.githubusercontent.com/wiboma/hudiy/main/examples/api/python/common/Client.py](https://raw.githubusercontent.com/wiboma/hudiy/main/examples/api/python/common/Client.py)
wget [https://raw.githubusercontent.com/wiboma/hudiy/main/examples/api/python/common/Message.py](https://raw.githubusercontent.com/wiboma/hudiy/main/examples/api/python/common/Message.py)

# Compile
protoc --python_out=. Api.proto
```

### 5\. Configure Hardware (CAN HAT)

1.  **Edit Config:** Open `/boot/firmware/config.txt` (or `/boot/config.txt` on older OS versions) and add the following lines. **Replace `12000000` with your specific oscillator frequency.**

    ```ini
    dtparam=spi=on
    dtoverlay=mcp2515-can0,oscillator=12000000,interrupt=25,spimaxfrequency=1000000
    ```

2.  **Configure Network:** Create/Edit `/etc/systemd/network/80-can.network`:

    ```ini
    [Match]
    Name=can0

    [CAN]
    BitRate=100K
    RestartSec=100ms
    ```

3.  **Enable Networkd:**

    ```bash
    sudo systemctl enable --now systemd-networkd
    ```

### 6\. Install Services

Copy the `.service` files (ensure paths in `ExecStart` inside these files match your installation, e.g., `/home/pi/...`) to `/etc/systemd/system/`.

If you are setting this up manually or need to debug specific services, use the configurations below.

CAN Network Configuration

This file creates the interface link for the CAN HAT.

**File:** `/etc/systemd/network/80-can.network`

```ini
[Match]
Name=can0

[CAN]
BitRate=100K
RestartSec=100ms
```

-----

Systemd Services

Create the following files in `/etc/systemd/system/`.

> **Note:** These examples assume your username is `pi` and your installation path is `/home/pi`.

A. CAN Handler (Gateway)**

The core service that manages the connection to the CAN bus hardware.

**File:** `/etc/systemd/system/can_handler.service`

```ini
[Unit]
Description=RNS-E CAN-Bus Handler
BindsTo=sys-subsystem-net-devices-can0.device
After=sys-subsystem-net-devices-can0.device

[Service]
User=pi
Group=pi
WorkingDirectory=/home/pi/rns-e_can
Environment=PYTHONUNBUFFERED=1
ExecStart=/usr/bin/python3 /home/pi/rns-e_can/can_handler.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

B. Base Functionality**

Handles TV tuner simulation and time sync.

**File:** `/etc/systemd/system/can_base_function.service`

```ini
[Unit]
Description=RNS-E CAN-Bus Base Functionality
Requires=can_handler.service
After=can_handler.service
BindsTo=can_handler.service

[Service]
ExecStart=/usr/bin/python3 /home/pi/rns-e_can/can_base_function.py
WorkingDirectory=/home/pi/rns-e_can
User=pi
Group=pi
Environment=PYTHONUNBUFFERED=1
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

C. Keyboard Control**

Maps steering wheel buttons to system keystrokes.
*Requires `uinput` permissions.*

**File:** `/etc/systemd/system/can_keyboard_control.service`

```ini
[Unit]
Description=RNS-E CAN-Bus Keyboard Simulation
Wants=can_handler.service
After=can_handler.service

[Service]
ExecStart=/usr/bin/python3 /home/pi/rns-e_can/can_keyboard_control.py
WorkingDirectory=/home/pi/rns-e_can
Environment=PYTHONUNBUFFERED=1
Restart=always
RestartSec=3
User=pi
Group=input

[Install]
WantedBy=multi-user.target
```

D. Hudiy Integration APIs**

These services allow the system to communicate with the Hudiy interface (Dark mode and Data).

**File:** `/etc/systemd/system/dark_mode_api.service`

```ini
[Unit]
Description=Hudiy Dark Mode CAN Bus Service
Requires=can_handler.service
After=can_handler.service

[Service]
User=pi
Group=pi
WorkingDirectory=/home/pi/hudiy_client
Environment=PYTHONUNBUFFERED=1
ExecStart=/usr/bin/python3 /home/pi/hudiy_client/dark_mode_api.py
Restart=always
RestartSec=5s

[Install]
WantedBy=multi-user.target
```

**File:** `/etc/systemd/system/hudiy_data_api.service`

```ini
[Unit]
Description=Hudiy Data Extractor
Requires=can_handler.service
After=can_handler.service

[Service]
User=pi
Group=pi
WorkingDirectory=/home/pi/hudiy_client
Environment=PYTHONUNBUFFERED=1
ExecStart=/usr/bin/python3 /home/pi/hudiy_client/hudiy_data.py
Restart=always
RestartSec=5s

[Install]
WantedBy=multi-user.target
```

E. DIS (Cluster Display) Services**

These services control the screen in the instrument cluster. Note the built-in delays to ensure the system is ready before launching.

**File:** `/etc/systemd/system/dis_service.service`

```ini
[Unit]
Description=DIS CAN Driver
Requires=can_handler.service
After=can_handler.service
BindsTo=can_handler.service

[Service]
ExecStartPre=/bin/sleep 30
ExecStart=/usr/bin/python3 /home/pi/dis_client/dis_service.py
WorkingDirectory=/home/pi/dis_client
Environment=PYTHONUNBUFFERED=1
Restart=always
RestartSec=10
User=pi
Group=input

[Install]
WantedBy=multi-user.target
```

**File:** `/etc/systemd/system/dis_display.service`

```ini
[Unit]
Description=DIS Menu Structure
Requires=dis_service.service
After=dis_service.service
BindsTo=dis_service.service

[Service]
ExecStartPre=/bin/sleep 15
ExecStart=/usr/bin/python3 /home/pi/dis_client/dis_display.py
WorkingDirectory=/home/pi/dis_client
Environment=PYTHONUNBUFFERED=1
Restart=always
RestartSec=10
User=pi
Group=input

[Install]
WantedBy=multi-user.target
```

Applying Changes

After creating or modifying these files, reload the systemd daemon:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now can_handler.service can_base_function.service \
    can_keyboard_control.service dark_mode_api.service hudiy_data_api.service \
    dis_service.service dis_display.service
```

-----

## 📺 Composite Video Configuration (Pi 4)

To use the RNS-E screen via composite video output (RCA) on a Raspberry Pi 4:

**1. Edit `/boot/firmware/config.txt`**
Enable composite output and set the timing for RNS-E 193 PU (800x480):

```ini
 # To use a configuration, uncomment ONE of the blocks below:

# --- 1. RNS-E 193 (800 x 480 pixels) ---

# hdmi_cvt=800 480 60 6 0 0 0 
# hdmi_group=2
# hdmi_mode=87
# enable_tvout=1
# overscan_scale=1
# # Overscan/Margins (Set all to 0 if the image fits perfectly)
# overscan_bottom=0
# overscan_top=0 
# overscan_left=0
# overscan_right=0

# --- 2. RNS-E 192 / Old (480 x 234 pixels) ---

# hdmi_cvt=480 234 60 6 0 0 0
# hdmi_group=2
# hdmi_mode=87
# enable_tvout=1
# overscan_scale=1
# # Overscan/Margins (Set all to 0 if the image fits perfectly)
# overscan_bottom=0
# overscan_top=0 
# overscan_left=0
# overscan_right=0

``` 
```

**2. Edit `/boot/firmware/cmdline.txt`**

in `cmdline.txt` you have to add at the end and replace XXXxXXX@60 with your resolution. If the picture isn't centered, too big or you have borders, you can add values to `margin_left` `margin_right` `margin_top` or `margin_bottom`. Positive values shrink the picture to this side and negative ones will extend it.  

video=Composite-1:XXXxXXX@60,margin_left=0,margin_right=0,margin_top=0,margin_bottom=0

``` 
```

-----

## 💡 Troubleshooting

| Issue | Cause | Fix |
| :--- | :--- | :--- |
| **`OSError: [Errno 19] No such device`** | `uinput` kernel module is not loaded. | Ensure Step 3 (Permissions) was run. Verify with `ls -l /dev/uinput`. Reboot. |
| **Permission denied errors** | Service user cannot access files. | Run `sudo chown -R pi:pi /home/pi/rns-e_can` (and other project folders). |
| **Services fail (`code=exited, status=1`)** | Python syntax error or missing library. | Check logs: `journalctl -u can_handler.service -f` |
| **"Network is down"** | CAN interface failed to start. | Check `/boot/firmware/config.txt`. Verify oscillator frequency matches your HAT. Run `systemctl status systemd-networkd`. |
