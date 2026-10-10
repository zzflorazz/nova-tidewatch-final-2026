# TideWatch 河道水位水质智能监测系统

## 项目简介
面向多河道断面的水位与水质智能监测、预警与多端协同系统。
系统同时处理 3 个模拟河道断面（reach-a 上游 / reach-b 中游 / reach-c 下游），实现环境监测、预警、干预、恢复的完整事件闭环。

## 核心能力
- **2 条数据链**：离线分析链（CSV → Python → report.html）+ 边缘实时链（MQTT → 多端同步）
- **多端协同**：Web 监测台 + 微信小程序 + 3D 数字空间，共享同一套实时状态
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
- 微信开发者工具

## MQTT 约定
- Broker TCP: localhost:1883
- Broker WebSocket: localhost:8083/mqtt
- 数据 Topic: tidewatch/{reachId}/data
- 事件 Topic: tidewatch/events/{reachId}
- 消息格式：{"reachId":"reach-b","waterLevel":9.0,"turbidity":20,"flowLevel":2,"status":"洪水预警","time":"2026-10-08 20:30:00"}
- 订阅通配符：tidewatch/+/data

## Python 依赖
离线分析链（数据链 A）所需：pandas, matplotlib, scikit-learn, joblib
```bash
pip3 install pandas matplotlib scikit-learn joblib
```
- 生成离线分析报告：`python3 python/generate_report.py` → 输出 `report/report.html`（含统计表格、趋势图、异常记录、ML 摘要）。自动读取 `data/history.csv`，不存在时回退 `data/history_example.csv`（克隆后开箱即用）
- 训练轻量 ML 模型：`python3 python/train_ml.py` → 输出 `python/ml_model.joblib`

自动化验证（可选，用于 Web 监测台回归测试）：
```bash
pip3 install playwright && python3 -m playwright install chromium
python3 scripts/verify_web.py   # 验证不串线/断线横幅/自动重连，截图输出到 evidence/D1/
```

## 目录结构
```
nova-tidewatch-final-2026/
├── web/              # Web 监测台
│   ├── js/rules.js   # 统一判级规则（JS）
│   └── ...
├── python/           # Python 数据分析
│   ├── rules.py      # 统一判级规则（Python）
│   └── ...
├── miniapp/          # 微信小程序
├── dashboard/        # 实时 Dashboard + 3D
├── data/             # CSV/JSON 数据
├── report/           # report.html + 趋势图
├── evidence/         # 证据截图
├── scripts/          # 演示脚本
└── README.md
```

## 快速启动（边缘实时链）
> 若本机已有 mosquitto 服务在运行（`brew services list` 查看），可跳过第 1 步直接复用。

1. 启动 MQTT Broker：`bash scripts/start_broker.sh`（TCP:1883 + WebSocket:8083）
2. 启动共享状态服务：`python3 python/shared_state.py`（HTTP API 端口 8000）
3. 启动水文节点模拟器：`python3 python/mqtt_node.py --interval 5`
4. 浏览器访问 `http://localhost:8000/api/state` 查看实时状态（健康检查 `/api/health`）

手动发送一条测试消息：
```bash
python3 scripts/send_test_msg.py --reach reach-b --water 9.0 --turbidity 20 --flow 2
```

D3 事件链（事件状态机，HTTP API 端口 8001）：
```bash
python3 python/event_manager.py     # 事件管理器：异常自动建单，处理干预指令
python3 scripts/test_event_flow.py  # 端到端事件闭环测试
```

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
