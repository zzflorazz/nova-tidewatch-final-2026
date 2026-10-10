#!/usr/bin/env python3
# TideWatch 水文节点模拟器
# 模拟 reach-a / reach-b / reach-c 三个河道断面，定时向 MQTT 发布水位 / 浊度 / 水流等级数据
#
# 用法:
#   python3 python/mqtt_node.py                        # 三断面一起发布，间隔 5 秒
#   python3 python/mqtt_node.py --reach reach-b        # 只发布 reach-b
#   python3 python/mqtt_node.py --reach all --interval 2
#
# 优先使用 paho-mqtt 发布；未安装 paho-mqtt 时自动回退到 mosquitto_pub 命令行。

import argparse
import json
import os
import random
import subprocess
import sys
import time
from datetime import datetime

# 保证能直接以 python3 python/mqtt_node.py 方式运行时导入同目录 rules.py
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rules import judge

# ===== 统一 MQTT 约定 =====
TOPIC_TEMPLATE = "tidewatch/{reachId}/data"
BROKER_HOST = "localhost"
BROKER_PORT = 1883

REACHES = ["reach-a", "reach-b", "reach-c"]

try:
    import paho.mqtt.client as mqtt
    HAS_PAHO = True
except ImportError:
    HAS_PAHO = False


def build_message(reach_id):
    """生成一条模拟断面数据，status 由统一判级规则计算"""
    water_level = round(random.uniform(3.0, 10.0), 1)
    turbidity = random.randint(5, 90)
    flow_level = random.randint(0, 3)

    result = judge(water_level, turbidity)
    status = result['status'] if isinstance(result, dict) else result

    return {
        "reachId": reach_id,
        "waterLevel": water_level,
        "turbidity": turbidity,
        "flowLevel": flow_level,
        "status": status,
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def publish_via_cli(topic, payload):
    """无 paho-mqtt 时回退到 mosquitto_pub"""
    subprocess.run(
        ["mosquitto_pub", "-h", BROKER_HOST, "-p", str(BROKER_PORT), "-t", topic, "-m", payload],
        check=False,
    )


def main():
    parser = argparse.ArgumentParser(description="TideWatch 水文节点模拟器")
    parser.add_argument("--reach", choices=REACHES + ["all"], default="all",
                        help="发布的断面，默认 all（三断面一起发布）")
    parser.add_argument("--interval", type=float, default=5,
                        help="发布间隔秒数，默认 5")
    args = parser.parse_args()

    reaches = REACHES if args.reach == "all" else [args.reach]

    client = None
    if HAS_PAHO:
        try:
            client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="tidewatch-node")
        except AttributeError:
            client = mqtt.Client(client_id="tidewatch-node")
        try:
            client.connect(BROKER_HOST, BROKER_PORT, 60)
        except Exception as e:
            print(f"[tidewatch-node] 无法连接 Broker {BROKER_HOST}:{BROKER_PORT}：{e}")
            print("[tidewatch-node] 请先运行 scripts/start_broker.sh 启动 mosquitto")
            sys.exit(1)
        client.loop_start()
        mode = "paho-mqtt"
    else:
        mode = "mosquitto_pub（paho-mqtt 未安装，命令行回退）"

    print(f"[tidewatch-node] 发布模式: {mode}")
    print(f"[tidewatch-node] Broker: {BROKER_HOST}:{BROKER_PORT}，"
          f"断面: {', '.join(reaches)}，间隔: {args.interval}s")
    print("-" * 60)

    try:
        while True:
            for reach_id in reaches:
                msg = build_message(reach_id)
                topic = TOPIC_TEMPLATE.format(reachId=reach_id)
                payload = json.dumps(msg, ensure_ascii=False)
                print(f"[{msg['time']}] {topic} -> {payload}")
                if client is not None:
                    client.publish(topic, payload, qos=0)
                else:
                    publish_via_cli(topic, payload)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\n[tidewatch-node] 已停止")
    finally:
        if client is not None:
            client.loop_stop()
            client.disconnect()


if __name__ == "__main__":
    main()
