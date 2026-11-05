# TideWatch Trace 提交—AI 对话—验证对应索引

本索引仅记录已发生且可回查的提交，不补造历史日期、对话或验证结果。

| 日期 | Git Hash | 提交说明 | AI 协同记录 | 实际验证 | 主要改动 |
|---|---|---|---|---|---|
| 2026-11-02 | `4c8a9e7` | 增加分断面 IQR 基线与 S1 回归测试 | [S1 对话整理](ai-sessions-20261102.md#第一阶段m0-与-s1-基线) | Trace 9 passed；旧规则 4/4；S1 无活动异常 | M0 记录、独立基线、S1 固定场景和测试 |
| 2026-11-02 | `796c925` | 增加 S2 溯源排序与传播经过判定 | [S2 对话整理](ai-sessions-20261102.md#第二阶段s2-单源归因与传播经过) | Trace 23 passed；旧规则 4/4；A 为唯一独立来源，B 为传播经过 | S2 场景、确定性归因、传播与排除测试 |
| 2026-11-03 | `c39251b` | 完善 S3 多候选与 S5 缺失证据归因 | [S3/S5 对话整理](ai-sessions-20261103.md) | Trace 41 passed；旧规则 4/4；S3 保留两个候选，S5 因 A 缺失而来源未定 | S3/S5 固定场景、缺失/过期数据处理、人工记录证据等级、回归测试与真实输出 |

## 运行入口

```bash
python3 -m backend.baseline --scenario S1
python3 -m backend.attribution --scenario S2
/private/tmp/tidewatch-m0-venv/bin/python -m backend.attribution --scenario S3
/private/tmp/tidewatch-m0-venv/bin/python -m backend.attribution --scenario S5
/private/tmp/tidewatch-m0-venv/bin/python -m pytest tests -q
python3 python/rules.py
```

## 原始证据与复验文件

### S1

- 原始提问：[界面截图](../evidence/trace_s1_01_original_prompt.png)｜[完整文字](../evidence/trace_s1_01_original_prompt.txt)
- 首次回复：[原始日志提取文本](../evidence/trace_s1_02_first_response.txt)
- 实测失败与复测说明：[原始日志提取文本](../evidence/trace_s1_03_review_and_revision.txt)
- 结果验证：[界面截图](../evidence/trace_s1_04_result_summary.png)｜[当前复验测试日志](../evidence/trace_s1_04_test_output.txt)｜[S1 JSON 输出](../evidence/trace_s1_04_s1_output.json)
- 对应提交：[Git show 记录](../evidence/trace_s1_05_commit.txt)

### S2

- 原始提问：[界面截图](../evidence/trace_s2_01_original_prompt.png)｜[完整文字](../evidence/trace_s2_01_original_prompt.txt)
- 首次回复：[原始日志提取文本](../evidence/trace_s2_02_first_response.txt)
- 设计与测试复核：[设计说明](../evidence/trace_s2_03_design_review.txt)｜[测试说明](../evidence/trace_s2_03_test_review.txt)
- 结果验证：[当前复验测试日志](../evidence/trace_s2_04_test_output.txt)｜[S2 JSON 输出](../evidence/trace_s2_04_s2_output.json)
- 对应提交：[Git show 记录](../evidence/trace_s2_05_commit.txt)

### S3/S5

- 协同过程与证据边界：[2026-11-03 会话整理](ai-sessions-20261103.md)
- 正式仓库不一致纠偏：[原始日志提取与核查](../evidence/trace_s3_s5_01_repository_review.txt)
- 隔离副本确认、补丁原文哈希及数值复核：[补丁核查记录](../evidence/trace_s3_s5_02_patch_review.txt)
- 最终复验：[测试日志](../evidence/trace_s3_s5_test_output.txt)｜[S3 JSON 输出](../evidence/trace_s3_output.json)｜[S5 JSON 输出](../evidence/trace_s5_output.json)
- 对应提交：[Git show 记录](../evidence/trace_s3_s5_05_commit.txt)

### Codex 完整对话 PDF

- [trace_codex_session.pdf](../evidence/trace_codex_session.pdf)（561 KB）
- 数据来源：`~/.codex/sessions/2026/11/02/rollout-2026-11-02T13-47-28-01a19acc-641a-7290-af23-1ef8e1da6be0.jsonl`
- 生成方式：`scripts/export_codex_session.py` 提取 23 条 user/assistant 消息 → HTML → Playwright printToPDF
- 覆盖范围：S1 基线、S2 单源归因、S3 多候选、S5 缺失证据、隔离副本纠偏、补丁导出
- 格式说明：本 PDF 由原始 JSONL 重新排版生成，**不是** Codex GUI 原始截图或原生 PDF

上述文本证据均由真实 Codex 主会话或实际命令输出生成；测试日志是当前复验结果，不冒充首次开发时的终端截图。

## 参赛材料状态

- `4c8a9e7`、`796c925`、`c39251b` 均为真实提交；截至代码提交 `c39251b`，已通过 GitHub API 核对远程 `main` 与本地一致。本索引及新增归档文件的提交、推送状态以实际 Git 状态为准。
- [trace_codex_session.pdf](../evidence/trace_codex_session.pdf) 是从 Codex 原始 JSONL 导出的完整对话 PDF（23 条消息，覆盖 S1–S3/S5），**不是 GUI 原始截图**；对话整理稿、PDF 和原始 JSONL 三者互相印证。
- Codex 原始形态只有 JSONL 会话文件，从未存在 GUI 原生 PDF 导出或截图；PDF 由 `scripts/export_codex_session.py` 重新排版生成，已在索引中明确标注来源。
- 最终提交前仍需从真实 Codex/Trae 会话导出所需原始材料，并核对 Hash、文件和测试记录一致。
- 技术文档尚未单独成章；后续可引用 M0 记录、S1/S2/S3/S5 对话和本索引，不虚构未完成章节。
