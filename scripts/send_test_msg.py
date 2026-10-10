#!/usr/bin/env python3
# TideWatch 测试消息发送脚本
# 向指定断面发布一条测试数据，status 由统一判级规则计算
#
# 用法: python3 scripts/send_test_msg.py --reach reach-b --water 9.0 --turbidity 20 --flow 2

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
TOPIC_TEMPLATE = "tidewatch/{reachId}/data"
BROKER_HOST = "localhost"
BROKER_PORT = 1883


def build_message(reach_id, water_level, turbidity, flow_level):
    result = judge(water_level, turbidity)
    status = result["status"] if isinstance(result, dict) else result
    return {
        "reachId": reach_id,
        "waterLevel": water_level,
        "turbidity": turbidity,
        "flowLevel": flow_level,
        "status": status,
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def publish(topic, payload):
    """优先 paho-mqtt，失败或未安装时回退 mosquitto_pub。返回使用的模式名"""
    try:
        import paho.mqtt.client as mqtt
    except ImportError:
        mqtt = None

    if mqtt is not None:
        try:
            try:
                client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="tidewatch-send-test")
            except AttributeError:  # paho-mqtt 1.x
                client = mqtt.Client(client_id="tidewatch-send-test")
            client.connect(BROKER_HOST, BROKER_PORT, 60)
            client.loop_start()
            client.publish(topic, payload, qos=0)
            time.sleep(0.3)  # 等待 QoS0 消息实际发出
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
        print(f"  请确认 Broker 已启动: bash scripts/start_broker.sh")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="向 TideWatch 发布一条测试断面消息")
    parser.add_argument("--reach", default="reach-b", help="断面 ID（默认 reach-b）")
    parser.add_argument("--water", type=float, default=4.5, help="水位（米，默认 4.5）")
    parser.add_argument("--turbidity", type=float, default=20, help="浊度（NTU，默认 20）")
    parser.add_argument("--flow", type=int, default=1, help="水流等级 0-3（默认 1）")
    args = parser.parse_args()

    msg = build_message(args.reach, args.water, args.turbidity, args.flow)
    topic = TOPIC_TEMPLATE.format(reachId=args.reach)
    payload = json.dumps(msg, ensure_ascii=False)

    mode = publish(topic, payload)

    print(f"✓ 已发布（{mode}）")
    print(f"  Topic: {topic}")
    print(f"  判级结果: {msg['status']}")
    print(f"  消息: {payload}")


if __name__ == "__main__":
    main()
