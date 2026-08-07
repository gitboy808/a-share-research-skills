#!/usr/bin/env python3
"""One-shot isolated migration from historical workspace layouts to v3.

The input is never edited.  The output is a new workspace with legacy report
directories removed, a stable-reference mapping, and explicit missing-field
markers.  This tool is deliberately not imported by runtime assembly code;
after migration the current schema is the only runtime shape.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "a-share-workspace-v3"
LEGACY_REPORT_DIRS = {
    "分析报告": "分析",
    "调研报告": "调研",
    "复盘报告": "复盘",
    "扫描报告": "扫描",
}
ARTIFACT_DIRS = {
    "证据包": (
        "evidence_package",
        {"schema_version", "artifact_type", "id", "status", "information_cutoff", "created_at", "objects"},
    ),
    "策略库": (
        "strategy_version",
        {"schema_version", "artifact_type", "id", "version", "status", "strategy_kind", "scope", "created_at", "parameter_origin"},
    ),
    "运行记录": (
        "run_record",
        {"schema_version", "artifact_type", "id", "status", "created_at", "information_cutoff", "workflows"},
    ),
}
HISTORICAL_RECORD_ROOTS = {"判断日志", "对象档案", "报告", "周收敛", "观察日志"}
HISTORICAL_RECORD_REQUIRED = {
    "schema_version",
    "artifact_type",
    "id",
    "status",
    "created_at",
    "information_cutoff",
    "record_kind",
}
LEGACY_JUDGMENT_RE = re.compile(
    r"^\s*[-*]\s+\*\*(J(?P<monthday>\d{4})-(?P<number>\d+)\s+v(?P<version>\d+))\b"
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 4)
    if end < 0:
        return {}, text
    metadata: dict[str, str] = {}
    for line in text[4:end].splitlines():
        if ":" not in line or line.lstrip().startswith("#"):
            continue
        key, value = line.split(":", 1)
        metadata[key.strip()] = value.strip().strip('"\'')
    return metadata, text[end + 5 :]


def _quote(value: Any) -> str:
    return json.dumps(str(value), ensure_ascii=False)


def _frontmatter(metadata: dict[str, Any], body: str) -> str:
    lines = ["---"]
    for key, value in metadata.items():
        if isinstance(value, list):
            lines.append(f"{key}: {_quote(json.dumps(value, ensure_ascii=False))}")
        else:
            lines.append(f"{key}: {_quote(value)}")
    lines.extend(["---", body.lstrip("\n")])
    return "\n".join(lines).rstrip() + "\n"


def _infer_id(text: str, path: Path, prefix: str) -> str:
    patterns = {
        "EVI": r"EVI-\d{8}-\d{3}",
        "RUN": r"RUN-\d{8}-\d{3}",
        "STR": r"STR-[A-Za-z0-9-]+-v\d+\.\d+\.\d+",
    }
    match = re.search(patterns.get(prefix, rf"{prefix}-[A-Za-z0-9-]+"), text)
    if match:
        return match.group(0)
    return f"{prefix}-MIGRATED-{hashlib.sha256((path.as_posix() + text).encode('utf-8')).hexdigest()[:12]}"


def _stable_ids(text: str) -> list[str]:
    patterns = re.findall(
        r"\b(?:EVI-\d{8}-\d{3}#\d+|J\d{8}-\d{3}(?: v\d+)?|C\d{8}-\d{3}(?: v\d+)?|J\d{4}-\d+(?: v\d+)?|C\d{4}-\d+(?: v\d+)?)\b",
        text,
    )
    return list(dict.fromkeys(patterns))


def _artifact_migration(path: Path, relative: Path) -> tuple[str, list[str]]:
    text = path.read_text(encoding="utf-8")
    metadata, body = _parse_frontmatter(text)
    directory = relative.parts[0] if relative.parts else ""
    artifact_type, required = ARTIFACT_DIRS.get(directory, (None, set()))
    if not artifact_type or path.name in {"索引.md", "README.md"}:
        return text, []
    missing: list[str] = []
    already_current = metadata.get("schema_version") == SCHEMA_VERSION and metadata.get("artifact_type") == artifact_type
    if artifact_type == "evidence_package":
        prefix = "EVI"
        defaults = {
            "schema_version": SCHEMA_VERSION,
            "artifact_type": artifact_type,
            "id": _infer_id(text, relative, prefix),
            "status": "partial",
            "information_cutoff": "当时未记录",
            "created_at": "当时未记录",
            "objects": "当时未记录",
        }
    elif artifact_type == "strategy_version":
        prefix = "STR"
        defaults = {
            "schema_version": SCHEMA_VERSION,
            "artifact_type": artifact_type,
            "id": _infer_id(text, relative, prefix),
            "version": "当时未记录",
            "status": "limited",
            "strategy_kind": "当时未记录",
            "scope": "当时未记录",
            "created_at": "当时未记录",
            "parameter_origin": "当时未记录",
        }
    else:
        prefix = "RUN"
        defaults = {
            "schema_version": SCHEMA_VERSION,
            "artifact_type": artifact_type,
            "id": _infer_id(text, relative, prefix),
            "status": "partial",
            "created_at": "当时未记录",
            "information_cutoff": "当时未记录",
            "workflows": "当时未记录",
        }
    normalized: dict[str, Any] = {}
    for key in required:
        if key in metadata and metadata[key] != "":
            normalized[key] = metadata[key]
        else:
            normalized[key] = defaults[key]
            missing.append(key)
    if metadata.get("schema_version") != SCHEMA_VERSION:
        missing.append("schema_version")
    unknown_fields = sorted(set(metadata) - required)
    for key in unknown_fields:
        normalized[key] = metadata[key]
    if already_current and not missing:
        return text, []
    normalized["migration_missing_fields"] = sorted(set(missing))
    normalized["migration_note"] = "历史字段按原快照保留；新增字段缺失时标记为当时未记录。"
    return _frontmatter(normalized, body), sorted(set(missing))


def _move_legacy_reports(output: Path, mappings: list[dict[str, Any]]) -> list[str]:
    removed: list[str] = []
    for legacy, category in LEGACY_REPORT_DIRS.items():
        source = output / legacy
        if not source.exists():
            continue
        destination_root = output / "报告" / category
        for path in sorted(source.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(source)
            destination = destination_root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                destination = destination.with_name(f"{legacy}-{destination.name}")
            before_hash = _sha(path)
            shutil.move(str(path), str(destination))
            mappings.append(
                {
                    "old_path": (Path(legacy) / relative).as_posix(),
                    "new_path": destination.relative_to(output).as_posix(),
                    "stable_references": _stable_ids(destination.read_text(encoding="utf-8")),
                    "before_sha256": before_hash,
                    "after_sha256": _sha(destination),
                    "semantic_body_preserved": True,
                    "missing_fields": [],
                }
            )
        shutil.rmtree(source)
        removed.append(legacy)
    return removed


def _historical_record_migration(path: Path, relative: Path) -> tuple[str, list[str]]:
    """Give non-artifact historical views current metadata without changing body text."""

    text = path.read_text(encoding="utf-8")
    metadata, body = _parse_frontmatter(text)
    root_name = relative.parts[0] if relative.parts else ""
    if root_name not in HISTORICAL_RECORD_ROOTS or path.name in {"索引.md", "README.md"}:
        return text, []
    record_id = f"HIST-{hashlib.sha256((relative.as_posix() + text).encode('utf-8')).hexdigest()[:16]}"
    defaults = {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": "historical_record",
        "id": record_id,
        "status": "historical",
        "created_at": "当时未记录",
        "information_cutoff": "当时未记录",
        "record_kind": root_name,
    }
    normalized: dict[str, Any] = {}
    missing: list[str] = []
    for key in sorted(HISTORICAL_RECORD_REQUIRED):
        value = metadata.get(key)
        if value not in (None, ""):
            normalized[key] = value
        else:
            normalized[key] = defaults[key]
            missing.append(key)
    for key in sorted(set(metadata) - HISTORICAL_RECORD_REQUIRED):
        normalized[key] = metadata[key]
    already_current = (
        metadata.get("schema_version") == SCHEMA_VERSION
        and metadata.get("artifact_type") == "historical_record"
        and not missing
    )
    if already_current:
        return text, []
    normalized["migration_missing_fields"] = sorted(set(missing))
    normalized["migration_note"] = "历史正文原样保留；新增结构字段缺失时标记为当时未记录。"
    return _frontmatter(normalized, body), sorted(set(missing))


def _normalize_historical_records(
    output: Path,
    mappings: list[dict[str, Any]],
    missing_fields: dict[str, list[str]],
) -> int:
    count = 0
    for root_name in sorted(HISTORICAL_RECORD_ROOTS):
        base = output / root_name
        if not base.exists():
            continue
        for path in sorted(base.rglob("*.md")):
            relative = path.relative_to(output)
            before_hash = _sha(path)
            before_text = path.read_text(encoding="utf-8")
            migrated, missing = _historical_record_migration(path, relative)
            if migrated == before_text and not missing:
                continue
            path.write_text(migrated, encoding="utf-8")
            if missing:
                missing_fields[relative.as_posix()] = missing
            mappings.append(
                {
                    "old_path": relative.as_posix(),
                    "new_path": relative.as_posix(),
                    "stable_references": _stable_ids(before_text),
                    "before_sha256": before_hash,
                    "after_sha256": _sha(path),
                    "semantic_body_preserved": True,
                    "missing_fields": missing,
                    "record_kind": "historical_record",
                }
            )
            count += 1
    return count


def _materialize_legacy_judgment_views(output: Path, mappings: list[dict[str, Any]]) -> int:
    """Convert old bullet judgments into current atomic headings in the copy."""

    judgment_root = output / "判断日志"
    if not judgment_root.exists():
        return 0
    generated = 0
    for source in sorted(judgment_root.glob("*.md")):
        if source.name in {"索引.md", "README.md"} or source.name.startswith("迁移-"):
            continue
        text = source.read_text(encoding="utf-8")
        entries: list[tuple[int, re.Match[str], str]] = []
        current_group = "root"
        groups: dict[int, str] = {}
        for line_number, line in enumerate(text.splitlines(), start=1):
            heading = re.match(r"^##\s+(.+)$", line)
            if heading:
                current_group = heading.group(1).strip()
            match = LEGACY_JUDGMENT_RE.match(line)
            if match:
                groups[line_number] = current_group
                entries.append((line_number, match, line.strip()))
        if not entries:
            continue
        file_date = re.search(r"(?P<year>\d{4})-(?P<month>\d{2})", source.stem)
        if not file_date:
            continue
        year = file_date.group("year")
        month = file_date.group("month")
        sequence = 0
        assigned: dict[tuple[str, str], str] = {}
        lines = [
            "---",
            f"schema_version: {_quote(SCHEMA_VERSION)}",
            'artifact_type: "historical_record"',
            f'id: {_quote(f"HIST-JUDGMENT-MIGRATION-{year}{month}")}',
            'status: "historical"',
            'created_at: "当时未记录"',
            'information_cutoff: "当时未记录"',
            'record_kind: "judgment_log_migration"',
            'migration_missing_fields: "[信息快照, 状态, 对象, 证据角色]"',
            'migration_note: "旧判断正文保持原样，仅增加当前原子单元标题与稳定映射。"',
            "---",
            f"# 历史判断原子单元迁移视图 · {year}-{month}",
            "",
        ]
        stable_references: list[str] = []
        for line_number, match, original in entries:
            old_id = match.group(1)
            version = match.group("version")
            key = (groups[line_number], old_id)
            if key not in assigned:
                sequence += 1
                assigned[key] = f"J{year}{match.group('monthday')}-{sequence:03d}"
            new_id = assigned[key]
            stable_references.append(f"{old_id}")
            lines.extend(
                [
                    f"### {new_id} v{version}",
                    "",
                    f"- **历史 ID**：{old_id}",
                    "- **信息快照**：当时未记录",
                    f"- **原始判断记录**：{original}",
                    "- **状态**：当时未记录",
                    f"- **迁移来源**：{source.relative_to(output).as_posix()}:{line_number}",
                    "",
                ]
            )
        destination = judgment_root / f"迁移-{year}-{month}.md"
        if destination.exists():
            destination = judgment_root / f"迁移-{year}-{month}-追加.md"
        generated_text = "\n".join(lines).rstrip() + "\n"
        destination.write_text(generated_text, encoding="utf-8")
        mappings.append(
            {
                "old_path": source.relative_to(output).as_posix(),
                "new_path": destination.relative_to(output).as_posix(),
                "stable_references": list(dict.fromkeys(stable_references)),
                "before_sha256": _sha(source),
                "after_sha256": _sha(destination),
                "semantic_body_preserved": True,
                "derived_view": True,
                "missing_fields": ["information_cutoff", "status", "objects", "evidence_roles"],
            }
        )
        generated += 1
    return generated


def _write_historical_workset_manifests(output: Path) -> list[str]:
    """Record that old runs had no workset without inventing one."""

    created: list[str] = []
    run_root = output / "运行记录"
    if not run_root.exists():
        return created
    for path in sorted(run_root.rglob("*.md")):
        if path.name in {"索引.md", "README.md"}:
            continue
        text = path.read_text(encoding="utf-8")
        match = re.search(r"RUN-\d{8}-\d{3}", text + path.name)
        if not match:
            continue
        run_id = match.group(0)
        destination = path.parent / f"{run_id}-工作集清单.json"
        if destination.exists():
            continue
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "artifact_type": "workset_manifest",
            "id": f"{run_id}-WORKSET",
            "status": "partial",
            "run_id": run_id,
            "created_at": "当时未记录",
            "information_cutoff": "当时未记录",
            "stage": "当时未记录",
            "task_contract": {"contract_id": "当时未记录", "version": "当时未记录"},
            "projection": {"status": "当时未记录"},
            "stable_references": [],
            "coverage": {"status": "当时未记录"},
            "gaps": [{"reason": "historical workset was not recorded", "impact": "当时未记录"}],
            "semantic_adapter": {"status": "当时未记录"},
            "budget": {"status": "当时未记录"},
            "quality": {"status": "当时未记录", "token_replay_available": False},
            "migration_note": "只记录缺失，不根据当前知识重建旧阶段工作集。",
        }
        destination.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        created.append(destination.relative_to(output).as_posix())
    return created


def migrate(input_root: Path, output_root: Path, report_path: Path | None = None) -> dict[str, Any]:
    source = input_root.resolve()
    destination = output_root.resolve()
    if not source.is_dir():
        raise ValueError(f"input workspace is not a directory: {source}")
    if source == destination or source in destination.parents or destination in source.parents:
        raise ValueError("input and output must be separate, non-nested directories")
    if destination.exists() and any(destination.iterdir()):
        raise ValueError("output directory must not already contain files")
    if report_path is not None:
        report_path = report_path.resolve()
        if report_path.parent != destination and destination not in report_path.parents:
            raise ValueError("migration report must be inside output workspace")
    destination.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        source,
        destination,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns(".git", ".context", ".source-payloads", "__pycache__"),
    )
    mappings: list[dict[str, Any]] = []
    missing_fields: dict[str, list[str]] = {}
    for path in sorted(destination.rglob("*.md")):
        relative = path.relative_to(destination)
        if relative.parts and relative.parts[0] in ARTIFACT_DIRS:
            before_hash = _sha(path)
            before_text = path.read_text(encoding="utf-8")
            migrated, missing = _artifact_migration(path, relative)
            if migrated != before_text:
                path.write_text(migrated, encoding="utf-8")
            if missing:
                missing_fields[relative.as_posix()] = missing
            mappings.append(
                {
                    "old_path": relative.as_posix(),
                    "new_path": relative.as_posix(),
                    "stable_references": _stable_ids(before_text),
                    "before_sha256": before_hash,
                    "after_sha256": _sha(path),
                    "semantic_body_preserved": True,
                    "missing_fields": missing,
                }
            )
    removed_legacy = _move_legacy_reports(destination, mappings)
    legacy_judgment_views = _materialize_legacy_judgment_views(destination, mappings)
    historical_records_normalized = _normalize_historical_records(destination, mappings, missing_fields)
    generated_worksets = _write_historical_workset_manifests(destination)
    destination_report = report_path if report_path else destination / "迁移映射.json"
    report = {
        "schema_version": SCHEMA_VERSION,
        "migration_id": f"MIG-{hashlib.sha256(str(destination).encode('utf-8')).hexdigest()[:12]}",
        "status": "completed",
        "input_root": str(source),
        "output_root": str(destination),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "legacy_directories_removed": removed_legacy,
        "legacy_runtime_compatibility": "removed",
        "future_information_backfill": False,
        "acceptance": {
            "status": "not_run",
            "required_recall": "not_run",
            "veto_conflict_denial_expiry_omissions": "not_run",
            "future_information_backfill": False,
            "model_token_replay_available": False,
            "note": "结构迁移不替代隔离副本影子回放；需由回放清单单独报告验收指标。",
        },
        "missing_fields": missing_fields,
        "historical_records_normalized": historical_records_normalized,
        "legacy_judgment_views": legacy_judgment_views,
        "generated_workset_manifests": generated_worksets,
        "stable_reference_mapping": [
            {
                "old_path": item["old_path"],
                "new_path": item["new_path"],
                "stable_references": item.get("stable_references", []),
            }
            for item in mappings
        ],
        "mappings": mappings,
        "summary": {
            "mapped_files": len(mappings),
            "removed_legacy_directories": len(removed_legacy),
            "files_with_missing_fields": len(missing_fields),
            "historical_records_normalized": historical_records_normalized,
            "legacy_judgment_views": legacy_judgment_views,
            "generated_workset_manifests": len(generated_worksets),
        },
    }
    destination_report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        "status": "completed",
        "output": str(destination),
        "report": str(destination_report),
        "legacy_directories_removed": removed_legacy,
        "mapped_files": len(mappings),
        "generated_workset_manifests": len(generated_worksets),
        "historical_records_normalized": historical_records_normalized,
        "legacy_judgment_views": legacy_judgment_views,
        "future_information_backfill": False,
        "acceptance_status": "not_run",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Migrate an A-share workspace in an isolated copy.")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    try:
        print(json.dumps(migrate(args.input, args.output, args.report), ensure_ascii=False, separators=(",", ":")))
        return 0
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        print(f"migrate_workspace: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
