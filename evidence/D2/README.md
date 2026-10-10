# D2 证据｜持续风险与优先关注

算法：`calcPriority(status, flowLevel, anomalyCount) = STATUS_WEIGHTS[status] × 2 + flowLevel + anomalyCount × 0.5`
（JS 与 Python 实现完全一致，见 `web/js/rules.js` 与 `python/rules.py`）

| 文件 | 证明什么 |
|------|----------|
| `test_priority_output.txt` | 3 组三断面数据 → 优先断面分别为 reach-a / reach-c / reach-b，优先度与理由随数据变化，无任何写死的断面 |
| `d2_priority_reach-c.png` | 组2 情况：reach-c 洪水预警+湍急（9.0/20/flow2）→ 面板显示"当前优先关注 reach-c（下游）"，理由完整公式 `环境:洪水预警(8) + 水流:湍急(2) + 连续异常:1次(0.5) = 10.5`，reach-c 卡片红色脉冲高亮 |
| `d2_priority_reach-b.png` | 换一组数据：reach-b 洪水预警+湍流（9.0/20/flow3），reach-c 复位 → 优先关注自动变为 reach-b（11.5），高亮随之转移 —— 证明优先结论由数据计算、随数据变化 |

## 优先规则说明（可解释 / 可重复）

| 因素 | 权重 | 理由 |
|------|------|------|
| 环境 status | STATUS_WEIGHTS × 2（最高） | 水位/浊度阈值代表实际环境风险，优先级最高 |
| 水流 flowLevel | +0~3 直接加 | 感知维度：水流越急（湍流>湍急>平稳>缓流）越值得现场关注 |
| 连续异常次数 | +0.5/次 | 持续恶化比单次异常更值得处理（次要因素，避免短时抖动盖过环境风险） |

同分时取 REACH_IDS 顺序靠前者（reach-a → b → c）；规则写在程序逻辑中，无写死断面。

## 自动化复现

```bash
python3 scripts/test_priority.py        # 3 组数据（离线，纯算法验证）
python3 scripts/verify_priority_web.py  # 全链路（Playwright，验证 Web 面板与高亮跟随数据变化）
```

> 注：Web 端连续异常计数按"收到的消息条数"累计，正常消息立即清零；
> 离线测试 `test_priority.py` 按新数据（anomaly_count=0）计算，因此分数不含异常项。
