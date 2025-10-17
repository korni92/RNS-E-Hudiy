🔄 Updating the Software

This project includes an automatic update script that fetches the latest versions of all files directly from GitHub.

How to Update

    Navigate to the Project Directory Open a terminal on your Pi and go to the project folder.
    Bash

cd /home/pi/RNS-E-Hudiy-main

Run the Update Script with sudo You must use sudo because the script needs permission to restart the system services after updating files.
Bash

    sudo ./update.sh

What the Update Script Does

The updater will automatically:

    Check for updates to itself first, and restart if a new version is found.

    Compare local and remote versions for each script.

    Create a backup of any file that is about to be updated (e.g., can_handler.py.bak).

    Download the latest version from the GitHub repository.

    Restart all relevant services if any of their files were changed.

You will see a summary of the update process. If no updates are found, it will simply let you know that your system is up to date.

💡 Troubleshooting

If any of the services fail to run, your first step should always be to check the logs.

Check Service Status

To see if all services are active and running:
Bash

sudo systemctl status configure-can0 can-handler can-base-function can-keyboard-control can-fis-writer

Look for Active: active (running) in the output for each service.

View Live Logs

This is the most powerful tool for debugging.
Bash

# Example: Check the logs for the keyboard control service in real-time
journalctl -u can-keyboard-control.service -f

Look for any [ERROR] or Traceback messages. They will usually tell you exactly what is wrong.

Common Problems

    ModuleNotFoundError: A required Python library is missing. The installer should handle this, but you can manually install packages with sudo apt install <package-name>.

    FATAL: Could not load or parse config.json: Your main configuration file at /home/pi/config.json has a syntax error (like a missing comma) or is missing a key.

    Permission Denied: A file or directory has the wrong owner. The installer should set this, but you can fix it manually with sudo chown -R pi:pi /home/pi/RNS-E-Hudiy-main.

    Keyboard Control Fails with OSError: No such device: The uinput module isn't loaded correctly. The installer configures this, but a reboot is required for all permission changes to take effect.
