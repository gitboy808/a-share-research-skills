---
schema_version: "a-share-workspace-v3"
artifact_type: "strategy_version"
id: "STR-TACT-EVENT"
version: "0.1.0"
status: "trial"
strategy_kind: "tactical_modifier"
scope: "industry-chain,trading-theme,stock"
created_at: "2026-08-06T00:00:00+08:00"
parameter_origin: "design-prior"
---

# 事件驱动 · 试运行 0.1.0

- 激活：事件时间、结果范围和传导对象可界定，且存在可信事前预期基准。
- 否决：事件真假或时间不清；预期基准不存在；价格接受失败。
- 主证据：事实状态、预期差、计价程度、事件窗口和直接传导。
- 确认：竞价/开盘接受、成交结构、成员广度和后续事实。
- 最大默认周期：事件窗口，事件结束或预期差关闭即失效。
- L2 可调：窗口、接受阈值、传导时滞；不得把标题方向直接映射为价格方向。
