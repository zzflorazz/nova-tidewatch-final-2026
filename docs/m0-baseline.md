# M0 基线记录与第一阶段验收

## 版本与范围

- 日期：2026-11-02（Asia/Shanghai）；本次 Web 受控验证约 13:54–13:55。
- 改动前 Git HEAD：`561e1aa744cc1a0f58d907d875705aa6e2bb8c72`。
- 仓库：`/Users/xiaoyi/nova-tidewatch-final-2026`；改动前 `git status --short` 无输出。
- Python：系统 `python3 --version` 为 `Python 3.14.7`。
- 已阅读仓库 README，以及用户指定的《学生案例书 V0.8R1》《学生实践指南 V0.8R1》。本阶段只做 M0、分断面中位数/IQR 基线、S1 自动测试。
- 实测复用了已经运行的 C02 服务，**没有执行“从关闭状态冷启动”验收**。没有修改旧业务代码。
- 未实现候选生成/排序、排除与归因、FastAPI、SQLite、Trace 前端；S2–S5 仅保留空结构。未自动执行 git add/commit/push，未修改 Git 历史或时间戳。

## C02 启动命令与端口

以下从仓库根目录执行，命令来源为 README 与现有脚本；本次没有重新启动整套服务。

| 组件 | 启动命令 | 端口 |
|---|---|---|
| 一键入口 | `bash scripts/start_all.sh` | 下列全部 |
| MQTT Broker | `bash scripts/start_broker.sh` | TCP 1883、WebSocket 8083（`/mqtt`） |
| 共享实时状态 | `python3 python/shared_state.py` | HTTP 8000 |
| 旧事件管理器 | `python3 python/event_manager.py` | HTTP 8001 |
| 三断面模拟器 | `python3 python/mqtt_node.py --interval 3` | 向 MQTT 发布，不监听 HTTP |
| Web 静态服务 | `python3 -m http.server 8080 --directory web` | HTTP 8080 |

Web 监测台：`http://localhost:8080/index.html`。Trace 本阶段是离线计算模块，不新增监听端口。

## 本次真实验证

没有生成或伪造截图、对话。以下均来自本次实际执行；仓库旧 `evidence/D1/` 截图没有重写，也没有作为本次截图宣称。

| 验证 | 实际操作与结果 |
|---|---|
| 三断面在线 | GET `http://localhost:8000/api/health`；13:54:54 返回 `online_count=3`、`total=3`，a/b/c 的 `online` 全为 true，数据年龄均为 2.2 秒。随后浏览器也断言三卡片在线。 |
| reach-b 受控消息不串线 | Playwright 打开未修改的 index.html，MQTT 连接成功后，短暂 SIGSTOP 现有随机模拟器（PID 13074），等待 1 秒排空消息并稳定页面，再通过 paho 向 `tidewatch/reach-b/data` 发布 `waterLevel=9.0, turbidity=20, flowLevel=2, status=洪水预警`。对比现有 `scripts/verify_web.py` 的完整卡片快照，`changed == ['reach-b']`，a/c 不变。finally 中 SIGCONT 恢复模拟器，日志确认 `SIMULATOR_RESUMED 13074`。 |
| Web 无运行错误 | 同一真实浏览器会话监听 `pageerror` 与 error 级 console，载入页面、等待 MQTT、发送受控消息及额外观察 500ms 后，错误列表为 `[]`。这是该次观察窗口的结果，不代表所有页面或长时间运行均已验证。 |
| 旧规则回归 | 实际执行 `python3 python/rules.py`；四组分别为正常、高水位警戒、洪水预警、水质浑浊，输出 `通过 4/4`。 |
| Trace 回归 | 临时虚拟环境下实际执行 `python3 -m pytest tests/test_trace.py -q`；输出 `9 passed in 0.04s`。 |
| S1 离线运行 | 实际执行 `python3 -m backend.baseline --scenario S1`，六组计算均无异常，`status=无活动异常`、`active_anomalies=[]`。 |

受控消息的关键前后值（实测，不是预期值）：

| 断面 | waterLevel 前→后 | turbidity 前→后 | time 前→后 |
|---|---|---|---|
| reach-a | 6.8 → 6.8 | 62 → 62 | 13:54:55 → 13:54:55 |
| reach-b | 7.6 → 9 | 40 → 20 | 13:54:55 → 13:54:57 |
| reach-c | 4 → 4 | 31 → 31 | 13:54:55 → 13:54:55 |

验证中的实际问题：最初 localhost 请求受环境代理影响返回 502，使用 `NO_PROXY=localhost,127.0.0.1` 后成功。最初隔离断言观察到三卡片变化，没有把失败算作通过；随后隔离随机发布、等待初始渲染稳定后复测通过，没有为此修改 C02。系统 Python 原本缺 pytest，安装于 `/private/tmp/tidewatch-m0-venv`，没有修改系统依赖或仓库配置。

本机临时验证脚本为 `/private/tmp/tidewatch_m0_verify.py`，真实成功输出在 `/private/tmp/tidewatch-m0-web-result.txt`；均为临时文件，非仓库交付，PID 仅对本次会话有效，不能直接当作通用复现参数。该脚本复用旧快照表达式，没有改写页面函数或模拟浏览器输出。

## 资产清单：REUSE / ADAPT / NEW

| 分类 | 现有资产或新增能力 | 处理与边界 |
|---|---|---|
| REUSE | `python/mqtt_node.py`、`scripts/mosquitto.conf`、`python/shared_state.py` | 复用三断面模拟接入与 MQTT topic，保留 C02 行为。来源为 simulated，不能宣称真实传感器数据。 |
| REUSE | `web/` 中监测台、移动端、3D、多模态与规则/ML 对照 | 保留全部现有 C02 页面。本阶段不改前端，不代表这些页面均已重新验收。 |
| REUSE | `scripts/send_test_msg.py`、`scripts/verify_web.py`、旧故障与事件测试脚本、`evidence/` | 复用消息构造、快照与验证入口；本阶段未重跑全部 D1–D5/E1–E3。 |
| ADAPT | `data/history_example.csv`、`data/reach_example.json`、实时 JSON 时间字段 | 有样例历史与最新状态，不能等同于 Trace 时序存储。Trace 需统一 `reach_id/metric/event_time/received_time/source`；当前先用独立 JSON 快照，不改旧格式。 |
| ADAPT | `python/rules.py` 与 `web/js/rules.js` | 旧固定阈值只负责 C02 告警；不能替代 Trace 的分断面基线异常判定，更不能用于判定来源。 |
| ADAPT | `python/event_manager.py` 的事件/去重机制 | 旧实时事件链按 `_last_data_time` 丢弃早于最新时刻的数据。这只能保护实时状态，**不能直接用于 Trace**。后续 Trace 须保留迟到样本、按事件时间回填、同一异常追加结论版本并保留旧版本；本阶段不实现。 |
| NEW（本次） | `backend/__init__.py`、`backend/baseline.py` | 纯函数分组基线、配置化偏离阈值、绝对变化量、零 IQR fallback、离线命令入口；不调用旧 judge，不读写旧服务全局状态。 |
| NEW（本次） | `data/trace_scenarios.json`、`tests/test_trace.py` | 固定 S1 快照与真实计算回归；S2–S5 仅 reserved，调用时明确拒绝，不能伪称已实现。 |
| NEW（后续） | 候选来源、排序依据、排除与证据强度、未定与排查建议 | 尚未实现，本次不提前创建模块或伪造结果。 |
| NEW（后续） | 迟到数据版本链、Run Trace、持久化、API 与溯源工作台 | 尚未实现；不把现有 CSV/内存状态宣称为已具备这些能力。 |

现有数据路径：`mqtt_node.py` 生成模拟 JSON → MQTT `tidewatch/{reachId}/data` → `shared_state.py` 最新内存状态与 HTTP 快照 → Web 订阅更新；`event_manager.py` 旁路消费并调用固定阈值规则驱动旧告警事件。离线 CSV → Python → HTML 报告是另一条链，当前不存在自动将每条 MQTT 样本写入 Trace 历史库的能力。旧 `time` 与接收时写入的 `last_seen` 也不能直接充当完整的双时刻历史契约。

## 第一阶段计算与 S1 数据约定

- 每组严格按 `(reach_id, metric)` 分开，历史和当前样本分开输入，当前值不进入历史统计。S1 每组 9 条历史，共 54 条；当前为 6 条，涵盖 a/b/c 的 turbidity 与 flow。
- 固定 `seed=20261102` 标识快照；数据直接保存在 JSON，运行时不随机生成。固定 `analysis_time=2026-11-02T09:46:00+08:00`，数据分类 `SIMULATED`，全部样本 `source=simulated`。
- 历史时段 09:00–09:40（5 分钟间隔）；当前 09:45，接收时间均晚 2 秒。分析拒绝快照时刻以后的样本，历史必须早于同组当前样本。本阶段不实现滚动窗口选择或迟到数据存储。
- Q1/Q3：排序后对索引 `(n-1)*p` 线性插值，p 分别为 0.25/0.75；中位数取标准统计中位数，IQR = Q3−Q1。
- 有符号偏离度 = `(value−median)/IQR`；异常条件 `abs(deviation) > deviation_threshold`，等于阈值不算异常。每项指标可单独配置阈值，当前两项均为 3；这不是统一原始水位/浊度阈值。
- 绝对变化量始终为 `abs(value−median)`，不使用 value/median 或相对基线倍数。
- IQR 恰为 0：使用指标配置的 `min_noise` 作为分母，保留原 `iqr=0`，输出 `effective_iqr` 和 `fallback=minimum_noise`。turbidity 的最小噪声为 0.1 NTU，flow 为 0.01（本阶段教学模拟流量单位）；正 IQR 仍使用实际 IQR。配置必须为有限正数。
- 缺历史不会默认为正常。S1 结论由每个样本实际计算后汇总，不按 scenario_id 硬编码，不生成候选来源。

本次 S1 运行结果（浮点显示四舍五入）：

| 断面 | 指标 | 中位数 | Q1 | Q3 | IQR | 当前值 | 偏离度 | 绝对变化量 |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| reach-a | turbidity | 6 | 5.5 | 6.5 | 1 | 6.25 | 0.25 | 0.25 |
| reach-b | turbidity | 12 | 11.5 | 12.5 | 1 | 12.25 | 0.25 | 0.25 |
| reach-c | turbidity | 24 | 23.5 | 24.5 | 1 | 24.25 | 0.25 | 0.25 |
| reach-a | flow | 1 | 0.95 | 1.05 | 0.1 | 1.025 | 0.25 | 0.025 |
| reach-b | flow | 1.5 | 1.45 | 1.55 | 0.1 | 1.525 | 0.25 | 0.025 |
| reach-c | flow | 2 | 1.95 | 2.05 | 0.1 | 2.025 | 0.25 | 0.025 |

六个当前值均处于自身历史 Q1–Q3 内，无 fallback，无活动异常。

## 测试与运行复现

在已具备 pytest 的 Python 环境下，从仓库根目录运行：

```bash
python3 -m pytest tests/test_trace.py -q
python3 python/rules.py
python3 -m backend.baseline --scenario S1
```

本次 pytest 实际使用临时虚拟环境的 python3：`PATH=/private/tmp/tidewatch-m0-venv/bin:$PATH python3 -m pytest tests/test_trace.py -q`；pytest 9.2.0 / Python 3.14.7。该临时环境可能被系统清理，其他机器应在自己的虚拟环境安装 pytest。

9 项 Trace 测试覆盖 S1、修改 a 历史不影响 b/c、指标隔离与精确分位数、零 IQR/单历史样本确定性、绝对变化量与倍数的反向排序反例、阈值配置与双向边界、缺历史/非法噪声/NaN、时刻快照隔离、S2–S5 reserved 拒绝。修改 a 历史后实际产生 a 的异常，证明没有把“S1 正常”写死。

S1 CLI 无需 MQTT/Web 服务或额外运行依赖，输出完整六组统计、fallback、异常标志和汇总；本次输出临时保存在 `/private/tmp/tidewatch-s1-result.json`。重新运行固定输入应得到相同输出。它不是持久化 Run Trace，也不包含候选归因能力。
