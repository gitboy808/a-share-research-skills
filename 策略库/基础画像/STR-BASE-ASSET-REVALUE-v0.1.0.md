---
schema_version: "a-share-workspace-v3"
artifact_type: "strategy_version"
id: "STR-BASE-ASSET-REVALUE"
version: "0.1.0"
status: "trial"
strategy_kind: "base_profile"
scope: "industry-chain,stock"
created_at: "2026-08-06T00:00:00+08:00"
parameter_origin: "design-prior"
---

# 资产重估型 · 试运行 0.1.0

适用于资产价值、资本结构或资产使用效率变化构成主要估值来源的对象。

| 证据角色 | 内容 |
|---|---|
| 否决项 | 资产权属或可变现性不清；重估路径无正式依据；价格纪律门失败 |
| 主证据 | 资产质量、负债约束、重组/注入/处置正式进度、可比资产价值 |
| 确认项 | 现金流改善、折价收敛、实施进度、价格接受与资金持续性 |
| 背景项 | 市值对资产总额的粗比、未证实重组传闻 |

- 估值锚：PB、净资产调整值、分部估值或可核验资产现金流。
- 默认周期：事件窗口至波段。
- 价格纪律：传闻阶段快速重估且实施路径未确认时优先等待。
- L2 可调：折价阈值、实施阶段权重、事件窗口；不得用传闻替代权属与路径证据。
