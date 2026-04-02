#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# v1.4.0
# can_base_function.py
#
# This service provides base CAN bus functionality, including TV Tuner simulation,
# Time Synchronization, Auto-Shutdown, CarPiHat Power Latching, and
# a robust Watchdog (fallback timer) for missing switch/ignition messages.

import zmq
import zmq.asyncio
import json
import time
import logging
import signal
import sys
from datetime import datetime
import pytz
from typing import Optional, List, Dict, Any
import asyncio
import subprocess
import RPi.GPIO as GPIO

# --- Global State ---
RUNNING = True
RELOAD_CONFIG = False
SYSTEM_SHUTTING_DOWN = False

CONFIG: Dict[str, Any] = {}
FEATURES: Dict[str, Any] = {}
ZMQ_CONTEXT: Optional[zmq.asyncio.Context] = None
ZMQ_PUSH_SOCKET: Optional[zmq.asyncio.Socket] = None

# --- Logging Setup ---
def setup_logging():
    log_file = '/var/log/rnse_control/can_base_function.log'
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(message)s',
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler(sys.stdout)
        ]
    )
    return logging.getLogger(__name__)

logger = setup_logging()

# --- Helper function for BCD conversion ---
def hex_to_bcd(hex_str: str) -> int:
    if not (isinstance(hex_str, str) and len(hex_str) == 2 and hex_str.isalnum()):
        raise ValueError(f"Input must be a 2-char hex string, got '{hex_str}'")
    return int(hex_str[0]) * 10 + int(hex_str[1])

# --- Hardware Setup ---
def setup_carpihat_latch(pin: int):
    """Initialize the specified GPIO pin to latch the power supply via CarPiHat."""
    logger.info(f"Initializing GPIO {pin} for CarPiHat power latch...")
    try:
        GPIO.setmode(GPIO.BCM)
        
        # Attempt to free the pin beforehand (helps with "GPIO busy" errors)
        try:
            GPIO.cleanup(pin)
        except:
            pass
        
        GPIO.setup(pin, GPIO.OUT, initial=GPIO.HIGH)
        GPIO.output(pin, 1)
        logger.info(f"GPIO {pin} successfully configured as output (latch active).")
    except Exception as e:
        logger.error(f"Failed to setup GPIO {pin}: {e}")
        logger.error("This is often caused by another process still holding the GPIO pin.")
        logger.error("Try: sudo killall python3   or reboot the Pi.")
        raise  # We want to know why startup failed

# --- State Management Class ---
class AppState:
    def __init__(self):
        self.last_time_sync_attempt_time: float = 0.0

        # CAN power status (for normal shutdown)
        self.last_kl15_status: Optional[int] = None
        self.last_kls_status: Optional[int] = None
        self.shutdown_pending: bool = False
        self.shutdown_trigger_timestamp: Optional[float] = None

        # Watchdog: last received switch/ignition message
        self.last_power_message_timestamp: float = time.time()

    def _execute_shutdown(self) -> bool:
        global SYSTEM_SHUTTING_DOWN
        SYSTEM_SHUTTING_DOWN = True
        logger.info("Executing system shutdown now...")
        shutdown_command = ["sudo", "shutdown", "-h", "now"]
        if execute_system_command(shutdown_command):
            logger.info("Shutdown command executed successfully.")
            return True
        else:
            logger.error("Shutdown command failed! Resetting shutdown state.")
            SYSTEM_SHUTTING_DOWN = False
            self.shutdown_pending = False
            self.shutdown_trigger_timestamp = None
            self.last_power_message_timestamp = time.time()
            return False

    def check_shutdown_condition(self) -> bool:
        current_time = time.time()

        # 1. Normal CAN shutdown timer
        if self.shutdown_pending and self.shutdown_trigger_timestamp:
            if current_time - self.shutdown_trigger_timestamp >= CONFIG.get('shutdown_delay', 300):
                logger.info("Normal shutdown delay reached. Shutting down system NOW.")
                return self._execute_shutdown()

        # 2. Watchdog: No switch/ignition message received
        fallback_delay = CONFIG.get('fallback_delay', 1800)
        if current_time - self.last_power_message_timestamp >= fallback_delay:
            logger.warning(
                f"WATCHDOG TRIGGERED: No switch/ignition message received for {fallback_delay} seconds. "
                "Forcing shutdown to protect battery."
            )
            return self._execute_shutdown()

        return False


# --- Configuration Handling ---
def load_and_initialize_config(config_path='/home/pi/config.json') -> bool:
    global CONFIG, FEATURES
    logger.info(f"Loading configuration from {config_path}...")
    try:
        with open(config_path, 'r') as f:
            cfg = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logger.critical(f"FATAL: Could not load or parse {config_path}: {e}")
        return False

    try:
        FEATURES = cfg.setdefault('features', {})
        FEATURES.setdefault('tv_simulation', {'enabled': False})
        FEATURES.setdefault('time_sync', {'enabled': False, 'data_format': 'new_logic'})
        FEATURES.setdefault('auto_shutdown', {'enabled': False, 'trigger': 'ignition_off'})
        FEATURES.setdefault('carpihat', {'enabled': False, 'latch_pin': 25})

        zmq_config = cfg.get('zmq', {})
        can_ids = cfg.get('can_ids', {})
        thresholds = cfg.get('thresholds', {})

        CONFIG = {
            'zmq_publish_address': zmq_config.get('publish_address'),
            'zmq_send_address': zmq_config.get('send_address'),
            'can_ids': {
                'tv_presence': int(can_ids.get('tv_presence', '0x602'), 16),
                'time_data': int(can_ids.get('time_data', '0x623'), 16),
                'ignition_status': int(can_ids.get('ignition_status', '0x2C3'), 16),
            },
            'time_data_format': FEATURES['time_sync']['data_format'],
            'car_time_zone': FEATURES.get('car_time_zone', 'UTC'),
            'time_sync_threshold_seconds': thresholds.get('time_sync_threshold_minutes', 1.0) * 60,
            'shutdown_delay': thresholds.get('shutdown_delay_ignition_off_seconds', 300),
            'fallback_delay': thresholds.get('fallback_shutdown_seconds', 1800),
            'carpihat_enabled': FEATURES['carpihat']['enabled'],
            'carpihat_pin': FEATURES['carpihat'].get('latch_pin', 25),
        }

        if not CONFIG.get('zmq_send_address') or not CONFIG.get('zmq_publish_address'):
            raise KeyError("'send_address' or 'publish_address' not found in 'zmq' section")

        log_level = logging.DEBUG if FEATURES.get('debug_mode', False) else logging.INFO
        logger.setLevel(log_level)

        logger.info("Configuration for base functions loaded successfully.")
        logger.info(f"Watchdog enabled: {CONFIG['fallback_delay']} seconds without switch/ignition -> shutdown")
        return True

    except (KeyError, ValueError) as e:
        logger.critical(f"FATAL: Config is missing a key or has an invalid value: {e}", exc_info=True)
        return False


# --- Core Logic ---
def initialize_zmq_sender() -> bool:
    global ZMQ_CONTEXT, ZMQ_PUSH_SOCKET
    try:
        logger.info(f"Connecting ZeroMQ PUSH socket to {CONFIG['zmq_send_address']}...")
        ZMQ_CONTEXT = zmq.asyncio.Context.instance()
        ZMQ_PUSH_SOCKET = ZMQ_CONTEXT.socket(zmq.PUSH)
        ZMQ_PUSH_SOCKET.connect(CONFIG['zmq_send_address'])
        return True
    except zmq.ZMQError as e:
        logger.error(f"Failed to connect ZMQ PUSH socket: {e}")
        return False


async def send_can_message(can_id: int, payload_hex: str) -> bool:
    if not ZMQ_PUSH_SOCKET:
        return False
    try:
        await ZMQ_PUSH_SOCKET.send_multipart([str(can_id).encode('utf-8'), payload_hex.encode('utf-8')])
        return True
    except zmq.ZMQError as e:
        logger.error(f"Failed to queue CAN message via ZMQ: {e}")
        return False


def execute_system_command(command_list: List[str]) -> bool:
    if not command_list:
        return False
    cmd_str = ' '.join(command_list)
    try:
        logger.info(f"Executing system command: {cmd_str}")
        subprocess.run(command_list, check=True, capture_output=True, text=True)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        logger.error(f"Failed to execute command '{cmd_str}': {e}")
        return False


# --- Message Handling ---
def handle_time_data_message(msg: Dict[str, Any], state: AppState):
    if not FEATURES.get('time_sync', {}).get('enabled', False) or msg.get('dlc', 0) < 8:
        return

    data_hex = msg['data_hex']
    time_format = CONFIG['time_data_format']

    try:
        if time_format == 'old_logic':
            second = hex_to_bcd(data_hex[6:8])
            minute = hex_to_bcd(data_hex[4:6])
            hour = hex_to_bcd(data_hex[2:4])
            day = hex_to_bcd(data_hex[8:10])
            month = hex_to_bcd(data_hex[10:12])
            year = int(data_hex[12:14] + data_hex[14:16])
        else:
            second = int(data_hex[6:8], 16)
            minute = int(data_hex[4:6], 16)
            hour = int(data_hex[2:4], 16)
            day = int(data_hex[8:10], 16)
            month = int(data_hex[10:12], 16)
            year = (int(data_hex[12:14], 16) * 100) + int(data_hex[14:16], 16)

        state.last_time_sync_attempt_time = time.time()
        car_dt = datetime(year=year, month=month, day=day, hour=hour, minute=minute, second=second)
        pi_utc_dt = datetime.now(pytz.utc)
        car_utc_dt = pytz.timezone(CONFIG['car_time_zone']).localize(car_dt).astimezone(pytz.utc)

        time_diff_seconds = abs((car_utc_dt - pi_utc_dt).total_seconds())

        if time_diff_seconds > CONFIG['time_sync_threshold_seconds']:
            date_str = car_utc_dt.strftime('%m%d%H%M%Y.%S')
            logger.info(f"Car time differs by {time_diff_seconds:.1f}s. Syncing system time.")
            execute_system_command(["sudo", "date", "-u", date_str])
        else:
            logger.debug(f"Time sync check: difference {time_diff_seconds:.1f}s")

    except Exception as e:
        logger.warning(f"Could not parse time message (data_hex: {data_hex}): {e}")


def handle_power_status_message(msg: Dict[str, Any], state: AppState):
    if msg.get('dlc', 0) < 1:
        return

    try:
        data_hex = msg['data_hex']
        data_byte0 = int(data_hex[:2], 16)
        kls_status = data_byte0 & 0x01
        kl15_status = (data_byte0 >> 1) & 0x01

        # Reset Watchdog
        state.last_power_message_timestamp = time.time()

        if not FEATURES.get('auto_shutdown', {}).get('enabled', False):
            state.last_kls_status = kls_status
            state.last_kl15_status = kl15_status
            return

        trigger_config = FEATURES['auto_shutdown'].get('trigger', 'ignition_off')
        is_active = (trigger_config == 'ignition_off' and kl15_status == 1) or \
                    (trigger_config == 'key_pulled' and kls_status == 1)

        if state.last_kls_status is not None and state.last_kl15_status is not None:
            kls_changed = kls_status != state.last_kls_status
            kl15_changed = kl15_status != state.last_kl15_status

            trigger_event = False
            if trigger_config == 'ignition_off' and kl15_changed and kl15_status == 0:
                trigger_event = True
                logger.info("Ignition OFF detected. Starting normal shutdown timer.")
            elif trigger_config == 'key_pulled' and kls_changed and kls_status == 0:
                trigger_event = True
                logger.info("Key PULLED detected. Starting normal shutdown timer.")

            if trigger_event and not state.shutdown_pending:
                state.shutdown_pending = True
                state.shutdown_trigger_timestamp = time.time()

            elif state.shutdown_pending and ((trigger_config == 'ignition_off' and kl15_changed and kl15_status == 1) or
                                             (trigger_config == 'key_pulled' and kls_changed and kls_status == 1)):
                logger.info("Active signal detected. Cancelling pending shutdown.")
                state.shutdown_pending = False
                state.shutdown_trigger_timestamp = None

        else:
            if not is_active:
                logger.info(f"Boot detected with {trigger_config} inactive. Watchdog is running.")

        state.last_kls_status = kls_status
        state.last_kl15_status = kl15_status

    except (IndexError, ValueError) as e:
        logger.warning(f"Could not parse power status message: {e}")


# --- Async Tasks ---
async def send_periodic_messages_task():
    logger.info("Periodic sender task started.")
    while RUNNING:
        try:
            if FEATURES.get('tv_simulation', {}).get('enabled'):
                await send_can_message(CONFIG['can_ids']['tv_presence'], "0912300000000000")
            await asyncio.sleep(0.5)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Error in periodic sender task: {e}", exc_info=True)
            await asyncio.sleep(5)


async def listen_for_can_messages_task(state: AppState):
    logger.info("ZMQ listener task started.")
    sub_socket = None
    try:
        sub_socket = ZMQ_CONTEXT.socket(zmq.SUB)
        sub_socket.connect(CONFIG['zmq_publish_address'])

        time_topic = f"CAN_{CONFIG['can_ids']['time_data']:03X}".encode('utf-8')
        power_topic = f"CAN_{CONFIG['can_ids']['ignition_status']:03X}".encode('utf-8')

        if FEATURES.get('time_sync', {}).get('enabled', False):
            sub_socket.setsockopt(zmq.SUBSCRIBE, time_topic)
        sub_socket.setsockopt(zmq.SUBSCRIBE, power_topic)

        while RUNNING:
            msg = await sub_socket.recv_multipart()
            if len(msg) < 2:
                continue
            _, msg_bytes = msg
            try:
                msg_dict = json.loads(msg_bytes.decode('utf-8'))
                can_id = msg_dict.get('arbitration_id', 0)

                if can_id == CONFIG['can_ids']['time_data']:
                    handle_time_data_message(msg_dict, state)
                elif can_id == CONFIG['can_ids']['ignition_status']:
                    handle_power_status_message(msg_dict, state)
            except json.JSONDecodeError:
                pass
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.error(f"Critical error in ZMQ listener task: {e}", exc_info=True)
    finally:
        if sub_socket:
            sub_socket.close()


async def shutdown_monitor_task(state: AppState):
    global RUNNING
    while RUNNING:
        try:
            if state.check_shutdown_condition():
                RUNNING = False
                break
            await asyncio.sleep(1.0)
        except Exception as e:
            logger.error(f"Error in shutdown monitor task: {e}", exc_info=True)
            await asyncio.sleep(5)


# --- Signal Handling ---
def setup_signal_handlers(loop):
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda s=sig: shutdown_handler(s))
    loop.add_signal_handler(signal.SIGHUP, lambda s=signal.SIGHUP: reload_config_handler(s))


def shutdown_handler(sig):
    global RUNNING
    if RUNNING:
        logger.info(f"Shutdown signal {sig.name} received. Stopping...")
        RUNNING = False


def reload_config_handler(sig):
    global RELOAD_CONFIG
    logger.info("SIGHUP signal received. Flagging for configuration reload.")
    RELOAD_CONFIG = True


# --- Main ---
async def main_async():
    global RELOAD_CONFIG
    state = AppState()

    tasks = [
        asyncio.create_task(listen_for_can_messages_task(state)),
        asyncio.create_task(send_periodic_messages_task()),
        asyncio.create_task(shutdown_monitor_task(state))
    ]

    while RUNNING:
        if RELOAD_CONFIG:
            logger.info("Reloading configuration...")
            if load_and_initialize_config():
                RELOAD_CONFIG = False
            else:
                logger.error("Config reload failed!")
        await asyncio.sleep(1)

    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


def main():
    logger.info("Starting CAN Base Function service v1.4.0")
    if not load_and_initialize_config():
        sys.exit(1)
    if not initialize_zmq_sender():
        sys.exit(1)

    # Conditionally initialize CarPiHat GPIO based on config
    if CONFIG.get('carpihat_enabled'):
        try:
            setup_carpihat_latch(CONFIG.get('carpihat_pin', 25))
        except Exception:
            sys.exit(1)   # Exit if GPIO cannot be initialized
    else:
        logger.info("CarPiHat power latch is disabled in configuration. Skipping GPIO setup.")

    loop = asyncio.get_event_loop()
    setup_signal_handlers(loop)

    try:
        logger.info("--- Service is running ---")
        loop.run_until_complete(main_async())
    finally:
        logger.info("Main loop terminated. Closing resources.")
        if ZMQ_PUSH_SOCKET and not getattr(ZMQ_PUSH_SOCKET, 'closed', True):
            ZMQ_PUSH_SOCKET.close()
        if ZMQ_CONTEXT:
            ZMQ_CONTEXT.term()

        # Conditionally cleanup CarPiHat GPIO
        if CONFIG.get('carpihat_enabled'):
            if not SYSTEM_SHUTTING_DOWN:
                logger.info("Cleaning up GPIO resources.")
                try:
                    GPIO.cleanup()
                except:
                    pass
            else:
                logger.info("Skipping GPIO cleanup: System is halting.")

        try:
            loop.close()
        except:
            pass
        logger.info("CAN Base Function service has finished.")


if __name__ == '__main__':
    main()
