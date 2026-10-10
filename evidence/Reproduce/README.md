# Reproduce 证据｜完整复现步骤

本目录记录所有场景的复现步骤，确保验收时可快速复现。

> ⚠️ **交叉复现（任务书 8.1 必做·待完成）**：需由另一位未参与开发的同学仅依据 README 复现，
> 记录"复现者卡点 → README 修订 → 再次复现结果"三样证据（填写 `交叉复现记录_模板.md`）。
> 作者本人按下面清单自测**不能**替代交叉复现。

## 前置：启动全链路

打开 4 个终端，依次执行：

```bash
# 终端1：MQTT Broker
bash scripts/start_broker.sh

# 终端2：共享状态服务（HTTP:8000）
python3 python/shared_state.py

# 终端3：事件管理器（HTTP:8001）
python3 python/event_manager.py

# 终端4：水文节点模拟器
python3 python/mqtt_node.py --interval 3

# 终端5：Web 静态服务
cd web && python3 -m http.server 8080
```

## 场景复现清单

### D1 多断面稳定运行
1. 浏览器打开 http://localhost:8080/index.html
2. 确认 3 个断面卡片同时显示数据
3. 运行 `python3 scripts/send_test_msg.py --reach reach-b --water 9.0 --turbidity 20 --flow 2`
4. 确认只有 reach-b 卡片变红，a/c 不变（不串线）

### D2 优先关注
1. 观察顶部"当前优先关注"区域
2. 发送不同断面数据，确认优先断面随数据变化
3. 运行 `python3 scripts/test_priority.py` 查看 3 组测试结果

### D3 事件状态机
1. 发送洪水预警数据 → 自动创建 OPEN 事件
2. 点击"开始处理" → HANDLING
3. 发送正常数据 → 状态保持 HANDLING
4. 点击"验证恢复" → RECOVERED（前提是当前 judge()==正常）
5. 运行 `python3 scripts/test_event_flow.py` 完整闭环测试

### D4 故障注入
```bash
python3 scripts/verify_fault.py
```
自动验证 5 种故障：Broker 断开 / 节点停止 / 非法 JSON / status 不一致 / seq 重复

### D5 规则/ML 对照
```bash
python3 python/compare_rule_ml.py
```
查看 report/compare_rule_ml.html，或访问 http://localhost:8080/compare.html

### E1 3D 数字空间
1. 访问 http://localhost:8080/3d.html
2. 拖拽旋转 / 滚轮缩放
3. 点击断面标记查看详情
4. 洪水预警时观察水面波动和闪烁

### E2 多模态交互
1. 访问 http://localhost:8080/multimodal.html
2. 允许摄像头权限
3. 点击"开始监听"，说"查看中游"
4. 说"播报状态"听语音播报

### E3 移动端 H5
1. 访问 http://localhost:8080/mobile.html
2. 桌面端和移动端同时打开
3. 在一端操作事件，另一端同步更新
