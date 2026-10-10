#!/usr/bin/env python3
# D3 端到端事件流程测试：完整闭环 异常 → 干预 → 恢复验证 → 再异常 → 关闭
# 前置：mosquitto + python3 python/event_manager.py 正在运行
# 用法: python3 scripts/test_event_flow.py

import json
import os
import sys
import time
import urllib.request
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "python"))
from rules import judge

import paho.mqtt.client as mqtt

EVENTS_API = "http://localhost:8001/api/events"
BROKER_HOST = "localhost"
BROKER_PORT = 1883
DATA_TOPIC_TEMPLATE = "tidewatch/{reachId}/data"
EVENT_TOPIC_TEMPLATE = "tidewatch/events/{reachId}"
REACH = "reach-b"

client = None


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def publish(topic, payload):
    client.publish(topic, json.dumps(payload, ensure_ascii=False), qos=0)
    time.sleep(0.3)


def send_data(reach, wl, tb, fl):
    msg = {
        "reachId": reach, "waterLevel": wl, "turbidity": tb, "flowLevel": fl,
        "status": judge(wl, tb)["status"], "time": _now(),
    }
    publish(DATA_TOPIC_TEMPLATE.format(reachId=reach), msg)
    print(f"  → 发送数据 {reach}: 水位={wl} 浊度={tb} 水流={fl} → {msg['status']}")


def send_cmd(reach, action, event_id=None, operator="tester"):
    msg = {"reachId": reach, "action": action, "operator": operator,
           "time": _now(), "timestamp": int(time.time() * 1000)}
    if event_id:
        msg["eventId"] = event_id
    publish(EVENT_TOPIC_TEMPLATE.format(reachId=reach), msg)
    print(f"  → 发送指令 {action}" + (f"（{event_id}）" if event_id else ""))


def api_get(path=""):
    with urllib.request.urlopen(EVENTS_API + path, timeout=5) as r:
        return json.load(r)


def get_event(eid):
    return api_get("/" + eid)


def active_event(reach):
    for ev in api_get("")["events"].values():
        if ev["reachId"] == reach and ev["state"] in ("OPEN", "HANDLING"):
            return ev
    return None


def main():
    global client
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="tidewatch-flow-test")
    try:
        client.connect(BROKER_HOST, BROKER_PORT, 60)
    except Exception as e:
        print(f"✗ 无法连接 Broker: {e}（请先启动 mosquitto 和 event_manager）")
        sys.exit(1)
    client.loop_start()
    time.sleep(0.5)

    # 预清理：如 reach-b 有上次遗留的活动事件，先关闭
    leftover = active_event(REACH)
    if leftover:
        print(f"[清理] 发现遗留活动事件 {leftover['eventId']}，先关闭")
        send_cmd(REACH, "close", leftover["eventId"])
        time.sleep(1.0)

    print("\n===== 步骤1: 发送洪水预警数据 → 自动创建 OPEN 事件 =====")
    send_data(REACH, 9.0, 20, 2)
    time.sleep(1.2)
    ev1 = active_event(REACH)
    assert ev1 is not None, "✗ 未自动创建事件"
    assert ev1["state"] == "OPEN", f"✗ 状态应为 OPEN，实际 {ev1['state']}"
    print(f"  ✓ 事件 {ev1['eventId']} 状态={ev1['state']} 数据=水位{ev1['waterLevel']}/"
          f"浊度{ev1['turbidity']}/status={ev1['status']}")

    print("\n===== 步骤2: 发送 handling 指令 → OPEN → HANDLING =====")
    send_cmd(REACH, "handling", ev1["eventId"])
    time.sleep(1.2)
    chk = get_event(ev1["eventId"])
    assert chk["state"] == "HANDLING", f"✗ 应为 HANDLING，实际 {chk['state']}"
    print(f"  ✓ 状态={chk['state']} 处理时间={chk['handling_time']}")

    print("\n===== 步骤3: 发送正常数据 → 状态保持 HANDLING（不自动恢复）=====")
    send_data(REACH, 4.5, 20, 0)
    time.sleep(1.2)
    chk = get_event(ev1["eventId"])
    assert chk["state"] == "HANDLING", f"✗ 不应自动恢复，实际 {chk['state']}"
    print(f"  ✓ 状态仍为 {chk['state']}，等待人工验证恢复")

    print("\n===== 步骤4: 发送 recovered 指令 → HANDLING → RECOVERED =====")
    send_cmd(REACH, "recovered", ev1["eventId"])
    time.sleep(1.2)
    chk = get_event(ev1["eventId"])
    assert chk["state"] == "RECOVERED", f"✗ 应为 RECOVERED，实际 {chk['state']}"
    print(f"  ✓ 状态={chk['state']} 恢复时间={chk['recovered_time']}")

    print("\n===== 步骤5: 再发洪水预警数据 → 创建新的 OPEN 事件 =====")
    send_data(REACH, 9.2, 25, 2)
    time.sleep(1.2)
    ev2 = active_event(REACH)
    assert ev2 is not None, "✗ 未创建新事件"
    assert ev2["state"] == "OPEN", f"✗ 新事件应为 OPEN，实际 {ev2['state']}"
    assert ev2["eventId"] != ev1["eventId"], "✗ 应为新事件而非复用"
    print(f"  ✓ 新事件 {ev2['eventId']}（与已恢复事件 {ev1['eventId']} 不同）状态=OPEN")

    print("\n===== 步骤5.5（附加）: 对 OPEN 事件直接验证恢复 → 应被拒绝 =====")
    send_cmd(REACH, "recovered", ev2["eventId"])
    time.sleep(1.2)
    chk = get_event(ev2["eventId"])
    assert chk["state"] == "OPEN", f"✗ 非法转移未被拒绝，实际 {chk['state']}"
    print("  ✓ 非法转移 OPEN → RECOVERED 被拒绝，状态保持 OPEN")

    print("\n===== 步骤6: 发送 closed 指令 → OPEN → CLOSED =====")
    send_cmd(REACH, "close", ev2["eventId"])
    time.sleep(1.2)
    chk = get_event(ev2["eventId"])
    assert chk["state"] == "CLOSED", f"✗ 应为 CLOSED，实际 {chk['state']}"
    print(f"  ✓ 状态={chk['state']} 关闭时间={chk['closed_time']}")

    print("\n完整闭环验证通过 ✓")
    print("  OPEN → HANDLING → RECOVERED（由干预后新数据触发）→ 再异常新建事件 → CLOSED")
    print("  非法转移被拒绝：OPEN 不能直接 → RECOVERED")

    client.loop_stop()
    client.disconnect()


if __name__ == "__main__":
    main()
