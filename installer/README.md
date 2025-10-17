RNS-E Hudiy CAN Bus Integration

This project provides a suite of Python services to integrate a Raspberry Pi with an Audi RNS-E (or similar) CAN bus for use with Hudiy. It uses a robust microservice architecture to ensure stability and provides features like TV-Tuner simulation, time synchronization, and keyboard control from car buttons.

🚀 Automatic Installation

This is the recommended method for a fresh setup on Hudiy or a similar Debian-based OS. The installer script will handle everything from dependencies to service creation.

Prerequisites:

    A Raspberry Pi with Hudiy installed.

    A configured CAN interface HAT (e.g., PiCAN).

    SSH access or a local terminal on the Pi.

Step-by-Step Installation

    Download the Project Download the latest release .zip or .tar.gz file from this repository's Releases page.

    Copy to Your Raspberry Pi Transfer the downloaded archive to your Pi's home directory (/home/pi). You can use a tool like scp or a USB drive.

    Extract the Archive SSH into your Pi and run the following commands:
    Bash

# Navigate to your home directory
cd ~

# If you downloaded a .zip file
unzip RNS-E-Hudiy-main.zip

# Or, if you downloaded a .tar.gz file
tar -xvf RNS-E-Hudiy-main.tar.gz

Run the Installer Navigate into the new project directory, make the script executable, and run it with sudo.
Bash

# Enter the project directory (the name may vary slightly)
cd RNS-E-Hudiy-main

# Make the script executable
chmod +x install.sh

# Run the installer with root privileges
sudo ./install.sh

Follow the On-Screen Prompts The script is interactive and will ask you for:

    Your CAN HAT's oscillator frequency.

    Your local time zone (e.g., Europe/Berlin).

    Whether to enable TV Tuner Simulation (required for video).

After the script finishes, it will prompt you to reboot. Once rebooted, the system will be fully operational.
