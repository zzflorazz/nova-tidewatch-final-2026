#!/usr/bin/env python3
"""E3 Web—移动端跨端同步自动验证（Playwright 无头 Chromium）

同一个浏览器同时打开桌面端与移动端两个页面，验证：
  1. 一条 MQTT 消息 → 两端同时出现同一个 OPEN 事件（同源）
  2. 【跨端联动①】桌面端点"开始处理" → 移动端事件同步变 HANDLING
  3. 发送正常数据（干预后新数据）
  4. 【跨端联动②】移动端点"验证恢复" → 桌面端事件同步变 RECOVERED
  5. 数据同步：一条新断面消息 → 两端卡片核心字段一致

依赖：pip3 install playwright && python3 -m playwright install chromium
前置：mosquitto + shared_state + event_manager + cd web && python3 -m http.server 8080
用法：python3 scripts/verify_mobile_sync.py    截图输出到 evidence/E3/
"""
import json
import os
import subprocess
import sys
import time
import urllib.request

from playwright.sync_api import sync_playwright

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(REPO, 'evidence', 'E3')
EVENTS_API = 'http://localhost:8001/api/events'
REACH = 'reach-b'

DUMP_CARDS = """() => {
  const out = {};
  ['reach-a','reach-b','reach-c'].forEach(rid => {
    const c = document.getElementById('card-' + rid);
    out[rid] = {
      waterLevel: c.querySelector('[data-field=waterLevel]').textContent,
      turbidity: c.querySelector('[data-field=turbidity]').textContent,
      status: c.querySelector('[data-field=status]').textContent,
    };
  });
  return out;
}"""

DUMP_EVENTS = """() => [...document.querySelectorAll('.event-card, .m-event')].map(c => ({
  id: c.id.replace(/^ev-card-/, ''),
  state: c.dataset.state,
  badge: c.querySelector('.event-badge').textContent,
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
    for ev in api('')['events'].values():
        if ev['reachId'] == REACH and ev['state'] in ('OPEN', 'HANDLING'):
            print(f'[清理] 关闭遗留活动事件 {ev["eventId"]}（{ev["state"]}）')
            api('/' + ev['eventId'] + '/close', 'POST')


def find_event(page, eid=None, active_only=False):
    cards = page.evaluate(DUMP_EVENTS)
    for c in cards:
        if eid and c['id'] != eid:
            continue
        if active_only and c['state'] not in ('OPEN', 'HANDLING'):
            continue
        return c
    return None


def main():
    os.makedirs(OUT, exist_ok=True)
    cleanup_active()
    time.sleep(0.5)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        desk = browser.new_page(viewport={'width': 1440, 'height': 1000})
        mob = browser.new_page(viewport={'width': 390, 'height': 844},
                               is_mobile=True, has_touch=True)
        desk.goto('http://localhost:8080/index.html')
        mob.goto('http://localhost:8080/mobile.html')
        desk.wait_for_timeout(4500)
        mob.wait_for_timeout(1000)

        print('===== 步骤1: 发送一条洪水预警消息 → 两端同时出现同一 OPEN 事件 =====')
        send_data(REACH, 9.0, 20, 2)
        desk.wait_for_timeout(1800)
        de = find_event(desk, active_only=True)
        me = find_event(mob, active_only=True)
        assert de and de['state'] == 'OPEN', f'桌面端无 OPEN: {de}'
        assert me and me['state'] == 'OPEN', f'移动端无 OPEN: {me}'
        assert de['id'] == me['id'], f'两端事件不一致: {de["id"]} vs {me["id"]}'
        eid = de['id']
        mob.screenshot(path=os.path.join(OUT, 'e3_mobile_open_event.png'), full_page=True)
        print(f'  ✓ 两端同一事件 {eid}，均为 OPEN')

        print('\n===== 步骤2: 【跨端联动①】桌面端点"开始处理" → 移动端同步 HANDLING =====')
        desk.locator(f'#ev-card-{eid} button:has-text("开始处理")').click()
        desk.wait_for_timeout(1800)
        me = find_event(mob, eid)
        de = find_event(desk, eid)
        assert me['state'] == 'HANDLING', f'移动端未同步: {me}'
        assert de['state'] == 'HANDLING', de
        assert '处理中' in me['badge'], me
        mob.screenshot(path=os.path.join(OUT, 'e3_cross_desk_click_mobile.png'), full_page=True)
        print(f'  ✓ 桌面操作后移动端同步为 {me["state"]}（黄）')

        print('\n===== 步骤3: 发送正常数据（干预后新数据）=====')
        send_data(REACH, 4.5, 20, 0)
        desk.wait_for_timeout(1500)
        me = find_event(mob, eid)
        assert me['state'] == 'HANDLING', '正常数据不应自动恢复'
        print('  ✓ 两端保持 HANDLING（不自动恢复）')

        print('\n===== 步骤4: 【跨端联动②】移动端点"验证恢复" → 桌面端同步 RECOVERED =====')
        mob.locator(f'#ev-card-{eid} button:has-text("验证恢复")').click()
        mob.wait_for_timeout(1800)
        de = find_event(desk, eid)
        assert de['state'] == 'RECOVERED', f'桌面端未同步: {de}'
        assert '已恢复' in de['badge'], de
        body = mob.inner_text('#event-list')
        assert '当前无待处理事件' in body, f'移动端应无活动事件: {body}'
        desk.screenshot(path=os.path.join(OUT, 'e3_cross_mobile_click_desktop.png'), full_page=True)
        mob.screenshot(path=os.path.join(OUT, 'e3_mobile_after_recovered.png'), full_page=True)
        print(f'  ✓ 移动操作后桌面端同步为 {de["state"]}（绿），移动端活动列表已清空')

        print('\n===== 步骤5: 数据同步——一条新消息 → 两端卡片字段一致 =====')
        send_data('reach-a', 7.6, 20, 0)
        desk.wait_for_timeout(1500)
        dc = desk.evaluate(DUMP_CARDS)
        mc = mob.evaluate(DUMP_CARDS)
        for rid in ('reach-a', 'reach-b', 'reach-c'):
            assert dc[rid] == mc[rid], f'{rid} 两端不一致: {dc[rid]} vs {mc[rid]}'
        assert dc['reach-a']['waterLevel'] == '7.6', dc['reach-a']
        assert dc['reach-a']['status'] == '高水位警戒', dc['reach-a']
        mob.screenshot(path=os.path.join(OUT, 'e3_mobile_data_sync.png'), full_page=True)
        print(f"  ✓ 三断面字段两端完全一致（reach-a 已更新为 7.6 / 高水位警戒）")

        browser.close()
    print('\n全部验证通过 ✓ 跨端联动双向同步 + 数据同源一致')


if __name__ == '__main__':
    sys.exit(main())
