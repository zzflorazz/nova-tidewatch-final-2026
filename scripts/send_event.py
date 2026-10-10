#!/usr/bin/env python3
# TideWatch 事件指令发送脚本（D3）
# open：直接构造事件消息发布（模拟自动检测，由 event_manager 建单）
# handling / recovered / close：操作已有事件（需 --event-id）
#
# 用法:
#   python3 scripts/send_event.py --reach reach-b --action open --operator admin
#   python3 scripts/send_event.py --reach reach-b --action handling --event-id evt-20261010-001
#   python3 scripts/send_event.py --reach reach-b --action recovered --event-id evt-20261010-001
#   python3 scripts/send_event.py --reach reach-b --action close --event-id evt-20261010-001

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "python"))
from rules import judge

# ===== 统一约定 =====
EVENT_TOPIC_TEMPLATE = "tidewatch/events/{reachId}"
BROKER_HOST = "localhost"
BROKER_PORT = 1883


def build_message(args):
    msg = {
        "reachId": args.reach,
        "action": "closed" if args.action == "close" else args.action,
        "operator": args.operator,
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "timestamp": int(time.time() * 1000),
    }
    if args.action == "open":
        # 模拟自动检测：可携带环境数据（可选）
        if args.water is not None and args.turbidity is not None:
            msg["waterLevel"] = args.water
            msg["turbidity"] = args.turbidity
            msg["flowLevel"] = args.flow
            msg["status"] = judge(args.water, args.turbidity)["status"]
        if args.event_id:
            msg["eventId"] = args.event_id
    else:
        if not args.event_id:
            print(f"✗ --action {args.action} 必须提供 --event-id")
            sys.exit(1)
        msg["eventId"] = args.event_id
    return msg


def publish(topic, payload):
    try:
        import paho.mqtt.client as mqtt
    except ImportError:
        mqtt = None

    if mqtt is not None:
        try:
            try:
                client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="tidewatch-send-event")
            except AttributeError:
                client = mqtt.Client(client_id="tidewatch-send-event")
            client.connect(BROKER_HOST, BROKER_PORT, 60)
            client.loop_start()
            client.publish(topic, payload, qos=0)
            time.sleep(0.3)
            client.loop_stop()
            client.disconnect()
            return "paho-mqtt"
        except Exception as e:
            print(f"⚠ paho-mqtt 发布失败（{e}），尝试回退 mosquitto_pub ...")

    try:
        subprocess.run(
            ["mosquitto_pub", "-h", BROKER_HOST, "-p", str(BROKER_PORT), "-t", topic, "-m", payload],
            check=True,
        )
        return "mosquitto_pub"
    except Exception as e:
        print(f"✗ 发布失败: {e}")
        print("  请确认 Broker 已启动: bash scripts/start_broker.sh")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="向 TideWatch 发布事件指令")
    parser.add_argument("--reach", default="reach-b", help="断面 ID（默认 reach-b）")
    parser.add_argument("--action", required=True,
                        choices=["open", "handling", "recovered", "close"],
                        help="事件动作：open 开启 / handling 开始处理 / recovered 验证恢复 / close 关闭")
    parser.add_argument("--event-id", default=None, help="事件 ID（handling/recovered/close 必填）")
    parser.add_argument("--operator", default="admin", help="操作者（默认 admin）")
    parser.add_argument("--water", type=float, default=None, help="水位（仅 open 可选携带）")
    parser.add_argument("--turbidity", type=float, default=None, help="浊度（仅 open 可选携带）")
    parser.add_argument("--flow", type=int, default=1, help="水流等级（仅 open 可选携带）")
    args = parser.parse_args()

    msg = build_message(args)
    topic = EVENT_TOPIC_TEMPLATE.format(reachId=args.reach)
    payload = json.dumps(msg, ensure_ascii=False)

    mode = publish(topic, payload)

    print(f"✓ 已发布（{mode}）")
    print(f"  Topic: {topic}")
    print(f"  消息: {payload}")


if __name__ == "__main__":
    main()
