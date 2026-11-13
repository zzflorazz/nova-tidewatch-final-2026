# TideWatch Trace V1.0

面向园区水系的多断面异常溯源与排查辅助系统（TideWatch Trace V1.0）。通过各断面的历史基线、上下游传播时序和本地记录，给出候选来源、排除依据、证据缺口与后续排查建议，辅助人工核查。

> **SIMULATED：全部数据为教学模拟，非真实水体观测。** 排序分为相对分，不是概率，也不是因果证明。模型输出的 sigmoid 分数未校准；“证据支持”不能解释为“已证明来源”。本地施工记录仍需人工核实。

## 已实现能力

| 能力 | 实现与边界 |
|---|---|
| 分断面基线 | `backend/baseline.py` 按 `reach_id + metric` 独立计算中位数、Q1、Q3、IQR。偏离度为 `(当前值−中位数)/IQR`，绝对变化量为两者差的绝对值；零 IQR 使用配置的最小噪声并记录 fallback。 |
| 案例与对照 | S1–S5 五类案例，其中 S4 拆为 S4a/S4b，共六个案例书条目；另有 D1 自建对照，明确标记非案例书场景。 |
| 候选与传播 | `backend/attribution.py` 沿拓扑逆向生成候选，结合事件时间窗口、真实基线变化和本地证据排序；传播经过保留展示，不计为独立来源。 |
| 缺证据保留未定 | 上游缺测或证据不足时返回 `UNRESOLVED`，同时给出缺口和核查建议，不强行唯一归因。 |
| 持久化与版本 | `backend/store.py`、`backend/trace_runner.py` 使用 SQLite 保存样本、异常、归因版本和 Run Trace。迟到样本追加、旧值及旧结论保留；重复运行跳过已存在的同断面/指标/事件时间/值样本，但仍生成新归因版本。 |
| API 与工作台 | `backend/api.py` 提供查询、运行、补数、版本证据链和重排序接口；`web/workbench/` 是无构建工具的静态工作台，含数据健康、候选/排除依据、历史版本和补数表单。 |
| 可解释重排序 | `backend/reranker.py` 为纯 Python 逻辑回归。只对 `retained + independent_source` 同池重排，提供排序前后对照、标准化特征贡献、截距与 logit，不改变业务状态、结论、证据等级或排除依据。 |

旧 C02 固定阈值仅负责告警，不能替代 Trace 基线和来源判断；旧事件链丢弃迟到数据的行为也不用于 Trace。C02 的 `python/` 与原有 `web/` 页面保留，Trace 工作台启动不需要同时启动 MQTT 或旧事件服务。

## 快速开始

以下命令从项目根目录执行，适用于 macOS 的 zsh/bash；在其他机器复现时，将第一条 cd 的路径替换为实际检出目录。当前复核环境为 Python **3.14.7**；依赖未锁版本，其他环境的安装版本与耗时以实际运行为准。

```bash
cd /Users/xiaoyi/nova-tidewatch-final-2026
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

训练 JSONL 是被 `.gitignore` 忽略的生成物。先生成，避免全量测试找不到训练数据；D1 可由固定 seed 重建：

```bash
python scripts/generate_training_data.py
python scripts/export_decoy_scenario.py
```

启动服务：

```bash
uvicorn backend.api:app --port 8010
```

打开 **http://127.0.0.1:8010/workbench/**。界面所有数据均为模拟。S1 无目标异常，卡片显示“基线场景”且不可点击；选 S2/S3/S4a/S4b/S5 或 D1 运行。运行会写入 `data/trace.db`，重复点击会追加归因版本。停止服务按 Ctrl+C。

另开终端并激活同一虚拟环境，运行测试：

```bash
source .venv/bin/activate
PYTHONPATH=. pytest tests -q
python python/rules.py
```

2026-11-13 实测：`156 passed, 1 warning in 6.45s`；旧 C02 规则 `通过 4/4`。警告来自 Starlette TestClient 对当前 httpx 的弃用提示，测试未失败。

重新生成数据、训练模型、导出重排序证据的三个主命令，按顺序执行：

```bash
python scripts/generate_training_data.py
python scripts/train_reranker.py
python scripts/export_rerank_contract.py
```

模型写入 `models/reranker.json`，对比结果写入 `evidence/model_comparison.json`，重排序契约证据写入 `evidence/rerank_contract.json`。模型在服务进程内缓存，重新训练后需重启服务。S5 的真实 API 双版本证据可另行导出，无需 uvicorn：

```bash
python scripts/export_trace_api_evidence.py
```

该脚本使用临时数据库，生成 `evidence/trace_api_s5_evidence.json`，不污染工作台数据库。更多命令、预期输出、单场景示例与故障处理见 [复现指南](docs/reproduce.md)。

## 建议演示顺序

| 场景 | 观察点 |
|---|---|
| S1 | 基线无活动异常；使用离线基线 CLI 验证，不运行目标归因。 |
| S2 | 证据支持单源 A；B 为传播经过，不是第二个独立来源。 |
| S3 | A 与 C 本地同时保留；`MULTI_CANDIDATE`，不强行唯一归因。 |
| S4a / S4b | C 浊度分别为 27/26，表现相近，但上游变化、传播时序与本地记录不同；分别支持 A / C 本地。 |
| S5 | A 缺测，版本 1 为 `UNRESOLVED`。补回新鲜正常 A 数据后产生版本 2，旧版本可回查；当前固定补数演示的版本 2 仍为 `UNRESOLVED`。 |
| D1 | 自建对照，非案例书场景。规则顺序 `reach-c:local → reach-a`，模型顺序 `reach-a → reach-c:local`；排序变化，业务状态仍为 `MULTI_CANDIDATE`。 |

S5 补数不是“补完必有答案”：版本 1 结论为“来源未定｜关键上游 reach-a 证据缺失”，版本 2 为“当前证据不足以给出单源结论”。证据改善不等于已证明单源。

## API 一览

下表对应 `backend/api.py` 实际路由。异常/运行/版本不存在时返回中文 404；无目标异常的 S1 不能通过运行接口执行。

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/api/health` | 健康状态与 SIMULATED 声明 |
| GET | `/api/scenarios` | 场景摘要及 origin：`case_book` / `self_built_demo` |
| GET | `/api/scenarios/{scenario_id}` | 完整场景输入 |
| GET | `/api/anomalies` | 异常列表及最新版本号 |
| GET | `/api/anomalies/{anomaly_id}` | 单个异常及最新版本号 |
| GET | `/api/anomalies/{anomaly_id}/attributions` | 按版本升序列出归因摘要 |
| GET | `/api/anomalies/{anomaly_id}/attributions/{version}` | 完整归因记录、输入快照与输出 |
| GET | `/api/anomalies/{anomaly_id}/evidence` | 汇总版本、Run Trace、输入摘要和完整证据；可用 `?version=1` |
| POST | `/api/attributions/run` | 请求 `{"scenario_id":"S2","use_rerank":true}`；执行并保存新版本，重排序默认关闭 |
| GET | `/api/anomalies/{anomaly_id}/rerank` | 对已保存输出重排序；可用 `?version=1`，不改变原记录 |
| GET | `/api/runs/{run_id}` | 完整运行状态、关联归因版本及错误信息 |
| POST | `/api/anomalies/{anomaly_id}/samples` | 请求 `{"scenario_id":"S5","sample":{...}}`；补数并生成新归因版本 |

静态挂载 `/workbench/` 提供页面及本地 CSS/JS。补数 sample 必须包含 `reach_id、metric、value、event_time、received_time、source`；时间使用带时区的 ISO8601。工作台会显示真实接口错误。

## 本次复核结果

2026-11-13 在当前仓库代码与数据的临时副本中创建新虚拟环境，实际安装依赖、运行全量测试和脚本，并短暂启动 8010 服务。为遵守本次只改文档的范围，生成物和数据库未覆盖正式仓库。`/api/health`、`/api/scenarios`、`/workbench/` 均实测返回 200，列表包含六个案例书条目与 D1；D1 运行请求也返回 200。

生成器七族各 200 个场景，共 1400 个；其中三个排序训练族按场景 80/20 切分为 480/120 个场景、960/240 行候选；其余 800 个场景用于契约校验。960/240 行是过滤前的数据文件行数，包含传播经过负例；实际测试排序池有 200 个保留独立候选。测试集同一保留独立来源池的结果如下，与仓库 [离线对比证据](evidence/model_comparison.json) 的数值一致：

| 策略 | top-1 准确率 | MRR |
|---|---:|---:|
| 规则 | 0.666667 | 0.833333 |
| 模型 | 1.000000 | 1.000000 |
| 融合 | 1.000000 | 1.000000 |

这不是业务结论准确率。测试场景中有 40 个只剩一个有效独立候选；离线融合用于比较，在线入口使用模型重排序，不默认应用融合。当前重排序契约汇总为 `total=6、order_changed=1、skipped=4`，S5 同时保存补数前后输出，汇总按其最新版计数。

## 关键设计取舍

1. **规则硬约束优先。** 基线、拓扑、时间窗口、缺测和传播经过决定可比较的候选池，模型不能把已排除项恢复成来源。
2. **缺证据不猜。** 保留 `UNRESOLVED` 与多候选状态，把未知转为明确的证据缺口与下一步核查事项。
3. **模型只重排。** 模型分数补充同池排序，逐特征贡献便于解释；它不改 status、conclusion、证据等级和排除依据。相同分数按 source_id 排序，保证确定性。
4. **旧结论可回查。** 样本变化和重新归因追加版本，而非覆盖旧结果。迟到数据不能沿用旧实时事件链的丢弃策略。

## 已知限制

- 全部训练和评估是程序化合成数据。seed 留出仅验证同一生成机制，不代表真实水体、新河道或新传播机制的效果；施工记录为 pending，仍需人工核查。
- decoy 族的 time 特征区分度较强，可能高估泛化能力；topology 与 local_evidence 在当前数据中共线，系数不能作独立效应或因果解释。模型分数未校准。
- 当前 Run Trace 输入快照保存 `history_samples/current_samples/target`，未冻结完整配置、代码和历史模型。归因原输出可回查；历史版本的重排序接口使用当前加载模型重新计算，不等于历史模型回放。
- 补数接口以原场景的内存副本重新分析，替换该断面/指标的当前样本；数据库保留旧样本版本，但接口不会自动合并全部历次补数形成完整重放。分析时间仍是场景的固定 `analysis_time`，不是系统当前时间。
- 当前为本机教学工作台，不承诺生产部署能力。旧 C02 功能保留，本次仅重跑旧规则，不把历史截图当作本次实时接入验收。

## 主要目录

| 目录 | 说明 |
|---|---|
| `backend/` | Trace 基线、归因、SQLite、运行编排、模型及 API |
| `data/` | 固定案例、自建对照、生成数据和本机运行数据库；JSONL 与数据库不入库 |
| `models/` | 已训练、可序列化的重排序模型 |
| `scripts/` | 模拟数据生成、训练、证据导出，以及保留的 C02 工具 |
| `web/workbench/` | 当前 Trace 静态工作台；`web/` 其余内容为保留的 C02 页面 |
| `python/` | C02 实时接入、旧告警规则等历史模块 |
| `tests/` | Trace、持久化、API、工作台与模型契约测试 |
| `docs/` | 复现步骤、阶段记录及模型解释 |
| `evidence/` | 真实运行导出、阶段验证记录与页面截图 |

## 文档与证据索引

- [详细复现与常见问题](docs/reproduce.md)
- [M0 基线与 C02 验证记录](docs/m0-baseline.md)：阶段历史记录，其中“尚未实现”等描述只对应当时范围。
- [离线模型重排序说明](docs/model-reranking.md)：训练口径、特征与系数；其“未接入 API”描述为离线阶段记录，当前已提供受约束的在线重排序。
- [提交索引](docs/commit-index.md)、[11 月 2 日协作记录](docs/ai-sessions-20261102.md)、[11 月 3 日协作记录](docs/ai-sessions-20261103.md)：过程资料，当前能力以实现、测试及本页为准。
- [模型对比及逐场景结果](evidence/model_comparison.json)、[重排序契约与 D1 贡献](evidence/rerank_contract.json)
- [S3 多候选输出](evidence/trace_s3_output.json)、[S4 对照输出](evidence/trace_s4_output.json)、[S5 缺证据输出](evidence/trace_s5_output.json)
- [S5 持久化双版本](evidence/trace_run_s5_versions.json)、[S5 API 双版本证据链](evidence/trace_api_s5_evidence.json)
- [工作台首屏](evidence/workbench_initial.png)、[S5 版本 1](evidence/workbench_s5_v1.png)、[S5 版本 2](evidence/workbench_s5_v2.png)、[D1 工作台](evidence/workbench_d1.png)：已有截图，不冒充本次重新截图。
- [C02 历史证据索引](evidence/README.md)：其中 `evidence/D1/` 是旧测试编号，与当前自建场景 D1 不是同一概念。
