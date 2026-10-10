# Debug 证据｜排错过程记录

本目录用于存放调试、排错过程的证据。

## 典型排错场景

### 1. MQTT 连接失败
**现象**：shared_state.py 或 Web 端连不上 Broker
**排查**：
```bash
# 检查 Broker 是否运行
ps aux | grep mosquitto
# 检查端口
lsof -i :1883
lsof -i :8083
# 手动发布测试
mosquitto_pub -h localhost -t "test" -m "hello"
```
**修复**：`bash scripts/start_broker.sh`

### 2. 断面数据不更新
**现象**：Web 卡片一直显示"等待数据"
**排查**：
```bash
# 检查 shared_state 是否收到消息
curl http://localhost:8000/api/state
# 检查 mqtt_node 是否在发布
python3 python/mqtt_node.py --reach reach-a --interval 2
# 检查 topic 是否一致（必须是 tidewatch/{reachId}/data）
```
**修复**：确认 publish 和 subscribe 的 topic 格式一致

### 3. judge() 返回值不一致
**现象**：Web 端 status 显示与消息中不同
**原因**：消息中的 status 与 judge() 计算结果不一致
**处理**：Web 端以 judge() 结果为准（二次校验）

## 已记录的排错证据

（按需添加排错日志截图）
