#!/bin/bash
#v1.0.0
# ==============================================================================
# RNS-E CAN Bus Integration - Automatic Update Script
# ==============================================================================
# This script checks for updates to the project files from the official GitHub
# repository, backs up existing files, and downloads new versions.
# It also restarts the necessary systemd services after an update.
#
# Usage:
# Run from the project directory with sudo:
# sudo ./update.sh
# ==============================================================================

# --- Configuration ---
REPO="korni92/RNS-E-Hudiy"
BRANCH="main" # Or "master", depending on your repository's default branch
BASE_URL="https://raw.githubusercontent.com/$REPO/$BRANCH"

# List of files to check for updates
FILES_TO_CHECK=(
    "can_base_function.py"
    "can_fis_writer.py"
    "can_handler.py"
    "can_keyboard_control.py"
    "install.sh"
)

# --- Color Definitions ---
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

# --- Helper Functions ---
echo_green() { echo -e "${GREEN}$1${NC}"; }
echo_yellow() { echo -e "${YELLOW}$1${NC}"; }
echo_red() { echo -e "${RED}$1${NC}"; }

# --- Versioning Functions ---
get_local_version() {
    # Extracts version from the second line of a local file
    grep "^#v" "$1" | head -n 1 | sed 's/#v//'
}

get_remote_version() {
    # Extracts version from the second line of a remote file
    curl -s "$1" | grep "^#v" | head -n 1 | sed 's/#v//'
}

# Robust version comparison: returns 0 if v1 > v2
version_gt() {
    # This checks if the first version is NOT the smallest when sorted
    test "$(printf '%s\n' "$1" "$2" | sort -V | head -n 1)" != "$1"
}

# --- Main Script Logic ---

# 1. Check for Root Permissions
if [ "$EUID" -ne 0 ]; then
    echo_red "❌ This script must be run as root to restart services. Please use 'sudo ./update.sh'"
    exit 1
fi

# 2. Self-Update Mechanism
SELF_NAME=$(basename "$0")
echo_green "▶ Step 1: Checking for updates to the updater script itself..."

REMOTE_VERSION_SELF=$(get_remote_version "$BASE_URL/$SELF_NAME")
LOCAL_VERSION_SELF=$(get_local_version "$0")

if [ -z "$REMOTE_VERSION_SELF" ]; then
    echo_red "Could not fetch remote version for $SELF_NAME. Please check your internet connection."
    exit 1
fi

if version_gt "$REMOTE_VERSION_SELF" "$LOCAL_VERSION_SELF"; then
    echo_yellow "Updater is outdated (Local: v$LOCAL_VERSION_SELF, Remote: v$REMOTE_VERSION_SELF). Downloading new version..."
    # Download to a temporary file
    if curl -s -o "$SELF_NAME.tmp" "$BASE_URL/$SELF_NAME"; then
        chmod +x "$SELF_NAME.tmp"
        # Replace the old script with the new one
        mv "$SELF_NAME.tmp" "$SELF_NAME"
        echo_green "✅ Updater updated successfully. Re-running the new script..."
        echo "------------------------------------------------------------------"
        # Re-execute the new script and exit the old one
        exec "./$SELF_NAME"
    else
        echo_red "Failed to download the new updater script. Aborting."
        exit 1
    fi
else
    echo "Updater is up to date (v$LOCAL_VERSION_SELF)."
fi

# 3. Check for Project File Updates
echo_green "\n▶ Step 2: Checking for project file updates..."
NEEDS_RESTART=false

for FILE in "${FILES_TO_CHECK[@]}"; do
    if [ ! -f "$FILE" ]; then
        echo_yellow "File '$FILE' not found locally. Skipping."
        continue
    fi
    
    REMOTE_URL="$BASE_URL/$FILE"
    REMOTE_VERSION=$(get_remote_version "$REMOTE_URL")
    LOCAL_VERSION=$(get_local_version "$FILE")

    if [ -z "$REMOTE_VERSION" ]; then
        echo_red "Could not fetch version for $FILE. Skipping."
        continue
    fi

    if version_gt "$REMOTE_VERSION" "$LOCAL_VERSION"; then
        echo_yellow "Found update for $FILE (Local: v$LOCAL_VERSION -> Remote: v$REMOTE_VERSION)"
        echo "  - Backing up old version to $FILE.bak"
        mv "$FILE" "$FILE.bak"
        
        echo "  - Downloading new version..."
        if curl -s -o "$FILE" "$REMOTE_URL"; then
            echo_green "  - ✅ Update for $FILE successful."
            # Make scripts executable
            if [[ "$FILE" == *.sh ]]; then
                chmod +x "$FILE"
            fi
            # Mark that services need a restart
            if [[ "$FILE" == *.py ]]; then
                NEEDS_RESTART=true
            fi
        else
            echo_red "  - ❌ Failed to download $FILE. Restoring from backup."
            mv "$FILE.bak" "$FILE"
        fi
    else
        echo "  - $FILE is up to date (v$LOCAL_VERSION)."
    fi
done

# 4. Restart Services if Needed
if [ "$NEEDS_RESTART" = true ]; then
    echo_green "\n▶ Step 3: Restarting services..."
    if systemctl restart can-handler.service can-base-function.service can-keyboard-control.service can-fis-writer.service; then
        echo_green "✅ Services restarted successfully."
    else
        echo_red "❌ Failed to restart one or more services. Please check their status with 'systemctl status <service-name>'."
    fi
else
    echo_green "\n▶ All files are up to date. No services needed to be restarted."
fi

echo_green "\nUpdate check complete."
exit 0
