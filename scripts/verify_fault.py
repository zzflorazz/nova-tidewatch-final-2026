#!/usr/bin/env python3
# D4 故障注入与修复自动验证：5 种故障 → 观察 → 定位 → 修复 → 重新运行 → 验证
# 自动启动/停止 shared_state / event_manager / mqtt_node / web 服务器（Broker 复用系统服务）
#
# 用法: python3 scripts/verify_fault.py
# 证据输出: evidence/D4/fault1..5_*.txt + evidence/D4/logs/

import json
import os
import random
import signal
import subprocess
import sys
import time
import urllib.request
from datetime import datetime

import paho.mqtt.client as mqtt

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(REPO, 'evidence', 'D4')
LOGS = os.path.join(OUT, 'logs')
STATE_API = 'http://localhost:8000/api/state'
HEALTH_API = 'http://localhost:8000/api/health'
EVENTS_API = 'http://localhost:8001/api/events'
DATA_TOPIC = 'tidewatch/{reachId}/data'
EVENT_TOPIC = 'tidewatch/events/{reachId}'

sys.path.insert(0, os.path.join(REPO, 'python'))
from rules import judge


def now_str():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


# ===================== 基础设施 =====================

class Service:
    def __init__(self, name, cmd, log_path, cwd=REPO):
        self.name = name
        self.cmd = cmd
        self.log_path = log_path
        self.cwd = cwd
        self.proc = None

    def start(self):
        f = open(self.log_path, 'w')
        self.proc = subprocess.Popen(self.cmd, cwd=self.cwd, stdout=f,
                                     stderr=subprocess.STDOUT, start_new_session=True)
        f.close()
        return self

    def stop(self):
        if self.proc and self.proc.poll() is None:
            try:
                os.killpg(os.getpgid(self.proc.pid), signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(os.getpgid(self.proc.pid), signal.SIGKILL)
                except ProcessLookupError:
                    pass

    def alive(self):
        return self.proc is not None and self.proc.poll() is None


def port_busy(port):
    out = subprocess.run(['lsof', '-nP', f'-i:{port}'], capture_output=True).stdout
    return len(out) > 0


def wait_port(port, timeout=10):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if port_busy(port):
            return True
        time.sleep(0.3)
    return False


def count_log(path, pattern):
    try:
        with open(path, 'r', errors='ignore') as f:
            return sum(1 for line in f if pattern in line)
    except FileNotFoundError:
        return 0


def wait_log(path, pattern, min_count, timeout=15):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if count_log(path, pattern) >= min_count:
            return True
        time.sleep(0.3)
    return False


def log_lines(path, pattern, limit=8):
    try:
        with open(path, 'r', errors='ignore') as f:
            hits = [line.rstrip() for line in f if pattern in line]
        return hits[-limit:]
    except FileNotFoundError:
        return []


def pub_once(topic, payload):
    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                    client_id='d4-verify-' + str(random.randint(1000, 9999)))
    c.connect('localhost', 1883, 30)
    c.loop_start()
    c.publish(topic, payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False))
    time.sleep(0.3)
    c.loop_stop()
    c.disconnect()


def send_valid(reach, wl, tb, fl):
    msg = {'reachId': reach, 'waterLevel': wl, 'turbidity': tb, 'flowLevel': fl,
           'status': judge(wl, tb)['status'], 'time': now_str()}
    pub_once(DATA_TOPIC.format(reachId=reach), msg)
    return msg


def http_get(url):
    with urllib.request.urlopen(url, timeout=5) as r:
        return json.load(r)


def write_evidence(filename, text):
    path = os.path.join(OUT, filename)
    with open(path, 'w') as f:
        f.write(text)
    print(f'  [证据] 已写入 {os.path.relpath(path, REPO)}')


# ===================== 5 种故障 =====================

def fault1_broker_down(ctx):
    """Broker 断开 → 观察 → 重启 → 验证链路恢复"""
    sh = ctx['shared_state'].log_path
    text = [f'# 故障1：MQTT Broker 断开与自动重连', f'时间: {now_str()}', '']
    text.append('[注入动作] brew services stop mosquitto')
    c_disc0 = count_log(sh, '与 Broker 断开')
    c_conn0 = count_log(sh, '已连接 Broker')
    subprocess.run(['brew', 'services', 'stop', 'mosquitto'], check=True, capture_output=True)
    ok1 = wait_log(sh, '与 Broker 断开', c_disc0 + 1, timeout=10)
    text.append(f'[现象] shared_state 检测到断开: {"✓" if ok1 else "✗"}')
    text += [f'  {l}' for l in log_lines(sh, '与 Broker 断开', 1)]

    text.append('\n[修复动作] brew services start mosquitto')
    subprocess.run(['brew', 'services', 'start', 'mosquitto'], check=True, capture_output=True)
    ok2 = wait_log(sh, '已连接 Broker', c_conn0 + 1, timeout=30)
    text.append(f'[现象] shared_state 自动重连成功: {"✓" if ok2 else "✗"}')
    text += [f'  {l}' for l in log_lines(sh, '已连接 Broker', 1)]

    # 验证恢复：重新跑同一链路（发一条消息 → API 能收到）
    send_valid('reach-a', 5.5, 30, 1)
    ok3 = False
    t0 = time.time()
    while time.time() - t0 < 10:
        try:
            if http_get(f'{STATE_API}/reach-a')['waterLevel'] == 5.5:
                ok3 = True
                break
        except Exception:
            pass
        time.sleep(0.5)
    text.append(f'[验证恢复] 重跑链路（发布 reach-a 5.5 → /api/state 收到）: {"✓" if ok3 else "✗"}')
    return (ok1 and ok2 and ok3), '\n'.join(text)


def fault2_node_stop(ctx):
    """节点停止 → age 增长 → offline → 重启 → online"""
    text = [f'# 故障2：水文节点停止发布（reach-b 离线）', f'时间: {now_str()}', '']
    node = ctx['node']
    if not node.alive():
        node.start()
    ok0 = False
    t0 = time.time()
    while time.time() - t0 < 12:
        try:
            rec = http_get(HEALTH_API)['reaches'].get('reach-b')
            if rec and rec['online'] and rec['age_seconds'] < 5:
                ok0 = True
                text.append(f'[前置] reach-b 在线: age={rec["age_seconds"]}s online={rec["online"]}')
                break
        except Exception:
            pass
        time.sleep(0.6)

    text.append('[注入动作] kill reach-b 水文节点进程')
    node.stop()
    samples = []
    for i in range(7):
        time.sleep(2)
        try:
            rec = http_get(HEALTH_API)['reaches'].get('reach-b')
            samples.append(f'  t+{(i + 1) * 2:>2}s  age={rec["age_seconds"]:>5}s  online={rec["online"]}')
        except Exception as e:
            samples.append(f'  t+{(i + 1) * 2:>2}s  查询失败: {e}')
    text += ['[现象] age_seconds 变化过程:', *samples]
    ok1 = bool(samples) and 'online=False' in samples[-1]
    text.append(f'[现象] >10 秒后标记离线: {"✓" if ok1 else "✗"}')

    text.append('\n[修复动作] 重新启动节点: python3 python/mqtt_node.py --reach reach-b --interval 2')
    node.start()
    ok2 = False
    t0 = time.time()
    while time.time() - t0 < 10:
        try:
            rec = http_get(HEALTH_API)['reaches'].get('reach-b')
            if rec['online']:
                ok2 = True
                text.append(f'[验证恢复] 节点重启后重新上线: age={rec["age_seconds"]}s online=true ✓')
                break
        except Exception:
            pass
        time.sleep(0.6)
    if not ok2:
        text.append('[验证恢复] 节点重启后重新上线: ✗')
    return (ok0 and ok1 and ok2), '\n'.join(text)


def fault3_bad_json(ctx):
    """非法 JSON → 拒绝 → 状态不被污染"""
    sh = ctx['shared_state'].log_path
    text = [f'# 故障3：非法 JSON（缺字段 / 非 JSON）', f'时间: {now_str()}', '']
    before = http_get(f'{STATE_API}/reach-a')
    keys = ['reachId', 'waterLevel', 'turbidity', 'flowLevel', 'status', 'time', 'last_seen']
    before_cmp = {k: before[k] for k in keys}
    text.append(f'[前置] reach-a 当前状态: {json.dumps(before_cmp, ensure_ascii=False)}')

    c0 = count_log(sh, '拒绝消息')
    bad1 = {'reachId': 'reach-a', 'waterLevel': 4.5, 'status': '正常'}
    pub_once(DATA_TOPIC.format(reachId='reach-a'), bad1)
    text.append(f'[注入动作1] 发布缺字段消息: {json.dumps(bad1, ensure_ascii=False)}')
    pub_once(DATA_TOPIC.format(reachId='reach-a'), 'this is not json')
    text.append('[注入动作2] 发布非 JSON 消息: this is not json')
    time.sleep(1.2)

    ok1 = wait_log(sh, '拒绝消息', c0 + 2, timeout=5)
    text.append(f'[现象] shared_state 拒绝日志 ≥2 条: {"✓" if ok1 else "✗"}')
    text += [f'  {l}' for l in log_lines(sh, '拒绝消息', 2)]

    after = http_get(f'{STATE_API}/reach-a')
    after_cmp = {k: after[k] for k in keys}
    ok2 = before_cmp == after_cmp
    text.append(f'\n[验证] 状态未被污染（前后逐字段一致）: {"✓" if ok2 else "✗"}')
    text.append(f'  注入后状态: {json.dumps(after_cmp, ensure_ascii=False)}')

    text.append('[修复动作] 发送合法消息恢复（无需重启）')
    msg = send_valid('reach-a', 6.0, 25, 1)
    time.sleep(0.8)
    rec = http_get(f'{STATE_API}/reach-a')
    ok3 = rec['waterLevel'] == 6.0 and rec['status'] == '正常'
    text.append(f'[验证恢复] 合法消息生效: {json.dumps(msg, ensure_ascii=False)} → {"✓" if ok3 else "✗"}')
    return (ok1 and ok2 and ok3), '\n'.join(text)


def fault4_status_mismatch(ctx):
    """status 写错 → shared_state 告警保留原始值 → Web 端以规则为准显示"""
    from playwright.sync_api import sync_playwright
    sh = ctx['shared_state'].log_path
    text = [f'# 故障4：状态与数据不一致（status 写错）', f'时间: {now_str()}', '']

    c0 = count_log(sh, 'status 与判级规则不一致')
    msg = {'reachId': 'reach-c', 'waterLevel': 9.0, 'turbidity': 20, 'flowLevel': 2,
           'status': '正常', 'time': now_str()}   # 9.0 按规则应为洪水预警
    pub_once(DATA_TOPIC.format(reachId='reach-c'), msg)
    text.append(f'[注入动作] 发布不一致消息: {json.dumps(msg, ensure_ascii=False)}')

    console_msgs = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={'width': 1440, 'height': 950})
        page.on('console', lambda m: console_msgs.append(f'[{m.type}] {m.text}'))
        page.goto('http://localhost:8080/index.html')
        page.wait_for_timeout(2500)
        pub_once(DATA_TOPIC.format(reachId='reach-c'), msg)   # 页面订阅期间重放一次
        page.wait_for_timeout(1500)
        dom_status = page.inner_text('#card-reach-c [data-field="status"]')
        browser.close()

    ok1 = wait_log(sh, 'status 与判级规则不一致', c0 + 1, timeout=5)
    text.append(f'[现象] shared_state ⚠ 不一致告警（保留原始值）: {"✓" if ok1 else "✗"}')
    text += [f'  {l}' for l in log_lines(sh, 'status 与判级规则不一致', 1)]

    raw = http_get(f'{STATE_API}/reach-c')['status']
    text.append(f'[分层验证] shared_state 原始 status 保留为 "{raw}"（>100ms 说明: 原始值未被改写）')

    warn = next((m for m in console_msgs if 'status 不一致' in m and 'reach-c' in m), None)
    ok2 = warn is not None
    text.append(f'[现象] Web 端二次校验告警: {"✓" if ok2 else "✗"}')
    if warn:
        text.append(f'  {warn}')

    ok3 = dom_status == '洪水预警'
    text.append(f'[验证] Web 卡片以规则为准显示 "{dom_status}"（期望 洪水预警）: {"✓" if ok3 else "✗"}')

    text.append('[修复动作] 发送 status 正确的消息')
    fixed = send_valid('reach-c', 9.0, 20, 2)
    text.append(f'  → {json.dumps(fixed, ensure_ascii=False)}（status 由 judge 计算，不再告警）')
    return (ok1 and ok2 and ok3), '\n'.join(text)


def fault5_seq_duplicate(ctx):
    """重复 seq 广播 → 丢弃 → seq+1 正常接受"""
    em = ctx['event_manager'].log_path
    text = [f'# 故障5：MQTT 事件消息重复（seq 去重）', f'时间: {now_str()}', '']
    ctx['node'].stop()   # 停节点，保证事件广播只来自本测试
    time.sleep(0.5)

    # 清理并新建一个 OPEN 事件
    for ev in http_get(EVENTS_API)['events'].values():
        if ev['reachId'] == 'reach-b' and ev['state'] in ('OPEN', 'HANDLING'):
            req = urllib.request.Request(f'{EVENTS_API}/{ev["eventId"]}/close', data=b'{}',
                                         method='POST', headers={'Content-Type': 'application/json'})
            urllib.request.urlopen(req, timeout=5)
    send_valid('reach-b', 9.0, 20, 2)
    time.sleep(1.2)
    ev = next((e for e in http_get(EVENTS_API)['events'].values()
               if e['reachId'] == 'reach-b' and e['state'] == 'OPEN'), None)
    assert ev is not None, '未能创建 OPEN 事件'
    text.append(f'[前置] 已创建事件 {ev["eventId"]}（state=OPEN, seq={ev["seq"]}）')

    c0 = count_log(em, '丢弃重复/过期事件广播')
    payload = dict(ev)   # 原样重复当前广播
    text.append(f'[注入动作] 重复发布相同 seq={payload["seq"]} 的广播 ×2')
    pub_once(EVENT_TOPIC.format(reachId='reach-b'), payload)
    pub_once(EVENT_TOPIC.format(reachId='reach-b'), payload)
    time.sleep(1.0)
    dropped = count_log(em, '丢弃重复/过期事件广播') - c0
    ok1 = dropped >= 2
    text.append(f'[现象] 两条重复被丢弃（丢弃日志 +{dropped}）: {"✓" if ok1 else "✗"}')
    text += [f'  {l}' for l in log_lines(em, '丢弃重复/过期事件广播', 2)]

    # 恢复：seq+1 被正常接受，再重复仍被丢弃
    c1 = count_log(em, '丢弃重复/过期事件广播')
    payload2 = dict(ev)
    payload2['seq'] = ev['seq'] + 1
    text.append(f'[修复动作] 发送 seq+1（seq={payload2["seq"]}）的广播')
    pub_once(EVENT_TOPIC.format(reachId='reach-b'), payload2)
    time.sleep(0.6)
    accepted_no_drop = (count_log(em, '丢弃重复/过期事件广播') == c1)
    text.append(f'[验证恢复] seq+1 被正常接受（无丢弃日志）: {"✓" if accepted_no_drop else "✗"}')
    pub_once(EVENT_TOPIC.format(reachId='reach-b'), payload2)
    time.sleep(0.6)
    dropped2 = count_log(em, '丢弃重复/过期事件广播') - c1
    ok2 = dropped2 >= 1
    text.append(f'[验证恢复] 新 seq 的重复仍被丢弃（+{dropped2}）: {"✓" if ok2 else "✗"}')

    # 事件本身从未被错误改变
    cur = http_get(f'{EVENTS_API}/{ev["eventId"]}')
    ok3 = cur['state'] == 'OPEN' and cur['seq'] == ev['seq']
    text.append(f'[验证] 事件状态未被重复消息改变: state={cur["state"]}, seq={cur["seq"]} {"✓" if ok3 else "✗"}')
    return (ok1 and ok2 and ok3), '\n'.join(text)


# ===================== 主流程 =====================

def main():
    os.makedirs(LOGS, exist_ok=True)

    print('=' * 64)
    print('D4 故障注入与修复自动验证')
    print('=' * 64)

    # 预检：端口
    for port in (8000, 8001, 8080):
        if port_busy(port):
            print(f'✗ 端口 {port} 已被占用，请先停止手动启动的服务再运行本脚本')
            sys.exit(1)

    # Broker（复用系统服务）
    if not port_busy(1883):
        print('[前置] Broker 未运行，启动 brew mosquitto ...')
        subprocess.run(['brew', 'services', 'start', 'mosquitto'], check=True, capture_output=True)
        if not wait_port(1883, 15):
            print('✗ Broker 启动失败')
            sys.exit(1)
    print('[前置] Broker OK (1883)')

    # 启动服务
    ctx = {}
    ctx['shared_state'] = Service('shared_state',
                                  ['python3', '-u', os.path.join(REPO, 'python', 'shared_state.py')],
                                  os.path.join(LOGS, 'shared_state.log')).start()
    ctx['event_manager'] = Service('event_manager',
                                   ['python3', '-u', os.path.join(REPO, 'python', 'event_manager.py')],
                                   os.path.join(LOGS, 'event_manager.log')).start()
    ctx['node'] = Service('mqtt_node',
                          ['python3', '-u', os.path.join(REPO, 'python', 'mqtt_node.py'),
                           '--reach', 'reach-b', '--interval', '2'],
                          os.path.join(LOGS, 'mqtt_node.log')).start()
    ctx['web'] = Service('web', ['python3', '-u', '-m', 'http.server', '8080'],
                         os.path.join(LOGS, 'web.log'), cwd=os.path.join(REPO, 'web')).start()

    for name, port in (('shared_state', 8000), ('event_manager', 8001), ('web', 8080)):
        if not wait_port(port, 10):
            print(f'✗ {name} 启动失败（端口 {port} 未监听），查看 {LOGS}/{name}.log')
            for s in ctx.values():
                s.stop()
            sys.exit(1)
    print('[前置] shared_state(8000) / event_manager(8001) / node(reach-b) / web(8080) 均已启动')
    time.sleep(1)

    results = []
    tests = [
        ('故障1 Broker 断开与自动重连', fault1_broker_down, 'fault1_broker_down.txt'),
        ('故障2 节点停止（断面离线）', fault2_node_stop, 'fault2_node_stop.txt'),
        ('故障3 非法 JSON 拒绝', fault3_bad_json, 'fault3_bad_json.txt'),
        ('故障4 status 不一致二次校验', fault4_status_mismatch, 'fault4_status_mismatch.txt'),
        ('故障5 seq 重复去重', fault5_seq_duplicate, 'fault5_seq_duplicate.txt'),
    ]
    for title, fn, evidence_file in tests:
        print(f'\n----- {title} -----')
        try:
            ok, text = fn(ctx)
        except Exception as e:
            ok, text = False, f'[异常] {type(e).__name__}: {e}'
            print(f'  ✗ 执行异常: {e}')
        results.append((title, ok))
        print(text)
        write_evidence(evidence_file, text + '\n')

    # 清理自启动服务
    for s in ctx.values():
        s.stop()
    print('\n[清理] 已停止本次启动的服务进程')

    print('\n' + '=' * 64)
    print('D4 验证结果:')
    all_ok = True
    for title, ok in results:
        print(f'  {"PASS" if ok else "FAIL"}  {title}')
        all_ok = all_ok and ok
    print('=' * 64)
    print('D4 验证通过 ✓（5 种故障全部 PASS）' if all_ok else 'D4 验证未通过 ✗')
    sys.exit(0 if all_ok else 1)


if __name__ == '__main__':
    main()
