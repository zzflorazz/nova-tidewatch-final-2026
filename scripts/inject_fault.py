#!/usr/bin/env python3
# D4 故障注入脚本（演示用）：注入单个故障并打印 现象/恢复方法
# 默认只注入（保持故障以便观察/录屏）；--recover 执行修复
#
# 用法:
#   python3 scripts/inject_fault.py --fault broker-down
#   python3 scripts/inject_fault.py --fault node-stop --reach reach-b
#   python3 scripts/inject_fault.py --fault bad-json --reach reach-a
#   python3 scripts/inject_fault.py --fault status-mismatch --reach reach-c
#   python3 scripts/inject_fault.py --fault seq-duplicate --reach reach-b
#   （加 --recover 对同一故障执行恢复）

import argparse
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
sys.path.insert(0, os.path.join(REPO, 'python'))
from rules import judge

BROKER_HOST, BROKER_PORT = 'localhost', 1883
STATE_API = 'http://localhost:8000/api/state'
EVENTS_API = 'http://localhost:8001/api/events'
DATA_TOPIC = 'tidewatch/{reachId}/data'
EVENT_TOPIC = 'tidewatch/events/{reachId}'

FAULTS = {
    'broker-down': {
        'desc': 'MQTT Broker 断开',
        'inject': '停止 mosquitto 进程（brew services stop mosquitto）',
        'expect': 'shared_state 打印"与 Broker 断开"；Web 页面顶部红色横幅"MQTT 连接已断开"；多端停止更新',
        'recover': '重启 mosquitto（brew services start mosquitto）→ paho 自动重连 → 多端自动恢复订阅',
    },
    'node-stop': {
        'desc': '水文节点停止发布（断面离线）',
        'inject': 'kill 指定 mqtt_node 进程（按 --reach 匹配）',
        'expect': '该断面 last_seen 不再更新，age_seconds 持续增长，>10 秒后 /api/health 标记 online=false；前端卡片变灰"离线"',
        'recover': '重新启动该断面节点：python3 python/mqtt_node.py --reach <reachId> --interval 3',
    },
    'bad-json': {
        'desc': '发送非法 JSON（缺字段 / 非 JSON）',
        'inject': '向 tidewatch/{reach}/data 发布缺字段消息和非 JSON 消息',
        'expect': 'shared_state 打印"拒绝消息"日志，状态字典不更新（数据保持上一次有效值），服务不崩溃',
        'recover': '发送一条合法消息（python3 scripts/send_test_msg.py ...）即恢复正常',
    },
    'status-mismatch': {
        'desc': '状态与数据不一致（status 写错）',
        'inject': '发布 waterLevel=9.0 但 status="正常" 的消息（按规则应为"洪水预警"）',
        'expect': 'shared_state 打印 ⚠ 不一致告警（保留原始值）；Web 端 judge() 二次校验以规则为准，卡片显示"洪水预警"',
        'recover': '发送 status 正确的消息（scripts/send_test_msg.py 自动用 judge 计算）',
    },
    'seq-duplicate': {
        'desc': 'MQTT 事件消息重复（seq 去重）',
        'inject': '对同一个事件发送两条相同 seq 的广播消息',
        'expect': 'event_manager 第二条打印"丢弃重复/过期事件广播"（seq <= last_seq），事件状态不被错误改变',
        'recover': '发送 seq+1 的消息被正常接受，后续流程不受影响',
    },
}


def pub_once(topic, payload):
    """独立连接发布一条消息（可抗 Broker 重启）"""
    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                    client_id='fault-inject-' + str(random.randint(1000, 9999)))
    c.connect(BROKER_HOST, BROKER_PORT, 30)
    c.loop_start()
    c.publish(topic, payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False))
    time.sleep(0.3)
    c.loop_stop()
    c.disconnect()


def http_get(url):
    with urllib.request.urlopen(url, timeout=5) as r:
        return json.load(r)


def now_str():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def send_valid(reach, wl, tb, fl):
    msg = {'reachId': reach, 'waterLevel': wl, 'turbidity': tb, 'flowLevel': fl,
           'status': judge(wl, tb)['status'], 'time': now_str()}
    pub_once(DATA_TOPIC.format(reachId=reach), msg)
    return msg


def find_node_pids(reach):
    out = subprocess.run(['ps', '-eo', 'pid=,command='], capture_output=True, text=True).stdout
    pids = []
    for line in out.splitlines():
        line = line.strip()
        if 'mqtt_node.py' in line and reach in line:
            try:
                pids.append(int(line.split(None, 1)[0]))
            except ValueError:
                pass
    return pids


# ===================== 各故障注入 / 恢复 =====================

def inject_broker_down(recover):
    if recover:
        subprocess.run(['brew', 'services', 'start', 'mosquitto'], check=True, capture_output=True)
        print('  [恢复动作] brew services start mosquitto 已执行')
        print('  [观察] shared_state / event_manager 将在数秒内自动重连（paho 自动重连）')
    else:
        subprocess.run(['brew', 'services', 'stop', 'mosquitto'], check=True, capture_output=True)
        print('  [注入动作] brew services stop mosquitto 已执行（Broker 已停止）')
        print('  [观察] 3 秒后查看 shared_state 终端输出"与 Broker 断开"')


def inject_node_stop(reach, recover):
    if recover:
        log = '/tmp/tidewatch/inject_node.log'
        p = subprocess.Popen(['python3', '-u', os.path.join(REPO, 'python', 'mqtt_node.py'),
                              '--reach', reach, '--interval', '3'],
                             cwd=REPO, stdout=open(log, 'w'), stderr=subprocess.STDOUT,
                             start_new_session=True)
        print(f'  [恢复动作] 已后台重启 {reach} 节点（PID {p.pid}，日志 {log}）')
    else:
        pids = find_node_pids(reach)
        if not pids:
            print(f'  ✗ 未找到 {reach} 的 mqtt_node 进程（节点未运行）')
            sys.exit(2)
        for pid in pids:
            os.kill(pid, signal.SIGTERM)
        print(f'  [注入动作] 已 kill 进程 {pids}（{reach} 水文节点已停止）')
        print('  [观察] 12 秒后查看 http://localhost:8000/api/health：该断面 online=false')


def inject_bad_json(reach, recover):
    if recover:
        msg = send_valid(reach, 4.5, 20, 0)
        print(f'  [恢复动作] 已发送合法消息: {json.dumps(msg, ensure_ascii=False)}')
    else:
        bad1 = {'reachId': reach, 'waterLevel': 4.5, 'status': '正常'}   # 缺 turbidity/flowLevel/time
        pub_once(DATA_TOPIC.format(reachId=reach), bad1)
        print(f'  [注入动作1] 已发布缺字段消息: {json.dumps(bad1, ensure_ascii=False)}')
        pub_once(DATA_TOPIC.format(reachId=reach), 'this is not json')
        print('  [注入动作2] 已发布非 JSON 消息: this is not json')
        print('  [观察] shared_state 终端应打印两条"拒绝消息"，/api/state 中该断面数据保持不变')


def inject_status_mismatch(reach, recover):
    if recover:
        msg = send_valid(reach, 9.0, 20, 2)
        print(f'  [恢复动作] 已发送 status 正确的消息: {json.dumps(msg, ensure_ascii=False)}')
    else:
        msg = {'reachId': reach, 'waterLevel': 9.0, 'turbidity': 20, 'flowLevel': 2,
               'status': '正常', 'time': now_str()}   # 故意把 洪水预警 写成 正常
        pub_once(DATA_TOPIC.format(reachId=reach), msg)
        print(f'  [注入动作] 已发布不一致消息: {json.dumps(msg, ensure_ascii=False)}')
        print('  [观察] shared_state 打印 ⚠ 不一致告警（保留原始值）；Web 监测台应显示"洪水预警"（以规则为准）')


def inject_seq_duplicate(reach, recover):
    # 找一个可用的活动事件；没有就先发洪水数据建单
    events = http_get(EVENTS_API)['events']
    ev = next((e for e in events.values()
               if e['reachId'] == reach and e['state'] == 'OPEN'), None)
    if ev is None:
        print('  [前置] 该断面暂无 OPEN 事件，先发送洪水数据自动建单 ...')
        send_valid(reach, 9.0, 20, 2)
        time.sleep(1.2)
        events = http_get(EVENTS_API)['events']
        ev = next((e for e in events.values()
                   if e['reachId'] == reach and e['state'] == 'OPEN'), None)
    if ev is None:
        print('  ✗ 无法获得事件（请确认 event_manager 已启动）')
        sys.exit(2)

    if recover:
        payload = dict(ev)
        payload['seq'] = ev['seq'] + 1
        pub_once(EVENT_TOPIC.format(reachId=reach), payload)
        print(f'  [恢复动作] 已发送 seq+1（seq={payload["seq"]}）的广播，被正常接受，流程不受影响')
    else:
        payload = dict(ev)   # 原样重复当前广播（seq 相同）
        pub_once(EVENT_TOPIC.format(reachId=reach), payload)
        pub_once(EVENT_TOPIC.format(reachId=reach), payload)
        print(f'  [注入动作] 已重复发布同一 seq={payload.get("seq")} 的事件广播 ×2（{ev["eventId"]}）')
        print('  [观察] event_manager 应打印"丢弃重复/过期事件广播"，事件状态保持不变')


def main():
    parser = argparse.ArgumentParser(description='TideWatch D4 故障注入')
    parser.add_argument('--fault', required=True, choices=list(FAULTS.keys()))
    parser.add_argument('--reach', default='reach-b')
    parser.add_argument('--recover', action='store_true', help='执行恢复而不是注入')
    args = parser.parse_args()

    meta = FAULTS[args.fault]
    print('=' * 64)
    print(f'故障类型: {meta["desc"]}（{args.fault}）')
    print(f'{"恢复动作" if args.recover else "注入动作"}: '
          f'{meta["recover"] if args.recover else meta["inject"]}')
    print(f'预期现象: {meta["expect"]}')
    print(f'恢复方法: {meta["recover"]}')
    print('-' * 64)

    if args.fault == 'broker-down':
        inject_broker_down(args.recover)
    elif args.fault == 'node-stop':
        inject_node_stop(args.reach, args.recover)
    elif args.fault == 'bad-json':
        inject_bad_json(args.reach, args.recover)
    elif args.fault == 'status-mismatch':
        inject_status_mismatch(args.reach, args.recover)
    elif args.fault == 'seq-duplicate':
        inject_seq_duplicate(args.reach, args.recover)
    print('=' * 64)


if __name__ == '__main__':
    main()
