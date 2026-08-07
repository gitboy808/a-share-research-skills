---
status: accepted
---

# 工作集装配作为 skill 套件内部 Python module

投研工作集装配 implementation 位于 `.agents/skills/a-share/shared/context/`，任务契约位于同一 shared 区域，并通过 `.agents/skills/a-share/shared/scripts/context_workspace.py` 提供紧凑 JSON CLI adapter；六个 skill 不直接依赖内部 parser、SQLite schema、权威映射或语义 adapter。相比现在提取为独立安装 package，这种布局沿用 skill 套件随工作区分发的现有方式，不引入部署前置条件；等出现第二个独立调用环境时再判断是否形成真实的外部 seam。

## Consequences

- implementation 优先使用 Python 标准库，包括 `sqlite3`、`json`、`pathlib` 与 `hashlib`；可选 Augment adapter 的外部依赖保持隔离。
- module interface 与 JSON CLI 是测试 surface，测试位于仓库根目录 `tests/context/`，通过输入运行清单和任务契约断言装配与核验结果，不穿透内部函数。
- CLI 输出只包含稳定引用、覆盖、缺口、选择原因、核验定位和运行质量字段，不向模型回传完整来源载荷或无关文档正文。
