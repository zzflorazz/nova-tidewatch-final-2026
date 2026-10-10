# Challenge 证据｜R18（C01 DormMate）前置能力索引

TideWatch（C02）以前置《C01｜DormMate Final Challenge｜学生任务书 V0.8R18》M1–M6 与 A/B/C
收口的能力训练为基础。由于 TideWatch 使用独立新仓库，本目录以**索引**方式登记 R18 阶段证据
（任务书 8.1：放副本或指向 nova-dormmate-final-2026 的索引说明，至少保留一份索引）。

## 1. 代码与提交历史（在线）

- 仓库：**https://github.com/zzflorazz/nova-dormmate-final-2026**
- 提交历史：3 次有意义 Commit（R18 阶段要求 ≥3，保留在该仓库，不计入本 C02 仓库）
- 目录与能力对应（以仓库实际内容为准）：

| 目录 | 对应 R18 能力 |
|------|----------------|
| `web/` | Web 主应用（M1–M2：阈值判级 / 状态展示 / 多端交互入口）|
| `miniapp/` | 微信小程序移动端（M4）|
| `dashboard/` | 实时监测台 / 3D 融合（M5 / M6）|
| `analysis/` | Python 离线分析（M4：CSV → 统计 → 报告）|
| `bridge/` | MQTT / 数据桥接（M5：Broker / Topic / 发布订阅）|
| `report/` | 分析报告产物 |
| `scripts/` | 演示与辅助脚本 |

## 2. 本地补充物证（作者机器 · 桌面 NOVA 目录）

| 位置 | 内容 |
|------|------|
| `~/Desktop/NOVA/录屏2026-10-07 16.43.28.mov`、`16.44.25.mov` | C01 综合运行录屏（2 段）|
| `~/Desktop/NOVA/DormMate_汇报.pptx` | C01 阶段汇报 PPT |
| `~/Desktop/NOVA/文静怡运行截图/` | 运行截图 7 张（2026-09-30 ~ 2026-10-07，含 M3 摄像头/ASR/TTS 与实时监测台）|

## 3. 现场核验路径建议

1. 打开在线仓库确认代码与 3 次 Commit 历史；
2. 播放本地录屏验证 M1–M6 综合运行；
3. 如需现场重跑：`cd ~/DormMate-Final`，按其 README/脚本启动（Web 静态服务 + bridge + Broker）。

> 说明：本索引由 C02 阶段整理。R18 提交历史与证据按任务书要求保留在原仓库/本地，未迁移到本仓库。
