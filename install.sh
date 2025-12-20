#!/bin/bash
# ==============================================================================
# RNS-E Hudiy Integration - Automated Installer (v1.1)
# ==============================================================================

export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

# Ensure script is run as root
if [ "$EUID" -ne 0 ]; then
  echo "? Please run as root (use sudo)"
  exit 1
fi

# --- CONFIGURATION & DETECTION ---

SYSTEMCTL=$(command -v systemctl || echo "/usr/bin/systemctl")
REBOOT=$(command -v reboot || echo "/usr/sbin/reboot")
IP_CMD=$(command -v ip || echo "/sbin/ip")
WGET_CMD=$(command -v wget || echo "/usr/bin/wget")
GIT_CMD=$(command -v git || echo "/usr/bin/git")

# Detect Real User
REAL_USER="${SUDO_USER:-$USER}"
if [ "$REAL_USER" = "root" ]; then
    REAL_HOME="/root"
else
    REAL_HOME="/home/$REAL_USER"
fi

REPO_URL="https://github.com/korni92/RNS-E-Hudiy.git"
BRANCH="main"

# Define Config Paths
CONFIG_TXT="/boot/firmware/config.txt"
[ ! -f "$CONFIG_TXT" ] && CONFIG_TXT="/boot/config.txt"
CMDLINE_TXT="/boot/firmware/cmdline.txt"
[ ! -f "$CMDLINE_TXT" ] && CMDLINE_TXT="/boot/cmdline.txt"

echo "==================================================="
echo "   RNS-E Hudiy Integration Installer (v1.3)"
echo "==================================================="
echo "   Target User: $REAL_USER"
echo "   Target Home: $REAL_HOME"
echo "   Config Path: $CONFIG_TXT"
echo "==================================================="

# ------------------------------------------------------------------------------
# 1. System Update & Dependencies
# ------------------------------------------------------------------------------
echo "? Step 1: Installing System Dependencies..."
apt-get update
apt-get install -y git python3-pip can-utils python3-can python3-serial \
    python3-tz python3-unidecode python3-zmq python3-aiozmq python3-uinput \
    python3-protobuf python3-full python3-venv protobuf-compiler

echo "   Checking websocket-client..."
if dpkg -s python3-websocket-client &> /dev/null || dpkg -s python3-websocket &> /dev/null; then
    echo "   - Installed via apt."
else
    echo "   - Not found in apt, attempting pip install..."
    pip3 install websocket-client --break-system-packages &> /dev/null
fi
echo "? Dependencies installed."

# ------------------------------------------------------------------------------
# 2. Download & Install Project Files
# ------------------------------------------------------------------------------
echo "? Step 2: Downloading Project Files..."

# Create a temporary directory for cloning
TEMP_DIR=$(mktemp -d)
echo "   Cloning repository to temporary folder..."
$GIT_CMD clone -b $BRANCH --depth 1 $REPO_URL "$TEMP_DIR"

echo "   Installing to $REAL_HOME..."

# Helper to move folder if it doesn't exist
install_folder() {
    FOLDER=$1
    if [ -d "$REAL_HOME/$FOLDER" ]; then
        echo "   - Folder $FOLDER already exists. Updating contents..."
        cp -r "$TEMP_DIR/$FOLDER/"* "$REAL_HOME/$FOLDER/"
    else
        echo "   - Installing $FOLDER..."
        cp -r "$TEMP_DIR/$FOLDER" "$REAL_HOME/"
    fi
}

# Install Core Folders
install_folder "rns-e_can"
install_folder "hudiy_client"
install_folder "dis_client"

# Install Config (Only if missing)
if [ ! -f "$REAL_HOME/config.json" ]; then
    echo "   - Installing default config.json..."
    cp "$TEMP_DIR/config.json" "$REAL_HOME/"
else
    echo "   - config.json exists, keeping your version."
fi

# Cleanup
echo "   Removing temporary files..."
rm -rf "$TEMP_DIR"

# REMOVE UNWANTED FILES/FOLDERS (Explicit Cleanup)
echo "   Removing tools, updater, and READMEs..."
rm -rf "$REAL_HOME/tools"
rm -rf "$REAL_HOME/updater"
find "$REAL_HOME" -name "README.md" -type f -delete

# Fix Permissions
echo "   Setting ownership to $REAL_USER..."
chown -R $REAL_USER:$REAL_USER "$REAL_HOME/rns-e_can"
chown -R $REAL_USER:$REAL_USER "$REAL_HOME/hudiy_client"
chown -R $REAL_USER:$REAL_USER "$REAL_HOME/dis_client"
chown $REAL_USER:$REAL_USER "$REAL_HOME/config.json"

echo "? Project files installed and cleaned."

# ------------------------------------------------------------------------------
# 3. Configure Device Permissions (uinput)
# ------------------------------------------------------------------------------
echo "? Step 3: Configuring Device Permissions..."

usermod -a -G input $REAL_USER
echo 'uinput' | tee /etc/modules-load.d/uinput.conf > /dev/null
echo 'KERNEL=="uinput", MODE="0660", GROUP="input", OPTIONS+="static_node=uinput"' | tee /etc/udev/rules.d/99-uinput.rules > /dev/null

udevadm control --reload-rules
udevadm trigger

echo "? Permissions configured."

# ------------------------------------------------------------------------------
# 4. Protobuf Setup (Fixed URLs)
# ------------------------------------------------------------------------------
echo "? Step 4: Setting up Protobuf..."

PROTO_DIR="$REAL_HOME/hudiy_client/api_files/common"
mkdir -p "$PROTO_DIR"
cd "$PROTO_DIR" || exit 1

# Download API dependencies (Updated URLs for Client/Message)
echo "   Downloading dependencies..."
$WGET_CMD -q -N https://raw.githubusercontent.com/wiboma/hudiy/main/api/Api.proto
$WGET_CMD -q -N https://raw.githubusercontent.com/wiboma/hudiy/main/examples/api/python/common/Client.py
$WGET_CMD -q -N https://raw.githubusercontent.com/wiboma/hudiy/main/examples/api/python/common/Message.py

# Verify downloads
if [ ! -s "Client.py" ] || [ ! -s "Message.py" ]; then
    echo "? ERROR: Failed to download Client.py or Message.py."
    echo "   Attempting to continue, but Hudiy API may fail."
else
    echo "   Dependencies downloaded successfully."
fi

# Compile
if [ -x "$(command -v protoc)" ]; then
    protoc --python_out=. Api.proto
    chown -R $REAL_USER:$REAL_USER "$REAL_HOME/hudiy_client/api_files"
    echo "? Protobuf code generated."
else
    echo "? ERROR: 'protoc' command not found."
fi

# ------------------------------------------------------------------------------
# 5. Configure CAN Interface (Hardware)
# ------------------------------------------------------------------------------
echo "? Step 5: Configuring CAN Interface Hardware..."

# Ensure SPI is enabled first (Uncomment dtparam=spi=on)
if grep -q "#dtparam=spi=on" "$CONFIG_TXT"; then
    echo "   Enabling SPI (uncommenting in config.txt)..."
    sed -i 's/#dtparam=spi=on/dtparam=spi=on/g' "$CONFIG_TXT"
elif ! grep -q "dtparam=spi=on" "$CONFIG_TXT"; then
    echo "   Enabling SPI (appending to config.txt)..."
    echo "dtparam=spi=on" >> "$CONFIG_TXT"
else
    echo "   SPI already enabled."
fi

if grep -q "dtoverlay=mcp2515-can0" "$CONFIG_TXT"; then
    echo "   CAN hardware overlay found in $CONFIG_TXT."
else
    echo ""
    echo "   Please select your CAN HAT Oscillator Frequency:"
    echo "   1) 8 MHz"
    echo "   2) 12 MHz (Most common for RNS-E HATs)"
    echo "   3) 16 MHz"
    echo "   4) Custom"
    read -p "   Enter choice [1-4]: " freq_choice

    case $freq_choice in
        1) OSC_FREQ=8000000 ;;
        2) OSC_FREQ=12000000 ;;
        3) OSC_FREQ=16000000 ;;
        4) read -p "   Enter frequency in Hz (e.g., 12000000): " OSC_FREQ ;;
        *) OSC_FREQ=12000000; echo "   Defaulting to 12 MHz" ;;
    esac

    read -p "   Enter Interrupt Pin (default 25): " INT_PIN
    INT_PIN=${INT_PIN:-25}

    cat <<EOF >> "$CONFIG_TXT"

# --- RNS-E Pi Control CAN HAT ---
dtoverlay=mcp2515-can0,oscillator=$OSC_FREQ,interrupt=$INT_PIN,spimaxfrequency=1000000
EOF
fi

# ------------------------------------------------------------------------------
# 6. Create RAM Disks
# ------------------------------------------------------------------------------
echo "? Step 6: Setting up RAM Disks..."

mkdir -p /var/log/rnse_control /run/rnse_control
chown $REAL_USER:$REAL_USER /var/log/rnse_control /run/rnse_control

if grep -q "rnse_control" /etc/fstab; then
    echo "   RAM disks already in fstab."
else
    cat <<EOF >> /etc/fstab
tmpfs   /var/log/rnse_control   tmpfs   defaults,noatime,nosuid,nodev,uid=$REAL_USER,gid=$REAL_USER,size=16m   0 0
tmpfs   /run/rnse_control       tmpfs   defaults,noatime,nosuid,uid=$REAL_USER,gid=$REAL_USER,mode=0755,size=2m     0 0
EOF
fi
mount -a

# ------------------------------------------------------------------------------
# 7. Install Systemd Services (Networkd Method)
# ------------------------------------------------------------------------------
echo "? Step 7: Installing Systemd Services..."

# --- A: NETWORK CONFIG ---
echo "   Configuring systemd-networkd for can0..."
mkdir -p /etc/systemd/network
cat <<EOF > /etc/systemd/network/80-can.network
[Match]
Name=can0

[CAN]
BitRate=100K
RestartSec=100ms
EOF

# --- B: SERVICE FILES ---
write_service() {
    NAME=$1
    CONTENT=$2
    # FIX: Renamed variable from PATH to SERVICE_PATH to avoid overwriting system PATH
    SERVICE_PATH="/etc/systemd/system/$NAME"
    echo "   Writing $NAME..."
    echo "$CONTENT" > "$SERVICE_PATH"
}

# 1. can_handler
write_service "can_handler.service" "[Unit]
Description=RNS-E CAN-Bus Handler
BindsTo=sys-subsystem-net-devices-can0.device
After=sys-subsystem-net-devices-can0.device

[Service]
User=${REAL_USER}
Group=${REAL_USER}
WorkingDirectory=${REAL_HOME}/rns-e_can
Environment=PYTHONUNBUFFERED=1
ExecStart=/usr/bin/python3 ${REAL_HOME}/rns-e_can/can_handler.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target"

# 2. can_base_function
write_service "can_base_function.service" "[Unit]
Description=RNS-E CAN-Bus Base Functionality
Requires=can_handler.service
After=can_handler.service
BindsTo=can_handler.service

[Service]
ExecStart=/usr/bin/python3 ${REAL_HOME}/rns-e_can/can_base_function.py
WorkingDirectory=${REAL_HOME}/rns-e_can
User=${REAL_USER}
Group=${REAL_USER}
Environment=PYTHONUNBUFFERED=1
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target"

# 3. can_keyboard_control
write_service "can_keyboard_control.service" "[Unit]
Description=RNS-E CAN-Bus Keyboard Simulation
Wants=can_handler.service
After=can_handler.service

[Service]
ExecStart=/usr/bin/python3 ${REAL_HOME}/rns-e_can/can_keyboard_control.py
WorkingDirectory=${REAL_HOME}/rns-e_can
Environment=PYTHONUNBUFFERED=1
Restart=always
RestartSec=3
User=${REAL_USER}
Group=input

[Install]
WantedBy=multi-user.target"

# 4. dark_mode_api
write_service "dark_mode_api.service" "[Unit]
Description=Hudiy Dark Mode CAN Bus Service
Requires=can_handler.service
After=can_handler.service

[Service]
User=${REAL_USER}
Group=${REAL_USER}
WorkingDirectory=${REAL_HOME}/hudiy_client
Environment=PYTHONUNBUFFERED=1
ExecStart=/usr/bin/python3 ${REAL_HOME}/hudiy_client/dark_mode_api.py
Restart=always
RestartSec=5s

[Install]
WantedBy=multi-user.target"

# 5. hudiy_data_api
write_service "hudiy_data_api.service" "[Unit]
Description=Hudiy Data Extractor
Requires=can_handler.service
After=can_handler.service

[Service]
User=${REAL_USER}
Group=${REAL_USER}
WorkingDirectory=${REAL_HOME}/hudiy_client
Environment=PYTHONUNBUFFERED=1
ExecStart=/usr/bin/python3 ${REAL_HOME}/hudiy_client/hudiy_data.py
Restart=always
RestartSec=5s

[Install]
WantedBy=multi-user.target"

# 6. dis_service (DELAYED 30s)
write_service "dis_service.service" "[Unit]
Description=DIS CAN Driver
Requires=can_handler.service
After=can_handler.service
BindsTo=can_handler.service

[Service]
ExecStartPre=/bin/sleep 30
ExecStart=/usr/bin/python3 ${REAL_HOME}/dis_client/dis_service.py
WorkingDirectory=${REAL_HOME}/dis_client
Environment=PYTHONUNBUFFERED=1
Restart=always
RestartSec=10
User=${REAL_USER}
Group=input

[Install]
WantedBy=multi-user.target"

# 7. dis_display (DELAYED 15s after dis_service)
write_service "dis_display.service" "[Unit]
Description=DIS Menu Structure
Requires=dis_service.service
After=dis_service.service
BindsTo=dis_service.service

[Service]
ExecStartPre=/bin/sleep 15
ExecStart=/usr/bin/python3 ${REAL_HOME}/dis_client/dis_display.py
WorkingDirectory=${REAL_HOME}/dis_client
Environment=PYTHONUNBUFFERED=1
Restart=always
RestartSec=10
User=${REAL_USER}
Group=input

[Install]
WantedBy=multi-user.target"

# --- Clean up old service ---
if [ -f "/etc/systemd/system/configure-can0.service" ]; then
    $SYSTEMCTL disable configure-can0.service 2>/dev/null
    rm /etc/systemd/system/configure-can0.service
fi

echo "   Reloading systemd daemon..."
$SYSTEMCTL daemon-reload

echo "   Enabling systemd-networkd..."
$SYSTEMCTL enable --now systemd-networkd

echo "   Enabling and Starting Application Services..."
$SYSTEMCTL enable --now can_handler.service can_base_function.service \
                        can_keyboard_control.service dark_mode_api.service hudiy_data_api.service

# Start delayed services non-blocking
$SYSTEMCTL enable --now --no-block dis_service.service dis_display.service

echo "? Services installed, network configured, and started."

# ------------------------------------------------------------------------------
# 8. Configure Composite Video (Optional)
# ------------------------------------------------------------------------------
echo "? Step 8: Configure Composite Video (RNS-E TV-Out)..."
echo "   Do you want to configure Composite Video output for the RNS-E screen?"
read -p "   (y/n): " composite_choice

if [[ "$composite_choice" =~ ^[Yy]$ ]]; then
    echo ""
    echo "   Which RNS-E version do you have?"
    echo "   1) RNS-E 192 (CD/TV button) -> 480x234"
    echo "   2) RNS-E 193 (MEDIA button) -> 800x480"
    read -p "   Enter choice [1-2]: " rnse_version

    case $rnse_version in
        1)
            CVT_MODE="480 234 60 6 0 0 0"
            CMDLINE_RES="480x234"
            echo "   -> Selected 192 (480x234)"
            ;;
        2)
            CVT_MODE="800 480 60 6 0 0 0"
            CMDLINE_RES="800x480"
            echo "   -> Selected 193 (800x480)"
            ;;
        *)
            CVT_MODE="800 480 60 6 0 0 0"
            CMDLINE_RES="800x480"
            echo "   -> Invalid choice. Defaulting to 193 (800x480)."
            ;;
    esac

    # 1. Edit config.txt
    echo "   Modifying $CONFIG_TXT..."
    cp "$CONFIG_TXT" "$CONFIG_TXT.bak"
    
    # We use sed to find the KMS line and REPLACE it with the FKMS line.
    # This handles the replacement inline without relying on unreliable grep checks.
    # If standard KMS is found, we comment it out and add FKMS below it.
    if grep -q "dtoverlay=vc4-kms-v3d" "$CONFIG_TXT"; then
        # Disable KMS, Add FKMS
        sed -i 's/^dtoverlay=vc4-kms-v3d/#dtoverlay=vc4-kms-v3d\ndtoverlay=vc4-fkms-v3d,composite=1/g' "$CONFIG_TXT"
    elif grep -q "dtoverlay=vc4-fkms-v3d" "$CONFIG_TXT"; then
         # Ensure composite=1 is present
         if ! grep -q "composite=1" "$CONFIG_TXT"; then
            sed -i 's/dtoverlay=vc4-fkms-v3d/dtoverlay=vc4-fkms-v3d,composite=1/g' "$CONFIG_TXT"
         fi
    else
         # If neither exists, append it
         echo "dtoverlay=vc4-fkms-v3d,composite=1" >> "$CONFIG_TXT"
    fi

    # Append the video settings block
    cat <<EOF >> "$CONFIG_TXT"

# --- RNS-E Composite Video Settings ---
# Force HDMI driver 2 for audio compatibility (standard DMT/custom mode practice)
hdmi_drive=2
hdmi_ignore_hotplug=1
hdmi_cvt=$CVT_MODE
hdmi_group=2
hdmi_mode=87
enable_tvout=1
# overscan_scale=1
# overscan_bottom=0
# overscan_top=0 
# overscan_left=0
# overscan_right=0
EOF

    # 2. Edit cmdline.txt - SAFER APPEND METHOD
    echo "   Modifying $CMDLINE_TXT..."
    cp "$CMDLINE_TXT" "$CMDLINE_TXT.bak"

    # Remove any existing Composite arguments to avoid duplicates
    sed -i 's/ video=Composite-1[^ ]*//g' "$CMDLINE_TXT"

    # Safely append to the end of the line using sed substitute
    # s/$/ text/ adds ' text' to the end of the line.
    sed -i "s/$/ video=Composite-1:${CMDLINE_RES}@60,margin_left=0,margin_right=0,margin_top=0,margin_bottom=0/" "$CMDLINE_TXT"

    echo "? Composite Video configured."
else
    echo "   Skipping Composite Video configuration."
fi


echo ""
echo "==================================================="
echo "   Installation Complete!"
echo "==================================================="
echo "A reboot is recommended."
read -p "Do you want to reboot now? (y/n): " reboot_choice
if [[ "$reboot_choice" =~ ^[Yy]$ ]]; then
    $REBOOT
else
    echo "Services are running. Check with: sudo systemctl status can_handler"
fi
