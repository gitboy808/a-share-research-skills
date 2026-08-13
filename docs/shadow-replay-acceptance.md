# 影子回放验收输入

`scripts/shadow_replay_workspace.py` 只接受 `a-share-shadow-replay-v2`。旧 v1 中由 scenario 自报 `old`、`baseline` 或 `candidate_observation` 的清单无效，不能形成发布验收。

## 四项绑定

suite 的 `bindings` 必须分别保存以下输入的 `path`、小写 `sha256` 和本次回放自定义的非空 `session_id`。CLI 不绑定任何仓库外部会话；四项工件、事件和 suite 的身份必须完全一致：

- `old_baseline`：冻结旧工作区的完整逐文件清单、每个旧语义单元的相对 locator、源文件哈希、片段哈希和字段哈希，以及每个场景的旧执行产物。旧执行产物必须从冻结原文重算 workflow、stage、contract、selector、完整 required IDs 与 selected IDs；`semantic_units` 必须与 selected IDs 集合严格相等。其工作区根与逐文件哈希必须等于迁移报告的 `input_root`、`input_snapshot`。
- `migration_report`：实际 `migrate_workspace.py` 生成的结构迁移报告，不接受手工替代的通过声明。
- `new_workspace`：隔离迁移输出的完整 tree hash；真实正式工作区不得作为回放写入目标。
- `measurement_trace`：独立事件级观测。raw payload、主上下文字符与 token 峰值必须分别有 baseline/candidate 事件，并绑定同一目标会话。每个事件还必须用 `source_locator` 指向 trace 文件之外的原始 JSONL telemetry export，逐文件哈希、单行片段哈希、session、migration、trace、event 与全部测量字段逐项一致。

逐文件总体哈希按 path 排序，对每项依次写入 `path UTF-8 + NUL + file sha256 ASCII + LF`。任一绑定文件、旧快照或新工作区发生字节变化后，旧 suite 都失效。

迁移报告的 `runtime_surface.files` 会在两个位置逐文件重算：当前执行 shadow CLI 的 checkout，以及迁移输出工作区。两者都必须等于迁移时的 installed hash，防止用 checkout A 迁移、checkout B 回放。

suite 的 `targets` 必须在回放前声明 `min_raw_tool_payload_reduction_ratio`、`max_main_context_ratio` 和 `max_main_context_peak_tokens`。目标值属于本次迁移验收输入，不得由 CLI 内置某个私有工作区的会话或阈值。

## 测量与风险探针

只有 `observation_status=observed`、原始 telemetry locator 可复核，且来源类型、单位、带时区观测时间都匹配的事件可以进入指标。一个 trace 必须绑定恰好一个 JSONL export，所有非空行各被一个事件引用且不得留下未引用行。手写事件、trace 自引用、越界路径、源文件或单行哈希不符、原始记录身份/字段不符均是输入完整性错误；估算、不可取得、缺失或重复事件不能通过 token/raw gate。无法取得独立原始 telemetry export 时必须报告限制并保持失败，不得把代理值写成已观测 token。

旧语义单元的命题、快照、状态和关系都必须回到冻结 locator 的原文。关系列表不能只靠 `fields_sha256` 自证：旧片段中每个证据、上游判断和策略引用都必须在关系集合中出现，且不得添加片段不存在的关系。迁移时无法继续作为 live 支撑证据的旧引用只能转换成 `historically_referenced_evidence` 审计关系，不能伪装成 `supported_by` 或计入证据覆盖。

冲突探针只接受 canonical 状态 `冲突/conflict`；否证探针只接受 `已否证/否证/denied/falsified`。两类必须使用不同 stable unit ID，并分别计数。同一单元复用或只依赖装配层的合并 `conflict_or_denial` 原因会失败。

## CLI

```bash
python3 scripts/shadow_replay_workspace.py \
  --workspace /absolute/path/to/migrated-copy \
  --scenarios /absolute/path/to/shadow-suite-v2.json \
  --old-baseline /absolute/path/to/frozen-old-baseline.json \
  --migration-report /absolute/path/to/migrated-copy/迁移映射.json \
  --measurement-trace /absolute/path/to/measurement-trace.json \
  --output /absolute/path/outside-workspace/shadow-report.json
```

退出码：`0` 表示所有发布门通过；`1` 表示输入有效但一个或多个验收门失败；`2` 表示 schema、身份、路径、哈希、runtime surface 或来源完整性无效。只有退出码 `0` 的报告可以进入另行授权的正式切换申请。
