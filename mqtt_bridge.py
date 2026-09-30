#!/usr/bin/env python3
"""
MQTT Bridge for QHM-F430 LED Strip with Home Assistant Auto-Discovery.
Broker: mqtt.retallack.org.uk:1883 (No auth, No TLS)
"""

import asyncio
import json
import logging
import signal
import sys
import bleak
import paho.mqtt.client as mqtt
from bleak import BleakScanner, BleakClient
from led_ble import LEDBLE
from led_ble.led_ble import EFFECT_LIST

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("mqtt_bridge")

MQTT_BROKER = "mqtt.retallack.org.uk"
MQTT_PORT = 1883
MAC_ADDRESS = "12:08:20:00:F4:30"
DEVICE_ID = "qhm_f430_ble"

TOPIC_DISCOVERY = f"homeassistant/light/{DEVICE_ID}/config"
TOPIC_STATE = f"homeassistant/light/{DEVICE_ID}/state"
TOPIC_COMMAND = f"homeassistant/light/{DEVICE_ID}/set"
TOPIC_AVAILABILITY = f"homeassistant/light/{DEVICE_ID}/availability"

class LEDBridge:
    def __init__(self):
        self.mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=DEVICE_ID)
        self.mqtt_client.on_connect = self.on_connect
        self.mqtt_client.on_message = self.on_message

        self.led: LEDBLE | None = None
        self._lock = asyncio.Lock()
        self.running = True

    def on_connect(self, client, userdata, flags, reason_code, properties):
        logger.info(f"Connected to MQTT broker with result code {reason_code}")
        # Subscribe to command topic
        client.subscribe(TOPIC_COMMAND)
        # Publish discovery payload
        self.publish_discovery()
        self.publish_availability("online")

    def publish_discovery(self):
        discovery_payload = {
            "name": "QHM-F430 LED Strip",
            "unique_id": f"{DEVICE_ID}_light",
            "schema": "json",
            "command_topic": TOPIC_COMMAND,
            "state_topic": TOPIC_STATE,
            "availability": [
                {
                    "topic": TOPIC_AVAILABILITY
                }
            ],
            "brightness": True,
            "rgb": True,
            "supported_color_modes": ["rgb"],
            "effect": True,
            "effect_list": EFFECT_LIST[:10],
            "device": {
                "identifiers": [DEVICE_ID],
                "name": "QHM-F430 Bluetooth LED Strip",
                "model": "QHM-F430",
                "manufacturer": "Triones / LEDNet"
            }
        }
        self.mqtt_client.publish(TOPIC_DISCOVERY, json.dumps(discovery_payload), retain=True)
        logger.info("Published Home Assistant MQTT discovery config.")

    def publish_availability(self, status: str):
        self.mqtt_client.publish(TOPIC_AVAILABILITY, status, retain=True)

    def publish_state(self):
        if not self.led:
            return
        
        state = self.led.state
        payload = {
            "state": "ON" if state.power else "OFF",
            "brightness": self.led.brightness if state.power else 0,
            "color": {
                "r": state.rgb[0],
                "g": state.rgb[1],
                "b": state.rgb[2]
            }
        }
            
        self.mqtt_client.publish(TOPIC_STATE, json.dumps(payload), retain=True)
        logger.debug(f"Published state: {payload}")

    def on_message(self, client, userdata, msg):
        payload_str = msg.payload.decode("utf-8")
        logger.info(f"Received MQTT message on {msg.topic}: {payload_str}")
        try:
            data = json.loads(payload_str)
        except json.JSONDecodeError:
            logger.error(f"Failed to parse JSON payload: {payload_str}")
            return

        # Queue command execution on asyncio loop
        asyncio.run_coroutine_threadsafe(self.handle_command(data), self.loop)

    async def handle_command(self, data: dict):
        async with self._lock:
            logger.info(f"Scanning for BLE device {MAC_ADDRESS}...")
            try:
                device = await BleakScanner.find_device_by_address(MAC_ADDRESS, timeout=5.0)
                if not device:
                    logger.error(f"Device {MAC_ADDRESS} not found")
                    return

                async with BleakClient(device, timeout=10.0) as client:
                    logger.info(f"Connected to {MAC_ADDRESS}")
                    # Initialize LEDBLE using the connected client/device
                    self.led = LEDBLE(device)
                    await self.led.update()

                    state_changed = False

                    # Handle Power
                    if "state" in data:
                        val = data["state"].upper()
                        if val == "ON":
                            await self.led.turn_on()
                            state_changed = True
                        elif val == "OFF":
                            await self.led.turn_off()
                            state_changed = True

                    # Handle RGB Color & Brightness
                    rgb = None
                    brightness = None

                    if "color" in data:
                        color = data["color"]
                        r = color.get("r", 0)
                        g = color.get("g", 0)
                        b = color.get("b", 0)
                        rgb = (r, g, b)

                    if "brightness" in data:
                        hw_brightness = max(1, int((data["brightness"] / 255) * 100))
                        brightness = hw_brightness

                    if rgb is not None:
                        if brightness is not None:
                            await self.led.set_rgb(rgb, brightness=brightness)
                        else:
                            await self.led.set_rgb(rgb)
                        state_changed = True
                    elif brightness is not None:
                        await self.led.set_brightness(brightness)
                        state_changed = True

                    # Handle Effect
                    if "effect" in data:
                        effect_name = data["effect"]
                        speed = data.get("speed", 50)
                        brightness_eff = brightness if brightness is not None else 100
                        await self.led.async_set_effect(effect_name, speed=speed, brightness=brightness_eff)
                        state_changed = True

                    if state_changed:
                        await asyncio.sleep(0.5)
                        await self.led.update()
                        self.publish_state()
                        logger.info("Command executed and state published.")

            except Exception as e:
                logger.error(f"BLE operation failed: {e}")
                self.led = None

    async def run(self):
        self.loop = asyncio.get_running_loop()
        
        # Connect to MQTT broker
        logger.info(f"Connecting to MQTT broker {MQTT_BROKER}:{MQTT_PORT}...")
        self.mqtt_client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
        self.mqtt_client.loop_start()

        try:
            while self.running:
                await asyncio.sleep(30)
        finally:
            self.publish_availability("offline")
            self.mqtt_client.loop_stop()
            self.mqtt_client.disconnect()
            if self.led:
                try:
                    await self.led.stop()
                except Exception:
                    pass

def main():
    bridge = LEDBridge()

    def handle_sigint(sig, frame):
        logger.info("Stopping MQTT bridge...")
        bridge.running = False
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_sigint)
    signal.signal(signal.SIGTERM, handle_sigint)

    asyncio.run(bridge.run())

if __name__ == "__main__":
    main()
