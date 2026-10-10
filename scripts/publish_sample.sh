#!/bin/bash
TOPIC="tidewatch/reach-b/data"
PAYLOAD='{"reachId":"reach-b","waterLevel":9.0,"turbidity":20,"flowLevel":2,"status":"洪水预警","time":"2026-10-09 12:00:00"}'
echo "发布到: $TOPIC"
mosquitto_pub -h localhost -t "$TOPIC" -m "$PAYLOAD"
echo "完成"
