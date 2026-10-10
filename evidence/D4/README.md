# D4 证据｜故障注入与修复验证

原则：**主动制造故障 → 观察现象 → 定位原因 → 修复 → 重新运行 → 验证恢复**。
自动化脚本一次跑完 5 种故障，每种都完成完整闭环（含"修复后重跑同一链路"验证）。

## 5 种故障与结果（全部 PASS）

| # | 故障 | 现象 | 修复 | 恢复验证 |
|---|------|------|------|----------|
| 1 | Broker 断开 | shared_state 打印"与 Broker 断开（rc=128）"；前端红色横幅（D1 已证） | `brew services start mosquitto` | paho 自动重连（"已连接 Broker"）+ **重跑链路**：发消息 → /api/state 收到 ✓ |
| 2 | 节点停止（reach-b 离线） | age_seconds 2.3s→8.3s 持续增长，**10.3s 时 online=false**（10秒阈值） | 重启 `mqtt_node --reach reach-b` | age=0.5s online=true ✓ |
| 3 | 非法 JSON（缺字段/非JSON）| shared_state 两条"拒绝消息"日志（列出缺失字段），**状态逐字段未被污染** | 发送一条合法消息 | 新数据生效 ✓ |
| 4 | status 写错（9.0 却写"正常"）| shared_state ⚠ 告警且**保留原始值"正常"**（不改写）；Web 端 judge() 二次校验告警 | 发送 status 正确的消息 | Web 卡片显示"洪水预警"（以规则为准）✓ |
| 5 | 事件消息重复（同 seq 广播 ×2）| event_manager 两条"丢弃重复/过期事件广播 seq=1 <= last_seq=1"；事件状态/seq 不变 | 发送 seq+1 广播 | seq+1 被正常接受，其重复仍被丢弃 ✓ |

## 文件说明

- `fault1_broker_down.txt` ~ `fault5_seq_duplicate.txt`：每种故障的注入动作 / 现象日志 / 修复动作 / 验证结果
- `logs/`：验证期间四个服务的原始运行日志（shared_state / event_manager / mqtt_node / web）

## 复现

```bash
python3 scripts/verify_fault.py         # 自动跑完 5 种故障（约 90 秒，期间会短暂重启一次 Broker）
```

现场演示（手动控制节奏）：

```bash
python3 scripts/inject_fault.py --fault broker-down        # 注入（保持故障用于讲解/录屏）
python3 scripts/inject_fault.py --fault broker-down --recover   # 现场修复
python3 scripts/inject_fault.py --fault status-mismatch --reach reach-c
python3 scripts/inject_fault.py --fault seq-duplicate --reach reach-b
```

## 值得讲解的分层设计（故障4）

```
消息 status="正常"（错误）
  → shared_state：存原始值 + ⚠ 告警（不掩盖上游数据问题）
  → event_manager：用 judge() 重新判定（洪水预警）驱动事件
  → Web/移动端/3D：judge() 二次校验，以规则为准显示"洪水预警"
```
错误数据不会被静默吞掉，也不会污染展示结论 —— 三层各自"如实记录 + 按规则判断"。
