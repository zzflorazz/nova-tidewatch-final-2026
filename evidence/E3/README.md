# E3 证据｜Web—移动端实时数据同步与协同

## 产品分工（为什么两端信息组织不同）

| 入口 | 定位 | 信息架构 |
|------|------|----------|
| Web 监测台（index.html） | 值班总览 | 三断面横排 + 热力色彩 + 优先关注完整公式 + 全部事件历史 |
| 移动端（mobile.html） | 现场快速处置 | 单列大卡片 + 优先关注精简版 + **只看活动事件** + ≥48px 触屏按钮 + Toast 反馈 |

两端不维护各自的同步逻辑：按钮只发布**指令**到 `tidewatch/events/{reachId}`，
`event_manager` 状态机校验后广播权威状态，所有订阅端（桌面/移动端/3D）**天然同步**。

| 文件 | 证明什么 |
|------|----------|
| `e3_mobile_open_event.png` | 一条洪水预警消息 → 两端同时出现**同一个** OPEN 事件（evt-20261010-001）；移动端布局：优先关注精简版 + 单列卡片 + 大按钮 |
| `e3_cross_desk_click_mobile.png` | **跨端联动①**：在**桌面端**点"开始处理" → **移动端**事件同步变 HANDLING（黄）+ 双按钮 + 时间线出现处理时间 |
| `e3_cross_mobile_click_desktop.png` | **跨端联动②**：在**移动端**点"验证恢复" → **桌面端**事件同步变 RECOVERED（绿）|
| `e3_mobile_after_recovered.png` | 恢复后移动端活动事件列表清空（"当前无待处理事件"）|
| `e3_mobile_data_sync.png` | 一条新断面消息 → 两端三断面卡片核心字段完全一致（reach-a 7.6 / 高水位警戒）|

## 自动化复现（Playwright 同屏双端）

```bash
python3 scripts/verify_mobile_sync.py   # 桌面+移动双页面同时验证双向跨端联动
```

验证断言（全部通过）：
1. 同一 MQTT 消息 → 两端出现同一 eventId 的 OPEN 事件
2. 桌面点击"开始处理" → 移动端 = HANDLING
3. 正常数据不自动恢复（两端保持 HANDLING）
4. 移动端点击"验证恢复" → 桌面端 = RECOVERED
5. 两端三断面 waterLevel/turbidity/status 逐字段相等

## 移动端实现说明

- 复用 `web/js/mqtt_client.js`（TideWatchMQTT）与 `web/js/rules.js`（judge / calcPriority），未重复造轮子
- 与桌面端相同的不串线逻辑：`reachId → #card-{reachId}` 严格映射 + judge() 二次校验
- 数据更新全部来自 MQTT 回调，无轮询
- 视图：浏览器开发者工具切换移动端视图即可；真机可访问局域网地址（同一 `scripts/mosquitto.conf` 已监听 0.0.0.0）
