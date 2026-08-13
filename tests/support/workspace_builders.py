from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable


SCHEMA = "a-share-workspace-v3"
DEFAULT_CUTOFF = "2026-08-09T09:00:00+08:00"


def write_text(root: Path, relative: str, text: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.strip() + "\n", encoding="utf-8")
    return path


def evidence_item(
    unit_id: str,
    *,
    fact: str = "已核验事实。",
    status: str = "已确认",
    object_name: str = "个股:测试公司(600001)",
    field: str = "business",
    cutoff: str | None = None,
    role: str | None = "primary",
    source_group: str | None = None,
    source_locator: dict[str, Any] | str | None = None,
    logical_field_id: str | None = None,
    version: int | None = None,
    supersedes: str | None = None,
    valid_until: str | None = "2026-08-31",
    invalidated_at: str | None = None,
    heading_level: int = 3,
) -> str:
    fields = []
    if cutoff:
        fields.append(("信息快照", cutoff))
    fields.extend((("事实陈述", fact), ("状态", status)))
    if role:
        fields.append(("证据角色", role))
    if source_group:
        fields.append(("来源组 ID", source_group))
    if source_locator is not None:
        locator = (
            json.dumps(source_locator, ensure_ascii=False, separators=(",", ":"))
            if isinstance(source_locator, dict)
            else source_locator
        )
        fields.append(("来源定位", locator))
    if logical_field_id:
        fields.append(("逻辑字段 ID", logical_field_id))
    if version is not None:
        fields.append(("字段版本", version))
    if supersedes:
        fields.append(("替代", supersedes))
    if invalidated_at:
        fields.append(("事件失效时间", invalidated_at))
    fields.append(("关联对象 / 档案字段", f"{object_name} / {field}"))
    if valid_until:
        fields.append(("过期条件 / 下次复核", valid_until))
    return "\n".join(
        [f"{'#' * heading_level} {unit_id}", "", *(f"- **{name}**：{value}" for name, value in fields)]
    )


def write_evidence(
    root: Path,
    package_id: str,
    items: Iterable[str],
    *,
    cutoff: str = DEFAULT_CUTOFF,
    status: str = "complete",
    objects: str | None = None,
) -> Path:
    object_line = f'objects: "{objects}"\n' if objects else ""
    return write_text(
        root,
        f"证据包/2026-08/{package_id}.md",
        f'''---
schema_version: "{SCHEMA}"
artifact_type: "evidence_package"
id: "{package_id}"
status: "{status}"
created_at: "{cutoff}"
information_cutoff: "{cutoff}"
{object_line}---

# {package_id}

{chr(10).join(items)}''',
    )


def judgment_entry(
    unit_id: str,
    *,
    proposition: str = "测试公司在窗口内相对基准走强。",
    research_status: str = "研究条件成立",
    object_name: str = "个股:测试公司(600001)",
    cutoff: str = DEFAULT_CUTOFF,
    evidence_ids: Iterable[str] = (),
    deadline: str | None = "2026-08-10T15:00:00+08:00",
    outcome: str | None = None,
    outcome_at: str | None = None,
) -> str:
    fields: list[tuple[str, Any]] = [
        ("研究状态", research_status),
        ("研究对象", object_name),
        ("信息快照", cutoff),
        ("原子命题", proposition),
    ]
    evidence = list(evidence_ids)
    if evidence:
        fields.append(("证据包 / 原子证据项", ",".join(evidence)))
    if deadline:
        fields.append(("时限", deadline))
    if outcome:
        fields.append(("结果状态", outcome))
    if outcome_at:
        fields.append(("结果记录时间", outcome_at))
    return "\n".join(
        [f"### {unit_id}", "", *(f"- **{name}**：{value}" for name, value in fields)]
    )


def write_judgments(
    root: Path,
    entries: Iterable[str],
    *,
    cutoff: str = DEFAULT_CUTOFF,
    status: str = "active",
) -> Path:
    return write_text(
        root,
        "判断日志/2026-08.md",
        f'''---
schema_version: "{SCHEMA}"
artifact_type: "judgment_log"
id: "JLOG-202608"
status: "{status}"
created_at: "{cutoff}"
information_cutoff: "{cutoff}"
record_kind: "judgment_log"
write_stages: "analyze,review"
authority: "judgment_fact_source"
---

# 判断日志

{chr(10).join(entries)}''',
    )


def dossier_field(
    name: str,
    *,
    value: str = "字段事实已经核验。",
    version: int = 1,
    status: str = "active",
    verified_at: str = DEFAULT_CUTOFF,
    valid_until: str = "2026-08-31T23:59:59+08:00",
    source_refs: str = "unknown",
) -> str:
    return f'''### FIELD {name} v{version}

- **字段身份**：{name}
- **字段版本**：{version}
- **字段状态**：{status}
- **字段值**：{value}
- **最后核实时间**：{verified_at}
- **有效至 / 下次复核**：{valid_until}
- **来源引用**：{source_refs}'''


def write_dossier(
    root: Path,
    fields: Iterable[str],
    *,
    dossier_id: str = "DOS-STOCK-600001",
    object_name: str = "个股:测试公司(600001)",
    cutoff: str = DEFAULT_CUTOFF,
) -> Path:
    return write_text(
        root,
        "对象档案/个股/600001-测试公司.md",
        f'''---
schema_version: "{SCHEMA}"
artifact_type: "object_dossier"
id: "{dossier_id}"
status: "partial"
created_at: "{cutoff}"
information_cutoff: "{cutoff}"
record_kind: "object_dossier"
objects: "{object_name}"
field_authority: "facts:investigate;analysis:analyze"
authority: "dossier_current_view"
source_refs: "unknown"
---

# 对象档案

{chr(10).join(fields)}''',
    )


def write_strategy(
    root: Path,
    strategy_id: str,
    *,
    version: str = "1.0.0",
    status: str = "official",
    cutoff: str = "2026-08-01T09:00:00+08:00",
) -> Path:
    return write_text(
        root,
        f"策略库/{strategy_id}-v{version}.md",
        f'''---
schema_version: "{SCHEMA}"
artifact_type: "strategy_version"
id: "{strategy_id}"
version: "{version}"
status: "{status}"
strategy_kind: "base_profile"
scope: "stock"
created_at: "{cutoff}"
information_cutoff: "{cutoff}"
parameter_origin: "validated"
---

# {strategy_id}''',
    )


def run_manifest(root: Path, **overrides: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "workspace_root": str(root),
        "run_id": "RUN-20260809-TEST",
        "information_cutoff": "2026-08-11T10:00:00+08:00",
    }
    value.update(overrides)
    return value


def contract(requirements: Iterable[dict[str, Any]], **overrides: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "contract_id": "test.semantic-builder",
        "version": "1.0.0",
        "required_evidence": list(requirements),
    }
    value.update(overrides)
    return value
