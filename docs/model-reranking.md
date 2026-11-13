# TideWatch Trace：离线可解释候选重排序

## 范围与证据

本步只做标准库逻辑回归的离线训练和评估，不接入 API、工作台或在线推理，不改变现有归因算法。结果来自实际运行的 [model_comparison.json](../evidence/model_comparison.json)，模型参数见 [reranker.json](../models/reranker.json)。生成时刻：2026-11-12T08:06:29.169370+00:00。

数据 SHA256：`1b6084744119cc408cc96dd3fa679923d54c7031fcc58bc3d42e13c49dc04a71`。模型 SHA256：`65bb6f4cf7261b24090adcd61cae52f0d54f69a9c53db03f8e2a97556ec22f14`。证据包含逐场景的真实来源、各策略首选来源和真实来源排名，便于复查失败案例。重新生成数据后必须重新训练；时间戳会变化，固定输入及参数下模型与指标应保持一致。

## 为什么保留规则硬约束

缺测、不满足传播窗口、无活动异常、传播经过与独立来源的区别由现有规则负责。模型仅对规则保留下来的独立来源排序，不能恢复已排除候选、把传播经过变为来源、推翻来源未定或生成因果结论。

本次训练保留传播经过的负例，但评估时 rule/model/fusion 全部只对 `role=independent_source` 且 `status=retained` 的同一个候选池排序。这意味着“model”是模型替换排序分的离线策略，不是脱离规则的纯模型系统。没有在本次实现新的业务判断。原有规则与模型并非互斥替代关系。

## 特征与预处理

输入是 `simulated_candidates.jsonl`，数值由生成器直接提取自 `analyze_attribution` 的候选，而非重新计算基线或另写评分算法。固定特征顺序如下：

| 特征 | 来源和变换 |
|---|---|
| time | 候选 `score_components.time`，传播窗口内的时间匹配分 |
| absolute_change | `score_components.absolute_change`，规则使用的相对幅度分量 |
| topology | `score_components.topology`，规则使用的拓扑分量 |
| local_evidence | `score_components.local_evidence`，本地证据分量 |
| log1p(absolute_change_value) | 对候选真实绝对变化量 `absolute_change` 作 `log1p` 变换 |
| time_lag_minutes | 候选的事件时间滞后；缺失时使用同一场景的其他候选观测均值 |

`label/source_id/scenario_uid/status/role/family` 不进入模型特征。ID 只用于分组、稳定排序和结果展示；role/status 只用于规则硬约束。证据等级字段也不进入这六维模型。

需要补缺时，先调用 `build_scenario_groups(rows)`，再对其返回的候选调用 `predict_proba(row)`。`build_scenario_groups` 使用传入 rows 中同场景的全部非空滞后，包括传播经过负例；本数据文件已经移除了 excluded 候选，因此补值池不含 excluded 行。顺序为先按场景补值，再按 role/status 过滤排序池，不能先过滤掉传播经过再计算均值。该函数不读取标签。训练和测试分别分组，不跨 split 补缺。整个场景均无有效滞后时无法计算场景均值，将显式报错，不偷偷用 0 或测试集全局均值。当前数据的滞后均不缺失；补缺能力由构造测试验证。

六维数据的均值和总体标准差仅在 train 上拟合；零方差列的标准差置 1。test 与反序列化模型复用这些参数，不重新拟合。模型文件保存了均值、标准差、标准化系数和截距。

## 数据、训练与评估口径

全部数据均为 SIMULATED。训练包括 single_upstream、decoy_local、decoy_upstream，每族 200 个场景；按每族连续 seed 的前 80%/后 20% 划分，不按候选划分。训练 480 个场景、960 行候选；测试 120 个场景、240 行候选。这些 960/240 是排序硬过滤前的文件候选行数，含训练集 160 行、测试集 40 行传播经过负例。过滤后的测试排序池共 200 行候选、120 个场景。每场景一个正例和至少一个负例（过滤前）。single_local、multi_candidate、missing_upstream、baseline 留在契约文件，不参与排序训练或指标。

其中 40 个 single_upstream 测试场景在硬约束后只有 A 一个来源，排序天然成功；另外 80 个 decoy 测试场景才包含真实的候选选择。整体成绩不能忽略这一区别。

训练使用固定 random seed=42 初始化、全批量梯度下降、学习率 0.1、1500 次迭代、L2=0.01。目标为平均二元 log loss 加 `L2/2 * sum(w²)`；截距不正则化。实际训练 log loss=0.080954550。没有根据 test 调整超参数。

规则权重从现有场景 `attribution_config.ranking_weights` 读取并验证一致：time=30、absolute_change=40、topology=10、local_evidence=20。无 `ranking_score` 字段时按这四项复原；传播经过等非独立候选规则来源分为 0。测试对照重新调用现有归因函数，检查复原值与真实 ranking_score 一致。

三种策略：rule 按规则分降序；model 按逻辑回归 sigmoid 输出降序；fusion 为 `0.5 * (rule_score / 100) + 0.5 * predict_proba`。100 是现有非负权重总和以及分量上界对应的规则分上界，不从 test 拟合。没有使用两候选 min-max 归一化，否则规则首尾固定相差 1，等权融合将无法纠正规则排序。平分时均按 source_id 升序，三策略统一。

Top-1 为每场景首选是否等于注入真源，先按场景计数后取平均；MRR 为真实来源排名倒数的场景平均值。模型输出未做概率校准，不能解释为真实来源置信度或因果概率。

## 真实评估结果

| 策略 | Top-1 | MRR |
|---|---:|---:|
| rule | 0.666667 | 0.833333 |
| model | 1.000000 | 1.000000 |
| fusion | 1.000000 | 1.000000 |

| family | 测试场景数 | rule Top-1 | model Top-1 | fusion Top-1 |
|---|---:|---:|---:|---:|
| decoy_local | 40 | 0.000000 | 1.000000 | 1.000000 |
| decoy_upstream | 40 | 1.000000 | 1.000000 | 1.000000 |
| single_upstream | 40 | 1.000000 | 1.000000 | 1.000000 |

本次规则的 40 次错误全部来自 decoy_local；模型与融合在三个测试族均无错误。这是该固定合成数据的实际观察，不是对真实水体的性能保证。

## 标准化系数与解释

| 特征 | 标准化系数 |
|---|---:|
| `time` | +2.604958113 |
| `absolute_change` | -0.174012292 |
| `topology` | -2.290940197 |
| `local_evidence` | +1.774283905 |
| `log1p(absolute_change_value)` | +0.083412435 |
| `time_lag_minutes` | -0.030825351 |

按绝对系数最大的是 time（+2.604958）：其他特征固定时，时间匹配分提高一个训练集标准差，会提高正例的 log-odds。topology 为 -2.290940，local_evidence 为 +1.774284。拓扑与本地证据分量在这些候选模板中相关，系数不能独立解释为水文影响或污染传播规律。

absolute_change 的负系数与本次特意设计的“大幅度干扰项”有关，不表示异常幅度越大现实中越不可能是来源。log1p 幅度和滞后的小系数只表示在这六个相关特征、当前标准化及 L2 下的条件关联，不证明这些信息无价值。截距保存在模型 JSON 中，未混入六个特征系数。

## 局限与后续边界

- 训练与评估均基于程序化生成数据，不代表真实水体场景准确率。
- decoy 族中 true source 与 decoy 在 time 特征上差异明显，模型可能高估泛化能力。场景 seed 留出只验证同一生成机制的再抽样，不是新河道、新传播机制或真实观测的外部验证。
- 真实标签来自生成器注入，人工施工记录仍为 pending；训练标签不是现场证据核实结果。
- 规则与模型不是互斥关系。当前未在线使用模型，未改变候选、证据等级、排除项和排查建议。
- 候选排序概率为相对分，不是因果概率；未校准概率、未进行置信区间或多随机切分评估。
- 本次 test 成绩不能用于声称真实场景效果。后续应增加跨生成机制、模糊时间差、记录冲突和真实人工复核数据，再讨论接入。

## 复现

在仓库根目录执行：

```bash
/private/tmp/tidewatch-m0-venv/bin/python scripts/train_reranker.py
PYTHONPATH=. /private/tmp/tidewatch-m0-venv/bin/python -m pytest tests -q
```

`models/reranker.json` 小于 50KB，作为可版本控制的模型工件保留；数据 JSONL 仍为被忽略的生成物。加载可使用 `Reranker.from_dict(json.loads(...))`，不会训练或执行任何在线归因。
