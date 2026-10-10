# Evidence 证据索引｜TideWatch 综合工程作品

本索引说明"**哪份证据证明什么**"。每项只保留最能证明结果的材料，避免堆砌截图。
演示视频中对应的时间点可在录制完成后补充（占位：`[视频 mm:ss]`）。

| 目录 | 对应任务 | 核心证据 |
|------|----------|----------|
| `Challenge/` | R18 前置 | DormMate 仓库索引 + 本地录屏/截图位置（见其 README）|
| `D1/` | 多断面稳定运行 | 三断面同屏在线；**跨断面隔离**（仅 reach-b 更新）；断线红横幅 + 自动重连；README 含 4 张截图 |
| `D2/` | 持续风险与优先关注 | 3 组数据优先断面变化（reach-a→c→b）；Web 面板完整理由公式 + 脉冲高亮；test_priority_output.txt |
| `D3/` | 干预—验证—恢复 | 事件状态机 OPEN→HANDLING→RECOVERED→新OPEN→CLOSED 四张状态截图 + 端到端输出；恢复三重校验、非法转移拒绝 |
| `D4/` | 故障注入与修复 | 5 种故障（Broker断/节点停/坏JSON/status错/seq重）全套 PASS 证据 + logs/ 原始服务日志 |
| `D5/` | 规则与 ML 对照 | 一致率 78.3% / 交叉统计 / 13 条不一致案例 / 决策边界散点图 / 3 策略对比；对照页截图 |
| `E1/` | 3D 数字空间 | 三状态同屏（黄/红/青）+ 红色优先光圈 + 事件状态牌；点击选中断面详情面板 |
| `E2/` | Camera/ASR/TTS | 摄像头红边框叠加层 + 自动播报；语音指令执行 + 带水印现场照片（关联断面/事件）|
| `E3/` | Web—移动端同步 | 双向跨端联动截图（桌面点处理→移动端同步；移动端点恢复→桌面端同步）+ 字段一致性断言 |
| `Debug/` | 排错 | 常见故障排查手册（与 D4 日志互相引用）|
| `Reproduce/` | 复现 | 交叉复现记录（任务书 8.1 必做；同学复现完成后将记录放入此目录）|
| `Open/` | 开放增强 | 自主设计/增强项清单与说明（消息健壮性、组合裁决、3D/多模态增强等）|

## 自动化验证入口（可一键复跑）

```bash
# 纯算法 / 离线
python3 python/rules.py                          # 统一判级回归 4/4
python3 scripts/test_priority.py                 # D2 三组优先度
python3 python/compare_rule_ml.py                # D5 对照报告（report/compare_rule_ml.html）

# 需要全链路（Broker + shared_state + event_manager + web 8080）
python3 scripts/test_event_flow.py               # D3 端到端事件闭环
python3 scripts/verify_web.py                    # D1 不串线/断线横幅/自动重连（Playwright）
python3 scripts/verify_priority_web.py           # D2 网页端优先度跟随
python3 scripts/verify_events_web.py             # D3 网页按钮闭环
python3 scripts/verify_mobile_sync.py            # E3 双端同屏跨端联动
python3 scripts/verify_3d.py                     # E1 3D 颜色/高度/点击
python3 scripts/verify_multimodal.py             # E2 摄像头/语音/拍照（虚拟摄像头）
python3 scripts/verify_fault.py                  # D4 五种故障自动验证（约90秒）
```

## 演示视频时间点对照（录制后填写）

| 证据 | 视频时间点 |
|------|-----------|
| D1 不串线 | [视频 --:--] |
| D3 事件闭环 | [视频 --:--] |
| D4 故障修复 | [视频 --:--] |
| D5 对照案例 | [视频 --:--] |
