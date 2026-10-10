#!/usr/bin/env python3
"""D3 Web 事件面板自动验证（Playwright 无头 Chromium）

浏览器内真实点击按钮，指令走 MQTT，状态由 event_manager 权威状态机更新：
  1. 发送洪水预警数据 → 页面出现 OPEN 事件卡片（红）
  2. 点击"开始处理"   → HANDLING（黄）
  3. 发送正常数据     → 状态保持 HANDLING
  4. 点击"验证恢复"   → RECOVERED（绿）
  5. 再发洪水预警     → 出现新的 OPEN 事件卡片
  6. 点击"关闭事件"   → CLOSED（灰）

依赖：pip3 install playwright && python3 -m playwright install chromium
前置：mosquitto + python3 python/shared_state.py + python3 python/event_manager.py
      + cd web && python3 -m http.server 8080
用法：python3 scripts/verify_events_web.py    截图输出到 evidence/D3/
"""
import json
import os
import subprocess
import sys
import time
import urllib.request

from playwright.sync_api import sync_playwright

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(REPO, 'evidence', 'D3')
EVENTS_API = 'http://localhost:8001/api/events'
REACH = 'reach-b'

DUMP_CARDS = """() => [...document.querySelectorAll('.event-card')].map(c => ({
  id: c.id.replace(/^ev-card-/, ''),
  state: c.dataset.state,
  badge: c.querySelector('.event-badge').textContent,
  data: c.querySelector('.event-data').textContent,
  timeline: c.querySelector('.event-timeline').textContent,
  buttons: [...c.querySelectorAll('button')].map(b => b.textContent),
}))"""


def send_data(reach, wl, tb, fl):
    subprocess.run(['python3', os.path.join(REPO, 'scripts', 'send_test_msg.py'),
                    '--reach', reach, '--water', str(wl),
                    '--turbidity', str(tb), '--flow', str(fl)],
                   check=True, capture_output=True, text=True)


def api(path, method='GET'):
    req = urllib.request.Request(EVENTS_API + path, data=(b'{}' if method == 'POST' else None),
                                 method=method, headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.load(r)


def cleanup_active():
    """关闭 reached-b 上次遗留的活动事件，保证测试可重复"""
    for ev in api('')['events'].values():
        if ev['reachId'] == REACH and ev['state'] in ('OPEN', 'HANDLING'):
            print(f'[清理] 关闭遗留活动事件 {ev["eventId"]}（{ev["state"]}）')
            api('/' + ev['eventId'] + '/close', 'POST')


def find_active(cards):
    """按 eventId 无法预知，找到 reach-b 的活动卡片（活动事件排序在最前）"""
    for c in cards:
        if c['state'] in ('OPEN', 'HANDLING'):
            return c
    return None


def main():
    os.makedirs(OUT, exist_ok=True)
    cleanup_active()
    time.sleep(0.5)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={'width': 1440, 'height': 1100})
        page.on('console', lambda msg: print(f'  [console:{msg.type}] {msg.text}'))
        page.goto('http://localhost:8080')
        page.wait_for_timeout(4000)

        print('===== 步骤1: 发送洪水预警数据 → 应出现 OPEN 事件卡片 =====')
        ev1_id = None
        send_data(REACH, 9.0, 20, 2)
        page.wait_for_timeout(1500)
        page.screenshot(path=os.path.join(OUT, 'd3_event_open.png'), full_page=True)
        cards = page.evaluate(DUMP_CARDS)
        card = find_active(cards)
        assert card is not None and card['state'] == 'OPEN', f'未出现 OPEN 卡片: {cards}'
        ev1_id = card['id']
        assert '待处理' in card['badge'], card
        assert '洪水预警' in card['data'], card
        assert card['buttons'] == ['开始处理'], card
        print(f"  ✓ {ev1_id} OPEN（红），按钮={card['buttons']}，数据={card['data']}")

        print('\n===== 步骤2: 点击"开始处理" → HANDLING =====')
        page.locator(f'#ev-card-{ev1_id} button:has-text("开始处理")').click()
        page.wait_for_timeout(1800)
        page.screenshot(path=os.path.join(OUT, 'd3_event_handling.png'), full_page=True)
        card = [c for c in page.evaluate(DUMP_CARDS) if c['id'] == ev1_id][0]
        assert card['state'] == 'HANDLING', card
        assert '处理中' in card['badge'], card
        assert set(card['buttons']) == {'验证恢复', '关闭事件'}, card
        print(f"  ✓ {ev1_id} HANDLING（黄），按钮={card['buttons']}")

        print('\n===== 步骤3: 发送正常数据 → 状态保持 HANDLING（不自动恢复）=====')
        send_data(REACH, 4.5, 20, 0)
        page.wait_for_timeout(1500)
        card = [c for c in page.evaluate(DUMP_CARDS) if c['id'] == ev1_id][0]
        assert card['state'] == 'HANDLING', card
        print('  ✓ 状态保持 HANDLING，等待人工点"验证恢复"')

        print('\n===== 步骤4: 点击"验证恢复" → RECOVERED =====')
        page.locator(f'#ev-card-{ev1_id} button:has-text("验证恢复")').click()
        page.wait_for_timeout(1800)
        page.screenshot(path=os.path.join(OUT, 'd3_event_recovered.png'), full_page=True)
        card = [c for c in page.evaluate(DUMP_CARDS) if c['id'] == ev1_id][0]
        assert card['state'] == 'RECOVERED', card
        assert '已恢复' in card['badge'], card
        assert card['buttons'] == [], card
        assert '恢复' in card['timeline'], card
        print(f"  ✓ {ev1_id} RECOVERED（绿），时间线={card['timeline']}")

        print('\n===== 步骤5: 再发洪水预警 → 出现新的 OPEN 事件 =====')
        send_data(REACH, 9.3, 22, 3)
        page.wait_for_timeout(1500)
        cards = page.evaluate(DUMP_CARDS)
        actives = [c for c in cards if c['state'] == 'OPEN']
        assert actives, f'未出现新的 OPEN 事件: {cards}'
        ev2_id = actives[0]['id']
        assert ev2_id != ev1_id, '应为新事件'
        print(f'  ✓ 新事件 {ev2_id} OPEN（原事件 {ev1_id} 保持 RECOVERED）')

        print('\n===== 步骤6: 点击"开始处理"（→HANDLING），再点"关闭事件" → CLOSED =====')
        page.locator(f'#ev-card-{ev2_id} button:has-text("开始处理")').click()
        page.wait_for_timeout(1800)
        card = [c for c in page.evaluate(DUMP_CARDS) if c['id'] == ev2_id][0]
        assert card['state'] == 'HANDLING', card
        page.locator(f'#ev-card-{ev2_id} button:has-text("关闭事件")').click()
        page.wait_for_timeout(1800)
        page.screenshot(path=os.path.join(OUT, 'd3_event_closed.png'), full_page=True)
        card = [c for c in page.evaluate(DUMP_CARDS) if c['id'] == ev2_id][0]
        assert card['state'] == 'CLOSED', card
        assert '已关闭' in card['badge'], card
        print(f"  ✓ {ev2_id} CLOSED（灰），时间线={card['timeline']}")

        browser.close()
    print('\n全部验证通过 ✓ Web 端完整闭环：OPEN → HANDLING → RECOVERED → 新OPEN → CLOSED')


if __name__ == '__main__':
    sys.exit(main())
