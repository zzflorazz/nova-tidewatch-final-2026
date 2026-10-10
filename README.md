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

## 验证 Web—移动端实时同步

1. 启动全链路（Broker → shared_state → event_manager → mqtt_node → web server）
2. 桌面端打开 `http://localhost:8080/index.html`
3. 移动端（或浏览器 F12 切移动视图）打开 `http://localhost:8080/mobile.html`
4. 发送一条洪水预警数据：`python3 scripts/send_test_msg.py --reach reach-b --water 9.0 --turbidity 20 --flow 2`
5. **预期**：两端同时显示 reach-b 为洪水预警，事件卡片变为 OPEN
6. 在桌面端点击"开始处理" → **预期**：移动端事件卡片同步变为 HANDLING
7. 发送正常数据后，在移动端点击"验证恢复" → **预期**：桌面端事件卡片同步变为 RECOVERED

原理：两端订阅同一 MQTT topic（`tidewatch/+/data` 和 `tidewatch/events/+`），MQTT 广播天然保证一致性。

## 正常情况下应看到的结果

| 操作 | 预期结果 |
|------|---------|
| 启动 mqtt_node 后访问 /api/state | 返回 3 个断面的实时 JSON 数据 |
| 打开 index.html | 3 个断面卡片显示水位/浊度/status，持续更新 |
| 发送 waterLevel=9.0 | 对应断面卡片变红（洪水预警），自动创建 OPEN 事件 |
| 点击"开始处理" | 事件状态变黄（HANDLING） |
| 发送正常数据后点"验证恢复" | 事件状态变绿（RECOVERED） |
| 打开 3d.html | 看到 3D 河道，3 断面标记颜色随状态变化 |
| 打开 multimodal.html | 摄像头预览正常，语音指令可执行 |

## 常见问题与排查

| 问题 | 排查方法 |
|------|---------|
| MQTT 连不上 | 检查 mosquitto 是否运行：`ps aux \| grep mosquitto`；端口 1883/8083 是否被占用 |
| Web 不更新数据 | 检查 mqtt_node 是否在运行；shared_state 日志是否有报错；浏览器控制台是否有 MQTT 连接错误 |
| 断面显示离线 | 检查 mqtt_node 是否在发布；/api/health 中 age_seconds 是否 > 10 |
| 中文显示方框 | matplotlib 中文字体问题，需安装 PingFang SC 或 SimHei |
| 语音识别无反应 | 使用 Chrome/Edge 浏览器；检查麦克风权限是否授权 |
| 摄像头打不开 | 使用 localhost 访问（非 IP）；检查摄像头权限 |

## 已知限制

1. **数据来源**：水文数据由 mqtt_node.py 模拟器随机生成，未接入真实传感器
2. **ML 模型**：IsolationForest 训练数据仅 60 条历史样本，异常判断可能不够稳定；对未见数据可能误报或漏报
3. **flowLevel 感知**：当前 source 为 `rule` 或 `simulated`（共同完成线），未使用真实视觉模型；confidence 为 null
4. **移动端**：H5 页面需浏览器打开，非原生微信小程序
5. **3D 场景**：简化河道模型，非真实地理数据
6. **语音识别**：依赖浏览器 Web Speech API，Chrome 支持最好，Safari 部分功能受限

## 开源库与资料来源

| 库/工具 | 用途 | 来源 |
|---------|------|------|
| mosquitto | MQTT Broker | https://mosquitto.org/ |
| paho-mqtt | Python MQTT 客户端 | https://www.eclipse.org/paho/ |
| mqtt.js | 浏览器 MQTT 客户端 | https://github.com/mqttjs/MQTT.js |
| pandas | 数据分析 | https://pandas.pydata.org/ |
| matplotlib | 图表绘制 | https://matplotlib.org/ |
| scikit-learn | IsolationForest 异常检测 | https://scikit-learn.org/ |
| Three.js | 3D 渲染 | https://threejs.org/ |
| Web Speech API | ASR + TTS | https://developer.mozilla.org/zh-CN/docs/Web/API/Web_Speech_API |

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
