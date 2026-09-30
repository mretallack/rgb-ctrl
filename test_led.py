#!/usr/bin/env python3
"""
Test script for connecting to QHM-F430 LED strip and setting it to dim green
using the led-ble library.
"""

import asyncio
import logging
import bleak
from led_ble import LEDBLE

logging.basicConfig(level=logging.INFO)

MAC_ADDRESS = "12:08:20:00:F4:30"

async def main():
    print(f"Scanning for device {MAC_ADDRESS}...")
    scanner = bleak.BleakScanner()
    await scanner.start()
    await asyncio.sleep(5.0)
    dev_dict = scanner.discovered_devices_and_advertisement_data
    await scanner.stop()

    ble_device = None
    adv_data = None
    for addr, (d, a) in dev_dict.items():
        if addr.upper() == MAC_ADDRESS.upper():
            ble_device = d
            adv_data = a
            break

    if not ble_device:
        print(f"Error: Device {MAC_ADDRESS} not found during scan.")
        return

    print(f"Found device: {ble_device}. Connecting...")
    led = LEDBLE(ble_device, adv_data)

    # Fetch initial state and connect
    await led.update()
    print(f"Connected! Current state: {led.state}")

    # Turn on and set to dim green
    print("Turning on and setting to dim green (RGB: 0, 255, 0, brightness: 20)...")
    await led.turn_on()
    await led.set_rgb((0, 255, 0), brightness=20)
    print("Done! LED strip should now be dim green.")

    # Keep connection open briefly to ensure commands are sent/flushed
    await asyncio.sleep(2)
    await led.stop()
    print("Disconnected.")

if __name__ == "__main__":
    asyncio.run(main())
