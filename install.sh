#!/bin/bash
#v1.0.0
# ==============================================================================
# RNS-E CAN Bus Integration - Automatic Installation Script for Hudiy
# ==============================================================================
# This script automates the full setup process for the RNS-E Pi Control project
# on a Hudiy system. It is interactive and tailored for the Hudiy environment.
#
# It should be run from within the extracted project directory with sudo.
#
# Usage:
# 1. Extract the project archive.
# 2. cd into the project directory.
# 3. chmod +x install.sh
# 4. sudo ./install.sh
# ==============================================================================

# --- Configuration ---
USERNAME="pi" # Change if your user is not 'pi'
USER_HOME="/home/$USERNAME"
PROJECT_DIR_NAME="RNS-E-Pi-Control"

# --- Color Definitions ---
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# --- Helper Functions ---
echo_green() {
    echo -e "\n${GREEN}$1${NC}"
}
echo_yellow() {
    echo -e "${YELLOW}$1${NC}"
}
echo_red() {
    echo -e "${RED}$1${NC}"
}

# --- Script Functions ---

check_root() {
    if [ "$EUID" -ne 0 ]; then
        echo_red "❌ This script must be run as root. Please use 'sudo ./install.sh'"
        exit 1
    fi
}

install_dependencies() {
    echo_green "▶ Step 1: Installing System Dependencies..."
    apt-get update
    apt-get install -y git python3-pip can-utils python3-can python3-serial python3-tz python3-unidecode python3-zmq python3-aiozmq python3-uinput
    echo "System dependencies installed."
}

configure_permissions() {
    echo_green "▶ Step 2: Configuring Device Permissions for Virtual Keyboard..."
    echo "Adding user '$USERNAME' to the 'input' group..."
    usermod -a -G input "$USERNAME"
    
    echo "Ensuring 'uinput' module loads at boot..."
    echo 'uinput' | tee /etc/modules-load.d/uinput.conf > /dev/null

    echo "Creating udev rule for persistent device permissions..."
    cat <<EOF > /etc/udev/rules.d/99-uinput.rules
KERNEL=="uinput", MODE="0660", GROUP="input", OPTIONS+="static_node=uinput"
EOF
    echo "Permissions configured. A reboot is required for group changes to apply."
}

configure_can_hat() {
    echo_green "▶ Step 3: Configuring CAN HAT in /boot/firmware/config.txt..."
    BOOT_CONFIG_PATH="/boot/firmware/config.txt"
    
    if grep -q "dtoverlay=mcp2515-can0" "$BOOT_CONFIG_PATH"; then
        echo_yellow "CAN HAT configuration already seems to exist in $BOOT_CONFIG_PATH."
        read -p "Do you want to skip this step? (Y/n) " choice
        case "$choice" in 
          n|N )
            # Continue to the configuration part
            ;;
          * )
            echo "Skipping CAN HAT configuration."
            return
            ;;
        esac
    fi
    
    echo_yellow "Please provide your CAN HAT's oscillator frequency in Hz."
    echo_yellow "Common values are 8000000, 12000000, or 16000000."
    read -p "Enter oscillator frequency (e.g., 12000000): " OSCILLATOR

    if ! [[ "$OSCILLATOR" =~ ^[0-9]+$ ]]; then
        echo_red "Invalid input. Please enter a number. Aborting."
        exit 1
    fi

    echo "Adding CAN HAT configuration to $BOOT_CONFIG_PATH..."
    {
        echo ""
        echo "# --- RNS-E Pi Control CAN HAT ---"
        echo "dtparam=spi=on"
        echo "dtoverlay=mcp2515-can0,oscillator=$OSCILLATOR,interrupt=25,spimaxfrequency=1000000"
    } >> "$BOOT_CONFIG_PATH"
    echo "Configuration added."
}

setup_ramdisks() {
    echo_green "▶ Step 4: Setting up RAM Disks for Logs and Sockets..."
    mkdir -p /var/log/rnse_control /run/rnse_control
    chown "$USERNAME:$USERNAME" /var/log/rnse_control /run/rnse_control

    if grep -q "/var/log/rnse_control" /etc/fstab; then
        echo_yellow "RAM disk for logs already configured in /etc/fstab. Skipping."
    else
        echo "Adding log directory to /etc/fstab..."
        echo 'tmpfs   /var/log/rnse_control   tmpfs   defaults,noatime,nosuid,nodev,uid=pi,gid=pi,size=16m   0 0' >> /etc/fstab
    fi

    if grep -q "/run/rnse_control" /etc/fstab; then
        echo_yellow "RAM disk for sockets already configured in /etc/fstab. Skipping."
    else
        echo "Adding socket directory to /etc/fstab..."
        echo 'tmpfs   /run/rnse_control       tmpfs   defaults,noatime,nosuid,uid=pi,gid=pi,mode=0755,size=2m    0 0' >> /etc/fstab
    fi
    echo "RAM disks configured."
}

setup_project_files() {
    echo_green "▶ Step 5: Setting Up Project Files and Basic Configuration..."
    
    SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
    PROJECT_DIR="$SCRIPT_DIR" # The project dir is the current script dir

    echo "Setting ownership of project files..."
    chown -R "$USERNAME:$USERNAME" "$PROJECT_DIR"

    CONFIG_FILE="$USER_HOME/config.json"
    CONFIG_TEMPLATE="$PROJECT_DIR/config.json"
    
    if [ -f "$CONFIG_FILE" ]; then
        echo_yellow "Configuration file $CONFIG_FILE already exists. Skipping copy and interactive setup."
    else
        echo "Copying config.json template to $USER_HOME..."
        cp "$CONFIG_TEMPLATE" "$CONFIG_FILE"
        chown "$USERNAME:$USERNAME" "$CONFIG_FILE"
        
        # --- Interactive Configuration ---
        echo_yellow "\n--- Basic Configuration ---"
        
        # 1. Time Zone
        read -p "Enter your time zone (e.g., Europe/Berlin): " TIMEZONE
        sed -i "s|\"car_time_zone\": \".*\"|\"car_time_zone\": \"$TIMEZONE\"|g" "$CONFIG_FILE"
        
        # 2. TV Simulation
        read -p "Enable TV Tuner Simulation? (This is required for video output) (Y/n): " TV_SIM
        if [[ "$TV_SIM" =~ ^[nN]$ ]]; then
            sed -i 's|\"tv_simulation\": { \"enabled\": true|\"tv_simulation\": { \"enabled\": false|g' "$CONFIG_FILE"
        else
            sed -i 's|\"tv_simulation\": { \"enabled\": false|\"tv_simulation\": { \"enabled\": true|g' "$CONFIG_FILE"
        fi

        # 3. Light Sensor
        read -p "Do you have a physical light sensor for auto day/night mode? (y/N): " LIGHT_SENSOR
        if [[ "$LIGHT_SENSOR" =~ ^[yY]$ ]]; then
            sed -i 's|\"light_sensor_installed\": false|\"light_sensor_installed\": true|g' "$CONFIG_FILE"
        else
            sed -i 's|\"light_sensor_installed\": true|\"light_sensor_installed\": false|g' "$CONFIG_FILE"
        fi
        
        echo "Basic configuration has been applied to $CONFIG_FILE."
        echo_yellow "You can edit this file later for more advanced settings."

        # Cleanup the template file
        echo "Removing original config.json template from project directory..."
        rm "$CONFIG_TEMPLATE"
    fi
}

create_systemd_services() {
    echo_green "▶ Step 6: Creating and Hardening Systemd Services..."
    PROJECT_DIR_PATH=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )

    # Service 1: Configure CAN0
    echo "Creating configure-can0.service..."
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

    # Service 2: CAN Handler
    echo "Creating can-handler.service..."
    cat <<EOF > /etc/systemd/system/can-handler.service
[Unit]
Description=RNS-E CAN-Bus Handler
Requires=configure-can0.service
After=configure-can0.service
[Service]
User=$USERNAME
Group=$USERNAME
WorkingDirectory=$PROJECT_DIR_PATH
ExecStart=/usr/bin/python3 $PROJECT_DIR_PATH/can_handler.py
Restart=on-failure
RestartSec=5
[Install]
WantedBy=multi-user.target
EOF

    # Service 3: Base Functions
    echo "Creating can-base-function.service..."
    cat <<EOF > /etc/systemd/system/can-base-function.service
[Unit]
Description=RNS-E CAN-Bus Base Functionality
Requires=can-handler.service
After=can-handler.service
BindsTo=can-handler.service
[Service]
ExecStart=/usr/bin/python3 $PROJECT_DIR_PATH/can_base_function.py
WorkingDirectory=$PROJECT_DIR_PATH
User=$USERNAME
Group=$USERNAME
Restart=on-failure
RestartSec=5
[Install]
WantedBy=multi-user.target
EOF

    # Service 4: Keyboard Control
    echo "Creating can-keyboard-control.service..."
    cat <<EOF > /etc/systemd/system/can-keyboard-control.service
[Unit]
Description=RNS-E CAN-Bus Keyboard Simulation (uinput)
Requires=can-handler.service
After=can-handler.service
BindsTo=can-handler.service
[Service]
ExecStart=/usr/bin/python3 $PROJECT_DIR_PATH/can_keyboard_control.py
WorkingDirectory=$PROJECT_DIR_PATH
Restart=on-failure
RestartSec=3
User=$USERNAME
Group=input
[Install]
WantedBy=multi-user.target
EOF

    # Service 5: FIS Writer
    echo "Creating can-fis-writer.service..."
    cat <<EOF > /etc/systemd/system/can-fis-writer.service
[Unit]
Description=RNS-E FIS Display Writer
Requires=can-handler.service
After=can-handler.service
BindsTo=can-handler.service
[Service]
ExecStart=/usr/bin/python3 $PROJECT_DIR_PATH/can_fis_writer.py
WorkingDirectory=$PROJECT_DIR_PATH
Restart=on-failure
RestartSec=10
User=$USERNAME
Group=$USERNAME
[Install]
WantedBy=multi-user.target
EOF

    echo "Systemd service files created."
}

finalize_setup() {
    echo_green "▶ Step 7: Finalizing Setup..."
    echo "Reloading systemd manager..."
    systemctl daemon-reload
    
    echo "Enabling all services to start on boot..."
    systemctl enable configure-can0.service can-handler.service can-base-function.service can-keyboard-control.service can-fis-writer.service
    
    echo_green "✅ Installation complete!"
    echo_yellow "A reboot is required to apply all changes. After reboot: activate SPI interface via sudo raspi-config if not already done."
    read -p "Reboot now? (y/N) " choice
    case "$choice" in 
      y|Y )
        echo "Rebooting..."
        reboot
        ;;
      * )
        echo "Please reboot manually to complete the installation."
        ;;
    esac
}


# --- Main Execution ---
check_root
install_dependencies
configure_permissions
configure_can_hat
setup_ramdisks
setup_project_files
create_systemd_services
finalize_setup

exit 0
