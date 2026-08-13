# A 股投研工作区 v3 架构

状态：已实施。当前工作区 schema 为 `a-share-workspace-v3`。领域术语以根目录 `CONTEXT.md` 为准，规范规则只在 `研究规则.md` 维护；架构取舍见 ADR-0012–0022、ADR-0025–0027 和 ADR-0029。

## 1. 目标与边界

工作区把机会发现、事实核实、条件判断、双轴复盘和周期学习拆成可独立调用、可组合、可审计的工作流。当前边界是不执行无人值守调度、自动交易、具体买卖指令或仓位比例，不把数据库设为事实源，也不把不可观察的参与者意图写成事实。

风险、证据、判断、复盘和自治规则统一指向 `研究规则.md`；本文件只定义 module、interface、阶段协议、写入所有权和发布面。

## 2. 套件结构

```text
.agents/skills/a-share/
├── a-share-research/       # 路由与复合编排
├── a-share-scan/           # 机会扫描
├── a-share-investigate/    # 事实调研与证据包
├── a-share-analyze/        # 条件判断与原子判断
├── a-share-review/         # 结果轴 + 过程轴复盘
├── a-share-meta-review/    # 校准、生命周期与治理
└── shared/
    ├── context/            # 深 module：assemble / hydrate
    ├── contracts/          # 版本化任务契约
    ├── schemas/            # 当前 v3 schema
    └── scripts/            # context CLI、校验、ID、payload store
```

每个 skill 目录包含 `SKILL.md` 和 `agents/openai.yaml`。默认入口是 `a-share-research`；specialist 也可直接调用，但必须执行同一阶段协议。

## 3. 路由

| 用户意图 | 工作流 |
|---|---|
| 看看、研究一下、了解一下 | investigate |
| 核实事实、业务或产业链关系 | investigate |
| 分析方向、持续性、跟踪指标 | investigate → analyze |
| 扫描低热度、轮动、补涨机会 | scan |
| 盘前 | scan → investigate → analyze |
| 竞价、盘中、事件影响 | investigate(delta) → analyze(stage) |
| 复盘已形成判断 | review |
| 周度校准、策略治理 | meta-review |
| 先复盘再前瞻 | review 冻结 → 新 investigate → 新 analyze |

低置信路由停在调研。复合流程只复用 RUN ID，不复用阶段 manifest、cutoff、task contract、task evidence 或 workset。

## 4. 工作集装配深 module

`shared/context` 在运行清单与阶段上下文之间形成单一 seam。公共 interface 只有：

- `assemble(run_manifest, task_evidence_manifest=None)`：实例化正式任务契约，编译资格策略，返回覆盖、缺口、projection 状态、`stable_references`、audit-only references 和质量字段。
- `hydrate(stable_references)`：重新解析 Markdown，核对定位与内容哈希，并按 stable reference 中的编译策略复验资格和 cutoff。

字段权威映射、Markdown parser、SQLite/FTS5、语义 adapter、去重、生命周期、时效、替代关系、来源定位和软预算都封装在 implementation 内。skills 与测试只跨公共 seam；删除 module 会把这些复杂性扩散到每个调用方，因此该 module 保持深度和 locality。

### 4.1 资格模式

- `prospective_current`：供扫描、调研和分析选择 cutoff 时仍有效的当前原子。
- `historical_as_of`：供复盘分别重建原判断、原证据和结果窗口；形成判断后的信息不能进入过程评价。
- `calibration_window`：供元复盘选择窗口内已终止样本，不赋予其当前分析资格。

判断有效状态由不可变快照与追加的结果、时限、事件失效和终止记录派生。字段与策略版本使用稳定 logical identity、独立 version 和显式 `supersedes`；文件日期不表示替代。对象档案按字段原子分别保存核实时间、有效期、状态、来源和替代关系。

被排除原子只进入 audit-only reference。hydrate 不信任 assemble 的缓存结论，必须复验 Markdown 定位、hash、cutoff、生命周期和替代关系。

### 4.2 Projection 与来源载荷

Markdown 是唯一事实源。`a-share-context-projection-v1` SQLite/FTS5 数据库可从 Markdown 全量重建，不接受模型写入；schema、事实文件 hash 或 FTS 完整性异常时重建，失败时禁用陈旧结果并直读事实源，同时报告 `projection_degraded`。

语义 adapter 只能扩展候选，不能满足必需覆盖。来源载荷通过 `source_payload_store.py` 外置；工作集和 CLI 只携带定位与有界核验片段，不复制完整载荷。正式引用仍存在时不得删除 payload 或 sidecar。

## 5. 版本化任务契约

`shared/contracts/` 为每个 workflow、stage 和对象类型声明固定证据底线、资格模式、允许未知和阻断语义。研究运行只接受仓库注册契约的 ID、版本、路径和内容 hash；运行时条件只能加严，不能删除或放宽基础要求。

正式角色包括 scan、market/stock/industry/theme/event investigate、analyze、review 和 meta-review。修改基础证据底线属于 L3。

## 6. 阶段协议

每个阶段完成以下闭环：

1. 创建阶段唯一 manifest，写明 RUN ID、workflow、stage、objects、带时区 cutoff、注册 task contract、handoff ID 和独立 workset 目标。
2. 调用 assemble，处理 required coverage、blocking gaps、projection 和 adapter 状态；只 hydrate `stable_references`。
3. specialist 只写本阶段拥有的正式产物，并完成 schema、ID、引用、版本链和 workspace validation。
4. hydrate 把核验状态与质量字段更新到同一 workset manifest；清单不保存正文。
5. 生成阶段关闭记录，释放来源载荷正文、工具历史、核验片段和临时推理。
6. 下一阶段只接收关闭记录、正式产物 ID 和稳定引用。研究完成后另起呈现上下文，报告不得搜索、补充事实或改变正式状态。

## 7. 工作流与写入所有权

| 工作流 | 可以写入 | 不可写入 |
|---|---|---|
| router | 运行记录、组合报告 | 证据、判断、教训、参数事实源 |
| scan | 观察候选、扫描报告 | 正式证据、判断、教训、参数 |
| investigate | 证据包、档案事实字段、调研报告 | 判断结果、教训状态、参数 |
| analyze | 判断日志、当前判断、档案分析字段、分析报告 | 证据、旧判断评分、教训状态 |
| review | 结果轴、过程轴、错误、证据簇、当前判断镜像、复盘报告 | 原判断、教训晋级、参数、新前瞻 |
| meta-review | 教训状态、策略版本、校准统计、周收敛 | 正式证据和判断快照 |

正式证据、判断和复盘只追加。对象档案与当前视图是可重建/可更新视图，不能覆盖不可变时间流水。

## 8. 当前 v3 历史审计

review 的 `historical_as_of` 同时保留三条时间边界：原判断按自身 snapshot，过程证据按 judgment cutoff，结果数据按 review cutoff。已到期、已失效、已终止、已替代或 retired 的原子可以进入历史审计或校准，但不能回到 `prospective_current`。

当前 v3 原快照中明确记录的未知项、证据缺口和字段范围保持原样。复盘不得用未来信息补写判断、证据或过程字段，也不得因后来结果改变原过程评价。

## 9. 治理

L0 负责机械统计，L1 按固定门槛维护教训生命周期，L2 只在授权字段内版本化调整参数。候选策略版本与正式版本使用相同信息快照比较，并按过程完整性、风险错误、概率校准、判断质量、机会效率的顺序晋级或回滚。L3 变更只能提案并等待用户确认。

规范门槛和输出纪律见 `研究规则.md`；策略状态与字段约束见 `shared/schemas/artifacts.json` 和 `模板/策略版本模板.md`。

## 10. 当前发布面

`scripts/validate_release.py` 只有一个 `public_release_v3` interface。正式发布包括：

- README、AGENTS、CONTEXT、研究规则、架构和 ADR；
- 六个 skills、`shared/context`、全部注册 contracts、当前 schemas 和 shared scripts；
- 模板、scaffold、初始化器；
- `security_scan.py`、`validate_deployment.py` 和 `validate_release.py`。

release validation 从 Git 索引推导全部 runtime 文件，要求它们均被跟踪，并检查 contract role 闭包、私有数据路径隔离和敏感信息。workspace validation 只验证当前 v3 schema、ID、引用、版本链、payload 定位和 workset 审计。

## 11. 验证

```bash
python3 -m unittest discover -s tests -v
python3 .agents/skills/a-share/shared/scripts/validate_workspace.py --root . --json
python3 scripts/validate_release.py
git diff --check
```

验收必须覆盖资格模式、生命周期、时间边界、retired 隔离、字段有效性、semantic exclusion、projection rebuild、hydrate hash、validator CLI、source payload 安全和 clean-clone smoke test。
