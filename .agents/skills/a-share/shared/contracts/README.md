# 版本化任务契约

此目录保存进入各类工作流前的证据底线。运行清单可以实例化契约并增加条件，但不能删除 `required_evidence` 中的基础要求。契约只定义输入条件，不保存事实、判断或检索结果；修改证据底线属于 L3 变更。

当前内置契约使用 `a-share-task-contract-v1`，并由工作集装配 module 读取。research workflow 或任何持久运行必须在运行清单的 `task_contract` 中引用本目录已注册的原始契约；只接受本目录的文件、ID、版本与内容，不接受 inline 契约，也不从工作区 `contracts/` 解析同名或同 ID 契约。非 research 且非持久的测试/临时运行才可显式使用 inline 或工作区契约。

每个已注册 requirement 必须声明 `eligibility_mode`。模式、cutoff 和生命周期的唯一语义见 [工作集资格装配](../context/README.md)；skill 不得自行复刻筛选逻辑。scan、investigate、analyze 使用 `prospective_current`；review 分别使用 `historical_as_of` 的 unit/judgment/run cutoff；meta-review 使用需要 `calibration_window_start` 的 `calibration_window`。

需要对象专属条件时，通过运行清单传入 `task_contract` 和 `strategy_version`，不要把条件写入 skill 提示词。直接传入的任务证据清单只可增加条件，不能携带第二个契约，也不能代替正式 `task_contract`。追加项使用新 `requirement_id` 时增加要求；使用已有 ID 时必须在对象绑定前与正式要求合并为更严格约束：`required` 只能加严，`allow_unknown` 只能收紧，`min_source_groups` 取较大值，`max_age_days`（含 `freshness.max_age_days` 别名）取较小值。选择器、类型或其他字段无法安全合并时，装配必须 fail closed，不能静默保留先到值。

## 阶段运行协议

路由任务和直接调用都执行同一协议。

### 阶段信封

复合任务只复用 `run_id`。每个 workflow 都创建新的阶段运行清单，明确：

- `run_id`、本阶段唯一的 `workflow` 和 `stage`；
- 规范化 `objects`；
- ISO 8601 且带时区的 `information_cutoff`；
- meta-review 还必须提供 ISO 8601 且带时区的 `calibration_window_start`；
- 与 workflow、stage、对象类型相容的版本化 `task_contract`；
- 适用时的 `strategy_version` 和正式 `handoff` 稳定 ID；
- 本阶段独立的工作集清单目标；预计产生权威写入时设置 `persist_workset_manifest: true`。

前一阶段的 manifest、task contract 或 task evidence list 均不进入下一阶段。任务证据只能在本阶段契约底线上增加条件。

### Assemble → hydrate → validate → handoff

1. 用本阶段 manifest 和追加条件调用 `context_workspace.py assemble`。持久阶段由 `persist_workset_manifest: true` 写入初始工作集审计；契约缺失、不相容、必需覆盖不足或投影降级时，按对应 workflow 的弃权或补证规则处理。只能 hydrate `stable_references`；`audit_references` 仅供定位和解释排除原因。
2. 把完整 assemble 结果交给 `hydrate`，只核验本阶段决策所需的 stable references；`hydrate` 将核验状态和计量回写同一清单但不写原文。核验失败即保留缺口；来源载荷、网页回传、终端输出和临时推理留在本阶段。
3. specialist 写入其有权维护的权威产物；完成 schema、ID、引用、版本链及 workspace 校验后，权威写入才算完成。
4. 确认该阶段清单保存了自己的 workflow、stage、cutoff、contract、stable references、关系、覆盖、缺口、核验状态与质量字段。不得绕过 `assemble` / `hydrate` 直接调用 context module 内部持久化函数；只读阶段不落盘。
5. 生成阶段关闭记录：`run_id`、workflow、stage、cutoff、contract ID/version、正式产物 ID、stable references、工作集清单路径、覆盖/缺口、投影或核验降级。
6. 结束阶段上下文。下一阶段只接收阶段关闭记录；不接收工具历史、来源载荷、核验原文或临时推理。

“不跨阶段传递来源载荷”只表示从活跃上下文释放正文和句柄，不表示删除外置 store。凡被正式证据引用的 payload 与 sidecar，至少保留到该引用失效且完成复核；当前协议不执行自动清理。

分析阶段的 `handoff.evidence_ids` 只列调研正式写入并校验通过的原子证据稳定 ID。复盘阶段的 `handoff.judgment_ids` 和 `handoff.evidence_ids` 只列需验证的正式判断与证据稳定 ID。增量补证创建新的 investigate manifest 和快照；补证结束后，分析使用新的 analyze manifest 重新 assemble。

### 呈现上下文

权威写入和工作区校验通过后，显式结束研究上下文。另起呈现上下文，只装载模板、阶段关闭记录、正式产物 ID 和 stable references。它可以解释、重组和简化，但不得搜索、读取来源载荷、新增事实、改变判断、调整置信区间或生成新的研究版本。报告失败只重建呈现，不回写已校验的权威产物。
