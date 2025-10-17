#!/bin/bash
#v1.1.0
# ==============================================================================
# RNS-E CAN Bus Integration - Automatic Installation Script for Hudiy
# ==============================================================================
# This script is designed to be run from its subfolder (e.g., /installer).
# It will locate the project root and set up all files and services correctly.
# ==============================================================================

# --- Configuration ---
USERNAME="pi"
USER_HOME="/home/$USERNAME"

# --- Color Definitions ---
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

# --- Helper Functions ---
echo_green() { echo -e "\n${GREEN}$1${NC}"; }
echo_yellow() { echo -e "${YELLOW}$1${NC}"; }
echo_red() { echo -e "${RED}$1${NC}"; }

# --- Script Functions ---

check_root() {
    if [ "$EUID" -ne 0 ]; then
        echo_red "❌ This script must be run as root. Please use 'sudo ./install.sh'"
        exit 1
    fi
}

# --- Main Execution ---
check_root

# MODIFIED: Determine the project's root directory (one level up)
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &> /dev/null && pwd)
PROJECT_ROOT=$(dirname "$SCRIPT_DIR")

echo_green "▶ Step 1: Installing System Dependencies..."
apt-get update
apt-get install -y git python3-pip can-utils python3-can python3-serial python3-tz python3-unidecode python3-zmq python3-aiozmq python3-uinput
echo "System dependencies installed."

echo_green "▶ Step 2: Configuring Device Permissions..."
usermod -a -G input "$USERNAME"
echo 'uinput' | tee /etc/modules-load.d/uinput.conf > /dev/null
cat <<EOF > /etc/udev/rules.d/99-uinput.rules
KERNEL=="uinput", MODE="0660", GROUP="input", OPTIONS+="static_node=uinput"
EOF
echo "Permissions configured."

echo_green "▶ Step 3: Configuring CAN HAT..."
BOOT_CONFIG_PATH="/boot/firmware/config.txt"
if grep -q "dtoverlay=mcp2515-can0" "$BOOT_CONFIG_PATH"; then
    echo_yellow "CAN HAT configuration already exists. Skipping."
else
    read -p "Enter your CAN HAT's oscillator frequency in Hz (e.g., 12000000): " OSCILLATOR
    if ! [[ "$OSCILLATOR" =~ ^[0-9]+$ ]]; then echo_red "Invalid input. Aborting."; exit 1; fi
    echo "Adding CAN HAT configuration to $BOOT_CONFIG_PATH..."
    {
        echo ""
        echo "# --- RNS-E Pi Control CAN HAT ---"
        echo "dtparam=spi=on"
        echo "dtoverlay=mcp2515-can0,oscillator=$OSCILLATOR,interrupt=25,spimaxfrequency=1000000"
    } >> "$BOOT_CONFIG_PATH"
fi

echo_green "▶ Step 4: Setting up RAM Disks..."
mkdir -p /var/log/rnse_control /run/rnse_control
chown "$USERNAME:$USERNAME" /var/log/rnse_control /run/rnse_control
if ! grep -q "/var/log/rnse_control" /etc/fstab; then
    echo 'tmpfs   /var/log/rnse_control   tmpfs   defaults,noatime,nosuid,nodev,uid=pi,gid=pi,size=16m   0 0' >> /etc/fstab
fi
if ! grep -q "/run/rnse_control" /etc/fstab; then
    echo 'tmpfs   /run/rnse_control       tmpfs   defaults,noatime,nosuid,uid=pi,gid=pi,mode=0755,size=2m    0 0' >> /etc/fstab
fi
echo "RAM disks configured."

echo_green "▶ Step 5: Setting Up Project Files and Configuration..."
echo "Setting ownership of project files in $PROJECT_ROOT..."
chown -R "$USERNAME:$USERNAME" "$PROJECT_ROOT"

CONFIG_FILE="$USER_HOME/config.json"
CONFIG_TEMPLATE="$PROJECT_ROOT/config.json"

if [ -f "$CONFIG_FILE" ]; then
    echo_yellow "Configuration file $CONFIG_FILE already exists. Skipping interactive setup."
else
    echo "Copying config.json template to $USER_HOME..."
    cp "$CONFIG_TEMPLATE" "$CONFIG_FILE"
    chown "$USERNAME:$USERNAME" "$CONFIG_FILE"
    
    echo_yellow "\n--- Basic Configuration ---"
    read -p "Enter your time zone (e.g., Europe/Berlin): " TIMEZONE
    sed -i "s|\"car_time_zone\": \".*\"|\"car_time_zone\": \"$TIMEZONE\"|g" "$CONFIG_FILE"
    read -p "Enable TV Tuner Simulation? (Required for video) (Y/n): " TV_SIM
    [[ "$TV_SIM" =~ ^[nN]$ ]] && sed -i 's|\"tv_simulation\": { \"enabled\": true|\"tv_simulation\": { \"enabled\": false|g' "$CONFIG_FILE"
    read -p "Do you have a physical light sensor? (y/N): " LIGHT_SENSOR
    [[ "$LIGHT_SENSOR" =~ ^[yY]$ ]] && sed -i 's|\"light_sensor_installed\": false|\"light_sensor_installed\": true|g' "$CONFIG_FILE"
    
    echo "Basic configuration applied. Removing template..."
    rm "$CONFIG_TEMPLATE"
fi

echo_green "▶ Step 6: Creating Systemd Services..."
# MODIFIED: All ExecStart and WorkingDirectory paths now point to the project root.
cat <<EOF > /etc/systemd/system/configure-can0.service
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
EOF

cat <<EOF > /etc/systemd/system/can-handler.service
[Unit]
Description=RNS-E CAN-Bus Handler
Requires=configure-can0.service
After=configure-can0.service
[Service]
User=$USERNAME
WorkingDirectory=$PROJECT_ROOT
ExecStart=/usr/bin/python3 $PROJECT_ROOT/can_handler.py
Restart=on-failure
RestartSec=5
[Install]
WantedBy=multi-user.target
EOF

cat <<EOF > /etc/systemd/system/can-base-function.service
[Unit]
Description=RNS-E CAN-Bus Base Functionality
Requires=can-handler.service
After=can-handler.service
BindsTo=can-handler.service
[Service]
ExecStart=/usr/bin/python3 $PROJECT_ROOT/can_base_function.py
WorkingDirectory=$PROJECT_ROOT
User=$USERNAME
Restart=on-failure
RestartSec=5
[Install]
WantedBy=multi-user.target
EOF

cat <<EOF > /etc/systemd/system/can-keyboard-control.service
[Unit]
Description=RNS-E CAN-Bus Keyboard Simulation
Requires=can-handler.service
After=can-handler.service
BindsTo=can-handler.service
[Service]
ExecStart=/usr/bin/python3 $PROJECT_ROOT/can_keyboard_control.py
WorkingDirectory=$PROJECT_ROOT
Restart=on-failure
RestartSec=3
User=$USERNAME
Group=input
[Install]
WantedBy=multi-user.target
EOF

cat <<EOF > /etc/systemd/system/can-fis-writer.service
[Unit]
Description=RNS-E FIS Display Writer
Requires=can-handler.service
After=can-handler.service
BindsTo=can-handler.service
[Service]
ExecStart=/usr/bin/python3 $PROJECT_ROOT/can_fis_writer.py
WorkingDirectory=$PROJECT_ROOT
Restart=on-failure
RestartSec=10
User=$USERNAME
[Install]
WantedBy=multi-user.target
EOF

echo "Systemd service files created."

echo_green "▶ Step 7: Finalizing Setup..."
systemctl daemon-reload
systemctl enable configure-can0.service can-handler.service can-base-function.service can-keyboard-control.service can-fis-writer.service
echo_green "✅ Installation complete!"
read -p "A reboot is required to apply all changes. Reboot now? (y/N) " choice
if [[ "$choice" =~ ^[yY]$ ]]; then echo "Rebooting..."; reboot; else echo "Please reboot manually."; fi

exit 0
