#!/usr/bin/env python3
"""D2 Web 优先关注自动验证（Playwright 无头 Chromium）

流程（不写死任何断面，全部由数据计算）：
  1. 三断面复位为正常        → 优先 = reach-a（同分取第一个），优先度 0
  2. reach-c 洪水预警+湍急   → 优先 = reach-c（8+2+0.5=10.5，连续异常1次）
  3. reach-c 再来一条同类消息 → 连续异常 2 次（8+2+1.0=11）
  4. reach-b 洪水预警+湍流   → 优先 = reach-b（8+3+0.5=11.5），c 复位

依赖：pip3 install playwright && python3 -m playwright install chromium
前置：mosquitto + python3 python/shared_state.py + cd web && python3 -m http.server 8080
用法：python3 scripts/verify_priority_web.py    截图输出到 evidence/D2/
"""
import os
import subprocess
import sys

from playwright.sync_api import sync_playwright

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(REPO, 'evidence', 'D2')

DUMP_JS = """() => ({
  reach: document.getElementById('priority-reach').textContent,
  reason: document.getElementById('priority-reason').textContent,
  score: document.getElementById('priority-score').textContent,
  flagged: ['reach-a','reach-b','reach-c'].filter(
    rid => document.getElementById('card-' + rid).classList.contains('priority'))
})"""


def send(reach, water, turbidity, flow):
    subprocess.run(['python3', os.path.join(REPO, 'scripts', 'send_test_msg.py'),
                    '--reach', reach, '--water', str(water),
                    '--turbidity', str(turbidity), '--flow', str(flow)],
                   check=True, capture_output=True, text=True)


def main():
    os.makedirs(OUT, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={'width': 1440, 'height': 950})
        page.on('console', lambda msg: print(f'  [console:{msg.type}] {msg.text}'))
        page.goto('http://localhost:8080')
        page.wait_for_timeout(4000)

        print('===== 步骤1: 三断面复位为正常 → 优先应为 reach-a（同分取第一个）=====')
        send('reach-a', 4.5, 20, 0)
        send('reach-b', 4.5, 20, 0)
        send('reach-c', 4.5, 20, 0)
        page.wait_for_timeout(1500)
        s1 = page.evaluate(DUMP_JS)
        print(s1)
        assert 'reach-a' in s1['reach'], s1
        assert s1['score'] == '0', s1
        assert s1['flagged'] == ['reach-a'], s1

        print('\n===== 步骤2: reach-c 洪水预警+湍急 → 优先应为 reach-c（10.5）=====')
        send('reach-c', 9.0, 20, 2)
        page.wait_for_timeout(1000)
        s2 = page.evaluate(DUMP_JS)
        print(s2)
        page.screenshot(path=os.path.join(OUT, 'd2_priority_reach-c.png'), full_page=True)
        assert 'reach-c' in s2['reach'], s2
        assert s2['score'] == '10.5', s2
        assert '洪水预警' in s2['reason'] and '湍急' in s2['reason'], s2
        assert '连续异常:1次' in s2['reason'], s2
        assert s2['flagged'] == ['reach-c'], s2

        print('\n===== 步骤3: reach-c 再来一条同类消息 → 连续异常累计（11）=====')
        send('reach-c', 9.0, 20, 2)
        page.wait_for_timeout(1000)
        s3 = page.evaluate(DUMP_JS)
        print(s3)
        assert s3['reach'] == s2['reach'] and s3['score'] == '11', s3

        print('\n===== 步骤4: reach-b 洪水预警+湍流，c 复位 → 优先应变为 reach-b（11.5）=====')
        send('reach-c', 4.5, 20, 0)
        send('reach-b', 9.0, 20, 3)
        page.wait_for_timeout(1200)
        s4 = page.evaluate(DUMP_JS)
        print(s4)
        page.screenshot(path=os.path.join(OUT, 'd2_priority_reach-b.png'), full_page=True)
        assert 'reach-b' in s4['reach'], s4
        assert s4['score'] == '11.5', s4
        assert '湍流' in s4['reason'], s4
        assert s4['flagged'] == ['reach-b'], s4

        browser.close()
    print('\n全部验证通过 ✓ 优先断面随数据变化：reach-a → reach-c(10.5→11) → reach-b(11.5)')


if __name__ == '__main__':
    sys.exit(main())
