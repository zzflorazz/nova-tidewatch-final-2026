# D1 证据｜多断面稳定运行

| 文件 | 证明什么 |
|------|----------|
| `d1_three_reaches_live.png` | 三断面（reach-a 上游 / reach-b 中游 / reach-c 下游）同时在线，Web 监测台同一屏显示三张卡片，状态与颜色符合统一判级规则（正常=绿，水质浑浊=青） |
| `d1_isolation_reach-b_only.png` | 发送一条 reach-b 消息（9.0/20/flow2）后，**只有 reach-b 卡片**更新为"洪水预警"（红色），reach-a / reach-c 卡片数据完全不变 —— 证明三断面不串线 |
| `d1_mqtt_disconnected_banner.png` | 停止 Broker 后：顶部红色横幅"MQTT 连接已断开，正在自动重连"，右上指示灯变红 |
| `d1_mqtt_reconnected.png` | 恢复 Broker 后自动重连成功：横幅消失、指示灯恢复绿色、自动重新订阅 tidewatch/+/data |

## 运行环境

- mosquitto：1883 (TCP) + 8083 (WebSocket)
- 共享状态服务：`python3 python/shared_state.py`（:8000）
- Web 监测台：`cd web && python3 -m http.server 8080`
- 数据来源：MQTT `tidewatch/+/data`

## 自动化复现

```bash
python3 scripts/verify_web.py   # Playwright 无头浏览器，含上述全部断言，并重新生成截图
```

关键行为说明：
- 卡片"在线/离线"由 last_seen 距今是否 ≤10 秒决定（与 /api/health 同一阈值）
- 跨断面更新由 `web/js/app.js` 中 `reachId → #card-{reachId}` 的严格映射保证
