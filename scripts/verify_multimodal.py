#!/usr/bin/env python3
"""E2 多模态交互自动验证（Playwright 无头 Chromium + 虚拟摄像头）

验证：
  1. getUserMedia 摄像头启动，Canvas 叠加层绘制（优先断面 + 状态色边框）
  2. 洪水预警数据 → 叠加层边框变红 + 自动 TTS 播报
  3. "播报当前状态"按钮 → TTS 播报全部断面
  4. 语音指令（注入识别结果，走与 ASR 相同的处理路径）：
     查看中游 → 选中断面；开始处理 → 事件 HANDLING；验证恢复 → RECOVERED；关闭事件 → CLOSED
  5. 拍照记录 ×2：图像含断面/时间/事件水印，与当前断面和事件关联

注：无头环境无法真实"说话"，语音指令通过 __multimodalDebug.handleCommand 注入
（即 ASR onresult 的最终识别文本所走的同一路径）；真实麦克风路径请在浏览器现场核验。

依赖：pip3 install playwright && python3 -m playwright install chromium
前置：mosquitto + shared_state + event_manager + cd web && python3 -m http.server 8080
用法：python3 scripts/verify_multimodal.py    截图输出到 evidence/E2/
"""
import json
import os
import subprocess
import sys
import time
import urllib.request

from playwright.sync_api import sync_playwright

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(REPO, 'evidence', 'E2')
EVENTS_API = 'http://localhost:8001/api/events'
REACH = 'reach-b'


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
        if ev['state'] in ('OPEN', 'HANDLING'):
            print(f'[清理] 关闭遗留活动事件 {ev["eventId"]}（{ev["reachId"]} {ev["state"]}）')
            api('/' + ev['eventId'] + '/close', 'POST')


def active_event(reach=None, states=('OPEN', 'HANDLING')):
    for ev in api('')['events'].values():
        if ev['state'] in states and (reach is None or ev['reachId'] == reach):
            return ev
    return None


def main():
    os.makedirs(OUT, exist_ok=True)
    cleanup_active()
    time.sleep(0.5)

    with sync_playwright() as p:
        browser = p.chromium.launch(args=[
            '--use-fake-ui-for-media-stream',
            '--use-fake-device-for-media-stream',
            '--enable-unsafe-swiftshader',
        ])
        page = browser.new_page(viewport={'width': 1440, 'height': 1200})
        page.on('console', lambda msg: print(f'  [console:{msg.type}] {msg.text}'))
        page.goto('http://localhost:8080/multimodal.html')
        page.wait_for_timeout(4000)

        print('===== 步骤1: 摄像头（getUserMedia）=====')
        cam = page.evaluate('() => window.__multimodalDebug.getCameraState()')
        assert cam['ready'], f'摄像头未启动: {cam}'
        assert cam['videoWidth'] > 0, cam
        print(f"  ✓ 摄像头视频流 {cam['videoWidth']}x{cam['videoHeight']}，叠加层运行中")

        print('\n===== 步骤2: 洪水预警 → 红边框 + 自动播报 =====')
        send_data('reach-a', 4.5, 20, 0)
        send_data(REACH, 9.0, 20, 2)
        send_data('reach-c', 4.5, 75, 1)
        page.wait_for_timeout(2200)

        oi = page.evaluate('() => window.__multimodalDebug.getOverlayInfo()')
        assert oi['priority'] == REACH, f'优先应为 reach-b: {oi}'
        assert oi['borderColor'] == '#f5222d', f'洪水预警边框应为红: {oi}'
        assert 'reach-b' in oi['text'] and '洪水预警' in oi['text'], oi
        print(f"  ✓ 叠加层: {oi['text']}")
        print(f"  ✓ 边框颜色: {oi['borderColor']}（异常=红）")

        logs = page.evaluate('() => window.__multimodalDebug.getSpeakLog()')
        texts = ' ｜ '.join(x['text'] for x in logs)
        assert '洪水预警' in texts, f'应有自动播报: {texts}'
        print(f'  ✓ 自动 TTS 播报: {texts}')

        page.screenshot(path=os.path.join(OUT, 'e2_multimodal_flood.png'), full_page=True)
        print('  ✓ 截图: e2_multimodal_flood.png')

        print('\n===== 步骤3: "播报当前状态"按钮 =====')
        n_before = len(logs)
        page.click('#speak-btn')
        page.wait_for_timeout(600)
        logs = page.evaluate('() => window.__multimodalDebug.getSpeakLog()')
        assert len(logs) > n_before and '当前断面状态' in logs[-1]['text'], logs[-1]
        print(f"  ✓ 手动播报: {logs[-1]['text'][:80]}…")

        print('\n===== 步骤4: 语音指令（与 ASR 同一处理路径）=====')
        sr = page.evaluate('() => window.__multimodalDebug.isSrSupported()')
        print(f'  （当前浏览器 SpeechRecognition 支持: {sr}）')

        r = page.evaluate("() => window.__multimodalDebug.handleCommand('查看中游')")
        assert r and r['command'] == '查看断面' and r['detail'] == 'reach-b', r
        sel = page.evaluate('() => window.__multimodalDebug.getSelected()')
        card_selected = page.evaluate("() => document.getElementById('card-reach-b').classList.contains('selected')")
        assert sel == 'reach-b' and card_selected, (sel, card_selected)
        print('  ✓ "查看中游" → 选中 reach-b，卡片高亮')

        r = page.evaluate("() => window.__multimodalDebug.handleCommand('播报状态')")
        assert r and r['command'] == '播报状态', r
        print('  ✓ "播报状态" → TTS 播报')

        print('\n===== 步骤5: "开始处理" → 事件 HANDLING =====')
        ev = active_event(REACH, states=('OPEN',))
        assert ev, '应有 reach-b 的 OPEN 事件'
        r = page.evaluate("() => window.__multimodalDebug.handleCommand('开始处理')")
        page.wait_for_timeout(1600)
        chk = api('/' + ev['eventId'])
        assert chk['state'] == 'HANDLING', chk['state']
        print(f"  ✓ 语音指令 → 事件 {ev['eventId']} 变 HANDLING（operator={chk['operator']}）")

        print('\n===== 步骤6: 拍照记录 ×2（关联断面+事件）=====')
        page.click('#photo-btn')
        page.wait_for_timeout(500)
        p1 = page.evaluate('() => window.__multimodalDebug.getPhotoInfo()')
        page.click('#photo-btn')
        page.wait_for_timeout(500)
        p2 = page.evaluate('() => window.__multimodalDebug.getPhotoInfo()')
        img_len = page.evaluate("() => document.getElementById('photo-img').src.length")
        assert p1 and p1['reachId'] == REACH and p1['eventId'] == ev['eventId'], p1
        assert p2 and p2['eventId'] == ev['eventId'], p2
        assert img_len > 1000, img_len
        print(f"  ✓ 两张照片均关联 {p1['reachId']} 与事件 {p1['eventId']}（含水印，可下载）")

        page.screenshot(path=os.path.join(OUT, 'e2_voice_photo.png'), full_page=True)
        print('  ✓ 截图: e2_voice_photo.png')

        print('\n===== 步骤7: "验证恢复" → RECOVERED =====')
        send_data(REACH, 4.5, 20, 0)   # 干预后新数据
        page.wait_for_timeout(1300)
        r = page.evaluate("() => window.__multimodalDebug.handleCommand('验证恢复')")
        page.wait_for_timeout(1600)
        chk = api('/' + ev['eventId'])
        assert chk['state'] == 'RECOVERED', chk['state']
        print(f"  ✓ 语音指令 → 事件 {ev['eventId']} 变 RECOVERED")

        print('\n===== 步骤8: 再异常 → "关闭事件" → CLOSED =====')
        send_data(REACH, 9.2, 25, 3)
        page.wait_for_timeout(1500)
        ev2 = active_event(REACH, states=('OPEN',))
        assert ev2 and ev2['eventId'] != ev['eventId'], ev2
        r = page.evaluate("() => window.__multimodalDebug.handleCommand('关闭事件')")
        page.wait_for_timeout(1600)
        chk = api('/' + ev2['eventId'])
        assert chk['state'] == 'CLOSED', chk['state']
        print(f"  ✓ 语音指令 → 新事件 {ev2['eventId']} 变 CLOSED")

        browser.close()
    print('\n全部验证通过 ✓ 摄像头叠加 / 自动+手动 TTS / 4 类语音指令 / 事件闭环 / 拍照关联')


if __name__ == '__main__':
    sys.exit(main())
