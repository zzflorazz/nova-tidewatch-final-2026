# TideWatch Trace V1.0 复现指南

所有命令从项目根目录执行；其他机器请将 cd 路径替换为实际检出目录。全部输入为 **SIMULATED 教学模拟**；候选分数不是概率或因果证明。请先阅读 [项目首页](../README.md) 的能力边界。

## 验证环境与范围

2026-11-13 使用当前正式仓库代码、数据和模型的临时副本复核，未覆盖正式仓库代码、数据、模型、数据库或证据。新建虚拟环境基于 Python 3.14.7；实际安装 FastAPI 0.162.5、uvicorn 0.54.0、httpx 0.28.1、pytest 9.2.0。`requirements.txt` 未固定版本，这些是本次观测值，不是其他环境必然安装的版本。

以下列出实际执行过的步骤及对应输出。耗时、运行 ID、生成时间和绝对路径随运行变化，以实际运行为准。生成脚本会覆盖相应生成物；若需保护现有证据，请先在项目副本中复现。

## 1. 创建环境并安装

```bash
cd /Users/xiaoyi/nova-tidewatch-final-2026
python3 -m venv .venv
source .venv/bin/activate
python --version
python -m pip install -r requirements.txt
```

预期：虚拟环境创建成功；本次 `python --version` 输出 `Python 3.14.7`。首次安装完成，重复安装输出 `Requirement already satisfied`，退出码为 0。本次创建环境实际使用的解释器是 `/private/tmp/tidewatch-m0-venv/bin/python`（同为 Python 3.14.7）；本机如需复用它，可用该绝对路径替代 `python3`。

## 2. 生成训练数据与 D1

```bash
python scripts/generate_training_data.py
python scripts/export_decoy_scenario.py
```

第一个命令生成：

- `data/simulated_candidates.jsonl`：仅 single_upstream、decoy_local、decoy_upstream，用于训练和评估。
- `data/simulated_contract.jsonl`：single_local、multi_candidate、missing_upstream、baseline，用于契约检查，不参与排序训练。

本次控制台摘要：

| 项目 | 实测值 |
|---|---:|
| 每族场景数 / 全部场景数 | 200 / 1400 |
| train 场景 / test 场景 | 480 / 120 |
| train 候选行 / test 候选行 | 960 / 240 |
| 契约场景 | 800 |
| decoy_local 独立来源负例 / 难例场景 | 200 / 200 |
| decoy_upstream 独立来源负例 / 难例场景 | 200 / 200 |

每个训练族为 160/40 个 train/test 场景，320/80 行候选；同场景不跨 split。960/240 是文件行数，包含传播经过负例；评估过滤后测试排序池有 200 个保留独立候选（40 个场景各 1 个、80 个场景各 2 个）。两个 JSONL 被 `.gitignore` 忽略，因此新检出仓库应先生成再跑全量测试。

第二个命令打印 `data/demo_decoy_scenario.json` 的绝对路径。固定 seed 生成 D1，自建对照而非案例书 S1–S5，重复导出字节一致；此性质也有测试覆盖。

## 3. 运行自动测试

```bash
PYTHONPATH=. pytest tests -q
python python/rules.py
```

本次全量输出尾部：

```text
156 passed, 1 warning in 6.45s
```

警告原文为 `StarletteDeprecationWarning: Using httpx with starlette.testclient is deprecated; install httpx2 instead.`（原输出的 httpx 和 starlette.testclient 带反引号）。它来自当前依赖组合，不是测试失败；本项目仍按现有 requirements 安装，未为消除提示修改依赖。

旧 C02 规则四项为正常、高水位警戒、洪水预警、水质浑浊，输出尾部：

```text
通过 4/4
```

该命令不等于重验整个 MQTT/C02 实时系统。

## 4. 重新训练并检查离线指标

```bash
python scripts/train_reranker.py
```

输入为步骤 2 的候选 JSONL；只用 train 拟合，test 复用训练均值/标准差。输出文件：`models/reranker.json`、`evidence/model_comparison.json`。本次控制台表格：

```text
strategy       top-1       MRR
rule         0.666667   0.833333
model        1.000000   1.000000
fusion       1.000000   1.000000
```

| test family（各 40 场景） | 规则 top-1 | 模型 top-1 | 融合 top-1 |
|---|---:|---:|---:|
| single_upstream | 1.0 | 1.0 | 1.0 |
| decoy_local | 0.0 | 1.0 | 1.0 |
| decoy_upstream | 1.0 | 1.0 | 1.0 |

这是固定合成机制、同一候选池的排序指标，不能宣称真实水体准确率。模型解释与局限见 [离线模型说明](model-reranking.md)。训练会覆盖模型；服务已启动时要重启以清除模型缓存。

## 5. 导出真实证据

```bash
python scripts/export_trace_api_evidence.py
python scripts/export_rerank_contract.py
```

第一个命令通过 TestClient 运行真实 API 请求，使用临时 SQLite 文件并清理，不写 `data/trace.db`。它打印生成路径 `evidence/trace_api_s5_evidence.json` 和：

```text
version_1: status=UNRESOLVED; conclusion=来源未定｜关键上游 reach-a 证据缺失
version_2: status=UNRESOLVED; conclusion=当前证据不足以给出单源结论
```

文件中 `version_count=2`，版本 1 旧输出保留。补回的 A 正常样本：value=6，event_time=analysis_time−10 分钟、received_time=analysis_time−1 分钟、source=manual。**补数后仍未形成单源证据，不应改写为“已找到来源”。**

第二个命令生成 `evidence/rerank_contract.json`，包含 S2/S3/S4a/S4b/S5 与 D1，S5 内含补数前后版本。本次打印：

```json
{"total": 6, "order_changed": 1, "skipped": 4}
```

D1：rule_order 为 `["reach-c:local", "reach-a"]`，model_order 为 `["reach-a", "reach-c:local"]`，order_changed=true。被跳过的是保留独立来源不足两个的情况，模型不能恢复已排除项或改变状态。文件带生成时间，不要求重导出后整个证据文件字节一致。

## 6. 启动工作台

```bash
uvicorn backend.api:app --port 8010
```

启动日志包含：

```text
Application startup complete.
Uvicorn running on http://127.0.0.1:8010 (Press CTRL+C to quit)
```

浏览器打开 **http://127.0.0.1:8010/workbench/**。本次真实 HTTP 验证：

| 请求 | 实际结果 |
|---|---|
| GET `/api/health` | 200，`{"status":"ok","data_classification":"SIMULATED"}` |
| GET `/workbench/` | 200，HTML 包含 SIMULATED |
| GET `/api/scenarios` | 200，S1、S2、S3、S4a、S4b、S5、D1 |
| POST `/api/attributions/run`，D1 且 use_rerank=true | 200，首次版本 1、MULTI_CANDIDATE，规则/模型顺序如步骤 5 |

验证服务已停止；实际使用时按 Ctrl+C 停止。API 默认使用项目 `data/trace.db`，首次启动自动建表。不要把数据库当成固定场景文件提交。

界面操作：S1 显示为不可运行的基线卡片；其余场景点击运行后查看结论、数据健康、候选与排除依据、模型排序、版本历史。S5 可将补数表单改为 reach-a/turbidity，填上述样本和带时区时间；版本 1 与版本 2 均应可选。分析时刻固定为场景中的 `analysis_time`，不要直接填系统今天时间。接口会检查路径 anomaly_id 与场景 target 一致。

## 7. 只验证一个场景

不启动服务、不写数据库，最小归因示例（本次实际执行）：

```bash
python - <<'PY'
import json
from backend.attribution import analyze_attribution

with open('data/trace_scenarios.json', encoding='utf-8') as f:
    scenario = next(s for s in json.load(f)['scenarios']
                    if s['scenario_id'] == 'S3')
output = analyze_attribution(scenario)
print(output['status'])
print(output['conclusion'])
PY
```

本次输出：

```text
MULTI_CANDIDATE
多源候选，来源未定；按相对证据排序保留 reach-c:local、reach-a
```

S1 没有目标异常，使用基线入口：

```bash
python -m backend.baseline --scenario S1
```

实际 JSON 为 `status="无活动异常"`、`active_anomalies=[]`，含各断面指标的真实计算结果。不要用归因接口强行运行 S1。D1 位于独立的 `data/demo_decoy_scenario.json`，不在案例书 JSON 的 scenarios 列表内。

## 可回查范围

同一异常以 target 的 `reach_id-metric-event_time` 标识。重新分析追加 attribution_version；`current_version` 是下一个待用版本号，最新已存版本为其减一。版本列表、完整记录、证据链和 Run Trace 路由见 [API 表](../README.md#api-一览)。

当前输入快照只保存历史样本、当前样本与 target；不等于完整配置/代码冻结。在线重排序作为附加结果返回，历史 rerank 查询按当前缓存模型重算，原归因记录不改写。补数接口重建原场景并替换该断面指标的当前观测，不会自动聚合所有旧补数；SQLite 中的样本与归因旧版本仍保留。

## 常见问题

### pytest 找不到或导入不到 backend

先确认终端位于项目根目录、已激活 `.venv`，并完成 requirements 安装；使用 `PYTHONPATH=. pytest tests -q`。新检出仓库先完成步骤 2，否则模型测试所需的 JSONL 可能不存在。不要使用另一个系统环境里的 pytest。

### 8010 端口被占用

本次 8010 启动成功。若出现 `address already in use`，先停止自己此前启动的服务，再重试；不要直接终止不明进程。若需改端口，uvicorn 支持 `--port`，同时修改浏览器地址；其他端口未在本次核验。

### 模型文件缺失或提示“重排序模型不可用”

先运行步骤 2 的数据生成，再运行步骤 4 的训练，恢复 `models/reranker.json`，然后重启服务。`use_rerank=false` 不要求加载模型；工作台请求默认启用重排序。注意运行归因先保存版本、再尝试附加重排序，模型加载失败时已有归因版本可能已成功写入，请查询版本历史，避免误以为该次运行未发生。

### 补数后依然 UNRESOLVED，或补数时间报错

这可能是正确业务结果：S5 的默认正常 A 补数不提供明确来源证据。查看 evidence_gaps/next_checks。时间须带时区且不能晚于固定 analysis_time；本例不要把 2026-11-02 场景改填为今天。仅修正输入，不用场景名硬编码结论。

### 旧阶段文档与当前能力不同

M0、离线模型文档和旧 C02 证据保留阶段语境；例如 M0 中 S2–S5 reserved、离线模型文档中未接在线推理是当时事实。当前入口、限制与实测以 README 和本指南为准，阶段记录不作为当前运行状态证明。
