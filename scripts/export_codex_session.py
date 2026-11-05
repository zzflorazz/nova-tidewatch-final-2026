#!/usr/bin/env python3
"""从 Codex 原始 JSONL 导出对话 PDF。

输出：evidence/trace_codex_session.pdf
流程：JSONL → HTML → Playwright printToPDF

标注：这是从原始 JSONL 重新排版的对话 PDF，不是 Codex GUI 原始截图。
"""

import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

MAIN_JSONL = Path("/Users/xiaoyi/.codex/sessions/2026/11/02/rollout-2026-11-02T13-47-28-01a19acc-641a-7290-af23-1ef8e1da6be0.jsonl")
OUT_PDF = Path(__file__).resolve().parents[1] / "evidence" / "trace_codex_session.pdf"
OUT_HTML = Path(__file__).resolve().parents[1] / "evidence" / "trace_codex_session_temp.html"


def extract_messages(jsonl_path: Path):
    msgs = []
    for n, line in enumerate(jsonl_path.open(encoding="utf-8"), 1):
        d = json.loads(line)
        x = d.get("payload", d)
        role = x.get("role")
        content = x.get("content")
        if role not in ("user", "assistant") or not isinstance(content, list):
            continue
        text_parts = []
        for c in content:
            if isinstance(c, dict):
                t = c.get("text", "")
                if t:
                    text_parts.append(t)
        text = "\n".join(text_parts).strip()
        if not text:
            continue
        ts = None
        if "timestamp" in d:
            try:
                ts = datetime.fromisoformat(d["timestamp"].replace("Z", "+00:00"))
                ts = ts.astimezone(timezone(timedelta(hours=8)))
            except Exception:
                pass
        msgs.append({
            "line": n,
            "role": role,
            "text": text,
            "timestamp": ts,
        })
    return msgs


def html_escape(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def render_html(msgs, session_path: str):
    parts = []
    parts.append("<!DOCTYPE html>")
    parts.append('<html><head><meta charset="utf-8">')
    parts.append("<title>TideWatch Trace Codex 对话</title>")
    parts.append("""
<style>
  @page { margin: 18mm 16mm; }
  body { font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif;
         color: #1f2937; max-width: 820px; margin: 0 auto; padding: 24px; font-size: 13px; line-height: 1.6; }
  .banner { background: #fff3cd; border: 1px solid #e0a800; color: #6c4a00;
            padding: 10px 14px; border-radius: 6px; font-size: 12px; margin-bottom: 18px; }
  .banner strong { color: #5a3e00; }
  h1 { font-size: 22px; margin-bottom: 4px; }
  .meta { color: #6b7280; font-size: 12px; margin-bottom: 22px; }
  .meta code { background: #f3f4f6; padding: 1px 5px; border-radius: 3px; }
  .msg { margin-bottom: 16px; padding: 12px 16px; border-radius: 8px; page-break-inside: avoid; }
  .msg.user { background: #eff6ff; border-left: 4px solid #3b82f6; }
  .msg.assistant { background: #f0fdf4; border-left: 4px solid #22c55e; }
  .role { font-weight: 700; font-size: 12px; margin-bottom: 4px; }
  .role.user { color: #1d4ed8; }
  .role.assistant { color: #15803d; }
  .ts { color: #9ca3af; font-size: 11px; margin-left: 6px; font-weight: normal; }
  .body { white-space: pre-wrap; word-break: break-word; }
  .footer { margin-top: 30px; padding-top: 14px; border-top: 1px solid #e5e7eb;
            font-size: 11px; color: #9ca3af; text-align: center; }
  code { background: #f3f4f6; padding: 1px 5px; border-radius: 3px; font-family: Menlo, Consolas, monospace; font-size: 12px; }
  pre { background: #f3f4f6; padding: 10px 12px; border-radius: 6px; overflow-x: auto;
        font-family: Menlo, Consolas, monospace; font-size: 11.5px; line-height: 1.55; }
  pre code { background: none; padding: 0; }
</style>
""")
    parts.append("</head><body>")

    parts.append("""
<div class="banner">
  <strong>格式说明：</strong>本 PDF 由 Codex 原始 JSONL 会话文件重新排版生成，
  不是 Codex GUI 原始截图或原生 PDF。所有消息均来自会话日志中的 user/assistant 角色文本。
  生成日期：2026-11-05
</div>
""")

    parts.append("<h1>TideWatch Trace V1.0 · Codex 协同对话</h1>")
    parts.append(f'<div class="meta">会话文件：<code>{html_escape(session_path)}</code></div>')

    for m in msgs:
        ts_str = m["timestamp"].strftime("%Y-%m-%d %H:%M:%S CST") if m["timestamp"] else ""
        body = html_escape(m["text"])
        # 把 markdown ```...``` 代码块保留为 pre
        body = body.replace("```", "")
        parts.append(f'<div class="msg {m["role"]}">')
        parts.append(f'<div class="role {m["role"]}">{m["role"].upper()}<span class="ts">JSONL line {m["line"]} · {ts_str}</span></div>')
        parts.append(f'<div class="body">{body}</div>')
        parts.append("</div>")

    parts.append("""
<div class="footer">
  本 PDF 由 /Users/xiaoyi/nova-tidewatch-final-2026/scripts/export_codex_session.py 生成<br>
  数据来源：~/.codex/sessions/2026/11/02/rollout-2026-11-02T13-47-28-01a19acc-641a-7290-af23-1ef8e1da6be0.jsonl
</div>
""")
    parts.append("</body></html>")
    return "\n".join(parts)


def main():
    msgs = extract_messages(MAIN_JSONL)
    print(f"提取消息：user={sum(1 for m in msgs if m['role']=='user')} "
          f"assistant={sum(1 for m in msgs if m['role']=='assistant')} "
          f"共 {len(msgs)} 条")

    html = render_html(msgs, str(MAIN_JSONL))
    OUT_HTML.write_text(html, encoding="utf-8")
    print(f"HTML 已写入：{OUT_HTML}")

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("playwright 未安装", file=sys.stderr)
        sys.exit(1)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(f"file://{OUT_HTML}")
        page.pdf(
            path=str(OUT_PDF),
            format="A4",
            print_background=True,
            margin={"top": "18mm", "bottom": "18mm", "left": "16mm", "right": "16mm"},
        )
        browser.close()

    size_kb = OUT_PDF.stat().st_size / 1024
    print(f"PDF 已生成：{OUT_PDF}（{size_kb:.0f} KB）")


if __name__ == "__main__":
    main()
