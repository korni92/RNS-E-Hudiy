#!/usr/bin/env python3
#
# can_logger_savvy.py
#
# Interactive CAN logger for manual start via SSH/PuTTY.
# Saves data to a SavvyCAN compatible CSV file.
# IMPORTANT: Always stop with CTRL+C to save the file correctly.
#

import can
import csv
import time
import os
from datetime import datetime

# --- Configuration ---
CAN_INTERFACE = 'can0'
LOG_DIRECTORY = '/var/log/rnse_control'

def main():
    """The main function of the manual logger."""
    # Ensure the log directory exists
    if not os.path.exists(LOG_DIRECTORY):
        try:
            os.makedirs(LOG_DIRECTORY)
        except OSError as e:
            print(f"FATAL: Could not create log directory: {e}")
            return

    # Create filename with timestamp
    timestamp_str = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
    log_file_path = os.path.join(LOG_DIRECTORY, f"can_log_savvy_{timestamp_str}.csv")

    print("="*50)
    print("CAN-Bus Logger (SavvyCAN Format)")
    print(f"Interface: {CAN_INTERFACE}")
    print(f"Log file: {log_file_path}")
    print("\n>>> Logger running. Press CTRL+C to stop recording and save the file. <<<")
    print("="*50)

    bus = None
    msg_count = 0
    try:
        bus = can.interface.Bus(channel=CAN_INTERFACE, bustype='socketcan')
        
        with open(log_file_path, 'w', newline='') as csvfile:
            writer = csv.writer(csvfile)
            
            # Write SavvyCAN specific header
            writer.writerow([
                'Time Stamp', 'ID', 'Extended', 'Dir', 'Bus', 'LEN', 
                'D1', 'D2', 'D3', 'D4', 'D5', 'D6', 'D7', 'D8'
            ])
            
            last_print_time = time.time()

            while True:
                # Receive message with a short timeout
                msg = bus.recv(timeout=0.1)
                
                if msg is not None:
                    # 1. Prepare Timestamp (Microseconds)
                    # msg.timestamp is usually epoch seconds (float). 
                    # We convert to microseconds int.
                    ts_micros = int(msg.timestamp * 1000000)

                    # 2. Prepare ID (Hex, 8 chars padded)
                    arb_id = f"{msg.arbitration_id:08X}"

                    # 3. Extended Flag (lowercase string)
                    extended = 'true' if msg.is_extended_id else 'false'

                    # 4. Prepare Data Bytes
                    # We need exactly 8 columns for data.
                    # We convert present bytes to Hex, and leave the rest empty.
                    data_columns = []
                    for i in range(8):
                        if i < msg.dlc:
                            data_columns.append(f"{msg.data[i]:02X}")
                        else:
                            data_columns.append("") # Empty string for unused bytes

                    # Construct the row
                    row = [
                        ts_micros,      # Time Stamp
                        arb_id,         # ID
                        extended,       # Extended
                        'Rx',           # Dir
                        '0',            # Bus (Hardcoded 0)
                        msg.dlc,        # LEN
                    ] + data_columns    # D1..D8

                    writer.writerow(row)
                    msg_count += 1

                # Give feedback every 2 seconds or every 500 messages
                current_time = time.time()
                if current_time - last_print_time >= 2.0 or (msg_count > 0 and msg_count % 500 == 0):
                    print(f"\r>>> {msg_count} messages logged... (CTRL+C to stop)", end="")
                    last_print_time = current_time

    except KeyboardInterrupt:
        print("\n\n>>> CTRL+C detected. Stopping recording... <<<")

    except can.CanError as e:
        print(f"\nA CAN error occurred: {e}")
    except Exception as e:
        print(f"\nAn unexpected error occurred: {e}")
    finally:
        if bus:
            bus.shutdown()
        print(f"Recording stopped. {msg_count} messages were saved in the following file:")
        print(log_file_path)
        print("="*50)

if __name__ == '__main__':
    main()
