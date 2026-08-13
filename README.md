# A-Share Research Skills

面向 Codex 的风险优先型 A 股投研工作区：把机会扫描、事实调研、方向分析、双轴复盘和周期学习拆成可组合的 skills，并用版本化文档保留跨会话记忆。

> 本项目仅用于研究辅助，不构成投资建议，不提供自动交易、具体买卖指令或仓位比例。

## 能做什么

- 结合 A 股市场状态、产业链、交易主题、个股与外围传导形成条件判断。
- 扫描低热度启动、轮动、补涨与跷跷板机会，但不把观察候选冒充为预测。
- 区分消息事实、市场预期、计价程度和价格接受，降低追高与反向消息误判。
- 同时给出上涨逻辑、下跌逻辑、证伪条件和最多 6 个跟踪指标。
- 证据不足时正式弃权，并用结果轴 + 过程轴复盘历史判断。
- 按独立证据簇和策略版本持续学习，不因单次涨跌轻率修改规则。

## 系统结构

`a-share-research` 统一路由到扫描、调研、分析、复盘与元复盘；完整职责、阶段交接和写入所有权只在 [docs/architecture.md](docs/architecture.md) 维护。

## 阶段工作集

投研 skill 通过 `.agents/skills/a-share/shared/context/` 的 `assemble` 与
`hydrate` 构造阶段工作集。Markdown 仍是唯一事实源；SQLite/FTS5 只是可
重建投影，语义 adapter 未配置时本地结构化路径仍可用。持久任务的工作集
清单只保存稳定引用、覆盖、缺口和质量字段，不复制原文或来源载荷。

```bash
python3 .agents/skills/a-share/shared/scripts/context_workspace.py assemble \
  --run-manifest run.json --task-evidence task-evidence.json
python3 .agents/skills/a-share/shared/scripts/context_workspace.py hydrate \
  --references stable-references.json --root .
```

历史结构迁移必须使用隔离输入/输出：

```bash
python3 scripts/migrate_workspace.py --input <历史副本> --output <新副本>
```

迁移不会修改输入，也不会自动切换任何正式工作区。

## 环境要求

需要 Codex CLI、IDE 扩展或 Codex App，以及 Python 3.9+。项目不自带实时行情、付费数据授权或 API key；无法核实关键数据时应降级或弃权。

## 独立性

本仓库是完整、独立的 skill 套件和工作区模板，不依赖任何仓库外目录、固定用户名、历史会话或私有工作区。新用户可直接在克隆目录初始化并运行；历史工作区迁移是可选流程，调用方必须显式提供互不重叠的只读输入目录和新输出目录。

影子回放的会话身份与上下文目标由每个验收套件自行声明，并通过冻结工件和 telemetry 哈希绑定。仓库不内置私有会话 ID、机器绝对路径或特定用户的验收数值。

## 快速开始

```bash
git clone https://github.com/gitboy808/a-share-research-skills.git
cd a-share-research-skills
python3 scripts/init_workspace.py --root .
python3 .agents/skills/a-share/shared/scripts/validate_workspace.py --root .
python3 scripts/validate_release.py
```

然后从仓库根目录启动 Codex，或在 Codex App 中打开该目录。Codex 会自动发现 `.agents/skills` 中的 skills，并加载根目录 `AGENTS.md`。参见 OpenAI 的 [Build skills](https://learn.chatgpt.com/docs/build-skills) 和 [AGENTS.md](https://learn.chatgpt.com/docs/agent-configuration/agents-md) 文档。

### 第一次可以这样用

```text
分析长电科技未来一周的上涨与下跌逻辑，并给出跟踪指标。
扫描当前低热度但可能承接热点回调资金的沪深主板方向。
结合昨晚美股、日韩市场和 A 股当前状态做今日盘前分析。
复盘当前判断中已经到期的项目。
执行本周元复盘，检查错误类型和策略版本是否需要调整。
$a-share-research 分析某个板块当前的持续性。
```

也可用 `$a-share-research 分析某个板块当前的持续性。` 显式调用统一入口。

完整流程示例见 [examples/端到端示例.md](examples/端到端示例.md)。

## 研究状态

公开研究状态只有：弃权、规避、等待确认、研究条件成立、持仓逻辑失效；“研究条件成立”不是买入指令。

## 工作区数据

`scripts/init_workspace.py` 从 `scaffold/` 创建且不覆盖私有运行数据：当前判断、判断日志、观察池/日志、证据包、对象档案、报告、经验库和周收敛。

上述运行数据已在 `.gitignore` 中默认排除。如需跨设备保存，请使用私有仓库或加密备份，不要在公开 PR 中提交。

`策略库/` 是公开、受 Git 管理的基线配置，不由初始化器生成，也不在 `.gitignore` 中。元复盘修改策略版本时会产生 Git diff；只希望个人使用的参数应保留在私有 fork 或私有分支，不要提交到公开 PR。

## 验证

```bash
python3 .agents/skills/a-share/shared/scripts/validate_workspace.py --root .
```

正常结果：

```text
errors: 0
warnings: 0
```

## 边界与局限

- 不保证收益率、预测正确率或信息完整性。
- 不替代持牌投资顾问、专业数据服务或用户自身判断。
- 不把“主力意图”或叙事操纵当作已知事实。
- 消息真实不代表价格一定正向反应；所有消息都需要检查预期差与价格接受。
- 数据源可能存在时区、复权、单位、字段定义和授权差异，使用者需自行核实。

## 贡献

请阅读 [CONTRIBUTING.md](CONTRIBUTING.md)。修改不得携带个人持仓、未脱敏运行数据或未授权资料。

## License

[Apache License 2.0](LICENSE)
