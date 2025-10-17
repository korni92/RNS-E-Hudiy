# Hudiy / RNS-E CAN Bus Integration: Full Installation Guide

This guide provides a complete, step-by-step tutorial for setting up the RNS-E CAN bus integration project on a Raspberry Pi running Hudiy, Crankshaft NG, or a similar Debian-based OS. It is designed to be robust and reliable for long-term use in a vehicle.

## 🚀 Architecture Overview

The system uses a stable microservice architecture where each script runs as a separate, managed service:

  * **`can-handler.service`**: The central gateway to the CAN bus hardware.
  * **`can-base-function.service`**: Provides core functions like TV tuner simulation and time synchronization.
  * **`can-keyboard-control.service`**: Translates car button presses into virtual keyboard commands.
  * **`can-fis-writer.service`**: Writes custom text to the instrument cluster display (FIS).

-----

## 🔧 Step 1: System Preparation

These first steps ensure your system is ready for installation.

### Make Filesystem Writable

On read-only systems like Crankshaft, you must first enable write access.

```bash
sudo mount -o remount,rw /
sudo mount -o remount,rw /boot
```

### Update System Packages

Ensure your system is up-to-date.

```bash
sudo apt-get update
sudo apt-get full-upgrade -y
```

-----

## 📦 Step 2: Install All System Dependencies

Install all required packages using the system's package manager. This is the recommended method for modern Raspberry Pi OS versions (Debian Bookworm and newer) to avoid environment conflicts.

```bash
sudo apt-get install -y git python3-pip can-utils python3-can python3-serial python3-tz python3-unidecode python3-zmq python3-aiozmq python3-uinput
```

-----

## 🛡️ Step 3: Configure Device Permissions

These steps are **critical** for allowing the Python scripts to access the necessary hardware without running as root.

### Grant Virtual Keyboard Permissions

1.  **Add your user (e.g., `pi`) to the `input` group:**
    ```bash
    sudo usermod -a -G input pi
    ```
2.  **Ensure the `uinput` kernel module loads at boot:**
    ```bash
    echo 'uinput' | sudo tee /etc/modules-load.d/uinput.conf
    ```
3.  **Create a `udev` rule** to permanently set the correct permissions for the virtual keyboard device. This is the most reliable method.
    ```bash
    sudo nano /etc/udev/rules.d/99-uinput.rules
    ```
    Paste this single line into the file:
    ```
    KERNEL=="uinput", MODE="0660", GROUP="input", OPTIONS+="static_node=uinput"
    ```
    Save and exit (`Ctrl+X`, then `Y`, then `Enter`).

-----

## 📂 Step 4: Download and Place Project Files

1.  **Clone the Project from GitHub:**

    ```bash
    cd /home/pi
    git clone https://github.com/...
    ```

2.  **Set Ownership:**

    ```bash
    sudo chown -R pi:pi /home/pi/RNS-E-Pi-Control
    ```

-----

## ⚙️ Step 5: Configure the CAN Interface

This tells the Pi's operating system how to communicate with your CAN HAT.

1.  **Edit the Boot Configuration File:**

    ```bash
    sudo nano /boot/firmware/config.txt
    ```

2.  **Add the following lines** at the end of the file.

    > **⚠️ Important:** You **must** replace `12000000` with the correct oscillator frequency (in Hz) of your specific CAN HAT. Common values are `8000000`, `12000000`, or `16000000`. The interrupt pin (`25`) is also common but may vary.

    ```ini
    # --- RNS-E Pi Control CAN HAT ---
    dtparam=spi=on
    dtoverlay=mcp2515-can0,oscillator=12000000,interrupt=25,spimaxfrequency=1000000
    ```

    Save and exit.

-----

## 🧠 Step 6: Create RAM Disks for Longevity

To reduce wear and tear on your SD card, we'll create directories in RAM (`tmpfs`) for frequently written files like logs and communication sockets.

1.  **Create the Mount Points:**
    ```bash
    sudo mkdir -p /var/log/rnse_control /run/rnse_control
    sudo chown pi:pi /var/log/rnse_control /run/rnse_control
    ```
2.  **Open `/etc/fstab` to make the RAM disks permanent:**
    ```bash
    sudo nano /etc/fstab
    ```
3.  **Add these two lines** to the end of the file. They ensure the directories are created in RAM on every boot with the correct permissions for the `pi` user.
    ```
    tmpfs   /var/log/rnse_control   tmpfs   defaults,noatime,nosuid,nodev,uid=pi,gid=pi,size=16m   0 0
    tmpfs   /run/rnse_control       tmpfs   defaults,noatime,nosuid,uid=pi,gid=pi,mode=0755,size=2m    0 0
    ```
    Save and exit.

-----

## 📝 Step 7: Create the Project Configuration File

All services are controlled by a single `config.json` file.

1.  **Navigate to your project directory and open the file:**
    ```bash
    cd /home/pi/
    nano config.json
    ```
2.  **Paste the complete template from the repository** into this file.
3.  **Crucially, adjust these settings:**
      * `"can_interface"`: Should be `"can0"`.
      * `"car_time_zone"`: Set to your local time zone (e.g., `"Europe/Berlin"`).
      * Review all settings under `"features"` and enable/disable them as desired.

-----

## 🛠️ Step 8: Set Up Hardened Systemd Services

These service files ensure your scripts start in the correct order on boot and restart automatically if they fail.

1.  **File 1: `configure-can0.service`** (Sets up the hardware interface)

    ```bash
    sudo nano /etc/systemd/system/configure-can0.service
    ```

    \<details\>
    \<summary\>Click to view content\</summary\>

    ```ini
    [Unit]
    Description=Configure can0 Interface
    Wants=network.target
    After=network.target

    [Service]
    Type=oneshot
    ExecStart=/sbin/ip link set can0 up type can bitrate 100000
    RemainAfterExit=true

    [Install]
    WantedBy=multi-user.target
    ```

    \</details\>

2.  **File 2: `can-handler.service`** (The central gateway)

    ```bash
    sudo nano /etc/systemd/system/can-handler.service
    ```

    \<details\>
    \<summary\>Click to view content\</summary\>

    ```ini
    [Unit]
    Description=RNS-E CAN-Bus Handler
    Requires=configure-can0.service
    After=configure-can0.service

    [Service]
    User=pi
    Group=pi
    WorkingDirectory=/home/pi/RNS-E-Pi-Control
    ExecStart=/usr/bin/python3 /home/pi/RNS-E-Pi-Control/can_handler.py
    Restart=on-failure
    RestartSec=5

    [Install]
    WantedBy=multi-user.target
    ```

    \</details\>

3.  **File 3: `can-base-function.service`**

    ```bash
    sudo nano /etc/systemd/system/can-base-function.service
    ```

    \<details\>
    \<summary\>Click to view content\</summary\>

    ```ini
    [Unit]
    Description=RNS-E CAN-Bus Base Functionality
    Requires=can-handler.service
    After=can-handler.service
    BindsTo=can-handler.service

    [Service]
    ExecStart=/usr/bin/python3 /home/pi/RNS-E-Pi-Control/can_base_function.py
    WorkingDirectory=/home/pi/RNS-E-Pi-Control
    User=pi
    Group=pi
    Restart=on-failure
    RestartSec=5

    [Install]
    WantedBy=multi-user.target
    ```

    \</details\>

4.  **File 4: `can-keyboard-control.service`**

    ```bash
    sudo nano /etc/systemd/system/can-keyboard-control.service
    ```

    \<details\>
    \<summary\>Click to view content\</summary\>

    ```ini
    [Unit]
    Description=RNS-E CAN-Bus Keyboard Simulation (uinput)
    Requires=can-handler.service
    After=can-handler.service
    BindsTo=can-handler.service

    [Service]
    ExecStart=/usr/bin/python3 /home/pi/RNS-E-Pi-Control/can_keyboard_control.py
    WorkingDirectory=/home/pi/RNS-E-Pi-Control
    Restart=on-failure
    RestartSec=3
    User=pi
    Group=input

    [Install]
    WantedBy=multi-user.target
    ```

    \</details\>

5.  **File 5: `can-fis-writer.service`**

    ```bash
    sudo nano /etc/systemd/system/can-fis-writer.service
    ```

    \<details\>
    \<summary\>Click to view content\</summary\>

    ```ini
    [Unit]
    Description=RNS-E FIS Display Writer
    Requires=can-handler.service
    After=can-handler.service
    BindsTo=can-handler.service

    [Service]
    ExecStart=/usr/bin/python3 /home/pi/RNS-E-Pi-Control/can_fis_writer.py
    WorkingDirectory=/home/pi/RNS-E-Pi-Control
    Restart=on-failure
    RestartSec=10
    User=pi
    Group=pi

    [Install]
    WantedBy=multi-user.target
    ```

    \</details\>

-----

## 🎉 Step 9: Finalize and Reboot

Enable the new services to start on boot and reboot the system for all changes to take effect.

1.  **Reload the systemd manager to read the new service files:**
    ```bash
    sudo systemctl daemon-reload
    ```
2.  **Enable all 5 services to start on boot:**
    ```bash
    sudo systemctl enable configure-can0.service can-handler.service can-base-function.service can-keyboard-control.service can-fis-writer.service
    ```
3.  **Reboot the Raspberry Pi:**
    ```bash
    sudo reboot
    ```

After the reboot, your system is fully installed and operational\!

-----

## 💡 Usage and Troubleshooting

### Checking Service Status

To check if all services are running correctly:

```bash
sudo systemctl status configure-can0 can-handler can-base-function can-keyboard-control can-fis-writer
```

### Viewing Logs

If a service fails, its log is the best place to find the error.

```bash
# Example: Check the logs for the keyboard service
journalctl -u can-keyboard-control.service -f
```

### Common Errors

  * **`OSError: [Errno 19] No such device`** in `can-keyboard-control.service` log: The `uinput` module isn't loaded. This was fixed in Step 3, but a reboot is required for it to take effect.
  * **Permission Denied:** A file or directory has the wrong owner. Use `sudo chown -R pi:pi /path/to/dir` to fix.
  * **Service fails with `code=exited, status=1/FAILURE`:** Check the logs (`journalctl`) for a Python `Traceback`. This usually points to a missing config key or a bug in the script.
