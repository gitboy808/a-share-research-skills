---
schema_version: "a-share-workspace-v3"
artifact_type: "strategy_version"
id: "STR-BASE-CYCLICAL"
version: "0.1.0"
status: "trial"
strategy_kind: "base_profile"
scope: "industry-chain,stock"
created_at: "2026-08-06T00:00:00+08:00"
parameter_origin: "design-prior"
---

# 周期型 · 试运行 0.1.0

适用于盈利主要由商品价格、供需、库存和产能周期驱动的对象。低静态 PE 不能自动构成低估。

| 证据角色 | 内容 |
|---|---|
| 否决项 | 商品/价差数据口径冲突；盈利周期位置未知；价格纪律门失败 |
| 主证据 | 商品价格与价差、库存、产能利用率、供需边际变化 |
| 确认项 | 盈利预期修正、产业链广度、龙头相对强度与价格接受 |
| 背景项 | 静态 PE、叙事热度、单日资金流 |

- 估值锚：中周期盈利、PB、EV/EBITDA 或行业单位指标。
- 默认周期：短周期至波段，取决于供需变化半衰期。
- 价格纪律：顺周期价格已加速且内部广度收缩时转等待，不用低 PE 追高。
- L2 可调：滚动窗口、价差/库存异常分位、确认权重；不得删除周期位置否决项。
