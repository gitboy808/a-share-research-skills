# 工作集资格装配

`shared/context` 是工作集选择的深 module。公共 interface 只有：

- `assemble(run_manifest, task_evidence_manifest=None)`：实例化任务契约、编译资格策略、选择原子单元并返回覆盖、缺口、live stable references 与 audit-only references。
- `hydrate(stable_references, workspace_root=None, source_payload_store=None)`：从 Markdown 事实源重新解析同一原子，核对定位与内容哈希，并按 stable reference 内的编译策略再次验证资格和 cutoff；失败时只返回 missing reference，不使用缓存正文。

skill、模板和 adapter 不实现自己的生命周期、时效或多版本选择逻辑。Markdown 始终是事实源；SQLite/FTS 与语义 adapter 只扩展召回。

v3 只接受 `workspace_root`、`information_cutoff`、`calibration_window_start`、`task_contract` 和 `persist_workset_manifest` 等 canonical key。CLI 的输入文件参数固定为 `assemble --run-manifest` 与 `hydrate --references`，装配结果只以 `stable_references` 作为可 hydrate 引用集合。

## 资格模式

每个版本化任务契约 requirement 必须声明 `eligibility_mode`：

- `prospective_current`：扫描、调研、分析的当前视图。排除发生在 cutoff 之后的内容，以及在 cutoff 时已经过期、事件失效、终止、证伪、结案、退役或被合格新版本替代的原子。证据的冲突、否证和未知状态继续按风险优先规则处理。
- `historical_as_of`：复盘的历史重建。`cutoff_basis=unit_snapshot` 用于原判断，`judgment_snapshot` 用于过程轴的原始证据，`run_cutoff` 用于后来结果数据。证据今天已经过期或判断今天已经结案，不妨碍重建其当时状态；形成原判断之后的信息不得进入过程评价。
- `calibration_window`：元复盘窗口。`run_manifest.calibration_window_start` 和 `information_cutoff` 共同限定样本；窗口内已终止判断、复盘记录、教训或策略版本可以作为校准样本，但不会成为 `prospective_current` 的当前依据。

`max_age_days`、允许的策略状态、判断 cutoff 映射和窗口起点会编译进 stable reference。关联依赖继承来源原子的冻结 cutoff。hydrate 不信任 assemble 的缓存结论，而是对当前 Markdown 重算资格、替代关系和状态。

## 生命周期与版本

- 判断的 `研究状态` 只描述形成判断时的研究姿态。派生有效状态还读取不可回写原判断之后追加的 `结果状态`、`结果记录时间`、`时限`、事件失效和终止记录。
- `结果状态=未触发/兑现/证伪/不可判定` 在其记录时间到达后是终局；`prospective_current` 不再选择该判断，历史和校准模式仍可定位。
- 当前版本只由稳定 `logical_id` / `logical_version` 和显式 `supersedes` 判定。文件日期不能替代主张身份，也不能让互补事实互相覆盖。cutoff 前已出现的 successor 会永久阻止被替代版本复活；若 successor 自身已失效，当前视图保留缺口而不回退。
- 对象档案的当前视图以 `### FIELD <identity> v<version>` 为原子。每个字段独立保存状态、最后核实时间、有效期/复核时间、来源引用和替代关系；文件级 `status` 不能使过期字段继续有效，也不能让一个字段的过期拖累其他字段。
- 被排除单元进入 `audited_exclusions` 和 `audit_references`。audit reference 只含定位、哈希、逻辑身份与原因，并固定 `hydrate_eligible: false`；需要审计正文时必须重新以历史或校准 requirement assemble。

## 隔离边界

token 软预算只裁剪非必需语义候选，不是证据删减规则。来源 payload 的存储/GC 与 retrieval 分离：当前 module 不执行 GC，也不删除任何被正式引用的载荷。原快照、证据、判断和复盘只追加，不在工作集装配过程中补写。
