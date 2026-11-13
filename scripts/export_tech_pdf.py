#!/usr/bin/env python3
"""Export the repository technical report with Playwright's Chromium PDF path.

Run: python scripts/export_tech_pdf.py
Requires Playwright and an installed Chromium browser. pypdf is optional for
page counting. No API service or runtime database is used.
"""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HTML_PATH = ROOT / "docs" / "tech-report.html"
PDF_PATH = ROOT / "docs" / "TideWatch_Trace_V1.0_技术说明.pdf"


def main():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as error:
        raise SystemExit(
            "请在当前环境安装 playwright，并执行 python -m playwright install chromium"
        ) from error
    if not HTML_PATH.is_file():
        raise SystemExit(f"HTML 不存在：{HTML_PATH}")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page()
            page.goto(HTML_PATH.as_uri(), wait_until="networkidle")
            page.emulate_media(media="print")
            page.evaluate("document.fonts.ready")
            page.wait_for_function(
                "Array.from(document.images).every(img => img.complete)"
            )
            broken = page.locator("img").evaluate_all(
                "images => images.filter(img => !img.naturalWidth).map(img => img.src)"
            )
            if broken:
                raise RuntimeError(f"截图加载失败：{broken}")
            if page.locator("img").count() != 3:
                raise RuntimeError("技术说明应包含三张真实工作台截图")
            page.pdf(
                path=str(PDF_PATH),
                format="A4",
                print_background=True,
                prefer_css_page_size=True,
                margin={"top": "16mm", "bottom": "17mm", "left": "16mm", "right": "16mm"},
                display_header_footer=True,
                header_template="<span></span>",
                footer_template=(
                    '<div style="width:100%;padding:0 16mm;color:#637b88;'
                    'font-size:8px;font-family:sans-serif;display:flex;'
                    'justify-content:space-between">'
                    '<span>TideWatch Trace V1.0 · SIMULATED</span>'
                    '<span><span class="pageNumber"></span> / '
                    '<span class="totalPages"></span></span></div>'
                ),
            )
        finally:
            browser.close()

    size = PDF_PATH.stat().st_size
    if size <= 100 * 1024:
        raise RuntimeError(f"PDF 小于要求的 100KB：{size} bytes")
    print(f"PDF 已生成：{PDF_PATH}")
    print(f"文件大小：{size:,} bytes（{size / 1024:.2f} KiB）")
    try:
        from pypdf import PdfReader
    except ImportError:
        print("页数：未检测（未安装可选依赖 pypdf）")
    else:
        print(f"页数：{len(PdfReader(PDF_PATH).pages)}")


if __name__ == "__main__":
    main()
