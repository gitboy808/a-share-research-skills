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

```mermaid
flowchart LR
    U["用户请求"] --> R["a-share-research<br/>统一路由"]
    R --> S["机会扫描"]
    R --> I["事实调研"]
    I --> A["方向分析"]
    A --> J["原子判断"]
    J --> V["结果轴 + 过程轴复盘"]
    V --> M["元复盘与策略演进"]
    S --> I
```

| Skill | 职责 |
|---|---|
| `a-share-research` | 统一入口、预检、路由与复合工作流 |
| `a-share-scan` | 低热度、轮动和补涨机会扫描 |
| `a-share-investigate` | 原子事实核验与证据包 |
| `a-share-analyze` | 双向逻辑、价格纪律与可证伪判断 |
| `a-share-review` | 判断的结果轴 + 过程轴复盘 |
| `a-share-meta-review` | 教训、校准、扫描质量和策略版本治理 |

详细架构见 [docs/architecture.md](docs/architecture.md)。

## 环境要求

- Codex CLI、IDE 扩展或 Codex App。
- Python 3.9 或更高版本；核心脚本只使用标准库。
- 能够查询当日行情、公告和新闻的数据或搜索工具。

项目不自带实时行情、付费数据授权或 API key。无法核实关键数据时，系统应该降级或弃权。

## 快速开始

```bash
git clone <repository-url>
cd a-share-research-skills
python3 scripts/init_workspace.py --root .
python3 .agents/skills/a-share/shared/scripts/validate_workspace.py --root .
python3 scripts/validate_release.py
```

然后从仓库根目录启动 Codex，或在 Codex App 中打开该目录。Codex 会自动发现 `.agents/skills` 中的 skills，并加载根目录 `AGENTS.md`。参见 OpenAI 的 [Build skills](https://learn.chatgpt.com/docs/build-skills) 和 [AGENTS.md](https://learn.chatgpt.com/docs/agent-configuration/agents-md) 文档。

### 第一次可以这样用

```text
分析长电科技未来一周的上涨与下跌逻辑，并给出跟踪指标。
```

```text
扫描当前低热度但可能承接热点回调资金的沪深主板方向。
```

```text
结合昨晚美股、日韩市场和 A 股当前状态做今日盘前分析。
```

```text
复盘当前判断中已经到期的项目。
```

```text
执行本周元复盘，检查错误类型和策略版本是否需要调整。
```

也可显式调用：

```text
$a-share-research 分析某个板块当前的持续性。
```

完整流程示例见 [examples/端到端示例.md](examples/端到端示例.md)。

## 研究状态

系统只允许以下公开研究状态：

- 弃权
- 规避
- 等待确认
- 研究条件成立
- 持仓逻辑失效

“研究条件成立”不是买入指令。

## 工作区数据

`scripts/init_workspace.py` 会从 `scaffold/` 创建以下私有运行数据，且绝不覆盖已有文件：

| 内容 | 位置 |
|---|---|
| 活跃判断 | `当前判断.md` |
| 完整判断流水 | `判断日志/` |
| 机会候选 | `观察池.md`、`观察日志/` |
| 原子证据 | `证据包/` |
| 对象长期档案 | `对象档案/` |
| 日常报告 | `报告/` |
| 市场教训 | `经验库.md` |
| 周度收敛 | `周收敛/` |
| 策略版本 | `策略库/` |

运行数据已在 `.gitignore` 中默认排除。如需跨设备保存，请使用私有仓库或加密备份，不要在公开 PR 中提交。

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
