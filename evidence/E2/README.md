# E2 证据｜Camera / ASR / TTS 多模态交互

全部使用**浏览器原生 API**，无第三方库：`getUserMedia`（摄像头）/ `SpeechRecognition`（ASR）/ `speechSynthesis`（TTS）。
数据与事件经 MQTT 共享链，语音指令发布到 `tidewatch/events/{reachId}`，由 event_manager 状态机校验后多端同步。

## 能力清单

| 能力 | 实现 |
|------|------|
| 摄像头预览 | `getUserMedia` 前置摄像头 → `<video>`；失败时降级提示（语音/事件功能不受影响）|
| 画面叠加层 | Canvas 2D 每帧绘制：优先断面/状态/水位信息条 + **异常时边框变色（洪水预警=红）** + 右上时间 |
| 拍照记录 | 按钮截帧 + 水印（断面/状态/时间/事件id），缩略图 + 可下载 PNG，与当前断面和事件关联 |
| ASR 指令 | `SpeechRecognition`（zh-CN，continuous + interim）；5 类指令：查看上游/中游/下游、播报状态、开始处理、验证恢复、关闭事件；识别结果与执行指令高亮显示；不支持时提示浏览器 |
| TTS 播报 | `speechSynthesis`（zh-CN）：手动"播报当前状态"（全断面水位/浊度/状态）；**洪水预警转变时自动播报**（正常→预警触发一次，恢复后再异常再次触发）|
| MQTT 联动 | 订阅 `tidewatch/+/data` + `tidewatch/events/+`；语音指令走与多端相同的 MQTT 事件链 |

| 文件 | 证明什么 |
|------|----------|
| `e2_multimodal_flood.png` | 虚拟摄像头画面（自动化测试）+ **红色边框**（reach-b 洪水预警）+ 叠加信息条 + 自动播报日志"reach-b 中游 洪水预警，水位 9 米，请立即关注"；三端卡片状态同屏 |
| `e2_voice_photo.png` | 语音选中 reach-b（蓝色高亮卡片）；指令区显示"✓ 执行指令：开始处理（已发送）"；**现场照片缩略图带水印**（reach-b · 洪水预警 · 时间 · 事件id）+ 关联元数据 + 下载链接 |

## 自动化复现

```bash
python3 scripts/verify_multimodal.py
```

9 组断言：摄像头 640x360 就绪 / 叠加层优先断面+红边框 / 自动播报 / 手动播报 /
查看中游选中+高亮 / 开始处理→HANDLING / 两张照片关联同一断面与事件 / 验证恢复→RECOVERED / 关闭事件→CLOSED。

> 注：无头环境无法真实"说话"，语音指令通过 `__multimodalDebug.handleCommand()` 注入 ——
> 即 ASR `onresult` 最终识别文本所走的**同一处理路径**。真实麦克风链路请现场核验：
> 点"开始监听"→ 说"查看中游" → 卡片高亮 → 说"开始处理" → 事件变 HANDLING。

## flowLevel 来源说明（可追溯性）

当前 flowLevel 来源 = `simulated`（水文节点模拟数据），页面页脚如实标注；
未使用模型推理，因此不存在需要记录 confidence 的自动感知结果。
如后续开放增强引入 model/template 自动视觉感知，将按任务书 0.2 节记录 confidence 并按阈值处理低置信度结果。

## 已知限制

- SpeechRecognition 依赖浏览器（Chrome/Edge 最佳，Safari 部分支持）且 Chrome 需联网识别；不支持时页面给出明确提示
- TTS 语音列表取决于操作系统安装的语音包
- 摄像头需要 localhost 或 HTTPS 环境授权
