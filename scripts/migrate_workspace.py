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
REPO_ROOT = Path(__file__).resolve().parents[1]
GOVERNED_DOCUMENT_PATHS = (
    Path("docs/architecture.md"),
    Path("docs/shadow-replay-acceptance.md"),
    Path("docs/adr/0012-文档事实源与可重建检索投影分离.md"),
    Path("docs/adr/0013-复合投研采用阶段上下文隔离.md"),
    Path("docs/adr/0014-检索以原子研究单元为权威粒度.md"),
    Path("docs/adr/0015-检索按字段权威映射去重.md"),
    Path("docs/adr/0016-来源载荷外置并按需核验.md"),
    Path("docs/adr/0017-上下文预算不作为硬阻断条件.md"),
    Path("docs/adr/0018-权威研究写入与叙事呈现分阶段.md"),
    Path("docs/adr/0019-以深模块装配投研工作集.md"),
    Path("docs/adr/0020-Augment仅作为可替换语义adapter.md"),
    Path("docs/adr/0021-本地结构化投影采用SQLite与FTS5.md"),
    Path("docs/adr/0022-陈旧检索投影禁用并回退事实源.md"),
    Path("docs/adr/0023-历史产物彻底结构迁移并移除兼容.md"),
    Path("docs/adr/0024-影子回放验收后无兼容切换.md"),
    Path("docs/adr/0025-任务证据清单由版本化任务契约定义.md"),
    Path("docs/adr/0026-持久任务保存可视化就绪的工作集清单.md"),
    Path("docs/adr/0027-工作集装配作为skill套件内部Python-module.md"),
    Path("docs/adr/0028-先完成实现与影子迁移再申请正式切换.md"),
)
RUNTIME_SURFACE_PATHS = (
    Path("AGENTS.md"),
    Path(".agents/skills/a-share"),
    Path("模板"),
    Path("scripts/init_workspace.py"),
    Path("scripts/migrate_workspace.py"),
    Path("scripts/security_scan.py"),
    Path("scripts/shadow_replay_workspace.py"),
    Path("scripts/validate_deployment.py"),
    Path("scripts/validate_release.py"),
    *GOVERNED_DOCUMENT_PATHS,
)
LEGACY_REPORT_DIRS = {
    "分析报告": "分析",
    "调研报告": "调研",
    "复盘报告": "复盘",
    "扫描报告": "扫描",
}
ARTIFACT_DIRS = {
    "证据包": (
        "evidence_package",
        {
            "schema_version",
            "artifact_type",
            "id",
            "status",
            "information_cutoff",
            "created_at",
            "objects",
            "stage",
            "authority",
        },
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
MIGRATION_COPY_IGNORED_NAMES = {".git", ".context", "__pycache__"}
MIGRATION_REFERENCE_IGNORED_NAMES = {*MIGRATION_COPY_IGNORED_NAMES, ".source-payloads"}
LEGACY_RUNTIME_COMPATIBILITY_ROOT = Path(".claude/skills/a-share")
HISTORICAL_RECORD_REQUIRED = {
    "schema_version",
    "artifact_type",
    "id",
    "status",
    "created_at",
    "information_cutoff",
    "record_kind",
    "authority",
}
JUDGMENT_REQUIRED_FIELDS = {
    "类型",
    "研究状态",
    "研究对象",
    "信息快照",
    "判断周期",
    "原子命题",
    "证据包 / 原子证据项",
    "价格纪律门",
    "置信区间",
    "证伪条件",
    "时限",
    "核心跟踪指标",
}
LEGACY_JUDGMENT_RE = re.compile(
    r"^\s*[-*]\s+\*\*(J(?P<monthday>\d{4})-(?P<number>\d+)\s+v(?P<version>\d+))\*\*"
)
LEGACY_JUDGMENT_STATUS_REFERENCE_RE = re.compile(
    r"^\s*[-*]\s+\*\*(?P<id>J\d{4}-\d+)(?:\s+v(?P<version>\d+))?\s+(?P<label>[^*]+)\*\*"
)
SHARED_LEGACY_JUDGMENT_VERSION_RE = re.compile(r"\b维持\s+v(?P<version>\d+)\s+不变\b")
STABLE_REFERENCE_RE = re.compile(
    r"\b(?:EVI-\d{8}-\d{3}#\d+|J\d{8}-\d{3}(?: v\d+)?|C\d{8}-\d{3}(?: v\d+)?|J\d{4}-\d+(?: v\d+)?|C\d{4}-\d+(?: v\d+)?)\b"
)
STRATEGY_ID_PATTERN = r"STR-[A-Z0-9]+(?:-[A-Z0-9]+)*"
STRATEGY_VERSION_PATTERN = r"\d+\.\d+\.\d+"
INITIAL_STRATEGY_VERSION = "0.1.0"
LEGACY_STRATEGY_REFERENCE_RE = re.compile(
    rf"\b(?P<id>{STRATEGY_ID_PATTERN})-v(?P<version>{STRATEGY_VERSION_PATTERN})\b"
)
CANONICAL_STRATEGY_REFERENCE_RE = re.compile(
    rf"\b(?P<id>{STRATEGY_ID_PATTERN})@(?P<version>{STRATEGY_VERSION_PATTERN})\b"
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _validate_input_symlinks(source: Path) -> None:
    """Reject links whose final target is absent or outside the read-only input."""

    for path in sorted(source.rglob("*")):
        if not path.is_symlink():
            continue
        relative = path.relative_to(source).as_posix()
        try:
            target = path.resolve(strict=True)
        except OSError as exc:
            raise ValueError(f"input symlink target is unavailable: {relative}") from exc
        if source not in [target, *target.parents]:
            raise ValueError(f"input symlink escapes input workspace: {relative} -> {target}")


def _tree_snapshot(root: Path, ignored_names: set[str]) -> dict[str, Any]:
    files = [
        {
            "path": path.relative_to(root).as_posix(),
            "sha256": _sha(path),
        }
        for path in sorted(root.rglob("*"))
        if path.is_file()
        and not any(part in ignored_names for part in path.relative_to(root).parts)
    ]
    digest = hashlib.sha256()
    for item in files:
        digest.update(item["path"].encode("utf-8"))
        digest.update(b"\0")
        digest.update(item["sha256"].encode("ascii"))
        digest.update(b"\n")
    return {"sha256": digest.hexdigest(), "files": files}


def _surface_files(root: Path, relative: Path) -> list[Path]:
    source = root / relative
    if source.is_file():
        return [source]
    return [
        path
        for path in sorted(source.rglob("*"))
        if path.is_file() and not any(part in {"__pycache__", ".DS_Store"} for part in path.parts)
    ]


def _install_runtime_surface(output: Path) -> dict[str, Any]:
    """Install the migrator repository's versioned runtime into the isolated output."""

    replacements: list[dict[str, Any]] = []
    surface_entries: list[tuple[str, str]] = []
    for relative in RUNTIME_SURFACE_PATHS:
        source = REPO_ROOT / relative
        if not source.exists():
            raise ValueError(f"runtime surface source is missing: {relative.as_posix()}")
        destination = output / relative
        before_hashes = {
            path.relative_to(output).as_posix(): _sha(path)
            for path in _surface_files(output, relative)
        } if destination.exists() else {}
        if destination.is_dir():
            shutil.rmtree(destination)
        elif destination.exists():
            destination.unlink()
        if source.is_dir():
            shutil.copytree(
                source,
                destination,
                ignore=shutil.ignore_patterns("__pycache__", ".DS_Store"),
            )
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        for installed in _surface_files(output, relative):
            installed_relative = installed.relative_to(output).as_posix()
            after_hash = _sha(installed)
            surface_entries.append((installed_relative, after_hash))
            before_hash = before_hashes.get(installed_relative)
            if before_hash != after_hash:
                replacements.append(
                    {
                        "path": installed_relative,
                        "before_sha256": before_hash,
                        "after_sha256": after_hash,
                        "action": "replaced" if before_hash is not None else "installed",
                    }
                )
        removed_paths = sorted(set(before_hashes) - {path for path, _ in surface_entries})
        replacements.extend(
            {
                "path": path,
                "before_sha256": before_hashes[path],
                "after_sha256": None,
                "action": "removed_legacy_runtime",
            }
            for path in removed_paths
        )
    legacy_compatibility_relative = LEGACY_RUNTIME_COMPATIBILITY_ROOT
    legacy_compatibility_root = output / legacy_compatibility_relative
    if legacy_compatibility_root.exists():
        if legacy_compatibility_root.is_dir():
            before_sha256 = _tree_snapshot(legacy_compatibility_root, set())["sha256"]
            shutil.rmtree(legacy_compatibility_root)
        else:
            before_sha256 = _sha(legacy_compatibility_root)
            legacy_compatibility_root.unlink()
        replacements.append(
            {
                "path": legacy_compatibility_relative.as_posix(),
                "before_sha256": before_sha256,
                "after_sha256": None,
                "action": "removed_legacy_compatibility_root",
            }
        )
    files = [
        {"path": path, "sha256": file_hash}
        for path, file_hash in sorted(dict(surface_entries).items())
    ]
    digest = hashlib.sha256()
    for item in files:
        digest.update(item["path"].encode("utf-8"))
        digest.update(b"\0")
        digest.update(item["sha256"].encode("ascii"))
        digest.update(b"\n")
    return {
        "source": str(REPO_ROOT),
        "version": SCHEMA_VERSION,
        "schema_version": SCHEMA_VERSION,
        "sha256": digest.hexdigest(),
        "files": files,
        "installed_roots": [path.as_posix() for path in RUNTIME_SURFACE_PATHS],
        "replacements": replacements,
    }


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
            lines.append(f"{key}: {json.dumps(value, ensure_ascii=False)}")
        else:
            lines.append(f"{key}: {_quote(value)}")
    lines.extend(["---", body.lstrip("\n")])
    return "\n".join(lines).rstrip() + "\n"


def _metadata_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    if not isinstance(value, str) or not value.strip():
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    return [str(item) for item in parsed] if isinstance(parsed, list) else []


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


def _valid_timestamp(value: Any) -> bool:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None


def _is_current_evidence(metadata: dict[str, str], relative: Path) -> bool:
    evidence_id = str(metadata.get("id") or "")
    return (
        metadata.get("schema_version") == SCHEMA_VERSION
        and metadata.get("artifact_type") == "evidence_package"
        and re.fullmatch(r"EVI-\d{8}-\d{3}", evidence_id) is not None
        and relative.stem == evidence_id
        and metadata.get("status") in {"complete", "partial", "unavailable"}
        and bool(metadata.get("objects"))
        and metadata.get("stage") == "investigate"
        and metadata.get("authority") == "evidence_fact_source"
        and _valid_timestamp(metadata.get("created_at"))
        and _valid_timestamp(metadata.get("information_cutoff"))
    )


def _stable_ids(text: str) -> list[str]:
    patterns = STABLE_REFERENCE_RE.findall(text)
    for pattern in (LEGACY_STRATEGY_REFERENCE_RE, CANONICAL_STRATEGY_REFERENCE_RE):
        patterns.extend(
            f"{match.group('id')}@{match.group('version')}"
            for match in pattern.finditer(text)
        )
    return list(dict.fromkeys(patterns))


def _shared_legacy_judgment_references(line: str) -> list[str]:
    """Resolve only explicit, line-scoped legacy ``维持 vN 不变`` references."""

    markers = list(SHARED_LEGACY_JUDGMENT_VERSION_RE.finditer(line))
    if len(markers) != 1:
        return []
    marker = markers[0]
    version = marker.group("version")
    resolved: list[str] = []
    for match in STABLE_REFERENCE_RE.finditer(line[: marker.start()]):
        old_ref = match.group(0)
        if re.fullmatch(r"J\d{4}-\d+", old_ref):
            resolved.append(f"{old_ref} v{version}")
    return list(dict.fromkeys(resolved))


def _infer_strategy_identity(
    text: str,
    relative: Path,
    metadata: dict[str, str] | None = None,
) -> tuple[str, str] | None:
    metadata = metadata or {}
    strategy_id = str(metadata.get("id") or "")
    version = str(metadata.get("version") or "")
    candidates: set[tuple[str, str]] = set()
    if re.fullmatch(STRATEGY_ID_PATTERN, strategy_id) and re.fullmatch(STRATEGY_VERSION_PATTERN, version):
        candidates.add((strategy_id, version))
    identity_sources = [strategy_id, relative.name]
    identity_sources.extend(
        line
        for line in text.splitlines()
        if re.match(r"^#{1,6}\s+", line)
    )
    for candidate in identity_sources:
        match = LEGACY_STRATEGY_REFERENCE_RE.search(candidate) or CANONICAL_STRATEGY_REFERENCE_RE.search(candidate)
        if match:
            candidates.add((match.group("id"), match.group("version")))
    if re.fullmatch(STRATEGY_VERSION_PATTERN, version) and any(
        candidate_version != version for _, candidate_version in candidates
    ):
        return None
    if re.fullmatch(STRATEGY_ID_PATTERN, strategy_id) and any(
        candidate_id != strategy_id for candidate_id, _ in candidates
    ):
        return None
    return next(iter(candidates)) if len(candidates) == 1 else None


def _collect_provable_strategy_versions(root: Path) -> set[tuple[str, str]]:
    """Return only strategy versions whose complete predecessor chain exists in the snapshot."""

    candidates: dict[tuple[str, str], list[dict[str, str]]] = {}
    strategy_root = root / "策略库"
    if not strategy_root.exists():
        return set()
    for path in sorted(strategy_root.rglob("*.md")):
        if path.name in {"索引.md", "README.md"}:
            continue
        text = path.read_text(encoding="utf-8")
        metadata, _ = _parse_frontmatter(text)
        identity = _infer_strategy_identity(text, path.relative_to(root), metadata)
        if identity is not None:
            candidates.setdefault(identity, []).append(metadata)

    provable = {
        identity
        for identity, declarations in candidates.items()
        if identity[1] == INITIAL_STRATEGY_VERSION and len(declarations) == 1
    }
    changed = True
    while changed:
        changed = False
        for (strategy_id, version), declarations in candidates.items():
            if (strategy_id, version) in provable or len(declarations) != 1:
                continue
            previous_version = str(declarations[0].get("previous_version") or "").strip()
            if not re.fullmatch(STRATEGY_VERSION_PATTERN, previous_version):
                continue
            if tuple(map(int, previous_version.split("."))) >= tuple(map(int, version.split("."))):
                continue
            if (strategy_id, previous_version) in provable:
                provable.add((strategy_id, version))
                changed = True
    return provable


def _unresolved_strategy_migration(
    text: str,
    relative: Path,
    metadata: dict[str, str],
    *,
    unresolved_fields: set[str] | None = None,
    migration_note: str | None = None,
) -> tuple[str, list[str]]:
    _, body = _parse_frontmatter(text)
    missing = sorted(
        {
            *(unresolved_fields or {"version"}),
            *(
                key for key in ("id", "version", "created_at", "information_cutoff")
                if not metadata.get(key)
            ),
        }
    )
    normalized: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": "historical_record",
        "id": f"HIST-STRATEGY-{hashlib.sha256((relative.as_posix() + text).encode('utf-8')).hexdigest()[:16]}",
        "status": "historical",
        "created_at": metadata.get("created_at") or "当时未记录",
        "information_cutoff": metadata.get("information_cutoff") or "当时未记录",
        "record_kind": "strategy_version_migration_unresolved",
        "authority": "migration_audit",
        "migration_missing_fields": missing,
        "migration_note": migration_note
        or "旧策略版本无法从文件名、正文或元数据证明；保留为非权威迁移审计，不生成有效策略版本。",
    }
    if metadata:
        normalized["legacy_metadata"] = json.dumps(metadata, ensure_ascii=False, sort_keys=True)
    return _frontmatter(normalized, body), missing


def _unresolved_evidence_migration(
    text: str,
    relative: Path,
    metadata: dict[str, str],
) -> tuple[str, list[str]]:
    """Preserve unprovable legacy evidence without granting live fact authority."""

    _, body = _parse_frontmatter(text)
    required = ARTIFACT_DIRS["证据包"][1]
    missing = sorted(
        {
            *(key for key in required if not metadata.get(key)),
            *(field for field in ("created_at", "information_cutoff") if not _valid_timestamp(metadata.get(field))),
            *( ["id"] if re.fullmatch(r"EVI-\d{8}-\d{3}", str(metadata.get("id") or "")) is None else [] ),
            *( ["filename_from_id"] if metadata.get("id") and relative.stem != metadata.get("id") else [] ),
            *( ["stage"] if metadata.get("stage") != "investigate" else [] ),
            *( ["authority"] if metadata.get("authority") != "evidence_fact_source" else [] ),
        }
    )
    normalized: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": "historical_record",
        "id": f"HIST-EVIDENCE-{hashlib.sha256((relative.as_posix() + text).encode('utf-8')).hexdigest()[:16]}",
        "status": "historical",
        "created_at": metadata.get("created_at") if _valid_timestamp(metadata.get("created_at")) else "当时未记录",
        "information_cutoff": (
            metadata.get("information_cutoff")
            if _valid_timestamp(metadata.get("information_cutoff"))
            else "当时未记录"
        ),
        "record_kind": "evidence_package_migration_unresolved",
        "authority": "migration_audit",
        "migration_missing_fields": missing,
        "migration_note": "旧证据无法证明满足当前事实源契约；保留为非权威迁移审计，不进入实时证据覆盖。",
    }
    if metadata:
        normalized["legacy_metadata"] = json.dumps(metadata, ensure_ascii=False, sort_keys=True)
    return _frontmatter(normalized, body), missing


def _current_run_record(metadata: dict[str, str], relative: Path) -> bool:
    run_id = str(metadata.get("id") or "")
    return (
        metadata.get("schema_version") == SCHEMA_VERSION
        and metadata.get("artifact_type") == "run_record"
        and re.fullmatch(r"RUN-\d{8}-\d{3}", run_id) is not None
        and relative.stem == run_id
        and metadata.get("status") in {"completed", "partial", "failed"}
        and _valid_timestamp(metadata.get("created_at"))
        and _valid_timestamp(metadata.get("information_cutoff"))
        and bool(str(metadata.get("workflows") or "").strip())
    )


def _historical_run_migration(
    text: str,
    relative: Path,
    metadata: dict[str, str],
    *,
    additional_missing: set[str] | None = None,
) -> tuple[str, list[str]]:
    """Keep only the narrow fail-closed run shape when historical time is missing."""

    _, body = _parse_frontmatter(text)
    run_id = _infer_id(text, relative, "RUN")
    missing = {
        key for key in ARTIFACT_DIRS["运行记录"][1] if not metadata.get(key)
    }
    missing.update(additional_missing or set())
    if metadata.get("schema_version") != SCHEMA_VERSION:
        missing.add("schema_version")
    if metadata.get("status") not in {"completed", "partial", "failed"}:
        missing.add("status")
    for field in ("created_at", "information_cutoff"):
        if not _valid_timestamp(metadata.get(field)):
            missing.add(field)
    if metadata.get("workflows") != "historical":
        missing.add("workflows")
    normalized: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": "run_record",
        "id": run_id,
        "status": "partial",
        "created_at": (
            metadata.get("created_at")
            if _valid_timestamp(metadata.get("created_at"))
            else "当时未记录"
        ),
        "information_cutoff": (
            metadata.get("information_cutoff")
            if _valid_timestamp(metadata.get("information_cutoff"))
            else "当时未记录"
        ),
        "workflows": "historical",
        "migration_missing_fields": sorted(missing),
        "migration_note": "历史运行未保存完整时间或阶段工作集；仅保留 fail-closed 迁移审计入口。",
    }
    stage = str(metadata.get("stage") or "").strip()
    if re.fullmatch(r"[A-Za-z0-9._-]+", stage):
        normalized["stage"] = stage
    if metadata:
        normalized["legacy_metadata"] = json.dumps(metadata, ensure_ascii=False, sort_keys=True)
    return _frontmatter(normalized, body), sorted(missing)


def _unresolved_run_migration(
    text: str,
    relative: Path,
    metadata: dict[str, str],
) -> tuple[str, list[str]]:
    """Quarantine a legacy run that cannot use the narrow historical run exception."""

    _, body = _parse_frontmatter(text)
    missing = sorted(
        {
            *(key for key in ARTIFACT_DIRS["运行记录"][1] if not metadata.get(key)),
            *( ["schema_version"] if metadata.get("schema_version") != SCHEMA_VERSION else [] ),
            "recorded_workset",
        }
    )
    normalized: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": "historical_record",
        "id": f"HIST-RUN-{hashlib.sha256((relative.as_posix() + text).encode('utf-8')).hexdigest()[:16]}",
        "status": "historical",
        "created_at": (
            metadata.get("created_at")
            if _valid_timestamp(metadata.get("created_at"))
            else "当时未记录"
        ),
        "information_cutoff": (
            metadata.get("information_cutoff")
            if _valid_timestamp(metadata.get("information_cutoff"))
            else "当时未记录"
        ),
        "record_kind": "run_record_migration_unresolved",
        "authority": "migration_audit",
        "migration_missing_fields": missing,
        "migration_note": "旧运行不满足 current run 或窄历史 run 契约；保留为非权威迁移审计，不生成工作集清单。",
    }
    if metadata:
        normalized["legacy_metadata"] = json.dumps(metadata, ensure_ascii=False, sort_keys=True)
    return _frontmatter(normalized, body), missing


def _artifact_migration(
    path: Path,
    relative: Path,
    provable_strategy_versions: set[tuple[str, str]] | None = None,
    legacy_workset_run_ids: set[str] | None = None,
) -> tuple[str, list[str]]:
    text = path.read_text(encoding="utf-8")
    metadata, body = _parse_frontmatter(text)
    directory = relative.parts[0] if relative.parts else ""
    artifact_type, required = ARTIFACT_DIRS.get(directory, (None, set()))
    if not artifact_type or path.name in {"索引.md", "README.md"}:
        return text, []
    if artifact_type == "evidence_package" and not _is_current_evidence(metadata, relative):
        return _unresolved_evidence_migration(text, relative, metadata)
    if artifact_type == "run_record":
        inferred_run_id = _infer_id(text, relative, "RUN")
        if inferred_run_id in (legacy_workset_run_ids or set()):
            return _historical_run_migration(
                text,
                relative,
                metadata,
                additional_missing={"recorded_workset"},
            )
        if _current_run_record(metadata, relative):
            return text, []
        has_provable_run_id = re.fullmatch(r"RUN-\d{8}-\d{3}", inferred_run_id) is not None
        missing_historical_time = not _valid_timestamp(
            metadata.get("created_at")
        ) or not _valid_timestamp(metadata.get("information_cutoff"))
        if has_provable_run_id and missing_historical_time:
            return _historical_run_migration(text, relative, metadata)
        return _unresolved_run_migration(text, relative, metadata)
    if artifact_type == "strategy_version":
        strategy_identity = _infer_strategy_identity(text, relative, metadata)
        if strategy_identity is None:
            return _unresolved_strategy_migration(text, relative, metadata)
        if strategy_identity not in (provable_strategy_versions or set()):
            previous_version = str(metadata.get("previous_version") or "").strip()
            unresolved_field = (
                "previous_version"
                if not previous_version
                else "previous_version_resolution"
            )
            return _unresolved_strategy_migration(
                text,
                relative,
                metadata,
                unresolved_fields={unresolved_field},
                migration_note=(
                    "旧策略版本无法证明 previous_version 能解析到同一策略的更早有效版本；"
                    "保留为非权威迁移审计，不生成有效策略版本。"
                ),
            )
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
            "id": strategy_identity[0] if strategy_identity else _infer_id(text, relative, prefix),
            "version": strategy_identity[1] if strategy_identity else "当时未记录",
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
            "workflows": "historical",
        }
    normalized: dict[str, Any] = {}
    for key in required:
        if artifact_type == "strategy_version" and key in {"id", "version"} and strategy_identity:
            normalized[key] = defaults[key]
            if key not in metadata or metadata[key] == "":
                missing.append(key)
        elif key in metadata and metadata[key] != "":
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


def _relocate_unresolved_strategy(output: Path, path: Path) -> Path:
    """Move strategy migration audits into a directory allowed for historical records."""

    audit_root = output / "报告" / "迁移审计" / "策略库"
    audit_root.mkdir(parents=True, exist_ok=True)
    destination = audit_root / f"迁移-{path.name}"
    sequence = 1
    while destination.exists():
        sequence += 1
        destination = audit_root / f"迁移-{path.stem}-{sequence:03d}{path.suffix}"
    shutil.move(str(path), str(destination))
    return destination


def _relocate_unresolved_evidence(output: Path, path: Path) -> Path:
    """Move evidence migration audits out of the live evidence authority directory."""

    audit_root = output / "报告" / "迁移审计" / "证据包"
    audit_root.mkdir(parents=True, exist_ok=True)
    destination = audit_root / f"迁移-{path.name}"
    sequence = 1
    while destination.exists():
        sequence += 1
        destination = audit_root / f"迁移-{path.stem}-{sequence:03d}{path.suffix}"
    shutil.move(str(path), str(destination))
    return destination


def _relocate_unresolved_run(output: Path, path: Path) -> Path:
    """Move non-current run audits outside the live run directory."""

    audit_root = output / "报告" / "迁移审计" / "运行记录"
    audit_root.mkdir(parents=True, exist_ok=True)
    destination = audit_root / f"迁移-{path.name}"
    sequence = 1
    while destination.exists():
        sequence += 1
        destination = audit_root / f"迁移-{path.stem}-{sequence:03d}{path.suffix}"
    shutil.move(str(path), str(destination))
    return destination


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
            sequence = 1
            while destination.exists():
                sequence += 1
                destination = destination.with_name(
                    f"{legacy}-{Path(relative.name).stem}-{sequence:03d}{Path(relative.name).suffix}"
                )
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
        "authority": "migration_audit",
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
        and bool(metadata.get("migration_missing_fields"))
        and bool(metadata.get("migration_note"))
    )
    if already_current:
        return text, []
    normalized["migration_missing_fields"] = sorted(
        set(missing) | set(_metadata_list(metadata.get("migration_missing_fields")))
    )
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


def _provable_snapshot_timestamp(value: str) -> datetime | None:
    """Parse only an explicit timezone-bearing snapshot; never infer a date."""

    iso = re.search(
        r"\b\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})\b",
        value,
    )
    if iso:
        candidate = iso.group(0)
        date_part, time_part = candidate.split("T", 1)
        if re.match(r"^\d{2}:\d{2}(?:Z|[+-])", time_part):
            candidate = f"{date_part}T{time_part[:5]}:00{time_part[5:]}"
        try:
            parsed = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo is not None and parsed.utcoffset() is not None else None
    shanghai = re.search(
        r"\b(?P<date>\d{4}-\d{2}-\d{2})[ T](?P<time>\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?)\s+Asia/Shanghai\b",
        value,
    )
    if not shanghai:
        return None
    clock = shanghai.group("time")
    if clock.count(":") == 1:
        clock += ":00"
    try:
        return datetime.fromisoformat(f"{shanghai.group('date')}T{clock}+08:00")
    except ValueError:
        return None


def _canonicalize_explicit_judgment_fields(
    fields: dict[str, str],
) -> tuple[dict[str, str], dict[str, str]]:
    """Expand only fields explicitly present in a delta; do not inherit facts."""

    normalized = dict(fields)
    additions: dict[str, str] = {}
    if not normalized.get("信息快照") and normalized.get("新信息快照"):
        additions["信息快照"] = normalized["新信息快照"]
    combined_identity = normalized.get("类型 / 研究状态 / 周期")
    if combined_identity:
        parts = re.split(r"\s+/\s+", combined_identity, maxsplit=2)
        if len(parts) == 3:
            for field, value in zip(("类型", "研究状态", "判断周期"), parts):
                if not normalized.get(field) and value.strip():
                    additions[field] = value.strip()
    combined_expiry = normalized.get("证伪条件 / 时限")
    if combined_expiry:
        falsification, separator, expiry = combined_expiry.rpartition("；")
        if not separator:
            falsification, separator, expiry = combined_expiry.rpartition(";")
        if separator and re.match(r"^\s*\d{4}-\d{2}-\d{2}\b", expiry):
            if not normalized.get("证伪条件") and falsification.strip():
                additions["证伪条件"] = falsification.strip()
            if not normalized.get("时限") and expiry.strip():
                additions["时限"] = expiry.strip()
    normalized.update(additions)
    return normalized, additions


def _canonical_judgment_sections(text: str, relative: str) -> list[dict[str, Any]]:
    lines = text.splitlines()
    headings: list[tuple[int, re.Match[str]]] = []
    heading_pattern = re.compile(r"^###\s+(J(?P<date>\d{8})-\d{3})\s+v(?P<version>\d+)\s*$")
    for index, line in enumerate(lines):
        match = heading_pattern.match(line)
        if match:
            headings.append((index, match))
    sections: list[dict[str, Any]] = []
    for index, match in headings:
        end = len(lines)
        for candidate in range(index + 1, len(lines)):
            if re.match(r"^#{1,3}\s+", lines[candidate]):
                end = candidate
                break
        section_lines = lines[index:end]
        fields = {
            field.group(1).strip(): field.group(2).strip()
            for line in section_lines[1:]
            if (
                field := re.match(
                    r"^-[ \t]+\*\*([^*]+)\*\*[：:][ \t]*(.*?)[ \t]*$",
                    line,
                )
            )
        }
        normalized_fields, additions = _canonicalize_explicit_judgment_fields(fields)
        snapshot = _provable_snapshot_timestamp(normalized_fields.get("信息快照", ""))
        sections.append(
            {
                "source_path": relative,
                "source_line": index + 1,
                "judgment_id": match.group(1),
                "version": int(match.group("version")),
                "reference": f"{match.group(1)} v{match.group('version')}",
                "month": f"{match.group('date')[:4]}-{match.group('date')[4:6]}",
                "lines": section_lines,
                "fields": normalized_fields,
                "canonical_additions": additions,
                "snapshot": snapshot,
            }
        )
    return sections


def _judgment_output_lines(candidate: dict[str, Any]) -> list[str]:
    lines = list(candidate["lines"])
    additions = dict(candidate.get("canonical_additions") or {})
    replacements = dict(candidate.get("canonical_replacements") or {})
    seen_fields: set[str] = set()
    for index, line in enumerate(lines):
        match = re.match(r"^-[ \t]+\*\*([^*]+)\*\*[：:][ \t]*(.*?)[ \t]*$", line)
        if match is None:
            continue
        field = match.group(1).strip()
        seen_fields.add(field)
        if field in replacements:
            lines[index] = f"- **{field}**：{replacements[field]}"
    order = (
        "Schema",
        "类型",
        "研究状态",
        "状态",
        "研究对象",
        "信息快照",
        "判断周期",
        "上游判断",
        "证伪条件",
        "时限",
    )
    for field in order:
        if field in additions and field not in seen_fields:
            lines.append(
                f"- **{field}**：{replacements.get(field, additions[field])}"
            )
    if candidate.get("snapshot_audit"):
        lines.append(f"- **迁移原信息快照**：{candidate['snapshot_audit']}")
    inherited = list(candidate.get("inherited_fields") or [])
    if inherited:
        predecessor = str(candidate["predecessor_reference"])
        lines.append(
            f"- **迁移继承审计**：从唯一连续前序 `{predecessor}` 仅继承"
            f"{'、'.join(inherited)}；命题、状态、证据、快照、价格门、置信、证伪/时限与指标均取自本版本显式字段。"
        )
    return lines


def _live_evidence_atoms(output: Path) -> set[str]:
    atoms: set[str] = set()
    evidence_root = output / "证据包"
    if not evidence_root.exists():
        return atoms
    for path in sorted(evidence_root.rglob("*.md")):
        text = path.read_text(encoding="utf-8")
        metadata, _ = _parse_frontmatter(text)
        if (
            metadata.get("artifact_type") == "evidence_package"
            and metadata.get("authority") == "evidence_fact_source"
        ):
            atoms.update(_evidence_declarations(text))
    return atoms


def _judgment_evidence_is_live(value: str, live_atoms: set[str]) -> bool:
    if re.match(r"^unknown\s*[—:-]\s*正式弃权\s*[；;，,：:]\s*\S+", value):
        return False
    references = re.findall(r"EVI-[A-Za-z0-9-]+#\d+", value)
    has_shorthand_or_range = bool(re.search(r"(?:^|[；;、,，\s])#\d+|#\d+\s*[—–-]", value))
    return bool(references) and not has_shorthand_or_range and all(ref in live_atoms for ref in references)


def _formalize_judgment_evidence_gap(
    lines: list[str],
    *,
    source_path: str,
    reference: str,
) -> list[str]:
    transformed: list[str] = []
    for line in lines:
        match = re.match(
            r"^-[ \t]+\*\*证据包 / 原子证据项\*\*[：:][ \t]*(.*?)[ \t]*$",
            line,
        )
        if match is None:
            transformed.append(line)
            continue
        original = match.group(1).strip()
        transformed.extend(
            [
                "- **证据包 / 原子证据项**：unknown—正式弃权；迁移时无可核验 live evidence，原始引用仅作审计且不计覆盖。",
                f"- **迁移证据审计**：{original}（原文：`{source_path}` / `{reference}`）。",
                "- **迁移正式缺口**：必须按当前来源载荷与证据契约重新取证；本迁移条目不得证明证据覆盖。",
            ]
        )
    return transformed


def _materialize_provable_live_judgments(
    output: Path,
    mappings: list[dict[str, Any]],
    reference_mappings: list[dict[str, Any]],
) -> dict[str, Any]:
    """Split only independently provable canonical atoms out of mixed audit logs."""

    judgment_root = output / "判断日志"
    result: dict[str, Any] = {
        "files": [],
        "live_versions": 0,
        "formal_evidence_gaps": 0,
        "rejections": [],
    }
    if not judgment_root.exists():
        return result
    candidates: list[dict[str, Any]] = []
    source_texts: dict[str, str] = {}
    for source in sorted(judgment_root.glob("*.md")):
        if source.name in {"索引.md", "README.md"} or source.name.startswith("迁移-"):
            continue
        text = source.read_text(encoding="utf-8")
        metadata, _ = _parse_frontmatter(text)
        if metadata.get("artifact_type") == "judgment_log":
            continue
        relative = source.relative_to(output).as_posix()
        source_texts[relative] = text
        candidates.extend(_canonical_judgment_sections(text, relative))
    live_atoms = _live_evidence_atoms(output)
    by_month: dict[str, list[dict[str, Any]]] = {}
    for candidate in candidates:
        by_month.setdefault(str(candidate["month"]), []).append(candidate)
    for month, month_candidates in sorted(by_month.items()):
        selected: list[dict[str, Any]] = []
        by_id: dict[str, list[dict[str, Any]]] = {}
        for candidate in month_candidates:
            by_id.setdefault(str(candidate["judgment_id"]), []).append(candidate)
        for judgment_id, chain in sorted(by_id.items()):
            by_version: dict[int, list[dict[str, Any]]] = {}
            for candidate in chain:
                by_version.setdefault(int(candidate["version"]), []).append(candidate)
            selected_versions: dict[int, dict[str, Any]] = {}
            for version in range(1, max(by_version) + 1):
                version_candidates = by_version.get(version, [])
                if not version_candidates:
                    continue
                if len(version_candidates) != 1:
                    for candidate in version_candidates:
                        result["rejections"].append(
                            {
                                "source_path": candidate["source_path"],
                                "source_line": candidate["source_line"],
                                "reference": candidate["reference"],
                                "reasons": ["duplicate_judgment_version_identity"],
                            }
                        )
                    continue
                candidate = version_candidates[0]
                fields = dict(candidate["fields"])
                additions = dict(candidate["canonical_additions"])
                reasons: list[str] = []
                inherited_fields: list[str] = []
                predecessor = selected_versions.get(version - 1) if version > 1 else None
                expected_predecessor = f"{judgment_id} v{version - 1}" if version > 1 else ""
                if version > 1 and predecessor is None:
                    reasons.append("unique_contiguous_predecessor_not_provable")
                if predecessor is not None:
                    predecessor_fields = predecessor["fields"]
                    previous_declaration = str(fields.get("上一版本") or "")
                    needs_inheritance = any(not fields.get(field) for field in ("Schema", "研究对象"))
                    if needs_inheritance and expected_predecessor not in previous_declaration:
                        reasons.append("delta_missing_exact_previous_version_declaration")
                    for field in ("Schema", "研究对象"):
                        current_value = str(fields.get(field) or "").strip()
                        predecessor_value = str(predecessor_fields.get(field) or "").strip()
                        if current_value and predecessor_value and current_value != predecessor_value:
                            reasons.append(f"immutable_identity_changed:{field}")
                        elif not current_value and predecessor_value and expected_predecessor in previous_declaration:
                            fields[field] = predecessor_value
                            additions[field] = predecessor_value
                            inherited_fields.append(field)
                    if previous_declaration and expected_predecessor in previous_declaration and not fields.get("上游判断"):
                        additions["上游判断"] = (
                            f"{expected_predecessor}；唯一连续前序版本，仅用于对象身份与 Schema 继承。"
                        )
                        fields["上游判断"] = additions["上游判断"]
                if not fields.get("Schema"):
                    reasons.append("missing_provable_field:Schema")
                if candidate["snapshot"] is None:
                    reasons.append("invalid_or_timezone_missing:信息快照")
                else:
                    original_snapshot = str(fields.get("信息快照") or "").strip()
                    canonical_snapshot = candidate["snapshot"].isoformat()
                    fields["信息快照"] = canonical_snapshot
                    candidate["canonical_replacements"] = {
                        "信息快照": canonical_snapshot
                    }
                    if original_snapshot != canonical_snapshot:
                        candidate["snapshot_audit"] = original_snapshot
                for field in sorted(JUDGMENT_REQUIRED_FIELDS):
                    if not fields.get(field):
                        reasons.append(f"missing_required_field:{field}")
                if reasons:
                    result["rejections"].append(
                        {
                            "source_path": candidate["source_path"],
                            "source_line": candidate["source_line"],
                            "reference": candidate["reference"],
                            "reasons": list(dict.fromkeys(reasons)),
                        }
                    )
                    continue
                if not fields.get("状态"):
                    fields["状态"] = fields["研究状态"]
                    additions["状态"] = fields["研究状态"]
                candidate["fields"] = fields
                candidate["canonical_additions"] = additions
                candidate["inherited_fields"] = inherited_fields
                if inherited_fields:
                    candidate["predecessor_reference"] = expected_predecessor
                selected.append(candidate)
                selected_versions[version] = candidate
        if not selected:
            continue
        destination = judgment_root / f"{month}-live.md"
        sequence = 1
        while destination.exists():
            sequence += 1
            destination = judgment_root / f"{month}-live-{sequence:03d}.md"
        snapshots = [candidate["snapshot"] for candidate in selected]
        created_at = min(snapshots).isoformat()
        information_cutoff = max(snapshots).isoformat()
        compact_month = month.replace("-", "")
        lines = [
            "---",
            f'schema_version: "{SCHEMA_VERSION}"',
            'artifact_type: "judgment_log"',
            f'id: "JLOG-{compact_month}"',
            'status: "active"',
            f'created_at: "{created_at}"',
            f'information_cutoff: "{information_cutoff}"',
            'record_kind: "judgment_log"',
            'write_stages: "analyze,review"',
            'authority: "judgment_fact_source"',
            "---",
            f"# 判断日志 · {month} · 可证明迁移切片",
            "",
            "> 仅迁入源文逐字段可证明的 canonical 原子条目；历史正文仍由 migration_audit 保存。",
            "",
        ]
        heading_targets: dict[str, int] = {}
        formal_gaps = 0
        for candidate in sorted(
            selected,
            key=lambda item: (str(item["judgment_id"]), int(item["version"])),
        ):
            entry_lines = _judgment_output_lines(candidate)
            evidence_value = str(candidate["fields"]["证据包 / 原子证据项"])
            if not _judgment_evidence_is_live(evidence_value, live_atoms):
                entry_lines = _formalize_judgment_evidence_gap(
                    entry_lines,
                    source_path=str(candidate["source_path"]),
                    reference=str(candidate["reference"]),
                )
                formal_gaps += 1
            heading_targets[str(candidate["reference"])] = len(lines) + 1
            lines.extend(entry_lines)
            lines.append("")
        destination.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
        destination_relative = destination.relative_to(output).as_posix()
        selected_by_source: dict[str, list[dict[str, Any]]] = {}
        for candidate in selected:
            selected_by_source.setdefault(str(candidate["source_path"]), []).append(candidate)
        for source_relative, source_candidates in sorted(selected_by_source.items()):
            source_path = output / source_relative
            mappings.append(
                {
                    "old_path": source_relative,
                    "new_path": destination_relative,
                    "stable_references": [str(item["reference"]) for item in source_candidates],
                    "before_sha256": _sha(source_path),
                    "after_sha256": _sha(destination),
                    "semantic_body_preserved": False,
                    "derived_view": True,
                    "preserved_fields": sorted(JUDGMENT_REQUIRED_FIELDS | {"上游判断"}),
                    "missing_fields": [],
                }
            )
            occurrences = _reference_occurrences_from_text(source_texts[source_relative])
            for candidate in source_candidates:
                reference = str(candidate["reference"])
                target_line = heading_targets[reference]
                for source_line in occurrences.get(reference, []):
                    reference_mappings.append(
                        {
                            "old_ref": reference,
                            "old_source_locator": {
                                "path": source_relative,
                                "start_line": source_line,
                                "end_line": source_line,
                            },
                            "new_ref": reference,
                            "new_source_locator": {
                                "path": destination_relative,
                                "start_line": target_line,
                                "end_line": target_line,
                                "anchor": reference,
                            },
                            "mapping_status": "mapped",
                            "mapping_kind": "provable_live_judgment_split",
                        }
                    )
        result["files"].append(
            {
                "path": destination_relative,
                "source_paths": sorted(selected_by_source),
                "live_versions": len(selected),
                "formal_evidence_gaps": formal_gaps,
            }
        )
        result["live_versions"] += len(selected)
        result["formal_evidence_gaps"] += formal_gaps
    return result


def _materialize_legacy_judgment_views(
    output: Path,
    mappings: list[dict[str, Any]],
    reference_mappings: list[dict[str, Any]],
) -> int:
    """Convert old bullet judgments into current atomic headings in the copy."""

    judgment_root = output / "判断日志"
    if not judgment_root.exists():
        return 0
    generated = 0
    day_sequences: dict[str, int] = {}
    for source in sorted(judgment_root.glob("*.md")):
        if source.name in {"索引.md", "README.md"} or source.name.startswith("迁移-"):
            continue
        text = source.read_text(encoding="utf-8")
        entries: list[tuple[int, re.Match[str], str]] = []
        current_group = "root"
        groups: dict[int, str] = {}
        line_groups: dict[int, str] = {}
        for line_number, line in enumerate(text.splitlines(), start=1):
            heading = re.match(r"^##\s+(.+)$", line)
            if heading:
                current_group = heading.group(1).strip()
            line_groups[line_number] = current_group
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
        view_number = 1
        destination = judgment_root / f"迁移-{year}-{month}.md"
        while destination.exists():
            view_number += 1
            destination = judgment_root / f"迁移-{year}-{month}-追加-{view_number:03d}.md"
        assigned: dict[tuple[str, str, str], dict[str, Any]] = {}
        lines = [
            "---",
            f"schema_version: {_quote(SCHEMA_VERSION)}",
            'artifact_type: "historical_record"',
            f'id: {_quote(f"HIST-JUDGMENT-MIGRATION-{year}{month}-{view_number:03d}")}',
            'status: "historical"',
            'created_at: "当时未记录"',
            'information_cutoff: "当时未记录"',
            'record_kind: "judgment_log_migration"',
            'authority: "migration_audit"',
            'migration_missing_fields: "[信息快照, 状态, 对象, 证据角色]"',
            'migration_note: "旧判断正文保持原样，仅增加当前原子单元标题与稳定映射。"',
            "---",
            f"# 历史判断原子单元迁移视图 · {year}-{month}",
            "",
        ]
        stable_references: list[str] = []
        local_reference_mappings: list[dict[str, Any]] = []
        for line_number, match, original in entries:
            old_id = match.group(1)
            version = match.group("version")
            key = (groups[line_number], match.group("monthday"), match.group("number"))
            chain = assigned.get(key)
            if chain is None or version in chain["versions"]:
                day_key = f"{year}{match.group('monthday')}"
                day_sequences[day_key] = day_sequences.get(day_key, 0) + 1
                chain = {
                    "new_id": f"J{day_key}-{day_sequences[day_key]:03d}",
                    "versions": set(),
                    "origin_line": line_number,
                }
                assigned[key] = chain
            chain["versions"].add(version)
            new_id = str(chain["new_id"])
            stable_references.append(f"{old_id}")
            new_heading_line = len(lines) + 1
            local_reference_mappings.append(
                {
                    "old_ref": old_id,
                    "old_source_locator": {
                        "path": source.relative_to(output).as_posix(),
                        "start_line": line_number,
                        "end_line": line_number,
                        "source_group": groups[line_number],
                    },
                    "new_ref": f"{new_id} v{version}",
                    "new_source_locator": {
                        "path": None,
                        "start_line": new_heading_line,
                        "end_line": new_heading_line,
                        "anchor": f"{new_id} v{version}",
                    },
                    "mapping_status": "mapped",
                    "disambiguation": {
                        "source_group": groups[line_number],
                        "chain_origin_line": chain["origin_line"],
                    },
                }
            )
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
        declaration_mappings = list(local_reference_mappings)
        for line_number, line in enumerate(text.splitlines(), start=1):
            for old_ref in _shared_legacy_judgment_references(line):
                candidates = [
                    item
                    for item in declaration_mappings
                    if item["old_ref"] == old_ref
                    and item["old_source_locator"]["source_group"] == line_groups[line_number]
                ]
                if not candidates:
                    candidates = [item for item in declaration_mappings if item["old_ref"] == old_ref]
                if len(candidates) != 1:
                    # The final reference pass will retain this exact occurrence
                    # in its migration-audit document rather than guessing a chain.
                    continue
                target = candidates[0]
                local_reference_mappings.append(
                    {
                        "old_ref": old_ref,
                        "old_source_locator": {
                            "path": source.relative_to(output).as_posix(),
                            "start_line": line_number,
                            "end_line": line_number,
                            "source_group": line_groups[line_number],
                        },
                        "new_ref": target["new_ref"],
                        "new_source_locator": dict(target["new_source_locator"]),
                        "mapping_status": "mapped",
                        "mapping_kind": "shared_legacy_judgment_version",
                    }
                )
            status_reference = LEGACY_JUDGMENT_STATUS_REFERENCE_RE.match(line)
            if status_reference is None:
                continue
            legacy_id = status_reference.group("id")
            version = status_reference.group("version")
            old_ref = f"{legacy_id} v{version}" if version else legacy_id
            candidates = [
                item
                for item in declaration_mappings
                if (
                    item["old_ref"] == old_ref
                    if version
                    else item["old_ref"].startswith(f"{legacy_id} v")
                )
                and item["old_source_locator"]["source_group"] == line_groups[line_number]
            ]
            if not candidates:
                candidates = [
                    item
                    for item in declaration_mappings
                    if (
                        item["old_ref"] == old_ref
                        if version
                        else item["old_ref"].startswith(f"{legacy_id} v")
                    )
                ]
            unique_targets = {
                (
                    item["new_ref"],
                    int(item["new_source_locator"]["start_line"]),
                ): item
                for item in candidates
            }
            if len(unique_targets) != 1:
                continue
            target = next(iter(unique_targets.values()))
            local_reference_mappings.append(
                {
                    "old_ref": old_ref,
                    "old_source_locator": {
                        "path": source.relative_to(output).as_posix(),
                        "start_line": line_number,
                        "end_line": line_number,
                        "source_group": line_groups[line_number],
                    },
                    "new_ref": target["new_ref"],
                    "new_source_locator": dict(target["new_source_locator"]),
                    "mapping_status": "mapped",
                    "mapping_kind": "legacy_judgment_status_reference",
                }
            )
        generated_text = "\n".join(lines).rstrip() + "\n"
        destination.write_text(generated_text, encoding="utf-8")
        destination_relative = destination.relative_to(output).as_posix()
        for reference_mapping in local_reference_mappings:
            reference_mapping["new_source_locator"]["path"] = destination_relative
        reference_mappings.extend(local_reference_mappings)
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


def _legacy_workset_paths(output: Path) -> list[Path]:
    """Find pre-current workset sidecars that cannot remain live manifests."""

    run_root = output / "运行记录"
    if not run_root.exists():
        return []
    legacy: list[Path] = []
    for path in sorted(run_root.rglob("*.json")):
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if manifest.get("artifact_type") != "workset_manifest":
            continue
        workflow = str(manifest.get("workflow") or "")
        stage = str(manifest.get("stage") or "")
        current_shape = (
            manifest.get("schema_version") == SCHEMA_VERSION
            and re.fullmatch(r"[A-Za-z0-9._-]+", workflow) is not None
            and re.fullmatch(r"[A-Za-z0-9._-]+", stage) is not None
            and isinstance(manifest.get("relation_checks"), dict)
            and isinstance(manifest.get("verification"), dict)
            and isinstance(manifest.get("coverage"), dict)
            and isinstance(manifest.get("quality"), dict)
            and isinstance(manifest.get("stable_references"), list)
            and isinstance(manifest.get("relations"), list)
        )
        if not current_shape:
            legacy.append(path)
    return legacy


def _historical_workset_payload(
    run_id: str,
    stage: str,
    run_metadata: dict[str, str],
    *,
    migration_note: str,
    legacy_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": "workset_manifest",
        "id": f"{run_id}-WORKSET-HISTORICAL-{stage.upper()}",
        "status": "partial",
        "run_id": run_id,
        "workflow": "historical",
        "stage": stage,
        "created_at": run_metadata.get("created_at") or "当时未记录",
        "information_cutoff": run_metadata.get("information_cutoff") or "当时未记录",
        "task_contract": {"contract_id": "unknown", "version": "unknown", "status": "unknown"},
        "strategy_version": "unknown",
        "projection": {
            "status": "not_run",
            "projection_degraded": True,
            "reason": migration_note,
        },
        "stable_references": [],
        "relations": [],
        "relation_checks": {
            "status": "not_run",
            "total": 0,
            "resolved": 0,
            "blocking_gaps": 0,
            "reason": migration_note,
        },
        "verification": {
            "status": "not_run",
            "required_unit_ids": [],
            "verified_unit_ids": [],
            "missing_references": [],
            "reason": migration_note,
        },
        "coverage": {
            "status": "unknown",
            "required_total": 0,
            "required_covered": 0,
            "required_missing": 0,
            "coverage_ratio": 0.0,
            "blocking": True,
            "blocking_gap_count": 1,
            "requirements": [],
            "semantic_candidates_do_not_count": True,
        },
        "gaps": [
            {
                "reason": "historical_workset_not_recorded",
                "impact": "证据覆盖、关系和核验状态不可判定",
                "blocking": True,
            }
        ],
        "semantic_adapter": {"status": "not_run", "reason": migration_note},
        "budget": {"status": "unknown", "reason": migration_note},
        "quality": {
            "status": "unknown",
            "assembled_units": 0,
            "source_payload_bytes_in_workset": 0,
            "hydrate_units": 0,
            "projection_degraded": True,
            "verification_failures": "unknown",
            "source_payload_externalized_bytes": "unknown",
            "token_replay_available": False,
            "context_proxy": {
                "stable_reference_characters": 0,
                "selected_source_characters": 0,
                "indexed_source_characters": 0,
                "raw_tool_payload_characters_entered": "unknown",
                "token_replay_available": False,
            },
        },
        "migration_missing_fields": [
            "task_contract",
            "stable_references",
            "relations",
            "verification",
            "coverage",
        ],
        "migration_note": migration_note,
    }
    if legacy_snapshot is not None:
        payload["legacy_snapshot"] = legacy_snapshot
    return payload


def _legacy_workset_snapshot(manifest: dict[str, Any]) -> dict[str, Any]:
    """Seal legacy operational fields as a non-hydratable audit attachment."""

    preserved = {
        "stable_references": manifest.get("stable_references", []),
        "relations": manifest.get("relations", []),
        "gaps": manifest.get("gaps", []),
        "coverage": manifest.get("coverage", {}),
    }
    canonical = json.dumps(
        preserved,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return {
        **preserved,
        "hydrate_eligible": False,
        "canonical_sha256": hashlib.sha256(canonical).hexdigest(),
    }


def _migrate_legacy_workset_manifests(
    output: Path,
    legacy_paths: list[Path],
    mappings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    migrated: list[dict[str, Any]] = []
    run_root = output / "运行记录"
    run_metadata: dict[str, dict[str, str]] = {}
    for run_path in sorted(run_root.rglob("*.md")):
        metadata, _ = _parse_frontmatter(run_path.read_text(encoding="utf-8"))
        if metadata.get("artifact_type") == "run_record" and metadata.get("id"):
            run_metadata[str(metadata["id"])] = metadata
    for path in legacy_paths:
        if not path.is_file():
            continue
        old_relative = path.relative_to(output).as_posix()
        old_hash = _sha(path)
        legacy_manifest = json.loads(path.read_text(encoding="utf-8"))
        run_id = str(legacy_manifest.get("run_id") or "")
        if re.fullmatch(r"RUN-\d{8}-\d{3}", run_id) is None or run_id not in run_metadata:
            raise ValueError(f"legacy workset cannot resolve its run record: {old_relative}")
        raw_stage = str(legacy_manifest.get("stage") or "")
        stage = raw_stage if re.fullmatch(r"[A-Za-z0-9._-]+", raw_stage) else "unknown"
        note = "旧工作集只记录缺失且不满足当前契约；迁为 historical/not_run/blocking 零覆盖审计清单。"
        manifest = _historical_workset_payload(
            run_id,
            stage,
            run_metadata[run_id],
            migration_note=note,
            legacy_snapshot=_legacy_workset_snapshot(legacy_manifest),
        )
        destination = path.parent / f"{run_id}-historical-{stage}-工作集清单.json"
        if destination != path and destination.exists():
            raise ValueError(f"historical workset target already exists: {destination.relative_to(output)}")
        destination.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        if destination != path:
            path.unlink()
        new_relative = destination.relative_to(output).as_posix()
        missing = list(manifest["migration_missing_fields"])
        mappings.append(
            {
                "old_path": old_relative,
                "new_path": new_relative,
                "stable_references": [],
                "before_sha256": old_hash,
                "after_sha256": _sha(destination),
                "semantic_body_preserved": False,
                "missing_fields": missing,
                "record_kind": "historical_workset_migration",
            }
        )
        migrated.append(
            {
                "run_id": run_id,
                "old_path": old_relative,
                "new_path": new_relative,
                "before_sha256": old_hash,
                "after_sha256": _sha(destination),
                "status": "historical_not_run_blocking",
            }
        )
    return migrated


def _is_historical_migration_run(metadata: dict[str, str]) -> bool:
    missing = set(_metadata_list(metadata.get("migration_missing_fields")))
    unknown_timestamps = {
        field
        for field in ("created_at", "information_cutoff")
        if metadata.get(field) == "当时未记录"
    }
    return (
        metadata.get("schema_version") == SCHEMA_VERSION
        and metadata.get("artifact_type") == "run_record"
        and metadata.get("status") == "partial"
        and metadata.get("workflows") == "historical"
        and bool(str(metadata.get("migration_note") or "").strip())
        and bool(missing)
        and (bool(unknown_timestamps) or "recorded_workset" in missing)
        and unknown_timestamps <= missing
    )


def _write_historical_workset_manifests(output: Path) -> tuple[list[str], list[dict[str, Any]]]:
    """Record that old runs had no workset without inventing one."""

    created: list[str] = []
    gaps: list[dict[str, Any]] = []
    run_root = output / "运行记录"
    if not run_root.exists():
        return created, gaps
    recorded_run_ids: set[str] = set()
    for manifest_path in sorted(run_root.rglob("*.json")):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if manifest.get("artifact_type") == "workset_manifest" and manifest.get("run_id"):
            recorded_run_ids.add(str(manifest["run_id"]))
    for path in sorted(run_root.rglob("*.md")):
        if path.name in {"索引.md", "README.md"}:
            continue
        text = path.read_text(encoding="utf-8")
        match = re.search(r"RUN-\d{8}-\d{3}", text + path.name)
        if not match:
            continue
        run_id = match.group(0)
        metadata, _ = _parse_frontmatter(text)
        if not _is_historical_migration_run(metadata):
            if run_id not in recorded_run_ids:
                gaps.append(
                    {
                        "run_id": run_id,
                        "run_path": path.relative_to(output).as_posix(),
                        "reason": "current_run_missing_recorded_workset",
                        "blocking": True,
                    }
                )
            continue
        workflow = "historical"
        stage = str(metadata.get("stage") or "unknown").strip()
        if not re.fullmatch(r"[A-Za-z0-9._-]+", stage):
            stage = "unknown"
        destination = path.parent / f"{run_id}-{workflow}-{stage}-工作集清单.json"
        if destination.exists():
            continue
        unknown_reason = "历史运行未保存阶段工作集；禁止依据迁移时可得信息重建。"
        manifest = _historical_workset_payload(
            run_id,
            stage,
            metadata,
            migration_note=unknown_reason,
        )
        destination.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        created.append(destination.relative_to(output).as_posix())
        recorded_run_ids.add(run_id)
    return created, gaps


def _finalize_mapping_hashes(output: Path, mappings: list[dict[str, Any]]) -> None:
    """Seal every mapping against the file after all migration rewrites."""

    for mapping in mappings:
        destination = (output / str(mapping["new_path"])).resolve()
        if output not in destination.parents or not destination.is_file():
            raise ValueError(f"mapped output file is missing or outside workspace: {mapping['new_path']}")
        mapping["after_sha256"] = _sha(destination)
        mapping["after_hash_scope"] = "final_output"


def _mapped_output_path(old_path: str, mappings: list[dict[str, Any]]) -> str:
    current = old_path
    seen: set[str] = set()
    while current not in seen:
        seen.add(current)
        replacement = next(
            (
                str(mapping["new_path"])
                for mapping in mappings
                if mapping.get("old_path") == current and mapping.get("new_path") != current
            ),
            None,
        )
        if replacement is None:
            break
        current = replacement
    return current


def _reference_occurrences_from_text(text: str) -> dict[str, list[int]]:
    occurrences: dict[str, list[int]] = {}
    for line_number, line in enumerate(text.splitlines(), start=1):
        shared_references = set(_shared_legacy_judgment_references(line))
        for match in STABLE_REFERENCE_RE.finditer(line):
            reference = match.group(0)
            resolved = next(
                (
                    candidate
                    for candidate in shared_references
                    if candidate.rsplit(" v", 1)[0] == reference
                ),
                reference,
            )
            occurrences.setdefault(resolved, []).append(line_number)
        for pattern in (LEGACY_STRATEGY_REFERENCE_RE, CANONICAL_STRATEGY_REFERENCE_RE):
            for match in pattern.finditer(line):
                canonical = f"{match.group('id')}@{match.group('version')}"
                occurrences.setdefault(canonical, []).append(line_number)
    return occurrences


def _reference_occurrences(path: Path) -> dict[str, list[int]]:
    return _reference_occurrences_from_text(path.read_text(encoding="utf-8"))


def _evidence_declarations(text: str) -> list[str]:
    """Return atomic evidence IDs declared by headings, excluding mere citations."""

    return [
        match.group("unit_id")
        for line in text.splitlines()
        if (
            match := re.match(
                r"^#{2,6}\s+(?P<unit_id>EVI-\d{8}-\d{3}#\d+)\b",
                line.strip(),
            )
        )
    ]


def _strategy_locator(text: str, strategy_id: str, version: str) -> tuple[int, int] | None:
    lines = text.splitlines()
    combined = (f"{strategy_id}-v{version}", f"{strategy_id}@{version}")
    for line_number, line in enumerate(lines, start=1):
        if any(reference in line for reference in combined):
            return line_number, line_number
    id_lines = [
        line_number
        for line_number, line in enumerate(lines, start=1)
        if re.match(rf"^id:\s*[\"']?{re.escape(strategy_id)}[\"']?\s*$", line)
    ]
    version_lines = [
        line_number
        for line_number, line in enumerate(lines, start=1)
        if re.match(rf"^version:\s*[\"']?{re.escape(version)}[\"']?\s*$", line)
    ]
    if id_lines and version_lines:
        return min(id_lines[0], version_lines[0]), max(id_lines[0], version_lines[0])
    return None


def _frontmatter_field_locator(text: str, field: str, value: str) -> tuple[int, int] | None:
    pattern = re.compile(rf"^{re.escape(field)}:\s*[\"']?{re.escape(value)}[\"']?\s*$")
    for line_number, line in enumerate(text.splitlines(), start=1):
        if pattern.fullmatch(line):
            return line_number, line_number
    return None


def _strategy_source_locators(
    text: str,
    relative: str,
    strategy_id: str,
    version: str,
) -> list[dict[str, Any]]:
    canonical_reference = f"{strategy_id}@{version}"
    occurrences = _reference_occurrences_from_text(text).get(canonical_reference, [])
    if occurrences:
        return [
            {
                "path": relative,
                "start_line": line_number,
                "end_line": line_number,
            }
            for line_number in occurrences
        ]
    locator = _strategy_locator(text, strategy_id, version)
    if locator is not None:
        return [
            {
                "path": relative,
                "start_line": locator[0],
                "end_line": locator[1],
            }
        ]
    return [{"path": relative, "locator_kind": "path_identity"}]


def _complete_strategy_reference_mapping(
    source: Path,
    output: Path,
    mappings: list[dict[str, Any]],
    reference_mappings: list[dict[str, Any]],
) -> None:
    strategy_root = source / "策略库"
    if not strategy_root.exists():
        return
    for source_path in sorted(strategy_root.rglob("*.md")):
        if source_path.name in {"索引.md", "README.md"}:
            continue
        source_text = source_path.read_text(encoding="utf-8")
        relative = source_path.relative_to(source).as_posix()
        source_metadata, _ = _parse_frontmatter(source_text)
        identity = _infer_strategy_identity(source_text, Path(relative), source_metadata)
        if identity is None:
            continue
        strategy_id, version = identity
        target_relative = _mapped_output_path(relative, mappings)
        target = output / target_relative
        if not target.is_file():
            raise ValueError(f"strategy reference target is missing: {relative} -> {target_relative}")
        target_text = target.read_text(encoding="utf-8")
        target_metadata, _ = _parse_frontmatter(target_text)
        canonical_reference = f"{strategy_id}@{version}"
        if (
            target_metadata.get("artifact_type") == "historical_record"
            and target_metadata.get("record_kind") == "strategy_version_migration_unresolved"
        ):
            historical_id = str(target_metadata.get("id") or "")
            new_locator = _frontmatter_field_locator(target_text, "id", historical_id)
            old_locators = _strategy_source_locators(source_text, relative, strategy_id, version)
            if not historical_id or new_locator is None:
                raise ValueError(f"unresolved strategy audit locator is missing: {canonical_reference}")
            for old_locator in old_locators:
                reference_mappings.append(
                    {
                        "old_ref": canonical_reference,
                        "old_source_locator": old_locator,
                        "new_ref": historical_id,
                        "new_source_locator": {
                            "path": target_relative,
                            "start_line": new_locator[0],
                            "end_line": new_locator[1],
                        },
                        "mapping_status": "mapped_to_historical_audit",
                        "mapping_kind": "strategy_version_migration_unresolved",
                    }
                )
            continue
        if (
            target_metadata.get("artifact_type") != "strategy_version"
            or target_metadata.get("id") != strategy_id
            or target_metadata.get("version") != version
        ):
            continue
        old_locators = _strategy_source_locators(source_text, relative, strategy_id, version)
        new_locator = _strategy_locator(target_text, strategy_id, version)
        if new_locator is None:
            raise ValueError(f"strategy reference locator is missing: {strategy_id}@{version}")
        for old_locator in old_locators:
            reference_mappings.append(
                {
                    "old_ref": canonical_reference,
                    "old_source_locator": old_locator,
                    "new_ref": canonical_reference,
                    "new_source_locator": {
                        "path": target_relative,
                        "start_line": new_locator[0],
                        "end_line": new_locator[1],
                    },
                    "mapping_status": "mapped",
                    "mapping_kind": "strategy_version",
                }
            )


def _complete_stable_reference_mapping(
    source: Path,
    output: Path,
    mappings: list[dict[str, Any]],
    reference_mappings: list[dict[str, Any]],
) -> None:
    """Map every source reference occurrence to a precise final locator."""

    explicit = {
        (locator["path"], int(locator["start_line"]), item["old_ref"])
        for item in reference_mappings
        if isinstance((locator := item.get("old_source_locator")), dict)
        and locator.get("start_line") is not None
    }
    output_occurrences: dict[str, dict[str, list[int]]] = {}
    audit_occurrences: dict[str, dict[str, list[int]]] = {}
    source_ordinals: dict[tuple[str, str], int] = {}
    declaration_mappings = [item for item in reference_mappings if item.get("disambiguation")]
    quarantined_evidence: dict[str, list[dict[str, Any]]] = {}
    for path_mapping in mappings:
        old_path = str(path_mapping.get("old_path") or "")
        new_path = str(path_mapping.get("new_path") or "")
        if not old_path.startswith("证据包/") or not new_path:
            continue
        audit_path = output / new_path
        if not audit_path.is_file():
            continue
        audit_text = audit_path.read_text(encoding="utf-8")
        audit_metadata, _ = _parse_frontmatter(audit_text)
        if audit_metadata.get("record_kind") != "evidence_package_migration_unresolved":
            continue
        historical_id = str(audit_metadata.get("id") or "")
        locator = _frontmatter_field_locator(audit_text, "id", historical_id)
        source_path = source / old_path
        if not historical_id or locator is None or not source_path.is_file():
            continue
        for old_ref in _evidence_declarations(source_path.read_text(encoding="utf-8")):
            quarantined_evidence.setdefault(old_ref, []).append(
                {
                    "old_path": old_path,
                    "new_ref": historical_id,
                    "new_source_locator": {
                        "path": new_path,
                        "start_line": locator[0],
                        "end_line": locator[1],
                    },
                }
            )
    for source_path in sorted(source.rglob("*.md")):
        source_relative = source_path.relative_to(source)
        if (
            any(part in MIGRATION_REFERENCE_IGNORED_NAMES for part in source_relative.parts)
            or source_relative == LEGACY_RUNTIME_COMPATIBILITY_ROOT
            or LEGACY_RUNTIME_COMPATIBILITY_ROOT in source_relative.parents
        ):
            continue
        relative = source_relative.as_posix()
        target_relative = _mapped_output_path(relative, mappings)
        target = output / target_relative
        if not target.is_file():
            raise ValueError(f"stable-reference target is missing: {relative} -> {target_relative}")
        output_occurrences.setdefault(target_relative, _reference_occurrences(target))
        source_text = source_path.read_text(encoding="utf-8")
        source_lines = source_text.splitlines()
        for old_ref, line_numbers in _reference_occurrences_from_text(source_text).items():
            for occurrence_index, line_number in enumerate(line_numbers):
                if (relative, line_number, old_ref) in explicit:
                    continue
                line = source_lines[line_number - 1]
                evidence_candidates = quarantined_evidence.get(old_ref, [])
                same_source_evidence = [
                    item for item in evidence_candidates if item["old_path"] == relative
                ]
                evidence_target = (
                    same_source_evidence[0]
                    if len(same_source_evidence) == 1
                    else evidence_candidates[0]
                    if len(evidence_candidates) == 1
                    else None
                )
                if evidence_target is not None:
                    reference_mappings.append(
                        {
                            "old_ref": old_ref,
                            "old_source_locator": {
                                "path": relative,
                                "start_line": line_number,
                                "end_line": line_number,
                            },
                            "new_ref": evidence_target["new_ref"],
                            "new_source_locator": dict(evidence_target["new_source_locator"]),
                            "mapping_status": "mapped_to_historical_audit",
                            "mapping_kind": "evidence_package_migration_unresolved",
                        }
                    )
                    explicit.add((relative, line_number, old_ref))
                    continue
                status_reference = LEGACY_JUDGMENT_STATUS_REFERENCE_RE.match(line)
                if re.fullmatch(r"(?:[JC]\d{4}-\d+|[JC]\d{8}-\d{3})(?: v\d+)?", old_ref):
                    legacy_id = old_ref.split(" v", 1)[0]
                    versioned = " v" in old_ref
                    declaration_candidates = [
                        item
                        for item in declaration_mappings
                        if (
                            item.get("old_ref") == old_ref
                            if versioned
                            else str(item.get("old_ref") or "").startswith(f"{legacy_id} v")
                        )
                    ]
                    candidate_new_refs = sorted({str(item["new_ref"]) for item in declaration_candidates})
                    status_old_ref = None
                    if status_reference is not None:
                        status_version = status_reference.group("version")
                        status_old_ref = (
                            f"{status_reference.group('id')} v{status_version}"
                            if status_version
                            else status_reference.group("id")
                        )
                    mapping_kind = "unresolved_legacy_judgment_reference"
                    if old_ref in set(_shared_legacy_judgment_references(line)):
                        mapping_kind = (
                            "ambiguous_shared_legacy_judgment_version"
                            if len(candidate_new_refs) > 1
                            else "unresolved_shared_legacy_judgment_version"
                        )
                    elif status_old_ref == old_ref:
                        mapping_kind = (
                            "ambiguous_legacy_judgment_status_reference"
                            if len(candidate_new_refs) > 1
                            else "unresolved_legacy_judgment_status_reference"
                        )
                    audit_paths = list(dict.fromkeys((relative, target_relative)))
                    for audit_relative in audit_paths:
                        audit_target = output / audit_relative
                        if not audit_target.is_file():
                            continue
                        audit_text = audit_target.read_text(encoding="utf-8")
                        audit_metadata, _ = _parse_frontmatter(audit_text)
                        if (
                            audit_metadata.get("artifact_type") != "historical_record"
                            or audit_metadata.get("authority") != "migration_audit"
                        ):
                            continue
                        audit_occurrences.setdefault(
                            audit_relative,
                            _reference_occurrences_from_text(audit_text),
                        )
                        audit_lines = audit_occurrences[audit_relative].get(old_ref, [])
                        historical_id = str(audit_metadata.get("id") or "")
                        if not historical_id or occurrence_index >= len(audit_lines):
                            continue
                        reference_mappings.append(
                            {
                                "old_ref": old_ref,
                                "old_source_locator": {
                                    "path": relative,
                                    "start_line": line_number,
                                    "end_line": line_number,
                                },
                                "new_ref": historical_id,
                                "new_source_locator": {
                                    "path": audit_relative,
                                    "start_line": audit_lines[occurrence_index],
                                    "end_line": audit_lines[occurrence_index],
                                },
                                "mapping_status": "mapped_to_historical_audit",
                                "mapping_kind": mapping_kind,
                                "candidate_new_refs": candidate_new_refs,
                            }
                        )
                        explicit.add((relative, line_number, old_ref))
                        break
                    if (relative, line_number, old_ref) in explicit:
                        continue
                ordinal_key = (relative, old_ref)
                ordinal = source_ordinals.get(ordinal_key, 0)
                source_ordinals[ordinal_key] = ordinal + 1
                target_lines = output_occurrences[target_relative].get(old_ref, [])
                if ordinal >= len(target_lines):
                    raise ValueError(
                        f"stable reference lost during migration: {old_ref} at {relative}:{line_number}"
                    )
                target_line = target_lines[ordinal]
                reference_mappings.append(
                    {
                        "old_ref": old_ref,
                        "old_source_locator": {
                            "path": relative,
                            "start_line": line_number,
                            "end_line": line_number,
                        },
                        "new_ref": old_ref,
                        "new_source_locator": {
                            "path": target_relative,
                            "start_line": target_line,
                            "end_line": target_line,
                        },
                        "mapping_status": "mapped",
                    }
                )


def _authoritative_atomization_counts(output: Path) -> dict[str, Any]:
    """Count live authoritative atoms without constructing a runtime projection."""

    counts = {
        "rules": 0,
        "evidence_packages": 0,
        "evidence_items": 0,
        "judgment_logs": 0,
        "judgment_items": 0,
        "observation_logs": 0,
        "observation_items": 0,
        "strategy_versions": 0,
        "object_dossiers": 0,
    }
    rules_path = output / "研究规则.md"
    if rules_path.is_file():
        counts["rules"] = len(
            set(re.findall(r"(?m)^-\s+\*\*(R\d{2})\b", rules_path.read_text(encoding="utf-8")))
        )
    for path in sorted(output.rglob("*.md")):
        if any(part in {".git", ".context", ".source-payloads", "__pycache__"} for part in path.parts):
            continue
        text = path.read_text(encoding="utf-8")
        metadata, _ = _parse_frontmatter(text)
        artifact_type = metadata.get("artifact_type")
        if artifact_type == "evidence_package":
            counts["evidence_packages"] += 1
            counts["evidence_items"] += len(_evidence_declarations(text))
        elif artifact_type == "judgment_log":
            counts["judgment_logs"] += 1
            counts["judgment_items"] += len(
                re.findall(r"(?m)^#{2,6}\s+J\d{8}-\d{3}\s+v\d+\b", text)
            )
        elif artifact_type == "observation_log":
            counts["observation_logs"] += 1
            counts["observation_items"] += len(
                re.findall(r"(?m)^#{2,6}\s+C\d{8}-\d{3}\s+v\d+\b", text)
            )
        elif artifact_type == "strategy_version":
            counts["strategy_versions"] += 1
        elif artifact_type == "object_dossier":
            counts["object_dossiers"] += 1
    live_unit_fields = (
        "rules",
        "evidence_items",
        "judgment_items",
        "observation_items",
        "strategy_versions",
        "object_dossiers",
    )
    return {
        **counts,
        "live_unit_proxy_total": sum(int(counts[field]) for field in live_unit_fields),
        "projection_rebuild_run": False,
        "limitation": "仅计数当前权威文档可证明的原子单元；release 仍需重建投影与冻结 shadow replay。",
    }


def migrate(input_root: Path, output_root: Path, report_path: Path | None = None) -> dict[str, Any]:
    source = input_root.resolve()
    destination = output_root.resolve()
    if not source.is_dir():
        raise ValueError(f"input workspace is not a directory: {source}")
    if source == destination or source in destination.parents or destination in source.parents:
        raise ValueError("input and output must be separate, non-nested directories")
    _validate_input_symlinks(source)
    input_snapshot = _tree_snapshot(source, MIGRATION_COPY_IGNORED_NAMES)
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
        ignore=shutil.ignore_patterns(*sorted(MIGRATION_COPY_IGNORED_NAMES)),
    )
    runtime_surface = _install_runtime_surface(destination)
    legacy_workset_paths = _legacy_workset_paths(destination)
    legacy_workset_run_ids = {
        str(json.loads(path.read_text(encoding="utf-8")).get("run_id") or "")
        for path in legacy_workset_paths
    }
    mappings: list[dict[str, Any]] = []
    missing_fields: dict[str, list[str]] = {}
    provable_strategy_versions = _collect_provable_strategy_versions(destination)
    for path in sorted(destination.rglob("*.md")):
        relative = path.relative_to(destination)
        if relative.parts and relative.parts[0] in ARTIFACT_DIRS:
            before_hash = _sha(path)
            before_text = path.read_text(encoding="utf-8")
            migrated, missing = _artifact_migration(
                path,
                relative,
                provable_strategy_versions,
                legacy_workset_run_ids,
            )
            if migrated != before_text:
                path.write_text(migrated, encoding="utf-8")
            target_path = path
            migrated_metadata, _ = _parse_frontmatter(migrated)
            if (
                relative.parts[0] == "策略库"
                and migrated_metadata.get("artifact_type") == "historical_record"
                and migrated_metadata.get("record_kind") == "strategy_version_migration_unresolved"
            ):
                target_path = _relocate_unresolved_strategy(destination, path)
            elif (
                relative.parts[0] == "证据包"
                and migrated_metadata.get("artifact_type") == "historical_record"
                and migrated_metadata.get("record_kind") == "evidence_package_migration_unresolved"
            ):
                target_path = _relocate_unresolved_evidence(destination, path)
            elif (
                relative.parts[0] == "运行记录"
                and migrated_metadata.get("artifact_type") == "historical_record"
                and migrated_metadata.get("record_kind") == "run_record_migration_unresolved"
            ):
                target_path = _relocate_unresolved_run(destination, path)
            target_relative = target_path.relative_to(destination)
            if missing:
                missing_fields[target_relative.as_posix()] = missing
            mappings.append(
                {
                    "old_path": relative.as_posix(),
                    "new_path": target_relative.as_posix(),
                    "stable_references": _stable_ids(before_text),
                    "before_sha256": before_hash,
                    "after_sha256": _sha(target_path),
                    "semantic_body_preserved": True,
                    "missing_fields": missing,
                }
            )
    legacy_workset_manifests = _migrate_legacy_workset_manifests(
        destination,
        legacy_workset_paths,
        mappings,
    )
    removed_legacy = _move_legacy_reports(destination, mappings)
    reference_mappings: list[dict[str, Any]] = []
    legacy_judgment_views = _materialize_legacy_judgment_views(destination, mappings, reference_mappings)
    historical_records_normalized = _normalize_historical_records(destination, mappings, missing_fields)
    live_judgment_split = _materialize_provable_live_judgments(
        destination,
        mappings,
        reference_mappings,
    )
    generated_worksets, run_workset_gaps = _write_historical_workset_manifests(destination)
    _finalize_mapping_hashes(destination, mappings)
    _complete_strategy_reference_mapping(source, destination, mappings, reference_mappings)
    _complete_stable_reference_mapping(source, destination, mappings, reference_mappings)
    quarantined_evidence_packages = sum(
        1
        for item in mappings
        if str(item.get("old_path") or "").startswith("证据包/")
        and str(item.get("new_path") or "").startswith("报告/迁移审计/证据包/")
    )
    audit_mapped_references = sum(
        1
        for item in reference_mappings
        if item.get("mapping_status") == "mapped_to_historical_audit"
    )
    ambiguous_reference_mappings = sum(
        1
        for item in reference_mappings
        if str(item.get("mapping_kind") or "").startswith("ambiguous_")
        or len(item.get("candidate_new_refs") or []) > 1
    )
    authoritative_atomization = _authoritative_atomization_counts(destination)
    acceptance_blockers: list[dict[str, Any]] = [
        {
            "code": "shadow_replay_not_run",
            "count": 1,
            "required_action": "冻结 old/new 语义、上下文观测与风险场景后运行 shadow replay。",
        }
    ]
    if quarantined_evidence_packages:
        acceptance_blockers.append(
            {
                "code": "historical_evidence_quarantined",
                "count": quarantined_evidence_packages,
                "audit_mapped_references": audit_mapped_references,
                "required_action": "按当前来源载荷与证据契约重新取证；迁移审计不能恢复为实时事实源。",
            }
        )
    if ambiguous_reference_mappings:
        acceptance_blockers.append(
            {
                "code": "ambiguous_historical_references",
                "count": ambiguous_reference_mappings,
                "required_action": "冻结可证明的源组/定位映射；无法消歧的引用保持 historical audit。",
            }
        )
    if live_judgment_split["rejections"]:
        acceptance_blockers.append(
            {
                "code": "canonical_judgment_versions_quarantined",
                "count": len(live_judgment_split["rejections"]),
                "required_action": "逐条修复报告中的拒绝原因；不得以缺字段、断链或身份冲突的版本参与 live 研究。",
            }
        )
    if authoritative_atomization["evidence_items"] == 0:
        acceptance_blockers.append(
            {
                "code": "no_live_authoritative_evidence_items",
                "count": 1,
                "observed_live_evidence_items": 0,
                "required_action": "重新取证并生成当前 evidence_package 后，才能形成可验收研究覆盖。",
            }
        )
    if live_judgment_split["formal_evidence_gaps"]:
        acceptance_blockers.append(
            {
                "code": "live_judgments_missing_live_evidence",
                "count": live_judgment_split["formal_evidence_gaps"],
                "required_action": "保留命题与版本链审计，但必须按当前契约重新取证；不得把迁移原引用计入覆盖。",
            }
        )
    destination_report = report_path if report_path else destination / "迁移映射.json"
    report = {
        "schema_version": SCHEMA_VERSION,
        "migration_id": f"MIG-{hashlib.sha256(str(destination).encode('utf-8')).hexdigest()[:12]}",
        "status": "structural_migration_completed",
        "migration_status": "completed",
        "overall_status": "acceptance_pending",
        "overall_passed": False,
        "release_ready": False,
        "input_root": str(source),
        "input_snapshot": input_snapshot,
        "output_root": str(destination),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "legacy_directories_removed": removed_legacy,
        "legacy_runtime_compatibility": "removed",
        "future_information_backfill": False,
        "runtime_surface": runtime_surface,
        "acceptance": {
            "status": "not_run",
            "complete": False,
            "required_recall": "not_run",
            "veto_conflict_denial_expiry_omissions": "not_run",
            "future_information_backfill": False,
            "model_token_replay_available": False,
            "blockers": acceptance_blockers,
            "note": "结构迁移不替代隔离副本影子回放；需由回放清单单独报告验收指标。",
        },
        "missing_fields": missing_fields,
        "historical_records_normalized": historical_records_normalized,
        "legacy_judgment_views": legacy_judgment_views,
        "live_judgment_splits": live_judgment_split["files"],
        "live_judgment_rejections": live_judgment_split["rejections"],
        "generated_workset_manifests": generated_worksets,
        "legacy_workset_manifests": legacy_workset_manifests,
        "run_workset_gaps": run_workset_gaps,
        "stable_reference_mapping": reference_mappings,
        "mappings": mappings,
        "summary": {
            "mapped_files": len(mappings),
            "removed_legacy_directories": len(removed_legacy),
            "files_with_missing_fields": len(missing_fields),
            "historical_records_normalized": historical_records_normalized,
            "legacy_judgment_views": legacy_judgment_views,
            "live_judgment_versions": live_judgment_split["live_versions"],
            "live_judgment_formal_evidence_gaps": live_judgment_split["formal_evidence_gaps"],
            "live_judgment_rejections": len(live_judgment_split["rejections"]),
            "generated_workset_manifests": len(generated_worksets),
            "legacy_workset_manifests": len(legacy_workset_manifests),
            "run_workset_gaps": len(run_workset_gaps),
            "quarantined_evidence_packages": quarantined_evidence_packages,
            "audit_mapped_references": audit_mapped_references,
            "ambiguous_reference_mappings": ambiguous_reference_mappings,
            "authoritative_atomization": authoritative_atomization,
        },
    }
    destination_report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        "status": "completed",
        "migration_status": "completed",
        "overall_status": "acceptance_pending",
        "overall_passed": False,
        "release_ready": False,
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
