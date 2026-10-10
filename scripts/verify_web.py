#!/usr/bin/env python3
"""TideWatch Web 监测台自动验证（Playwright 无头 Chromium）

覆盖：三断面渲染 / 跨断面不串线 / 断线红色横幅 / 自动重连 + 恢复订阅
依赖：pip3 install playwright && python3 -m playwright install chromium

前置（三个都在运行）：
  1. mosquitto Broker（1883/8083，可通过 brew services 管理）
  2. python3 python/shared_state.py
  3. cd web && python3 -m http.server 8080

用法：python3 scripts/verify_web.py
截图输出到 evidence/D1/
"""
import json
import os
import subprocess
import sys

from playwright.sync_api import sync_playwright

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(REPO, 'evidence', 'D1')

DUMP_JS = """() => {
  const out = {};
  ['reach-a','reach-b','reach-c'].forEach(rid => {
    const c = document.getElementById('card-' + rid);
    out[rid] = {
      waterLevel: c.querySelector('[data-field=waterLevel]').textContent,
      turbidity: c.querySelector('[data-field=turbidity]').textContent,
      flowLevel: c.querySelector('[data-field=flowLevel]').textContent,
      status: c.querySelector('[data-field=status]').textContent,
      statusColor: c.querySelector('[data-field=status]').style.background,
      time: c.querySelector('[data-field=time]').textContent,
      online: c.querySelector('.online-text').textContent,
      topBorder: getComputedStyle(c).borderTopColor
    };
  });
  out.__conn = document.getElementById('conn-text').textContent;
  out.__banner = !document.getElementById('disconnect-banner').classList.contains('hidden');
  return out;
}"""


def send(reach, water, turbidity, flow):
    subprocess.run(['python3', os.path.join(REPO, 'scripts', 'send_test_msg.py'),
                    '--reach', reach, '--water', str(water),
                    '--turbidity', str(turbidity), '--flow', str(flow)],
                   check=True, capture_output=True, text=True)


def main():
    os.makedirs(OUT, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={'width': 1440, 'height': 900})
        page.on('console', lambda msg: print(f'  [console:{msg.type}] {msg.text}'))

        page.goto('http://localhost:8080')
        page.wait_for_timeout(4000)

        print('===== 阶段1: 三断面渲染 =====')
        page.screenshot(path=os.path.join(OUT, 'd1_three_reaches.png'), full_page=True)
        before = page.evaluate(DUMP_JS)
        print(json.dumps(before, ensure_ascii=False, indent=2))

        print('\n===== 阶段2: 发送 reach-b 9.0/20/flow2，验证只有 b 卡片更新 =====')
        send('reach-b', 9.0, 20, 2)
        page.wait_for_timeout(1500)
        after = page.evaluate(DUMP_JS)
        page.screenshot(path=os.path.join(OUT, 'd1_isolation.png'), full_page=True)

        changed = [rid for rid in ['reach-a', 'reach-b', 'reach-c'] if before[rid] != after[rid]]
        print('变化的卡片:', changed)
        assert changed == ['reach-b'], f'✗ 串线或未更新: {changed}'
        assert after['reach-b']['waterLevel'] == '9', after['reach-b']
        assert after['reach-b']['status'] == '洪水预警', after['reach-b']
        print('✓ 只有 reach-b 卡片更新，a/c 保持不变（不串线）')

        print('\n===== 阶段3: 停止 Broker → 红色横幅 =====')
        subprocess.run(['brew', 'services', 'stop', 'mosquitto'], check=True, capture_output=True)
        page.wait_for_timeout(5000)
        disc = page.evaluate(DUMP_JS)
        page.screenshot(path=os.path.join(OUT, 'd1_disconnected.png'), full_page=True)
        assert disc['__banner'] is True, '✗ 断开时横幅未显示'
        assert '未连接' in disc['__conn'], disc['__conn']
        print('✓ 断开时显示红色横幅 + 指示灯红色')

        print('\n===== 阶段4: 恢复 Broker → 自动重连 =====')
        subprocess.run(['brew', 'services', 'start', 'mosquitto'], check=True, capture_output=True)
        page.wait_for_timeout(9000)
        rec = page.evaluate(DUMP_JS)
        page.screenshot(path=os.path.join(OUT, 'd1_reconnected.png'), full_page=True)
        assert rec['__banner'] is False, '✗ 重连后横幅未隐藏'
        assert '已连接' in rec['__conn'], rec['__conn']
        print('✓ 自动重连成功，横幅隐藏、指示灯恢复')

        browser.close()
    print('\n全部验证通过 ✓')


if __name__ == '__main__':
    sys.exit(main())
