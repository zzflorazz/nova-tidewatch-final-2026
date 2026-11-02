# TideWatch Trace 提交—AI 对话—验证对应索引

本索引仅记录已发生且可回查的提交，不补造历史日期、对话或验证结果。

| 日期 | Git Hash | 提交说明 | AI 协同记录 | 实际验证 | 主要改动 |
|---|---|---|---|---|---|
| 2026-11-02 | `4c8a9e7` | 增加分断面 IQR 基线与 S1 回归测试 | [S1 对话整理](ai-sessions-20261102.md#第一阶段m0-与-s1-基线) | Trace 9 passed；旧规则 4/4；S1 无活动异常 | M0 记录、独立基线、S1 固定场景和测试 |
| 2026-11-02 | `796c925` | 增加 S2 溯源排序与传播经过判定 | [S2 对话整理](ai-sessions-20261102.md#第二阶段s2-单源归因与传播经过) | Trace 23 passed；旧规则 4/4；A 为唯一独立来源，B 为传播经过 | S2 场景、确定性归因、传播与排除测试 |

## 运行入口

```bash
python3 -m backend.baseline --scenario S1
python3 -m backend.attribution --scenario S2
python3 -m pytest tests/test_trace.py -q
python3 python/rules.py
```

## S1/S2 原始证据与复验文件

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

上述文本证据均由真实 Codex 主会话或实际命令输出生成；测试日志是当前复验结果，不冒充首次开发时的终端截图。

## 参赛材料状态

- 两次提交均为本地真实提交，尚未在本索引中声明远程推送状态。
- AI 会话文件是真实会话整理稿，不是原始截图或完整对话 PDF。
- 最终提交前仍需从 Codex 主会话导出完整对话 PDF；若需逐张满足六类界面截图，再补拍首次回复等历史界面，并核对 Hash、文件和测试记录一致。
- 技术文档尚未单独成章；后续可引用 M0 记录、S1/S2 对话和本索引，不虚构未完成章节。
