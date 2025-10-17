#!/bin/bash
#v1.1.0
# ==============================================================================
# RNS-E CAN Bus Integration - Automatic Update Script
# ==============================================================================
# This script is designed to be run from its subfolder (e.g., /updater).
# It will locate the project root and update files in their correct locations.
# ==============================================================================

# --- Configuration ---
REPO="korni92/RNS-E-Hudiy"
BRANCH="main"
BASE_URL="https://raw.githubusercontent.com/$REPO/$BRANCH"

# MODIFIED: Define the subfolders for installer and updater
INSTALLER_SUBFOLDER="installer"
UPDATER_SUBFOLDER="updater"

# MODIFIED: List of files to check, now relative to the project root
FILES_TO_CHECK=(
    "can_base_function.py"
    "can_fis_writer.py"
    "can_handler.py"
    "can_keyboard_control.py"
    "$INSTALLER_SUBFOLDER/install.sh"
)

# --- (No changes to helper functions) ---
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
echo_green() { echo -e "\n${GREEN}$1${NC}"; }
echo_yellow() { echo -e "${YELLOW}$1${NC}"; }
echo_red() { echo -e "${RED}$1${NC}"; }

get_local_version() { grep "^#v" "$1" | head -n 1 | sed 's/#v//'; }
get_remote_version() { curl -s "$1" | grep "^#v" | head -n 1 | sed 's/#v//'; }
version_gt() { test "$(printf '%s\n' "$1" "$2" | sort -V | head -n 1)" != "$1"; }

# --- Main Script Logic ---

if [ "$EUID" -ne 0 ]; then echo_red "❌ Run with 'sudo ./update.sh'"; exit 1; fi

# MODIFIED: Determine project root relative to this script
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &> /dev/null && pwd)
PROJECT_ROOT=$(dirname "$SCRIPT_DIR")

# --- Self-Update Mechanism ---
echo_green "▶ Step 1: Checking for updates to the updater script..."
SELF_NAME=$(basename "$0")
REMOTE_URL_SELF="$BASE_URL/$UPDATER_SUBFOLDER/$SELF_NAME" # MODIFIED: Use subfolder path

REMOTE_VERSION_SELF=$(get_remote_version "$REMOTE_URL_SELF")
LOCAL_VERSION_SELF=$(get_local_version "$0")

if [ -z "$REMOTE_VERSION_SELF" ]; then echo_red "Could not fetch remote version for $SELF_NAME. Check internet."; exit 1; fi

if version_gt "$REMOTE_VERSION_SELF" "$LOCAL_VERSION_SELF"; then
    echo_yellow "Updater is outdated (v$LOCAL_VERSION_SELF -> v$REMOTE_VERSION_SELF). Downloading..."
    if curl -s -o "$SELF_NAME.tmp" "$REMOTE_URL_SELF"; then
        chmod +x "$SELF_NAME.tmp"
        mv "$SELF_NAME.tmp" "$SELF_NAME"
        echo_green "✅ Updater updated. Re-running..."
        echo "------------------------------------------------------------------"
        exec "./$SELF_NAME"
    else
        echo_red "Failed to download new updater. Aborting."; exit 1
    fi
else
    echo "Updater is up to date (v$LOCAL_VERSION_SELF)."
fi

# --- Check Project File Updates ---
echo_green "▶ Step 2: Checking for project file updates..."
NEEDS_RESTART=false

# MODIFIED: Change directory to project root to simplify file operations
cd "$PROJECT_ROOT" || { echo_red "Could not navigate to project root. Aborting."; exit 1; }

for FILE in "${FILES_TO_CHECK[@]}"; do
    if [ ! -f "$FILE" ]; then echo_yellow "File '$FILE' not found locally. Skipping."; continue; fi
    
    REMOTE_URL="$BASE_URL/$FILE"
    REMOTE_VERSION=$(get_remote_version "$REMOTE_URL")
    LOCAL_VERSION=$(get_local_version "$FILE")

    if [ -z "$REMOTE_VERSION" ]; then echo_red "Could not fetch version for $FILE. Skipping."; continue; fi

    if version_gt "$REMOTE_VERSION" "$LOCAL_VERSION"; then
        echo_yellow "Found update for $FILE (v$LOCAL_VERSION -> v$REMOTE_VERSION)"
        echo "  - Backing up to $FILE.bak"
        mv "$FILE" "$FILE.bak"
        
        echo "  - Downloading new version..."
        if curl -s -o "$FILE" "$REMOTE_URL"; then
            echo_green "  - ✅ Update successful."
            if [[ "$FILE" == *.sh ]]; then chmod +x "$FILE"; fi
            if [[ "$FILE" == *.py ]]; then NEEDS_RESTART=true; fi
        else
            echo_red "  - ❌ Download failed. Restoring from backup."; mv "$FILE.bak" "$FILE"
        fi
    else
        echo "  - $FILE is up to date (v$LOCAL_VERSION)."
    fi
done

# --- Restart Services ---
if [ "$NEEDS_RESTART" = true ]; then
    echo_green "▶ Step 3: Restarting services..."
    if systemctl restart can-handler.service can-base-function.service can-keyboard-control.service can-fis-writer.service; then
        echo_green "✅ Services restarted successfully."
    else
        echo_red "❌ Failed to restart services. Check status with 'systemctl status <service-name>'."
    fi
else
    echo_green "▶ All files are up to date. No services needed to be restarted."
fi

echo_green "Update check complete."
exit 0
