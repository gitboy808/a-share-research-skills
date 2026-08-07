---
status: accepted
---

# Augment 仅作为可替换语义 adapter

Augment Context Engine 可以作为投研工作集装配 module 内部的语义候选扩展 adapter，但不成为必需基础设施，也不负责字段权威、时效、冲突、来源独立性、任务证据清单或研究状态判断；所有命中必须解析为原子研究单元，并在需要举证时通过 `hydrate` 回到权威原文。相比把系统直接建立在通用语义引擎之上，这可能降低 Augment 不可用时的跨对象发现能力，但保留了本地可运行性、投研规则所有权和 adapter 可替换性。

## Consequences

- 本地结构化 adapter 是 production 必需实现，Augment 是可选的 true-external adapter，测试使用内存 adapter；外部 seam 因生产与测试、启用与禁用两种行为而成立。
- Augment 不可用、未配置或返回结果异常时，结构化覆盖与风险护栏继续工作，并在运行清单披露语义候选扩展的降级状态。
- Augment 结果不得直接写入证据包、对象档案或判断；必须经过字段权威映射、去重、时效检查和原文核验。
