# D3 证据｜干预—验证—恢复（事件闭环）

## 状态机（显式规则，非法转移拒绝）

| 当前状态 | 允许转移到 | 触发条件 | 说明 |
|----------|-----------|----------|------|
| （无） | OPEN | 数据异常 / open 指令 | 自动建单，event_id = evt-YYYYMMDD-NNN |
| OPEN | HANDLING | "开始处理"指令 | 必须带匹配的 eventId |
| HANDLING | RECOVERED | "验证恢复"指令 **且** 干预后新数据 judge()==正常 | 缺任何一条都拒绝 |
| OPEN/HANDLING | CLOSED | "关闭事件"指令 | 放弃处理 |
| RECOVERED | （终态）| 再次异常 | 创建**新**事件（不复用旧 eventId）|
| OPEN | RECOVERED 直跳 | —— | **拒绝**（没有 HANDLING 就没有干预后验证）|

关键实现（`python/event_manager.py`）：
- 恢复三重校验：当前状态必须 HANDLING + 最新数据 judge()==正常 + 存在干预后到达的新数据（`_last_data_epoch > _handling_epoch`）
- 消息健壮性：事件广播带递增 `seq`（接收方丢弃 seq 过期消息）；指令必须匹配 eventId；数据重复消息去重（指纹）；迟到/乱序数据不覆盖新状态
- history 数组记录每次状态变更（时间+动作+操作者）

| 文件 | 证明什么 |
|------|----------|
| `test_event_flow_output.txt` | MQTT 端到端闭环：OPEN → HANDLING →（正常数据不自动恢复）→ RECOVERED → 再异常新建事件 → 非法转移被拒 → CLOSED |
| `d3_event_open.png` | 洪水预警数据自动建单：OPEN 红色卡片 + "开始处理"按钮；同屏可见历史事件（RECOVERED 绿 / CLOSED 灰）|
| `d3_event_handling.png` | 点击"开始处理"后：HANDLING 黄色 + "验证恢复"/"关闭事件"按钮；时间线出现"处理"时间 |
| `d3_event_recovered.png` | 点击"验证恢复"后：RECOVERED 绿色、无按钮、时间线完整（创建｜处理｜恢复）|
| `d3_event_closed.png` | 再异常新建事件 → 处理后关闭：CLOSED 灰色 |

## 自动化复现

```bash
python3 python/event_manager.py        # 终端A（需 mosquitto 已启动）
python3 scripts/test_event_flow.py     # MQTT 端到端测试（终端B）
python3 scripts/verify_events_web.py   # 网页按钮闭环测试（需 shared_state + web 8080）
```

网页端设计：按钮只发布**指令**；状态以 event_manager 的权威广播（带 seq）为准。
控制台可见"忽略事件指令回显 → 事件更新 xxx (seq=n)"，即指令与权威状态的分离。
