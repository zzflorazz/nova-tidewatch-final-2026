# TideWatch 河道水位水质智能监测系统

## 项目简介
面向多河道断面的水位与水质智能监测、预警与多端协同系统。
系统同时处理 3 个模拟河道断面（reach-a 上游 / reach-b 中游 / reach-c 下游），实现环境监测、预警、干预、恢复的完整事件闭环。

## 核心能力
- **2 条数据链**：离线分析链（CSV → Python → report.html）+ 边缘实时链（MQTT → 多端同步）
- **多端协同**：Web 监测台 + 移动端 H5 + 3D 数字空间，共享同一套实时状态
- **事件闭环**：发现 → 优先关注 → 干预 → 验证 → 恢复/未恢复 → 记录复盘
- **规则 + ML 对照**：固定阈值规则与 IsolationForest 并列判断，组合裁决

## 统一约定

### 3 个河道断面
| 断面 | 位置 |
|------|------|
| reach-a | 上游 |
| reach-b | 中游 |
| reach-c | 下游 |

### 统一判级规则（按顺序，水位优先）
1. waterLevel ≥ 8.5 → 洪水预警
2. 否则 waterLevel ≥ 7.0 → 高水位警戒
3. 否则 turbidity ≥ 60 → 水质浑浊
4. 其余 → 正常

### 水流等级 flowLevel
| 值 | 含义 |
|----|------|
| 0 | 缓流 |
| 1 | 平稳 |
| 2 | 湍急 |
| 3 | 湍流 |

### 统一 JSON 字段
```json
{"reachId":"reach-b","waterLevel":9.0,"turbidity":20,"flowLevel":2,"status":"洪水预警","time":"2026-10-08 20:30:00"}
```

## 环境要求
- Node.js ≥ 18
- Python ≥ 3.9
- mosquitto（MQTT Broker）
- 现代浏览器（Chrome / Edge 最佳；摄像头与语音功能需要浏览器授权）

## MQTT 约定
- Broker TCP: localhost:1883
- Broker WebSocket: localhost:8083/mqtt
- 数据 Topic: tidewatch/{reachId}/data
- 事件 Topic: tidewatch/events/{reachId}
- 消息格式：{"reachId":"reach-b","waterLevel":9.0,"turbidity":20,"flowLevel":2,"status":"洪水预警","time":"2026-10-08 20:30:00"}
- 订阅通配符：tidewatch/+/data

## Python 依赖
```bash
# 边缘实时链（数据链 B）所需
pip3 install paho-mqtt

# 离线分析链（数据链 A）所需
pip3 install pandas matplotlib scikit-learn joblib

# 全部依赖一行安装
pip3 install paho-mqtt pandas matplotlib scikit-learn joblib
```
- 生成离线分析报告：`python3 python/generate_report.py` → 输出 `report/report.html`（含统计表格、趋势图、异常记录、ML 摘要）。自动读取 `data/history.csv`，不存在时回退 `data/history_example.csv`（克隆后开箱即用）
- 训练轻量 ML 模型：`python3 python/train_ml.py` → 输出 `python/ml_model.joblib`
- D5 规则/ML 对照分析：`python3 python/compare_rule_ml.py` → 输出 `report/compare_rule_ml.html`（一致率 / 交叉统计 / 不一致案例 / 决策边界散点图 / 3 种组合裁决策略统计）。
  规则基于**阈值**（是否越界），ML 基于**历史分布**（是否异常）——两者关注不同问题，**不一致是正常现象**；
  报告推荐**策略C 组合裁决**（两者皆异常才确认报警，不一致转入人工复核，减少误报和漏报）。
  Web 端 4 组回归数据对照页：`http://localhost:8080/compare.html`

自动化验证（可选，用于 Web 监测台回归测试）：
```bash
pip3 install playwright && python3 -m playwright install chromium
python3 scripts/verify_web.py   # 验证不串线/断线横幅/自动重连，截图输出到 evidence/D1/
```

## 目录结构
```
nova-tidewatch-final-2026/
├── web/                  # 前端页面（全部为纯静态页，mqtt.js 走 CDN，无构建步骤）
│   ├── index.html        # Web 监测台（D1 / D2 / D3 事件面板）
│   ├── mobile.html       # 移动端 H5（E3 跨端同步，浏览器 F12 切移动视图）
│   ├── 3d.html           # 3D 数字空间（E1，Three.js r128 CDN）
│   ├── multimodal.html   # 摄像头 / 语音交互（E2，浏览器原生 API）
│   ├── compare.html      # 规则 / ML 对照页（D5）
│   ├── js/rules.js       # 统一判级规则（JS 版，与 python 版一致）
│   └── js/mqtt_client.js # TideWatchMQTT：多 topic 订阅 / 发布 / 自动重连
├── python/               # Python 后端与分析
│   ├── rules.py          # 统一判级规则（Python 版）
│   ├── shared_state.py   # 共享实时状态服务（HTTP :8000）
│   ├── event_manager.py  # 事件状态机（HTTP :8001）
│   ├── mqtt_node.py      # 水文节点模拟器（3 断面）
│   ├── analyze.py / generate_report.py / train_ml.py / compare_rule_ml.py
│   └── ml_model.joblib   # 已训练的 IsolationForest
├── data/                 # 示例数据（history_example.csv / reach_example.json）
├── report/               # 生成的分析报告（report.html / compare_rule_ml.html / 图表）
├── docs/                 # 技术文档 / 演示视频脚本 / 答辩 PPT 审校建议
├── evidence/             # 证据目录（Challenge / D1–D5 / E1–E3 / Debug / Reproduce / Open）
├── scripts/              # 演示与自动化验证脚本（send_* / test_* / verify_* / inject_*）
└── README.md
```

## 快速启动（边缘实时链）
> **推荐一键启动**（后台运行全部组件，日志在 `/tmp/tidewatch/`）：
> ```bash
> cd ~/nova-tidewatch-final-2026
> bash scripts/start_all.sh    # 一键启动；停止：bash scripts/stop_all.sh
> ```
> 以下手动步骤均需在**仓库根目录**执行；若本机已有 mosquitto 服务在运行（`brew services list` 查看），可跳过第 1 步直接复用。

1. 启动 MQTT Broker：`bash scripts/start_broker.sh`（TCP:1883 + WebSocket:8083）
2. 启动共享状态服务：`python3 python/shared_state.py`（HTTP API 端口 8000）
3. 启动水文节点模拟器：`python3 python/mqtt_node.py --interval 5`
4. 浏览器访问 `http://localhost:8000/api/state` 查看实时状态（健康检查 `/api/health`）
5. 前端页面：`cd web && python3 -m http.server 8080`，然后访问
   - Web 监测台（D1/D2/D3）：`http://localhost:8080/index.html`
   - 移动端 H5（E3）：`http://localhost:8080/mobile.html`
   - 3D 数字空间（E1）：`http://localhost:8080/3d.html`
   - 多模态交互（E2）：`http://localhost:8080/multimodal.html`
   - 规则/ML 对照（D5）：`http://localhost:8080/compare.html`

手动发送一条测试消息：
```bash
python3 scripts/send_test_msg.py --reach reach-b --water 9.0 --turbidity 20 --flow 2
```

D3 事件链（事件状态机，HTTP API 端口 8001）：
```bash
python3 python/event_manager.py     # 事件管理器：异常自动建单，处理干预指令
python3 scripts/test_event_flow.py  # 端到端事件闭环测试
```

D4 故障注入与修复（5 种故障自动验证）：
```bash
python3 scripts/inject_fault.py --fault broker-down [--recover]   # 演示用：注入单个故障/修复
python3 scripts/verify_fault.py                                   # 自动验证 5 种故障（约90秒）
```

## 场景复现

完整复现步骤见 [evidence/Reproduce/README.md](evidence/Reproduce/README.md)。

各场景一键验证命令：

| 场景 | 命令 | 说明 |
|------|------|------|
| D1 多断面稳定运行 | `python3 scripts/verify_web.py` | 不串线/断线横幅/自动重连 |
| D2 优先关注 | `python3 scripts/test_priority.py` | 3 组数据优先断面随数据变化 |
| D3 事件状态机 | `python3 scripts/test_event_flow.py` | OPEN→HANDLING→RECOVERED 闭环 |
| D4 故障注入 | `python3 scripts/verify_fault.py` | 5 种故障自动验证 |
| D5 规则/ML 对照 | `python3 python/compare_rule_ml.py` | 一致率+3 种组合策略 |
| 统一规则 | `python3 python/rules.py` | 4 组回归测试 4/4 通过 |

## 回归测试
```bash
cd python && python3 rules.py
```

## 提交物
- GitHub 仓库：nova-tidewatch-final-2026
- ≥5 次有意义 Commit
- Evidence 目录
- PPT（10-15 页）
- 技术文档（10-15 页）
- 演示视频（5-8 分钟）
