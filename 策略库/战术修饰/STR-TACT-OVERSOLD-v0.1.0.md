---
schema_version: "a-share-workspace-v3"
artifact_type: "strategy_version"
id: "STR-TACT-OVERSOLD"
version: "0.1.0"
status: "trial"
strategy_kind: "tactical_modifier"
scope: "industry-chain,trading-theme,stock"
created_at: "2026-08-06T00:00:00+08:00"
parameter_origin: "design-prior"
---

# 超跌修复 · 试运行 0.1.0

- 激活：经动态基线确认的超跌后，卖压缓和、相对强度和内部广度出现早期修复。
- 否决：仅单日反抽；量价不被接受；基本面前提继续恶化；错过最大价格位移。
- 主证据：波动调整跌幅、卖压衰减、广度、承接和轮动资金。
- 确认：催化、估值锚和相对基准改善。
- 最大默认周期：2–5 个交易日；没有结构证据时不得升级为趋势反转。
- L2 可调：超跌分位、确认窗口、位移上限；不得删除自动失效时间。
