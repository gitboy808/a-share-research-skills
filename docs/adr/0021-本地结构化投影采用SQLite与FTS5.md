---
status: accepted
---

# 本地结构化投影采用 SQLite 与 FTS5

本地必需 adapter 使用 Python 标准库可维护的 SQLite 与 FTS5 保存原子研究单元、稳定引用、对象字段、时间状态、证据角色、来源组、关系和规范文本索引；数据库文件完全由 Markdown 事实源重建，不接受模型直接写入研究事实。相比纯 JSON/Markdown 派生索引，SQLite 更适合精确过滤、关系、时效与去重；相比图数据库或把 embedding 设为必需能力，它在当前数 MB 工作区规模下更轻、更可检查，也不会把语义引擎变成系统可用性的前置条件。

## Consequences

- SQLite schema 和重建器属于投研工作集装配 module 的 implementation，不进入各 skill 的 interface。
- 缓存文件不纳入版本化事实源；删除、损坏或 schema 变化后必须能够从 Markdown 完整重建。
- FTS5 只辅助本地词法发现；Augment或未来本地 embedding adapter 继续位于内部 seam 后，不能绕过结构化覆盖和原文核验。
