#!/usr/bin/env python3
# D2 优先关注算法测试：3 组不同的三断面情况，验证优先断面随数据变化
# 用法: python3 scripts/test_priority.py

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "python"))
from rules import FLOW_LEVELS, STATUS_WEIGHTS, calc_priority, judge

# 每组数据：(reachId, waterLevel, turbidity, flowLevel)
GROUPS = [
    {
        "name": "组1：全部正常",
        "reaches": [("reach-a", 4.5, 20, 0), ("reach-b", 4.5, 20, 0), ("reach-c", 4.5, 20, 0)],
        "expected": "reach-a（同分取第一个）",
    },
    {
        "name": "组2：reach-c 洪水预警 + 湍急",
        "reaches": [("reach-a", 4.5, 20, 0), ("reach-b", 4.5, 75, 1), ("reach-c", 9.0, 20, 2)],
        "expected": "reach-c（洪水预警8 + 湍急2 = 10）",
    },
    {
        "name": "组3：reach-b 洪水预警 + 湍流",
        "reaches": [("reach-a", 7.6, 20, 0), ("reach-b", 9.0, 20, 3), ("reach-c", 4.5, 75, 1)],
        "expected": "reach-b（洪水预警8 + 湍流3 = 11）",
    },
]


def reason_text(status, flow_level, anomaly_count, score):
    """与 Web 端 getPriorityReason 相同格式"""
    weight = STATUS_WEIGHTS.get(status, 0)
    parts = [f"环境:{status}({weight * 2})"]
    if flow_level:
        parts.append(f"水流:{FLOW_LEVELS[flow_level]}({flow_level})")
    if anomaly_count:
        parts.append(f"连续异常:{anomaly_count}次({anomaly_count * 0.5})")
    return " + ".join(parts) + f" = {score}"


def main():
    picked = []
    for group in GROUPS:
        print(f"===== {group['name']} =====")
        rows = []
        for rid, wl, tb, fl in group["reaches"]:
            status = judge(wl, tb)["status"]
            score = calc_priority(status, fl, 0)  # 新数据，连续异常按 0 计
            rows.append((rid, status, fl, score))

        # 找优先度最高（同分取 REACH_IDS 顺序第一个，与 Web 端一致）
        best = None
        for row in rows:
            if best is None or row[3] > best[3]:
                best = row
        picked.append(best[0])

        for rid, status, fl, score in rows:
            mark = "  ← 当前优先关注" if rid == best[0] else ""
            print(f"  {rid}: status={status}, flowLevel={fl}({FLOW_LEVELS[fl]}), 优先度={score}{mark}")
        print(f"  → 优先断面: {best[0]}（预期 {group['expected']}）")
        print(f"    理由: {reason_text(best[1], best[2], 0, best[3])}")
        print()

    assert picked[0] == "reach-a", f"组1 同分应取第一个 reach-a，实际 {picked[0]}"
    assert picked[1] == "reach-c", f"组2 应为 reach-c，实际 {picked[1]}"
    assert picked[2] == "reach-b", f"组3 应为 reach-b，实际 {picked[2]}"
    print(f"3 组优先断面: {' → '.join(picked)}，随数据变化 ✓")


if __name__ == "__main__":
    main()
