#!/usr/bin/env python3
# TideWatch 共享实时状态服务（数据链 B 的核心）
# 水文节点 → MQTT(tidewatch/+/data) → 内存共享状态 → HTTP API 供多端读取
#
# 用法: python3 python/shared_state.py
#   GET /api/state           → 所有断面最新状态
#   GET /api/state/{reachId} → 单个断面状态
#   GET /api/health          → 各断面 last_seen 与在线状态（>10 秒无数据 = offline）

import json
import os
import sys
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rules import judge

try:
    import paho.mqtt.client as mqtt
    HAS_PAHO = True
except ImportError:
    HAS_PAHO = False

# ===== 统一约定 =====
TOPIC_SUBSCRIBE = "tidewatch/+/data"
BROKER_HOST = "localhost"
BROKER_PORT = 1883
HTTP_HOST = "0.0.0.0"   # 绑定所有网卡，便于局域网内多端访问
HTTP_PORT = 8000
OFFLINE_THRESHOLD = 10  # 秒，last_seen 距今超过该值标记 offline

REQUIRED_FIELDS = ["reachId", "waterLevel", "turbidity", "flowLevel", "status", "time"]

# ===== 内存共享状态 =====
# {reachId: {reachId, waterLevel, turbidity, flowLevel, status, time, last_seen}}
_state = {}
_lock = threading.Lock()


# ===================== 消息校验与处理 =====================

def validate_message(msg):
    """校验 MQTT 消息必须字段与类型，返回错误列表（空列表 = 合法）"""
    if not isinstance(msg, dict):
        return ["消息不是 JSON 对象"]
    errors = [f"缺少字段 {f}" for f in REQUIRED_FIELDS if f not in msg]
    if errors:
        return errors
    if not isinstance(msg["reachId"], str) or not msg["reachId"]:
        errors.append("reachId 必须为非空字符串")
    for f in ("waterLevel", "turbidity", "flowLevel"):
        if isinstance(msg[f], bool) or not isinstance(msg[f], (int, float)):
            errors.append(f"{f} 必须为数字")
    if not isinstance(msg["time"], str):
        errors.append("time 必须为字符串")
    return errors


def handle_mqtt_message(payload):
    """处理一条 MQTT 消息（bytes/str）。返回 True=已更新状态，False=被拒绝"""
    if isinstance(payload, bytes):
        try:
            payload = payload.decode("utf-8")
        except UnicodeDecodeError as e:
            print(f"[shared-state] ✗ 拒绝消息（非 UTF-8 编码）: {e}")
            return False

    try:
        msg = json.loads(payload)
    except json.JSONDecodeError as e:
        print(f"[shared-state] ✗ 拒绝消息（JSON 解析失败）: {e} | 原始内容: {payload[:120]!r}")
        return False

    errors = validate_message(msg)
    if errors:
        print(f"[shared-state] ✗ 拒绝消息（字段校验失败）: {'; '.join(errors)} | 原始内容: {payload[:120]!r}")
        return False

    reach_id = msg["reachId"]
    now = time.time()
    with _lock:
        _state[reach_id] = {
            "reachId": reach_id,
            "waterLevel": msg["waterLevel"],
            "turbidity": msg["turbidity"],
            "flowLevel": msg["flowLevel"],
            "status": msg["status"],
            "time": msg["time"],
            "last_seen": now,
        }

    # status 与统一判级规则的一致性检查（仅告警，不改写消息值）
    expected = judge(msg["waterLevel"], msg["turbidity"])["status"]
    if expected != msg["status"]:
        print(f"[shared-state] ⚠ {reach_id} status 与判级规则不一致: "
              f"消息={msg['status']}，规则={expected}（保留消息值）")

    print(f"[shared-state] ✓ 更新 {reach_id}: waterLevel={msg['waterLevel']} "
          f"turbidity={msg['turbidity']} flowLevel={msg['flowLevel']} status={msg['status']}")
    return True


# ===================== 状态快照 =====================

def _is_online(last_seen, now):
    return (now - last_seen) <= OFFLINE_THRESHOLD


def snapshot():
    """所有断面最新状态（含 last_seen / online）"""
    now = time.time()
    with _lock:
        items = list(_state.items())
    out = {}
    for rid, s in sorted(items):
        out[rid] = {
            "reachId": s["reachId"],
            "waterLevel": s["waterLevel"],
            "turbidity": s["turbidity"],
            "flowLevel": s["flowLevel"],
            "status": s["status"],
            "time": s["time"],
            "last_seen": datetime.fromtimestamp(s["last_seen"]).strftime("%Y-%m-%d %H:%M:%S"),
            "age_seconds": round(now - s["last_seen"], 1),
            "online": _is_online(s["last_seen"], now),
        }
    return out


def health_snapshot():
    """各断面 last_seen 与在线状态"""
    reaches = snapshot()
    online_count = sum(1 for r in reaches.values() if r["online"])
    return {
        "server_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "offline_threshold_seconds": OFFLINE_THRESHOLD,
        "online_count": online_count,
        "total": len(reaches),
        "reaches": {
            rid: {
                "status": r["status"],
                "last_seen": r["last_seen"],
                "age_seconds": r["age_seconds"],
                "online": r["online"],
            }
            for rid, r in reaches.items()
        },
    }


# ===================== HTTP API =====================

class StateHandler(BaseHTTPRequestHandler):
    server_version = "TideWatchState/1.0"

    def _send_json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?", 1)[0].rstrip("/")
        if path == "/api/state":
            self._send_json(snapshot())
        elif path == "/api/health":
            self._send_json(health_snapshot())
        elif path.startswith("/api/state/"):
            rid = path[len("/api/state/"):]
            data = snapshot()
            if rid in data:
                self._send_json(data[rid])
            else:
                known = ", ".join(data) or "暂无数据"
                self._send_json({"error": f"未知断面 {rid}，当前已上报断面: {known}"}, 404)
        else:
            self._send_json({"error": "not found",
                             "endpoints": ["/api/state", "/api/state/{reachId}", "/api/health"]}, 404)

    def log_message(self, fmt, *args):
        print(f"[http] {self.address_string()} {fmt % args}")


# ===================== 主流程 =====================

def _rc_value(rc):
    """兼容 paho v1(int) / v2(ReasonCode) 的返回码取值"""
    return rc.value if hasattr(rc, "value") else rc


def main():
    if not HAS_PAHO:
        print("[shared-state] ✗ 未安装 paho-mqtt（pip3 install paho-mqtt），无法订阅 MQTT")
        sys.exit(1)

    try:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="tidewatch-shared-state")
    except AttributeError:  # paho-mqtt 1.x
        client = mqtt.Client(client_id="tidewatch-shared-state")

    def on_connect(client, userdata, flags, reason_code, properties=None):
        rc = _rc_value(reason_code)
        if rc == 0:
            print(f"[shared-state] ✓ 已连接 Broker {BROKER_HOST}:{BROKER_PORT}，订阅 {TOPIC_SUBSCRIBE}")
            client.subscribe(TOPIC_SUBSCRIBE)
        else:
            print(f"[shared-state] ✗ 连接 Broker 失败 rc={rc}")

    def on_message(client, userdata, msg):
        handle_mqtt_message(msg.payload)

    def on_disconnect(client, userdata, *args):
        # v1: (rc)   v2: (disconnect_flags, reason_code, properties)
        rc = None
        for a in args:
            v = _rc_value(a)
            if isinstance(v, int):
                rc = v
        print(f"[shared-state] ⚠ 与 Broker 断开（rc={rc}），paho 将自动重连 ...")

    client.on_connect = on_connect
    client.on_message = on_message
    client.on_disconnect = on_disconnect
    client.reconnect_delay_set(min_delay=1, max_delay=10)

    try:
        client.connect(BROKER_HOST, BROKER_PORT, 60)
    except Exception as e:
        print(f"[shared-state] ✗ 无法连接 Broker {BROKER_HOST}:{BROKER_PORT}：{e}")
        print("[shared-state] 请先启动 mosquitto（bash scripts/start_broker.sh）")
        sys.exit(1)
    client.loop_start()

    httpd = ThreadingHTTPServer((HTTP_HOST, HTTP_PORT), StateHandler)
    print(f"[shared-state] HTTP API 已启动: http://localhost:{HTTP_PORT}/api/state"
          f"（健康检查 /api/health）")
    print("[shared-state] 等待 MQTT 消息 ... (Ctrl+C 退出)")

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[shared-state] 正在退出 ...")
    finally:
        httpd.server_close()
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    main()
