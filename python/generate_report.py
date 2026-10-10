#!/usr/bin/env python3
# TideWatch 离线分析链一键入口
# 读取 data/history.csv → 分析 → 生成 report/report.html
#
# 用法: python3 python/generate_report.py

import os

from analyze import BASE_DIR, generate_report

if __name__ == '__main__':
    out = generate_report()
    print(f"报告已生成: {os.path.relpath(out, BASE_DIR)}")
