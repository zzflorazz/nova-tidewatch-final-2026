# E1 证据｜3D 河道数字空间（Three.js）

## 实现要点

- Three.js **r128 CDN**（非模块）+ OrbitControls（CDN），无 npm 依赖
- 河道沿 X 轴分 **3 段**（各对应一个断面：reach-a 上游 / reach-b 中游 / reach-c 下游），
  中心线为 `z = 3.5·sin(0.12x)` 的正弦弯曲；每段水面独立颜色与高度
- **状态驱动**：水面颜色 4 档（正常蓝 #1890ff / 警戒黄 #faad14 / 预警红 #f5222d / 浑浊青 #13c2c2）；
  水面高度按 `(waterLevel-3.0)/7.0` 归一化（clamp 0~1）平滑过渡；洪水预警时水面正弦波动幅度加大 + 信标闪烁
- **优先关注**：复用 `calcPriority`，优先断面有红色脉冲光圈（浮于水面之上）
- **事件状态**：断面标记上方 CanvasTexture 状态牌（OPEN 红 / HANDLING 橙 / RECOVERED 绿 ✓）
- **交互**：OrbitControls 旋转/缩放/平移；Raycaster 悬停 tooltip + 真实点击选中（与拖拽区分）
- 水流箭头速度随 flowLevel 变化；数据全部由 MQTT 驱动（无刷新、无轮询）
- `window.__scene3dDebug` 调试钩子（供自动化验证与现场核验）

| 文件 | 证明什么 |
|------|----------|
| `e1_3d_three_states.png` | 三断面同屏三种状态：黄（reach-a 高水位警戒）/ 红（reach-b 洪水预警，可见波动）/ 青（reach-c 水质浑浊）；水面高度可见差异；reach-b 红色优先脉冲光圈；三块"事件 OPEN"状态牌；水闸/水流箭头/图例 |
| `e1_3d_click_panel.png` | Raycaster 点击 reach-c 标记后：右侧详情面板显示水位/浊度/水流/状态徽章/更新时间/事件状态；悬停 tooltip 同步显示；白色选中光圈 |

## 自动化复现

```bash
python3 scripts/verify_3d.py
```

断言覆盖：4 种水面颜色 / 归一化高度排序（9.0>7.6>4.5）/ 优先关注 = reach-b(10.5) /
事件状态牌出现 / projectReach + 鼠标真实点击 → 选中 + 面板联动。

## 已知限制

- 水面照明在波动时不做逐帧法线重算（性能取舍），波动视觉以颜色与顶点起伏为主
- 需要 WebGL 环境；无 WebGL 时页面显示提示文案而不是崩溃
