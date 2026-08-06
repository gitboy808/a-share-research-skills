---
schema_version: "a-share-workspace-v3"
artifact_type: "strategy_version"
id: "STR-TACT-SENTIMENT"
version: "0.1.0"
status: "trial"
strategy_kind: "tactical_modifier"
scope: "trading-theme,stock"
created_at: "2026-08-06T00:00:00+08:00"
parameter_origin: "design-prior"
---

# 情绪加速 · 试运行 0.1.0

- 激活：热度、扩散、拥挤、价格接受和衰减五维中，价格与广度确认情绪加速。
- 否决：热度上升但价格不接受；龙头放量滞涨且广度收缩；超过价格位移上限。
- 主证据：价格接受、龙头/梯队、内部广度、换手与衰减。
- 确认：叙事扩散和资金持续性；网络热度单独只能作为背景。
- 最大默认周期：次日；到期自动失效，不能用验证完成后的结论追涨。
- L2 可调：确认窗口、拥挤分位、衰减阈值；不得降低事实与价格纪律门。
