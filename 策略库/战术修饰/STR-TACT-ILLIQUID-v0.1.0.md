---
schema_version: "a-share-workspace-v3"
artifact_type: "strategy_version"
id: "STR-TACT-ILLIQUID"
version: "0.1.0"
status: "trial"
strategy_kind: "tactical_modifier"
scope: "stock"
created_at: "2026-08-06T00:00:00+08:00"
parameter_origin: "design-prior"
---

# 低流动性风险 · 试运行 0.1.0

- 激活：成交深度、自由流通、换手结构或价格跳跃显示常规确认信号不可靠。
- 否决：行情数据无法稳定核实；小额成交造成主要价格信号；退出风险无法量化。
- 主证据：成交深度、换手集中、连续价格接受和异常波动。
- 确认：基本面或事件事实只能说明逻辑，不能抵消流动性否决。
- 最大默认周期：日内观察；主动候选提高证据门槛，必要时直接规避。
- L2 可调：流动性分位和确认窗口；不得以网络热度提高结论置信度。
