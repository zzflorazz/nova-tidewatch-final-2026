#!/bin/bash
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
echo "启动 mosquitto (TCP:1883, WebSocket:8083)"
mosquitto -c "$SCRIPT_DIR/mosquitto.conf" -v
