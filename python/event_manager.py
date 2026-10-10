#!/usr/bin/env python3
# TideWatch D3 事件状态机（事件管理器）—— 唯一权威事件状态源
# 订阅 tidewatch/+/data（异常检测→自动创建 OPEN 事件）
# 订阅 tidewatch/events/+（接收干预指令 handling/recovered/closed）
# 状态变更广播回 tidewatch/events/{reachId}（带递增 seq）
# HTTP API（端口 8001）：GET /api/events、GET /api/events/{id}、POST /api/events/{id}/handle|verify|close
#
# 状态机（显式规则，非法转移拒绝）：
#   （无）       → OPEN      出现新异常（数据驱动，自动创建）
#   OPEN        → HANDLING  用户执行干预动作
#   HANDLING    → RECOVERED 恢复验证 + 干预后新数据 judge()==正常
#   OPEN/HANDLING → CLOSED  用户关闭事件（放弃处理）
#   RECOVERED   → （终态，之后再次异常创建新事件）
#
# 用法: python3 python/event_manager.py

import json
import os
import sys
import threading
import time as time_mod
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
DATA_TOPIC_SUBSCRIBE = "tidewatch/+/data"
EVENT_TOPIC_SUBSCRIBE = "tidewatch/events/+"
EVENT_TOPIC_TEMPLATE = "tidewatch/events/{reachId}"
BROKER_HOST = "localhost"
BROKER_PORT = 1883
HTTP_HOST = "0.0.0.0"
HTTP_PORT = 8001

# ===== 内存事件表 =====
# events = {eventId: {eventId, reachId, state, open_time, handling_time, recovered_time,
#                     closed_time, waterLevel, turbidity, flowLevel, status, data_time,
#                     operator, seq, history: [...]}}
_events = {}
_lock = threading.RLock()
_last_seq = {}          # eventId -> 已处理过的最大 seq（事件消息去重）
_last_data_key = {}     # reachId -> 已处理数据的指纹（重复数据去重）
_last_data_time = {}    # reachId -> 最近处理的数据 time 字符串（乱序/迟到判断）
_daily_counter = {}     # "YYYYMMDD" -> 当日事件序号
_client = None

VALID_STATES = ("OPEN", "HANDLING", "RECOVERED", "CLOSED")
ACTION_TO_STATE = {"open": "OPEN", "handling": "HANDLING",
                   "recovered": "RECOVERED", "closed": "CLOSED", "close": "CLOSED"}


def _now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _public(ev):
    """对外输出的事件对象（去掉内部字段）"""
    return {k: v for k, v in ev.items() if not k.startswith("_")}


def _log_history(ev, action, operator, note=""):
    ev["history"].append({
        "time": _now_str(), "action": action,
        "operator": operator, "note": note,
    })


def _new_event_id():
    today = datetime.now().strftime("%Y%m%d")
    n = _daily_counter.get(today, 0) + 1
    _daily_counter[today] = n
    return f"evt-{today}-{n:03d}"


def _active_event_for(reach_id):
    """该断面当前是否有活动事件（OPEN/HANDLING）"""
    for ev in _events.values():
        if ev["reachId"] == reach_id and ev["state"] in ("OPEN", "HANDLING"):
            return ev
    return None


def _broadcast(ev, action, operator):
    """把事件当前状态广播到 tidewatch/events/{reachId}（seq 递增）"""
    ev["seq"] += 1
    payload = _public(ev)
    payload.update({
        "action": action,
        "seq": ev["seq"],
        "operator": operator,
        "time": _now_str(),
        "timestamp": int(time_mod.time() * 1000),
    })
    topic = EVENT_TOPIC_TEMPLATE.format(reachId=ev["reachId"])
    if _client is not None:
        _client.publish(topic, json.dumps(payload, ensure_ascii=False), qos=0)
    print(f"[event-manager] → 广播 {topic} {ev['eventId']} action={action} "
          f"state={ev['state']} seq={ev['seq']} operator={operator}")


def _create_event(reach_id, data, operator, note):
    ev = {
        "eventId": _new_event_id(),
        "reachId": reach_id,
        "state": "OPEN",
        "open_time": _now_str(),
        "handling_time": None,
        "recovered_time": None,
        "closed_time": None,
        "waterLevel": data.get("waterLevel"),
        "turbidity": data.get("turbidity"),
        "flowLevel": data.get("flowLevel"),
        "status": data.get("status"),
        "data_time": data.get("time"),
        "operator": operator,
        "seq": 0,
        "history": [],
        "_handling_epoch": 0.0,
        "_last_data_epoch": time_mod.time(),
    }
    _log_history(ev, "open", operator, note)
    _events[ev["eventId"]] = ev
    print(f"[event-manager] ✚ 新事件 {ev['eventId']} 断面={reach_id} "
          f"status={ev['status']} 水位={ev['waterLevel']} 理由: {note}")
    return ev


# ===================== 数据消息：异常检测 =====================

def handle_data_message(payload):
    """处理 tidewatch/+/data 消息：异常时自动创建/更新事件（重复与乱序消息不生效）"""
    try:
        msg = json.loads(payload.decode("utf-8") if isinstance(payload, bytes) else payload)
    except Exception as e:
        print(f"[event-manager] ✗ 数据消息解析失败: {e}")
        return
    if not isinstance(msg, dict) or "reachId" not in msg:
        print("[event-manager] ✗ 数据消息缺少 reachId，忽略")
        return
    reach_id = msg["reachId"]
    wl, tb = msg.get("waterLevel"), msg.get("turbidity")
    if not isinstance(wl, (int, float)) or not isinstance(tb, (int, float)):
        print(f"[event-manager] ✗ 数据消息缺少有效水位/浊度，忽略: {msg}")
        return

    fingerprint = (msg.get("time"), wl, tb, msg.get("flowLevel"))
    data_time = str(msg.get("time", ""))

    with _lock:
        # 重复消息去重：同一断面同一条消息只生效一次
        if _last_data_key.get(reach_id) == fingerprint:
            print(f"[event-manager] ↺ {reach_id} 重复数据消息，忽略")
            return
        # 乱序/迟到：早于当前最新数据时间戳的消息不得覆盖新状态
        if data_time and data_time < _last_data_time.get(reach_id, ""):
            print(f"[event-manager] ↺ {reach_id} 迟到/乱序数据（{data_time} < "
                  f"{_last_data_time[reach_id]}），忽略")
            return
        _last_data_key[reach_id] = fingerprint
        if data_time:
            _last_data_time[reach_id] = data_time

        status = judge(wl, tb)["status"]
        ev = _active_event_for(reach_id)

        if status != "正常":
            if ev is None:
                ev = _create_event(reach_id, msg, operator="system",
                                   note=f"自动检测到异常（{status}）")
                _broadcast(ev, "open", "system")
            else:
                # 更新活动事件的最新数据
                ev.update({"waterLevel": wl, "turbidity": tb,
                           "flowLevel": msg.get("flowLevel"), "status": status,
                           "data_time": data_time})
                ev["_last_data_epoch"] = time_mod.time()
                print(f"[event-manager] ↻ {reach_id} 事件 {ev['eventId']} 数据更新 "
                      f"（{status}，水位={wl}）")
                _broadcast(ev, ev["state"].lower(), "system")
        else:
            if ev is not None:
                ev.update({"waterLevel": wl, "turbidity": tb,
                           "flowLevel": msg.get("flowLevel"), "status": status,
                           "data_time": data_time})
                ev["_last_data_epoch"] = time_mod.time()
                print(f"[event-manager] ↻ {reach_id} 数据恢复正常（事件 {ev['eventId']} "
                      f"保持 {ev['state']}，等待人工验证恢复）")
                _broadcast(ev, ev["state"].lower(), "system")


# ===================== 事件指令 =====================

def handle_event(event_id, operator="unknown"):
    """OPEN → HANDLING"""
    with _lock:
        ev = _events.get(event_id)
        if ev is None:
            return False, f"事件不存在: {event_id}"
        if ev["state"] != "OPEN":
            return False, f"非法转移: {ev['state']} → HANDLING（仅允许 OPEN → HANDLING）"
        ev["state"] = "HANDLING"
        ev["handling_time"] = _now_str()
        ev["operator"] = operator
        ev["_handling_epoch"] = time_mod.time()
        _log_history(ev, "handling", operator, "开始处理（执行干预动作）")
        _broadcast(ev, "handling", operator)
        return True, f"{event_id} → HANDLING"


def verify_recovery(event_id, operator="unknown"):
    """HANDLING → RECOVERED（必须由干预后的新数据 + judge()==正常 触发）"""
    with _lock:
        ev = _events.get(event_id)
        if ev is None:
            return False, f"事件不存在: {event_id}"
        if ev["state"] != "HANDLING":
            return False, f"非法转移: {ev['state']} → RECOVERED（必须先进入 HANDLING）"
        if ev["waterLevel"] is None or ev["turbidity"] is None:
            return False, "事件缺少有效环境数据，无法验证恢复"
        cur_status = judge(ev["waterLevel"], ev["turbidity"])["status"]
        if cur_status != "正常":
            return False, f"恢复条件未满足：当前数据判定为 {cur_status}（需为 正常）"
        if ev["_last_data_epoch"] <= ev["_handling_epoch"]:
            return False, "尚未收到干预后的新数据，不能确认恢复"
        ev["state"] = "RECOVERED"
        ev["recovered_time"] = _now_str()
        ev["operator"] = operator
        _log_history(ev, "recovered", operator,
                     f"验证恢复：干预后新数据 judge()==正常（水位={ev['waterLevel']}）")
        _broadcast(ev, "recovered", operator)
        return True, f"{event_id} → RECOVERED"


def close_event(event_id, operator="unknown"):
    """OPEN/HANDLING → CLOSED（放弃处理）"""
    with _lock:
        ev = _events.get(event_id)
        if ev is None:
            return False, f"事件不存在: {event_id}"
        if ev["state"] not in ("OPEN", "HANDLING"):
            return False, f"非法转移: {ev['state']} → CLOSED（仅允许 OPEN/HANDLING 关闭）"
        ev["state"] = "CLOSED"
        ev["closed_time"] = _now_str()
        ev["operator"] = operator
        _log_history(ev, "closed", operator, "关闭事件（放弃处理）")
        _broadcast(ev, "closed", operator)
        return True, f"{event_id} → CLOSED"


def open_event_manual(msg):
    """手动开启事件（模拟自动检测，来一次 tidewatch/events 的 open 指令）"""
    reach_id = msg.get("reachId")
    if not reach_id:
        return False, "缺少 reachId"
    operator = msg.get("operator", "unknown")
    with _lock:
        if _active_event_for(reach_id) is not None:
            return False, f"{reach_id} 已有活动事件，拒绝重复开启"
        ev = _create_event(reach_id, msg, operator=operator, note="手动开启事件（open 指令）")
    _broadcast(ev, "open", operator)
    return True, f"事件已创建: {ev['eventId']}"


def handle_event_message(payload):
    """处理 tidewatch/events/+ 消息：广播回显去重；指令按状态机执行"""
    try:
        msg = json.loads(payload.decode("utf-8") if isinstance(payload, bytes) else payload)
    except Exception as e:
        print(f"[event-manager] ✗ 事件消息解析失败: {e}")
        return
    if not isinstance(msg, dict):
        print("[event-manager] ✗ 事件消息不是 JSON 对象，忽略")
        return

    # 广播回显（带 seq 和 state）→ 记录 last_seq；seq 过期/重复的直接丢弃（可观测日志）
    if "seq" in msg and "state" in msg:
        eid, seq = msg.get("eventId"), msg.get("seq")
        with _lock:
            last = _last_seq.get(eid, 0)
            if isinstance(seq, int):
                if seq <= last:
                    print(f"[event-manager] ↺ 丢弃重复/过期事件广播 {eid} seq={seq} <= last_seq={last}")
                else:
                    _last_seq[eid] = seq
        return

    action = msg.get("action")
    operator = msg.get("operator", "unknown")
    if action == "open":
        ok, info = open_event_manual(msg)
        print(f"[event-manager] {'✓' if ok else '✗ 拒绝'} open 指令: {info}")
        return

    event_id = msg.get("eventId")
    if not event_id:
        print(f"[event-manager] ✗ 拒绝指令（缺少 eventId 无法匹配）: {msg}")
        return

    if action == "handling":
        ok, info = handle_event(event_id, operator)
    elif action == "recovered":
        ok, info = verify_recovery(event_id, operator)
    elif action in ("closed", "close"):
        ok, info = close_event(event_id, operator)
    else:
        print(f"[event-manager] ✗ 未知 action: {action!r}")
        return
    print(f"[event-manager] {'✓' if ok else '✗ 拒绝'} {action} 指令（{operator}）: {info}")


# ===================== HTTP API =====================

class EventHandler(BaseHTTPRequestHandler):
    server_version = "TideWatchEvents/1.0"

    def _send_json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            return {}

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        path = self.path.split("?", 1)[0].rstrip("/")
        if path == "/api/events":
            with _lock:
                events = {eid: _public(ev) for eid, ev in _events.items()}
            self._send_json({"count": len(events), "events": events})
        elif path.startswith("/api/events/"):
            eid = path[len("/api/events/"):]
            with _lock:
                ev = _events.get(eid)
                data = _public(ev) if ev else None
            if data is None:
                self._send_json({"error": f"事件不存在: {eid}"}, 404)
            else:
                self._send_json(data)
        else:
            self._send_json({"error": "not found",
                             "endpoints": ["GET /api/events", "GET /api/events/{id}",
                                           "POST /api/events/{id}/handle|verify|close"]}, 404)

    def do_POST(self):
        path = self.path.split("?", 1)[0].rstrip("/")
        parts = path.split("/")
        # /api/events/{id}/{op}
        if len(parts) == 5 and parts[1] == "api" and parts[2] == "events":
            eid, op = parts[3], parts[4]
            body = self._read_body()
            operator = body.get("operator", "api-user")
            if op == "handle":
                ok, info = handle_event(eid, operator)
            elif op == "verify":
                ok, info = verify_recovery(eid, operator)
            elif op == "close":
                ok, info = close_event(eid, operator)
            else:
                self._send_json({"error": f"未知操作: {op}"}, 404)
                return
            with _lock:
                ev = _events.get(eid)
                data = _public(ev) if ev else None
            self._send_json({"ok": ok, "message": info, "event": data}, 200 if ok else 409)
        else:
            self._send_json({"error": "not found"}, 404)

    def log_message(self, fmt, *args):
        print(f"[http:8001] {self.address_string()} {fmt % args}")


# ===================== 主流程 =====================

def _rc_value(rc):
    return rc.value if hasattr(rc, "value") else rc


def main():
    global _client
    if not HAS_PAHO:
        print("[event-manager] ✗ 未安装 paho-mqtt，无法运行")
        sys.exit(1)

    try:
        _client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="tidewatch-event-manager")
    except AttributeError:
        _client = mqtt.Client(client_id="tidewatch-event-manager")

    def on_connect(client, userdata, flags, reason_code, properties=None):
        rc = _rc_value(reason_code)
        if rc == 0:
            print(f"[event-manager] ✓ 已连接 Broker，订阅 {DATA_TOPIC_SUBSCRIBE} 和 {EVENT_TOPIC_SUBSCRIBE}")
            client.subscribe(DATA_TOPIC_SUBSCRIBE)
            client.subscribe(EVENT_TOPIC_SUBSCRIBE)
        else:
            print(f"[event-manager] ✗ 连接 Broker 失败 rc={rc}")

    def on_message(client, userdata, msg):
        if msg.topic.startswith("tidewatch/events/"):
            handle_event_message(msg.payload)
        else:
            handle_data_message(msg.payload)

    def on_disconnect(client, userdata, *args):
        print("[event-manager] ⚠ 与 Broker 断开，等待自动重连 ...")

    _client.on_connect = on_connect
    _client.on_message = on_message
    _client.on_disconnect = on_disconnect
    _client.reconnect_delay_set(min_delay=1, max_delay=10)

    try:
        _client.connect(BROKER_HOST, BROKER_PORT, 60)
    except Exception as e:
        print(f"[event-manager] ✗ 无法连接 Broker：{e}")
        print("[event-manager] 请先启动 mosquitto（bash scripts/start_broker.sh）")
        sys.exit(1)
    _client.loop_start()

    httpd = ThreadingHTTPServer((HTTP_HOST, HTTP_PORT), EventHandler)
    print(f"[event-manager] HTTP API 已启动: http://localhost:{HTTP_PORT}/api/events")
    print("[event-manager] 状态机: OPEN → HANDLING → RECOVERED（+ CLOSED）；异常自动建单")
    print("[event-manager] (Ctrl+C 退出)")

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[event-manager] 正在退出 ...")
    finally:
        httpd.server_close()
        _client.loop_stop()
        _client.disconnect()


if __name__ == "__main__":
    main()
