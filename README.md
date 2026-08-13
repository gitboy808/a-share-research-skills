# A 股投研工作区 v3

面向 Codex 的风险优先型 A 股投研工作区。版本化 Markdown 保存正式状态，`assemble` / `hydrate` 构造有界阶段工作集，SQLite/FTS5 只作为可重建投影。

## 产品能力和风险边界

- 覆盖机会扫描、事实调研、方向分析、双轴复盘和周期校准。
- 支持 `prospective_current`、`historical_as_of`、`calibration_window` 三种资格模式，以及字段级时效、生命周期、事件失效、显式替代和哈希复验。
- 证据不足时正式弃权；事实、叙事和竞争假设分离；方向逻辑与价格纪律分离。
- 正式证据、判断和复盘只追加，不回写原快照；Markdown 是唯一事实源，projection 可随时重建。
- 输出只用于研究辅助，不构成投资建议，不提供具体买卖指令、仓位比例或自动交易。

## 当前 skills

- `a-share-research`：统一路由与复合阶段编排。
- `a-share-scan`：机会扫描，只生成限时观察候选。
- `a-share-investigate`：事实核验、证据包和档案事实字段。
- `a-share-analyze`：条件判断、价格纪律和原子判断。
- `a-share-review`：结果轴与过程轴复盘。
- `a-share-meta-review`：生命周期、校准和 L0–L2 治理。

完整职责、写入所有权和阶段交接见 [docs/architecture.md](docs/architecture.md)。规范规则只在 [研究规则.md](研究规则.md) 维护，领域语言见 [CONTEXT.md](CONTEXT.md)。

## 初始化

需要 Python 3.9+ 和支持 skills/AGENTS.md 的 Codex 环境。

```bash
git clone https://github.com/gitboy808/a-share-research-skills.git
cd a-share-research-skills
python3 scripts/init_workspace.py --root .
python3 .agents/skills/a-share/shared/scripts/validate_workspace.py --root .
```

初始化器从 `scaffold/` 创建私有运行数据且不覆盖已有文件。当前判断、判断日志、观察记录、证据包、对象档案、报告、运行记录、经验库和周收敛默认被 `.gitignore` 排除；公开基线策略仍由 Git 跟踪。

## 当前工作集接口

工作流只通过 `.agents/skills/a-share/shared/context/` 的两个 interface 构造上下文：

- `assemble(run_manifest, task_evidence_manifest=None)`：按版本化任务契约返回覆盖、缺口、`stable_references` 和 audit-only exclusions。
- `hydrate(stable_references)`：重新解析 Markdown，复验资格、cutoff、替代关系、定位和内容哈希。

CLI 只接受当前参数：

```bash
python3 .agents/skills/a-share/shared/scripts/context_workspace.py assemble \
  --run-manifest run.json --task-evidence task-evidence.json
python3 .agents/skills/a-share/shared/scripts/context_workspace.py hydrate \
  --references stable-references.json --root .
```

持久任务的工作集清单只保存稳定引用、覆盖、缺口、关系、核验和质量字段，不复制来源载荷或研究正文。

## 验证命令

```bash
python3 -m unittest discover -s tests -v
python3 .agents/skills/a-share/shared/scripts/validate_workspace.py --root . --json
python3 scripts/validate_release.py
python3 scripts/validate_deployment.py --root .
```

`validate_release.py` 校验当前 v3 的完整 Git 发布面、正式任务契约、私有路径隔离和敏感信息扫描；`validate_deployment.py` 校验包含本地私有运行数据的工作区。

## 最小使用示例

在仓库根目录启动 Codex 后，可以直接输入：

```text
研究一下长电科技当前业务与产业链事实，不形成方向判断。
分析长电科技未来一周的上涨与下跌逻辑，并给出跟踪指标。
扫描当前低热度但可能承接轮动资金的沪深主板方向。
复盘当前 v3 判断中已经到期的项目。
执行本周元复盘，检查校准和策略生命周期。
```

也可显式调用：`$a-share-research 分析某个交易主题当前的持续性。`
