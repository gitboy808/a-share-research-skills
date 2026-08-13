#!/usr/bin/env python3
"""Replay frozen research scenarios against an isolated migrated workspace."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "a-share-shadow-replay-v2"
BASELINE_SCHEMA_VERSION = "a-share-shadow-baseline-v1"
MEASUREMENT_TRACE_SCHEMA_VERSION = "a-share-shadow-measurement-trace-v1"
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")
TREE_IGNORED_PARTS = frozenset({".git", ".context", "__pycache__"})
TRACE_METRICS = {
    ("baseline", "raw_tool_payload_characters"): ("characters", "tool_payload_counter"),
    ("candidate", "raw_tool_payload_characters"): ("characters", "tool_payload_counter"),
    ("baseline", "main_context_characters"): ("characters", "context_character_counter"),
    ("candidate", "main_context_characters"): ("characters", "context_character_counter"),
    ("baseline", "main_context_peak_tokens"): ("tokens", "model_context_telemetry"),
    ("candidate", "main_context_peak_tokens"): ("tokens", "model_context_telemetry"),
}
EXPECTED_RUNTIME_ROOTS = (
    "AGENTS.md",
    ".agents/skills/a-share",
    "模板",
    "scripts/init_workspace.py",
    "scripts/migrate_workspace.py",
    "scripts/security_scan.py",
    "scripts/shadow_replay_workspace.py",
    "scripts/validate_deployment.py",
    "scripts/validate_release.py",
    "docs/architecture.md",
    "docs/shadow-replay-acceptance.md",
    "docs/adr/0012-文档事实源与可重建检索投影分离.md",
    "docs/adr/0013-复合投研采用阶段上下文隔离.md",
    "docs/adr/0014-检索以原子研究单元为权威粒度.md",
    "docs/adr/0015-检索按字段权威映射去重.md",
    "docs/adr/0016-来源载荷外置并按需核验.md",
    "docs/adr/0017-上下文预算不作为硬阻断条件.md",
    "docs/adr/0018-权威研究写入与叙事呈现分阶段.md",
    "docs/adr/0019-以深模块装配投研工作集.md",
    "docs/adr/0020-Augment仅作为可替换语义adapter.md",
    "docs/adr/0021-本地结构化投影采用SQLite与FTS5.md",
    "docs/adr/0022-陈旧检索投影禁用并回退事实源.md",
    "docs/adr/0023-历史产物彻底结构迁移并移除兼容.md",
    "docs/adr/0024-影子回放验收后无兼容切换.md",
    "docs/adr/0025-任务证据清单由版本化任务契约定义.md",
    "docs/adr/0026-持久任务保存可视化就绪的工作集清单.md",
    "docs/adr/0027-工作集装配作为skill套件内部Python-module.md",
    "docs/adr/0028-先完成实现与影子迁移再申请正式切换.md",
)
REQUIRED_WORKFLOWS = frozenset({"scan", "investigate", "analyze", "event", "review"})
CASE_TYPE_WORKFLOWS = {
    "scan": "scan",
    "investigate": "investigate",
    "analyze": "analyze",
    "event": "investigate",
    "review": "review",
}
REPO_ROOT = Path(__file__).resolve().parents[1]
SHARED_ROOT = REPO_ROOT / ".agents/skills/a-share/shared"
if str(SHARED_ROOT) not in sys.path:
    sys.path.insert(0, str(SHARED_ROOT))

from context import assemble, hydrate  # noqa: E402
from context.contracts import contract_directory, load_contract  # noqa: E402
from context.markdown import extract_units, iter_source_files  # noqa: E402


def _read_object(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"JSON object expected: {path}")
    return value


def _sha256_file(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"bound acceptance artifact must be a regular file: {path}")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _workspace_sha256(root: Path) -> str:
    if root.is_symlink() or not root.is_dir():
        raise ValueError(f"bound shadow workspace must be a regular directory: {root}")
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if any(part in TREE_IGNORED_PARTS for part in relative.parts):
            continue
        if path.is_symlink():
            raise ValueError(f"bound shadow workspace contains a symlink: {relative.as_posix()}")
        if not path.is_file():
            continue
        digest.update(relative.as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).hexdigest().encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def _binding_path(value: Any, suite_path: Path) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("acceptance binding requires a non-empty path")
    path = Path(value)
    return (suite_path.parent / path).resolve() if not path.is_absolute() else path.resolve()


def _validated_bound_inputs(
    suite: dict[str, Any],
    suite_path: Path,
    workspace: Path,
    old_baseline_path: Path,
    migration_report_path: Path,
    measurement_trace_path: Path,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    forbidden_observations = sorted(
        key for key in ("baseline", "candidate", "candidate_observation") if key in suite
    )
    if forbidden_observations:
        raise ValueError(
            "shadow suite cannot self-report observations: " + ", ".join(forbidden_observations)
    )
    session_id = suite.get("session_id")
    migration_id = suite.get("migration_id")
    if not isinstance(session_id, str) or SESSION_ID_PATTERN.fullmatch(session_id) is None:
        raise ValueError("shadow suite requires a safe non-empty session_id")
    if not isinstance(migration_id, str) or not migration_id.strip():
        raise ValueError("shadow suite requires migration_id")
    bindings = suite.get("bindings")
    if not isinstance(bindings, dict):
        raise ValueError("shadow suite requires artifact bindings")
    supplied_paths = {
        "old_baseline": old_baseline_path.resolve(),
        "migration_report": migration_report_path.resolve(),
        "new_workspace": workspace.resolve(),
        "measurement_trace": measurement_trace_path.resolve(),
    }
    records: dict[str, dict[str, Any]] = {}
    for name, supplied in supplied_paths.items():
        binding = bindings.get(name)
        if not isinstance(binding, dict):
            raise ValueError(f"shadow suite requires binding {name!r}")
        bound_path = _binding_path(binding.get("path"), suite_path)
        if bound_path != supplied:
            raise ValueError(f"{name} CLI path does not match the bound path")
        expected_hash = binding.get("sha256")
        if not isinstance(expected_hash, str) or SHA256_PATTERN.fullmatch(expected_hash) is None:
            raise ValueError(f"{name} binding requires a lowercase sha256")
        if binding.get("session_id") != session_id:
            raise ValueError(f"{name} binding has a different session identity")
        actual_hash = _workspace_sha256(supplied) if name == "new_workspace" else _sha256_file(supplied)
        if actual_hash != expected_hash:
            raise ValueError(f"{name} sha256 mismatch")
        records[name] = {
            "path": str(supplied),
            "sha256": actual_hash,
            "session_id": session_id,
        }

    old_baseline = _read_object(supplied_paths["old_baseline"])
    migration_report = _read_object(supplied_paths["migration_report"])
    measurement_trace = _read_object(supplied_paths["measurement_trace"])
    if old_baseline.get("schema_version") != BASELINE_SCHEMA_VERSION:
        raise ValueError("unsupported frozen old baseline schema_version")
    if measurement_trace.get("schema_version") != MEASUREMENT_TRACE_SCHEMA_VERSION:
        raise ValueError("unsupported measurement trace schema_version")
    for name, artifact in (("old_baseline", old_baseline), ("measurement_trace", measurement_trace)):
        if artifact.get("session_id") != session_id:
            raise ValueError(f"{name} artifact has a different session identity")
        if artifact.get("migration_id") != migration_id:
            raise ValueError(f"{name} artifact has a different migration identity")
    if migration_report.get("migration_id") != migration_id:
        raise ValueError("migration report has a different migration identity")
    if Path(str(migration_report.get("output_root") or "")).resolve() != workspace.resolve():
        raise ValueError("migration report output_root does not match the bound workspace")
    return old_baseline, migration_report, measurement_trace, {
        "session_id": session_id,
        "migration_id": migration_id,
        **records,
    }


def _runtime_file(root: Path, relative: str) -> Path:
    candidate = Path(relative)
    if candidate.is_absolute() or not candidate.parts or any(part in {"", ".", ".."} for part in candidate.parts):
        raise ValueError(f"runtime surface contains an unsafe path: {relative!r}")
    path = root.joinpath(candidate)
    resolved = path.resolve()
    if root.resolve() not in resolved.parents:
        raise ValueError(f"runtime surface path escapes its root: {relative!r}")
    current = root.resolve()
    for part in candidate.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"runtime surface path follows a symlink: {relative!r}")
    if not resolved.is_file():
        raise ValueError(f"runtime surface file is missing: {relative!r}")
    return resolved


def _surface_digest(entries: list[tuple[str, str]]) -> str:
    digest = hashlib.sha256()
    for path, file_hash in sorted(entries):
        digest.update(path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(file_hash.encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def _managed_runtime_paths(root: Path) -> set[str]:
    managed: set[str] = set()
    resolved_root = root.resolve()
    for relative in EXPECTED_RUNTIME_ROOTS:
        source = resolved_root / relative
        if source.is_symlink() or not source.exists():
            raise ValueError(f"managed runtime root is missing or aliased: {relative}")
        candidates = [source] if source.is_file() else sorted(source.rglob("*"))
        for path in candidates:
            relative_path = path.relative_to(resolved_root)
            if any(part in {"__pycache__", ".DS_Store"} for part in relative_path.parts):
                continue
            if path.is_symlink():
                raise ValueError(f"managed runtime surface contains a symlink: {relative_path.as_posix()}")
            if path.is_file():
                managed.add(relative_path.as_posix())
    return managed


def _validate_runtime_surface(migration_report: dict[str, Any], workspace: Path) -> dict[str, Any]:
    if migration_report.get("migration_status") != "completed":
        raise ValueError("migration report is not structurally completed")
    surface = migration_report.get("runtime_surface")
    if not isinstance(surface, dict):
        raise ValueError("migration report requires runtime_surface")
    installed_hash = surface.get("sha256")
    if not isinstance(installed_hash, str) or SHA256_PATTERN.fullmatch(installed_hash) is None:
        raise ValueError("migration runtime_surface requires a lowercase sha256")
    files = surface.get("files")
    if not isinstance(files, list) or not files:
        raise ValueError("migration runtime_surface requires a non-empty files manifest")
    declared: list[tuple[str, str]] = []
    seen: set[str] = set()
    for item in files:
        if not isinstance(item, dict):
            raise ValueError("runtime surface file entries must be JSON objects")
        relative = item.get("path")
        file_hash = item.get("sha256")
        if not isinstance(relative, str) or not relative or relative in seen:
            raise ValueError("runtime surface file paths must be non-empty and unique")
        if not isinstance(file_hash, str) or SHA256_PATTERN.fullmatch(file_hash) is None:
            raise ValueError(f"runtime surface file requires sha256: {relative!r}")
        seen.add(relative)
        declared.append((relative, file_hash))
    declared_hash = _surface_digest(declared)
    if declared_hash != installed_hash:
        raise ValueError("migration runtime_surface file manifest does not match its installed sha256")
    installed_roots = surface.get("installed_roots")
    if (
        not isinstance(installed_roots, list)
        or any(not isinstance(item, str) for item in installed_roots)
        or len(installed_roots) != len(set(installed_roots))
        or set(installed_roots) != set(EXPECTED_RUNTIME_ROOTS)
    ):
        raise ValueError("migration runtime_surface installed_roots do not match the managed runtime surface")
    declared_paths = {path for path, _ in declared}
    runner_paths = _managed_runtime_paths(REPO_ROOT)
    workspace_paths = _managed_runtime_paths(workspace.resolve())
    if declared_paths != runner_paths or declared_paths != workspace_paths:
        raise ValueError("runtime surface files manifest is incomplete")

    observed: dict[str, list[tuple[str, str]]] = {"runner": [], "workspace": []}
    for label, root in (("runner", REPO_ROOT), ("workspace", workspace.resolve())):
        for relative, expected_hash in declared:
            actual_hash = _sha256_file(_runtime_file(root, relative))
            if actual_hash != expected_hash:
                raise ValueError(f"{label} runtime surface differs from migration report: {relative}")
            observed[label].append((relative, actual_hash))
    runner_hash = _surface_digest(observed["runner"])
    workspace_hash = _surface_digest(observed["workspace"])
    if runner_hash != installed_hash or workspace_hash != installed_hash:
        raise ValueError("runtime surface aggregate hash mismatch")
    return {
        "passed": True,
        "file_count": len(declared),
        "installed_sha256": installed_hash,
        "runner_sha256": runner_hash,
        "workspace_sha256": workspace_hash,
    }


def _canonical_json_sha256(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _semantic_labeled_value(text: str, labels: tuple[str, ...]) -> str | None:
    for label in labels:
        marker = f"**{label}**"
        for line in text.splitlines():
            if marker not in line:
                continue
            value = line.split(marker, 1)[1].lstrip(" ：:").strip()
            if value:
                return value
    return None


def _semantic_evidence_references(text: str) -> list[str]:
    references = re.findall(r"EVI-[A-Za-z0-9-]+#\d+", text)
    base_matches = list(re.finditer(r"EVI-[A-Za-z0-9-]+(?=#|[；;、,，\s])", text))
    for index, match in enumerate(base_matches):
        base = match.group(0)
        suffix_end = base_matches[index + 1].start() if index + 1 < len(base_matches) else len(text)
        suffix = text[match.end() : suffix_end]
        for number_match in re.finditer(r"#(\d+)(?:\s*[—–-]\s*#?(\d+))?", suffix):
            start = int(number_match.group(1))
            end = int(number_match.group(2) or start)
            if end < start or end - start > 1_000:
                continue
            width = max(len(number_match.group(1)), len(number_match.group(2) or ""))
            references.extend(
                f"{base}#{number:0{width}d}" if width else f"{base}#{number}"
                for number in range(start, end + 1)
            )
    return list(dict.fromkeys(references))


def _canonical_semantic_cutoff(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    iso = re.search(
        r"\b\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})\b",
        value,
    )
    if iso:
        candidate = iso.group(0)
        date_part, time_part = candidate.split("T", 1)
        if re.match(r"^\d{2}:\d{2}(?:Z|[+-])", time_part):
            candidate = f"{date_part}T{time_part[:5]}:00{time_part[5:]}"
        parsed = _parse_aware_datetime(candidate)
        return parsed.isoformat() if parsed else None
    shanghai = re.search(
        r"\b(?P<date>\d{4}-\d{2}-\d{2})[ T](?P<time>\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?)\s+Asia/Shanghai\b",
        value,
    )
    if shanghai is None:
        return None
    clock = shanghai.group("time")
    if clock.count(":") == 1:
        clock += ":00"
    parsed = _parse_aware_datetime(f"{shanghai.group('date')}T{clock}+08:00")
    return parsed.isoformat() if parsed else None


def _semantic_source_status(text: str) -> str | None:
    direct = _semantic_labeled_value(text, ("研究状态", "状态"))
    if direct:
        return direct
    combined = _semantic_labeled_value(text, ("类型 / 研究状态 / 周期",))
    if not combined:
        return None
    parts = re.split(r"\s+/\s+", combined, maxsplit=2)
    return parts[1].strip() if len(parts) == 3 and parts[1].strip() else None


def _semantic_source_relations(
    text: str,
    unit_id: str,
    declared: Any,
) -> list[dict[str, Any]]:
    canonical_declared = _canonical_relations(declared)
    if canonical_declared is None:
        raise ValueError(f"frozen old semantic unit {unit_id!r} relations must be objects")
    expected_targets: list[tuple[str, frozenset[str]]] = []
    for line in text.splitlines():
        if "**证据包 / 原子证据项**" in line:
            expected_targets.extend(
                (target, frozenset({"supported_by", "historically_referenced_evidence"}))
                for target in _semantic_evidence_references(line)
            )
        if "**迁移证据审计**" in line or "**历史证据引用**" in line:
            expected_targets.extend(
                (target, frozenset({"historically_referenced_evidence"}))
                for target in _semantic_evidence_references(line)
            )
        if "**上游判断" in line:
            relation_type = "upstream_judgment"
            if "约束" in line:
                relation_type = "constrained_by"
            elif "例外" in line:
                relation_type = "exception_to"
            elif "支持" in line:
                relation_type = "supported_by_judgment"
            expected_targets.extend(
                (target, frozenset({relation_type}))
                for target in re.findall(r"J\d{8}-\d{3}(?:\s+v\d+)?", line)
            )
        if "**策略版本**" in line:
            expected_targets.extend(
                (f"{strategy_id}@{version}", frozenset({"uses_strategy"}))
                for strategy_id, version in re.findall(
                    r"\b(STR-[A-Za-z0-9-]+)(?:@|\s+v)([A-Za-z0-9._-]+)\b",
                    line,
                )
            )
    expected_targets = list(dict.fromkeys(expected_targets))
    if len(canonical_declared) != len(expected_targets):
        raise ValueError(
            f"frozen old semantic unit {unit_id!r} relations do not exhaust the source excerpt"
        )
    remaining = list(expected_targets)
    for relation in canonical_declared:
        if relation.get("from") != unit_id:
            raise ValueError(
                f"frozen old semantic unit {unit_id!r} relation source id mismatch"
            )
        target = relation.get("to")
        relation_type = relation.get("type")
        match_index = next(
            (
                index
                for index, (expected_target, allowed_types) in enumerate(remaining)
                if target == expected_target and relation_type in allowed_types
            ),
            None,
        )
        if match_index is None:
            raise ValueError(
                f"frozen old semantic unit {unit_id!r} relation does not match source excerpt"
            )
        remaining.pop(match_index)
    if remaining:
        raise ValueError(
            f"frozen old semantic unit {unit_id!r} relations omit source references"
        )
    return canonical_declared


def _validate_old_baseline(
    baseline: dict[str, Any],
    migration_report: dict[str, Any],
    new_workspace: Path,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    if _parse_aware_datetime(baseline.get("captured_at")) is None:
        raise ValueError("frozen old baseline requires a timezone-aware captured_at")
    old_root_value = baseline.get("workspace_root")
    if not isinstance(old_root_value, str) or not old_root_value.strip():
        raise ValueError("frozen old baseline requires workspace_root")
    old_root = Path(old_root_value).resolve()
    if old_root.is_symlink() or not old_root.is_dir():
        raise ValueError("frozen old baseline workspace_root must be a regular directory")
    if old_root == new_workspace.resolve() or old_root in new_workspace.resolve().parents or new_workspace.resolve() in old_root.parents:
        raise ValueError("frozen old baseline and migrated workspace must be isolated")
    report_input = Path(str(migration_report.get("input_root") or "")).resolve()
    if report_input != old_root:
        raise ValueError("frozen old baseline workspace_root does not match migration report input_root")
    expected_tree_hash = baseline.get("workspace_sha256")
    if not isinstance(expected_tree_hash, str) or SHA256_PATTERN.fullmatch(expected_tree_hash) is None:
        raise ValueError("frozen old baseline requires workspace_sha256")
    files = baseline.get("files")
    if not isinstance(files, list) or not files:
        raise ValueError("frozen old baseline requires a non-empty file manifest")
    declared: list[tuple[str, str]] = []
    seen_paths: set[str] = set()
    for item in files:
        if not isinstance(item, dict):
            raise ValueError("frozen old baseline file entries must be JSON objects")
        relative = item.get("path")
        file_hash = item.get("sha256")
        if not isinstance(relative, str) or not relative or relative in seen_paths:
            raise ValueError("frozen old baseline file paths must be non-empty and unique")
        if not isinstance(file_hash, str) or SHA256_PATTERN.fullmatch(file_hash) is None:
            raise ValueError(f"frozen old baseline file requires sha256: {relative!r}")
        seen_paths.add(relative)
        declared.append((relative, file_hash))
    declared.sort()
    if _surface_digest(declared) != expected_tree_hash:
        raise ValueError("frozen old baseline file manifest does not match workspace_sha256")
    input_snapshot = migration_report.get("input_snapshot")
    if not isinstance(input_snapshot, dict):
        raise ValueError("migration report requires input_snapshot")
    report_snapshot_hash = input_snapshot.get("sha256")
    report_snapshot_files = input_snapshot.get("files")
    if not isinstance(report_snapshot_hash, str) or SHA256_PATTERN.fullmatch(report_snapshot_hash) is None:
        raise ValueError("migration input snapshot requires sha256")
    if not isinstance(report_snapshot_files, list):
        raise ValueError("migration input snapshot requires files")
    report_declared: list[tuple[str, str]] = []
    for item in report_snapshot_files:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str) or not isinstance(item.get("sha256"), str):
            raise ValueError("migration input snapshot contains an invalid file entry")
        if SHA256_PATTERN.fullmatch(item["sha256"]) is None:
            raise ValueError("migration input snapshot contains an invalid file sha256")
        report_declared.append((item["path"], item["sha256"]))
    report_declared.sort()
    if (
        len({path for path, _ in report_declared}) != len(report_declared)
        or _surface_digest(report_declared) != report_snapshot_hash
    ):
        raise ValueError("migration input snapshot manifest does not match its sha256")
    if report_snapshot_hash != expected_tree_hash or report_declared != declared:
        raise ValueError("frozen old baseline differs from migration input snapshot")
    actual: list[tuple[str, str]] = []
    for path in sorted(old_root.rglob("*")):
        relative_path = path.relative_to(old_root)
        if any(part in TREE_IGNORED_PARTS for part in relative_path.parts):
            continue
        if path.is_symlink():
            raise ValueError(f"frozen old baseline contains a symlink: {relative_path.as_posix()}")
        if path.is_file():
            actual.append((relative_path.as_posix(), _sha256_file(path)))
    if actual != declared or _surface_digest(actual) != expected_tree_hash:
        raise ValueError("frozen old baseline workspace differs from its file manifest")
    file_hashes = dict(declared)

    execution_artifacts = baseline.get("execution_artifacts")
    if not isinstance(execution_artifacts, list):
        raise ValueError("frozen old baseline execution_artifacts must be a list")
    execution_by_scenario: dict[str, dict[str, Any]] = {}
    for item in execution_artifacts:
        if not isinstance(item, dict):
            raise ValueError("frozen old execution artifacts must be JSON objects")
        if set(item) != {"scenario_id", "source_locator", "fields", "fields_sha256"}:
            raise ValueError("frozen old execution artifact has an invalid field set")
        scenario_id = item.get("scenario_id")
        if not isinstance(scenario_id, str) or not scenario_id or scenario_id in execution_by_scenario:
            raise ValueError("frozen old execution artifacts require unique scenario_id values")
        locator = item.get("source_locator")
        if not isinstance(locator, dict):
            raise ValueError(f"frozen old execution artifact {scenario_id!r} requires source_locator")
        relative = locator.get("path")
        if not isinstance(relative, str) or relative not in file_hashes:
            raise ValueError(f"frozen old execution artifact {scenario_id!r} points outside the frozen manifest")
        if locator.get("source_sha256") != file_hashes[relative]:
            raise ValueError(f"frozen old execution artifact {scenario_id!r} source sha256 mismatch")
        source = _runtime_file(old_root, relative)
        try:
            start_line = int(locator.get("start_line"))
            end_line = int(locator.get("end_line"))
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"frozen old execution artifact {scenario_id!r} requires integer line bounds"
            ) from exc
        lines = source.read_text(encoding="utf-8").splitlines()
        if start_line < 1 or end_line < start_line or end_line > len(lines) or end_line - start_line + 1 > 500:
            raise ValueError(f"frozen old execution artifact {scenario_id!r} has invalid line bounds")
        excerpt = "\n".join(lines[start_line - 1 : end_line])
        if locator.get("content_sha256") != hashlib.sha256(excerpt.encode("utf-8")).hexdigest():
            raise ValueError(f"frozen old execution artifact {scenario_id!r} content sha256 mismatch")
        try:
            source_fields = json.loads(excerpt)
        except json.JSONDecodeError as exc:
            raise ValueError(f"frozen old execution artifact {scenario_id!r} is not JSON") from exc
        fields = item.get("fields")
        required_field_names = {
            "workflow",
            "stage",
            "contract",
            "selector",
            "required_unit_ids",
            "selected_unit_ids",
        }
        if not isinstance(fields, dict) or set(fields) != required_field_names:
            raise ValueError(f"frozen old execution artifact {scenario_id!r} has an invalid fields set")
        if source_fields != fields:
            raise ValueError(f"frozen old execution artifact {scenario_id!r} fields do not match source")
        if item.get("fields_sha256") != _canonical_json_sha256(fields):
            raise ValueError(f"frozen old execution artifact {scenario_id!r} fields sha256 mismatch")
        if not all(isinstance(fields.get(key), str) and fields[key] for key in ("workflow", "stage", "contract")):
            raise ValueError(f"frozen old execution artifact {scenario_id!r} identity is incomplete")
        if not isinstance(fields.get("selector"), dict) or not fields["selector"]:
            raise ValueError(f"frozen old execution artifact {scenario_id!r} selector is incomplete")
        required_ids = fields.get("required_unit_ids")
        selected_ids = fields.get("selected_unit_ids")
        if (
            not isinstance(required_ids, list)
            or any(not isinstance(value, str) or not value for value in required_ids)
            or len(required_ids) != len(set(required_ids))
            or not isinstance(selected_ids, list)
            or any(not isinstance(value, str) or not value for value in selected_ids)
            or len(selected_ids) != len(set(selected_ids))
            or not set(selected_ids).issubset(required_ids)
        ):
            raise ValueError(
                f"frozen old execution artifact {scenario_id!r} requires unique selected IDs within required IDs"
            )
        execution_by_scenario[scenario_id] = fields

    semantic_units = baseline.get("semantic_units")
    if not isinstance(semantic_units, list):
        raise ValueError("frozen old baseline semantic_units must be a list")
    by_scenario: dict[str, list[dict[str, Any]]] = {}
    seen_units: set[tuple[str, str]] = set()
    for item in semantic_units:
        if not isinstance(item, dict):
            raise ValueError("frozen old semantic units must be JSON objects")
        scenario_id = str(item.get("scenario_id") or "")
        unit_id = str(item.get("unit_id") or "").removeprefix("atom:")
        key = (scenario_id, unit_id)
        if not scenario_id or not unit_id or key in seen_units:
            raise ValueError("frozen old semantic units require unique scenario_id/unit_id pairs")
        seen_units.add(key)
        locator = item.get("source_locator")
        if not isinstance(locator, dict):
            raise ValueError(f"frozen old semantic unit {unit_id!r} requires source_locator")
        relative = locator.get("path")
        if not isinstance(relative, str) or relative not in file_hashes:
            raise ValueError(f"frozen old semantic unit {unit_id!r} points outside the frozen manifest")
        if locator.get("source_sha256") != file_hashes[relative]:
            raise ValueError(f"frozen old semantic unit {unit_id!r} source sha256 mismatch")
        source = _runtime_file(old_root, relative)
        try:
            start_line = int(locator.get("start_line"))
            end_line = int(locator.get("end_line"))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"frozen old semantic unit {unit_id!r} requires integer line bounds") from exc
        lines = source.read_text(encoding="utf-8").splitlines()
        if start_line < 1 or end_line < start_line or end_line > len(lines) or end_line - start_line + 1 > 500:
            raise ValueError(f"frozen old semantic unit {unit_id!r} has invalid line bounds")
        excerpt = "\n".join(lines[start_line - 1 : end_line])
        if locator.get("content_sha256") != hashlib.sha256(excerpt.encode("utf-8")).hexdigest():
            raise ValueError(f"frozen old semantic unit {unit_id!r} content sha256 mismatch")
        fields = item.get("fields")
        if not isinstance(fields, dict) or set(fields) != {"proposition", "information_cutoff", "status", "relations"}:
            raise ValueError(f"frozen old semantic unit {unit_id!r} has an invalid field set")
        if item.get("fields_sha256") != _canonical_json_sha256(fields):
            raise ValueError(f"frozen old semantic unit {unit_id!r} fields sha256 mismatch")
        if not isinstance(fields.get("relations"), list) or _parse_aware_datetime(fields.get("information_cutoff")) is None:
            raise ValueError(f"frozen old semantic unit {unit_id!r} has invalid semantic fields")
        source_relations = _semantic_source_relations(
            excerpt, unit_id, fields.get("relations")
        )
        source_fields = {
            "proposition": _semantic_proposition(excerpt),
            "information_cutoff": _canonical_semantic_cutoff(
                _semantic_labeled_value(excerpt, ("信息快照", "新信息快照"))
            ),
            "status": _semantic_source_status(excerpt),
        }
        for field, value in source_fields.items():
            if value != fields.get(field):
                raise ValueError(f"frozen old semantic unit {unit_id!r} {field} does not match source excerpt")
        by_scenario.setdefault(scenario_id, []).append(
            {
                "unit_id": unit_id,
                "old": {
                    "proposition": fields["proposition"],
                    "information_cutoff": fields["information_cutoff"],
                    "status": fields["status"],
                    "relations": source_relations,
                },
                "source_locator": dict(locator),
                "fields_sha256": item["fields_sha256"],
            }
        )
    semantic_scenario_ids = set(by_scenario)
    if not semantic_scenario_ids.issubset(execution_by_scenario):
        raise ValueError(
            "frozen semantic scenario set contains no matching execution artifact"
        )
    for scenario_id, execution in execution_by_scenario.items():
        semantic_ids = {
            str(item["unit_id"])
            for item in by_scenario.get(scenario_id, [])
        }
        if semantic_ids != set(execution["selected_unit_ids"]):
            raise ValueError(
                f"scenario {scenario_id!r} semantic_units do not match frozen selected_unit_ids"
            )
    return by_scenario, {
        "passed": True,
        "workspace_root": str(old_root),
        "workspace_sha256": expected_tree_hash,
        "file_count": len(declared),
        "semantic_unit_count": len(seen_units),
        "execution_artifact_count": len(execution_by_scenario),
        "execution_scenario_ids": sorted(execution_by_scenario),
    }


def _workspace_fingerprint(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _positive_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _nonnegative_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _ratio(value: Any) -> float | None:
    number = _positive_number(value)
    return number if number is not None and number <= 1 else None


def _repository_contract(reference: Any) -> tuple[dict[str, Any], Path]:
    if not isinstance(reference, str) or not reference.strip():
        raise ValueError("scenario requires a repository task contract reference")
    directory = contract_directory().resolve()
    for path in sorted(directory.glob("*.json")):
        contract = load_contract(path)
        aliases = {path.name, path.stem, str(contract["contract_id"])}
        if reference in aliases:
            return contract, path
    raise FileNotFoundError(f"repository task contract not found: {reference}")


def _scenario_contract_record(scenario: Any, index: int) -> dict[str, Any]:
    scenario_id = str(scenario.get("id") or f"scenario-{index + 1}") if isinstance(scenario, dict) else f"scenario-{index + 1}"
    base = {
        "id": scenario_id,
        "case_type": scenario.get("case_type") if isinstance(scenario, dict) else None,
        "workflow": scenario.get("workflow") if isinstance(scenario, dict) else None,
        "stage": scenario.get("stage") if isinstance(scenario, dict) else None,
        "status": "not_run",
    }
    try:
        if not isinstance(scenario, dict):
            raise ValueError("scenario must be a JSON object")
        contract, path = _repository_contract(scenario.get("contract"))
        case_type = str(scenario.get("case_type") or "")
        expected_workflow = CASE_TYPE_WORKFLOWS.get(case_type)
        if expected_workflow is None:
            raise ValueError(f"unsupported shadow replay case_type {case_type!r}")
        if scenario.get("workflow") != expected_workflow:
            raise ValueError(
                f"case_type {case_type!r} requires workflow {expected_workflow!r}, got {scenario.get('workflow')!r}"
            )
        if case_type == "event" and (
            scenario.get("object_type") != "event" or contract.get("contract_id") != "investigate.event"
        ):
            raise ValueError("case_type 'event' requires object_type 'event' and contract 'investigate.event'")
        if case_type == "investigate" and scenario.get("object_type") == "event":
            raise ValueError("event investigation must use case_type 'event'")
        if scenario.get("workflow") != contract.get("workflow"):
            raise ValueError(
                f"scenario workflow {scenario.get('workflow')!r} does not match contract {contract.get('workflow')!r}"
            )
        if scenario.get("stage") != contract.get("stage"):
            raise ValueError(f"scenario stage {scenario.get('stage')!r} does not match contract {contract.get('stage')!r}")
        object_types = contract.get("object_types") or []
        if object_types and scenario.get("object_type") not in object_types:
            raise ValueError(
                f"scenario object_type {scenario.get('object_type')!r} is outside contract object_types {object_types!r}"
            )
        base["contract"] = {
            "status": "validated",
            "contract_id": contract["contract_id"],
            "version": contract["version"],
            "path": path.relative_to(REPO_ROOT).as_posix(),
            "required_evidence_count": len(contract.get("required_evidence", [])),
        }
    except (FileNotFoundError, ValueError) as exc:
        base["contract"] = {
            "status": "invalid",
            "reference": scenario.get("contract") if isinstance(scenario, dict) else None,
            "reason": str(exc),
        }
    return base


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _parse_aware_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None and parsed.utcoffset() is not None else None


def _verified_telemetry_record(
    trace: dict[str, Any],
    trace_path: Path,
    event: dict[str, Any],
) -> tuple[Path, int, str, int]:
    locator = event.get("source_locator")
    if not isinstance(locator, dict):
        raise ValueError("measurement event requires a raw telemetry source_locator")
    if set(locator) != {
        "path",
        "start_line",
        "end_line",
        "source_sha256",
        "content_sha256",
    }:
        raise ValueError("raw telemetry source_locator has an invalid field set")
    relative = locator.get("path")
    if not isinstance(relative, str) or not relative.strip():
        raise ValueError("raw telemetry source_locator requires a relative path")
    candidate = Path(relative)
    if candidate.is_absolute() or any(part in {"", ".", ".."} for part in candidate.parts):
        raise ValueError("raw telemetry source_locator path is unsafe")
    source = _runtime_file(trace_path.parent.resolve(), relative)
    if source == trace_path.resolve():
        raise ValueError("measurement trace cannot be its own raw telemetry export")
    source_hash = _sha256_file(source)
    if locator.get("source_sha256") != source_hash:
        raise ValueError("raw telemetry source sha256 mismatch")
    try:
        start_line = int(locator.get("start_line"))
        end_line = int(locator.get("end_line"))
    except (TypeError, ValueError) as exc:
        raise ValueError("raw telemetry source_locator requires integer line bounds") from exc
    lines = source.read_text(encoding="utf-8").splitlines()
    if start_line < 1 or end_line != start_line or end_line > len(lines):
        raise ValueError("raw telemetry event must occupy exactly one valid JSONL line")
    excerpt = lines[start_line - 1]
    if locator.get("content_sha256") != hashlib.sha256(excerpt.encode("utf-8")).hexdigest():
        raise ValueError("raw telemetry event content sha256 mismatch")
    try:
        raw = json.loads(excerpt)
    except json.JSONDecodeError as exc:
        raise ValueError("raw telemetry event is not valid JSON") from exc
    if not isinstance(raw, dict):
        raise ValueError("raw telemetry event must be a JSON object")
    expected = {
        "event_id": event.get("event_id"),
        "trace_id": trace.get("trace_id"),
        "session_id": event.get("session_id"),
        "migration_id": trace.get("migration_id"),
        "phase": event.get("phase"),
        "metric": event.get("metric"),
        "value": event.get("value"),
        "unit": event.get("unit"),
        "source_kind": event.get("source_kind"),
        "observation_status": event.get("observation_status"),
        "observed_at": event.get("observed_at"),
    }
    if raw != expected:
        raise ValueError("measurement event differs from its raw telemetry record")
    return source, start_line, source_hash, len(lines)


def _measurement_metrics(
    trace: dict[str, Any], trace_path: Path
) -> tuple[dict[tuple[str, str], float], dict[str, Any]]:
    trace_id = trace.get("trace_id")
    if not isinstance(trace_id, str) or not trace_id.strip():
        raise ValueError("measurement trace requires trace_id")
    if _parse_aware_datetime(trace.get("observed_at")) is None:
        raise ValueError("measurement trace requires a timezone-aware observed_at")
    if any(key in trace for key in ("baseline", "candidate", "candidate_observation")):
        raise ValueError("measurement trace must contain event observations, not summary metrics")
    events = trace.get("events")
    if not isinstance(events, list):
        raise ValueError("measurement trace events must be a list")
    observations: dict[tuple[str, str], float] = {}
    invalid: list[dict[str, Any]] = []
    event_ids: set[str] = set()
    seen_metrics: set[tuple[str, str]] = set()
    telemetry_sources: dict[Path, dict[str, Any]] = {}
    for index, event in enumerate(events):
        if not isinstance(event, dict):
            invalid.append({"index": index, "reason": "event_not_object"})
            continue
        event_id = event.get("event_id")
        if not isinstance(event_id, str) or not event_id or event_id in event_ids:
            invalid.append({"index": index, "reason": "event_id_missing_or_duplicate"})
            continue
        event_ids.add(event_id)
        source, source_line, source_hash, source_line_count = _verified_telemetry_record(
            trace, trace_path, event
        )
        source_record = telemetry_sources.setdefault(
            source,
            {
                "sha256": source_hash,
                "line_count": source_line_count,
                "referenced_lines": set(),
            },
        )
        if (
            source_record["sha256"] != source_hash
            or source_record["line_count"] != source_line_count
            or source_line in source_record["referenced_lines"]
        ):
            raise ValueError("raw telemetry JSONL line is reused or changed")
        source_record["referenced_lines"].add(source_line)
        if event.get("session_id") != trace.get("session_id"):
            invalid.append({"event_id": event_id, "reason": "session_identity_mismatch"})
            continue
        key = (str(event.get("phase") or ""), str(event.get("metric") or ""))
        if key not in TRACE_METRICS:
            continue
        if key in seen_metrics:
            raise ValueError(f"measurement trace contains duplicate observation {key!r}")
        seen_metrics.add(key)
        expected_unit, expected_source = TRACE_METRICS[key]
        reasons: list[str] = []
        if event.get("observation_status") != "observed":
            reasons.append("not_observed")
        if event.get("unit") != expected_unit:
            reasons.append("wrong_unit")
        if event.get("source_kind") != expected_source:
            reasons.append("untrusted_measurement_source")
        if _parse_aware_datetime(event.get("observed_at")) is None:
            reasons.append("invalid_observed_at")
        value = _nonnegative_number(event.get("value"))
        if value is None or (key[0] == "baseline" and value == 0) or (key[1] == "main_context_peak_tokens" and value == 0):
            reasons.append("invalid_value")
        if reasons:
            invalid.append({"event_id": event_id, "phase": key[0], "metric": key[1], "reasons": reasons})
            continue
        observations[key] = value
    if len(telemetry_sources) > 1:
        raise ValueError("measurement trace must bind exactly one raw telemetry export")
    for source, record in telemetry_sources.items():
        if record["referenced_lines"] != set(range(1, record["line_count"] + 1)):
            raise ValueError("raw telemetry export contains unreferenced JSONL lines")
    missing = [
        {"phase": phase, "metric": metric}
        for phase, metric in TRACE_METRICS
        if (phase, metric) not in observations
    ]
    return observations, {
        "passed": not missing and not invalid,
        "trace_id": trace_id,
        "required_observation_count": len(TRACE_METRICS),
        "observed_count": len(observations),
        "missing_observations": missing,
        "invalid_observations": invalid,
        "telemetry_exports": [
            {
                "path": str(source),
                "sha256": record["sha256"],
                "line_count": record["line_count"],
            }
            for source, record in sorted(
                telemetry_sources.items(), key=lambda item: str(item[0])
            )
        ],
        "summary_metrics_accepted": False,
    }


def _condition_requirements(expectations: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    conditions = expectations.get("conditions")
    if not isinstance(conditions, list):
        raise ValueError("scenario expectations.conditions must be a list")
    requirements: list[dict[str, Any]] = []
    normalized: list[dict[str, str]] = []
    allowed = {"conflict", "denial", "expired", "future"}
    category_units: dict[str, set[str]] = {category: set() for category in allowed}
    for index, condition in enumerate(conditions):
        if not isinstance(condition, dict):
            raise ValueError("each expected condition must be a JSON object")
        category = str(condition.get("category") or "")
        unit_id = str(condition.get("unit_id") or "").removeprefix("atom:")
        if category not in allowed or not unit_id:
            raise ValueError("expected condition requires category and unit_id")
        requirement_id = f"shadow-condition:{category}:{index + 1}"
        category_units[category].add(unit_id)
        normalized.append({"requirement_id": requirement_id, "category": category, "unit_id": unit_id})
        requirements.append(
            {
                "requirement_id": requirement_id,
                "unit_id": unit_id,
                "required": True,
                "allow_unknown": False,
            }
        )
    if category_units["conflict"] & category_units["denial"]:
        raise ValueError("conflict and denial probes require different unit IDs")
    return requirements, normalized


def _condition_results(
    conditions: list[dict[str, str]],
    assembled: dict[str, Any],
    canonical_units: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    rows = {
        str(row.get("requirement_id")): row
        for row in assembled.get("coverage", {}).get("requirements", [])
    }
    results: list[dict[str, Any]] = []
    expected_reasons = {
        "conflict": "conflict_or_denial",
        "denial": "conflict_or_denial",
        "expired": "expired",
        "future": "future_information",
    }
    for condition in conditions:
        requirement_id = condition["requirement_id"]
        row = rows.get(requirement_id, {})
        selected_reference = next(
            (
                reference
                for reference in assembled.get("stable_references", [])
                if any(
                    reason.get("requirement_id") == requirement_id
                    for reason in reference.get("selection_reasons", [])
                    if isinstance(reason, dict)
                )
            ),
            None,
        )
        category = condition["category"]
        unit = canonical_units.get(condition["unit_id"])
        canonical_status = (unit or {}).get("status")
        risk_class = _canonical_risk_class(canonical_status)
        exclusion_counter = {
            "conflict": "conflict_count",
            "denial": "denial_count",
            "expired": "expired_count",
            "future": "future_count",
        }[category]
        independent_count = (
            1
            if category in {"conflict", "denial"} and risk_class == category
            else int(row.get(exclusion_counter, 0))
            if category not in {"conflict", "denial"}
            else 0
        )
        exact_exclusion = any(
            item.get("unit_id") == condition["unit_id"]
            and item.get("requirement_id") == requirement_id
            and item.get("reason") == expected_reasons[category]
            for item in assembled.get("selection_exclusions", [])
            if isinstance(item, dict)
        )
        excluded = (
            selected_reference is None
            and int(row.get("eligible_count", 0)) == 0
            and independent_count > 0
            and exact_exclusion
        )
        passed = (
            int(row.get("candidate_count", 0)) > 0
            and row.get("reason") == expected_reasons[category]
            and excluded
        )
        results.append(
            {
                "category": category,
                "unit_id": condition["unit_id"],
                "requirement_id": requirement_id,
                "candidate_count": int(row.get("candidate_count", 0)),
                "eligible_count": int(row.get("eligible_count", 0)),
                "observed_reason": row.get("reason"),
                "selected": selected_reference is not None,
                "excluded": excluded,
                "exclusion_counter": exclusion_counter,
                "exclusion_count": independent_count,
                "status": canonical_status,
                "passed": passed,
            }
        )
    return results


def _canonical_risk_class(value: Any) -> str | None:
    normalized = re.sub(r"\s+", "", str(value or "")).casefold()
    if normalized in {"冲突", "conflict"}:
        return "conflict"
    if normalized in {"已否证", "否证", "denied", "falsified"}:
        return "denial"
    return None


def _canonical_condition_units(root: Path, conditions: list[dict[str, str]]) -> dict[str, dict[str, Any]]:
    wanted = {condition["unit_id"] for condition in conditions}
    if not wanted:
        return {}
    found: dict[str, dict[str, Any]] = {}
    duplicates: set[str] = set()
    for path in iter_source_files(root):
        for unit in extract_units(root, path, strict=True):
            unit_id = str(unit.get("unit_id") or "")
            if unit_id not in wanted:
                continue
            if unit_id in found:
                duplicates.add(unit_id)
            else:
                found[unit_id] = unit
    if duplicates:
        raise ValueError("risk probe stable IDs are ambiguous: " + ", ".join(sorted(duplicates)))
    return found


def _canonical_relations(value: Any) -> list[dict[str, Any]] | None:
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        return None
    return sorted(
        ({str(key): item[key] for key in sorted(item)} for item in value),
        key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True),
    )


def _semantic_proposition(verification_text: Any) -> str | None:
    text = str(verification_text or "")
    for label in ("原子命题", "事实陈述"):
        marker = f"**{label}**"
        for line in text.splitlines():
            if marker not in line:
                continue
            value = line.split(marker, 1)[1].lstrip(" ：:").strip()
            if value:
                return value
    return None


def _semantic_comparisons(
    frozen: list[dict[str, Any]],
    hydrated: dict[str, Any],
) -> list[dict[str, Any]]:
    observed = {
        str(unit.get("unit_id")): unit
        for unit in hydrated.get("units", [])
        if isinstance(unit, dict) and unit.get("unit_id")
    }
    comparisons: list[dict[str, Any]] = []
    for item in frozen:
        if not isinstance(item, dict):
            comparisons.append(
                {"unit_id": None, "status": "invalid_frozen_semantics", "passed": False}
            )
            continue
        unit_id = str(item.get("unit_id") or "")
        old = item.get("old")
        unit = observed.get(unit_id)
        if not isinstance(old, dict) or unit is None:
            comparisons.append(
                {
                    "unit_id": unit_id,
                    "old": old,
                    "new": None,
                    "status": "missing_old_shape" if not isinstance(old, dict) else "unit_not_hydrated",
                    "passed": False,
                }
            )
            continue
        old_relations = _canonical_relations(old.get("relations"))
        new = {
            "proposition": _semantic_proposition(unit.get("verification_text")),
            "information_cutoff": _canonical_semantic_cutoff(
                unit.get("information_cutoff")
            ),
            "status": unit.get("status"),
            "relations": _canonical_relations(unit.get("relations", [])),
        }
        required_fields = ("proposition", "information_cutoff", "status", "relations")
        old_normalized = {
            "proposition": str(old.get("proposition") or "").strip() or None,
            "information_cutoff": _canonical_semantic_cutoff(
                old.get("information_cutoff")
            ),
            "status": str(old.get("status") or "").strip() or None,
            "relations": old_relations,
        }
        differences = [
            field
            for field in required_fields
            if old_normalized.get(field) != new.get(field)
        ]
        complete = all(old_normalized.get(field) is not None for field in required_fields)
        comparisons.append(
            {
                "unit_id": unit_id,
                "old": old_normalized,
                "new": new,
                "differences": differences,
                "status": "matched" if complete and not differences else "mismatch",
                "passed": complete and not differences,
            }
        )
    return comparisons


def _run_scenario(
    root: Path,
    scenario: dict[str, Any],
    record: dict[str, Any],
    index: int,
    projection_path: Path,
    frozen_semantic_units: list[dict[str, Any]],
) -> dict[str, Any]:
    if record.get("contract", {}).get("status") != "validated":
        return record
    try:
        cutoff = scenario.get("information_cutoff")
        if _parse_datetime(cutoff) is None:
            raise ValueError("scenario requires a parseable information_cutoff")
        expectations = scenario.get("expectations")
        if not isinstance(expectations, dict):
            raise ValueError("scenario requires explicit expectations")
        if "semantic_units" in expectations or "old" in expectations:
            raise ValueError("scenario cannot self-report old semantic observations")
        condition_requirements, expected_conditions = _condition_requirements(expectations)
        run_manifest = {
            "schema_version": "a-share-workspace-v3",
            "workspace_root": str(root),
            "run_id": f"RUN-SHADOW-{index + 1:03d}",
            "workflow": scenario.get("workflow"),
            "stage": scenario.get("stage"),
            "information_cutoff": cutoff,
            "objects": scenario.get("objects") or [],
            "object_type": scenario.get("object_type"),
            "handoff": scenario.get("handoff") or {},
            "strategy_version": scenario.get("strategy_version"),
            "task_contract": str(REPO_ROOT / record["contract"]["path"]),
            "projection_path": str(projection_path),
        }
        assembled = assemble(run_manifest, {"required_evidence": condition_requirements})
        hydrated = hydrate(assembled["stable_references"], workspace_root=root)
        rows = [row for row in assembled["coverage"]["requirements"] if row.get("required")]
        recalled = sum(1 for row in rows if int(row.get("candidate_count", 0)) > 0)
        base_rows = [row for row in rows if not str(row.get("requirement_id", "")).startswith("shadow-condition:")]
        cutoff_time = _parse_datetime(cutoff)
        future_selected = sum(
            1
            for reference in assembled["stable_references"]
            if (unit_time := _parse_datetime(reference.get("information_cutoff"))) is not None
            and cutoff_time is not None
            and unit_time > cutoff_time
        )
        coverage = {
            "required_total": len(rows),
            "required_recalled": recalled,
            "required_recall_ratio": recalled / len(rows) if rows else 0.0,
            "base_required_total": len(base_rows),
            "base_required_eligible": sum(1 for row in base_rows if row.get("covered")),
            "base_floor_covered": bool(base_rows) and all(row.get("covered") for row in base_rows),
            "blocking": bool(assembled.get("coverage", {}).get("blocking")),
            "blocking_gap_count": int(assembled.get("coverage", {}).get("blocking_gap_count", 0)),
            "blocking_gaps": [
                gap
                for gap in assembled.get("gaps", [])
                if isinstance(gap, dict) and gap.get("blocking")
            ],
        }
        quality = {
            "assembled_units": len(assembled["stable_references"]),
            "hydrate_units": len(hydrated["units"]),
            "hydrate_verification_failures": len(hydrated["missing_references"]),
            "future_information_selected": future_selected,
            "projection_degraded": bool(assembled.get("projection", {}).get("projection_degraded")),
            "context_proxy": assembled.get("quality", {}).get("context_proxy", {}),
            "verification_characters": hydrated.get("quality", {}).get("verification_characters", 0),
        }
        conditions = _condition_results(
            expected_conditions,
            assembled,
            _canonical_condition_units(root, expected_conditions),
        )
        semantic_comparisons = _semantic_comparisons(frozen_semantic_units, hydrated)
        passed_probe_ids = {
            condition["requirement_id"]
            for condition in conditions
            if condition.get("passed")
        }
        expected_probe_gaps = [
            gap
            for gap in coverage["blocking_gaps"]
            if gap.get("requirement_id") in passed_probe_ids
        ]
        unexpected_blocking_gaps = [
            gap
            for gap in coverage["blocking_gaps"]
            if gap.get("requirement_id") not in passed_probe_ids
        ]
        coverage.update(
            {
                "expected_probe_blocking_gap_count": len(expected_probe_gaps),
                "unexpected_blocking_gap_count": len(unexpected_blocking_gaps),
                "unexpected_blocking_gaps": unexpected_blocking_gaps,
                "acceptance_blocking": bool(unexpected_blocking_gaps),
            }
        )
        passed = (
            coverage["required_recall_ratio"] == 1.0
            and coverage["base_floor_covered"]
            and not coverage["acceptance_blocking"]
            and quality["hydrate_verification_failures"] == 0
            and quality["future_information_selected"] == 0
            and not quality["projection_degraded"]
            and all(condition["passed"] for condition in conditions)
        )
        record.update(
            {
                "status": "passed" if passed else "failed",
                "information_cutoff": cutoff,
                "coverage": coverage,
                "quality": quality,
                "conditions": conditions,
                "semantic_comparisons": semantic_comparisons,
            }
        )
    except (OSError, ValueError, UnicodeDecodeError, KeyError) as exc:
        record.update({"status": "failed", "execution_error": str(exc)})
    return record


def replay(
    workspace: Path,
    scenarios_path: Path,
    old_baseline_path: Path,
    migration_report_path: Path,
    measurement_trace_path: Path,
    output: Path,
) -> dict[str, Any]:
    root = workspace.resolve()
    if not root.is_dir():
        raise ValueError(f"shadow workspace is not a directory: {root}")
    output = output.resolve()
    if output == root or root in output.parents:
        raise ValueError("shadow replay report must be outside the input workspace")
    fingerprint_before = _workspace_fingerprint(root)
    manifest = _read_object(scenarios_path.resolve())
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"unsupported shadow replay schema_version: {manifest.get('schema_version')}")
    old_baseline, migration_report, measurement_trace, input_records = _validated_bound_inputs(
        manifest,
        scenarios_path.resolve(),
        root,
        old_baseline_path,
        migration_report_path,
        measurement_trace_path,
    )
    runtime_surface_check = _validate_runtime_surface(migration_report, root)
    frozen_semantics, old_baseline_check = _validate_old_baseline(
        old_baseline,
        migration_report,
        root,
    )
    scenarios = manifest.get("scenarios")
    if not isinstance(scenarios, list):
        raise ValueError("shadow replay scenarios must be a list")
    scenario_ids = {
        str(item.get("id"))
        for item in scenarios
        if isinstance(item, dict) and isinstance(item.get("id"), str) and item.get("id")
    }
    execution_scenario_ids = set(old_baseline_check["execution_scenario_ids"])
    if scenario_ids != execution_scenario_ids:
        raise ValueError(
            "shadow scenarios do not exactly match frozen old execution artifacts"
        )

    present = {
        str(item.get("case_type") or item.get("workflow"))
        for item in scenarios
        if isinstance(item, dict) and (item.get("case_type") or item.get("workflow"))
    }
    missing = sorted(REQUIRED_WORKFLOWS - present)
    workflow_check = {
        "passed": not missing,
        "required": sorted(REQUIRED_WORKFLOWS),
        "present": sorted(present),
        "missing": missing,
    }
    scenario_records = [_scenario_contract_record(scenario, index) for index, scenario in enumerate(scenarios)]
    failed_contracts = [
        record["id"]
        for record in scenario_records
        if record.get("contract", {}).get("status") != "validated"
    ]
    contract_check = {
        "passed": not failed_contracts,
        "repository_contract_directory": str(contract_directory().resolve()),
        "failed_scenarios": failed_contracts,
    }
    with tempfile.TemporaryDirectory(prefix="a-share-shadow-replay-") as replay_directory:
        replay_root = Path(replay_directory) / "workspace"
        shutil.copytree(root, replay_root)
        projection_path = replay_root / ".context" / "shadow-replay.sqlite3"
        projection_path.parent.mkdir(parents=True, exist_ok=True)
        scenario_records = [
            _run_scenario(
                replay_root,
                scenario,
                record,
                index,
                projection_path,
                frozen_semantics.get(record["id"], []),
            )
            if isinstance(scenario, dict)
            else record
            for index, (scenario, record) in enumerate(zip(scenarios, scenario_records))
        ]
    failed_executions = [record["id"] for record in scenario_records if record.get("status") != "passed"]
    execution_check = {"passed": not failed_executions, "failed_scenarios": failed_executions}
    completed_records = [record for record in scenario_records if isinstance(record.get("coverage"), dict)]
    required_total = sum(int(record["coverage"].get("required_total", 0)) for record in completed_records)
    required_recalled = sum(int(record["coverage"].get("required_recalled", 0)) for record in completed_records)
    required_ratio = required_recalled / required_total if required_total else 0.0
    recall_check = {
        "passed": required_total > 0 and required_ratio == 1.0,
        "required_total": required_total,
        "required_recalled": required_recalled,
        "ratio": required_ratio,
    }
    condition_results = [
        condition
        for record in completed_records
        for condition in record.get("conditions", [])
    ]
    required_condition_categories = {"conflict", "denial", "expired", "future"}
    exercised_categories = {str(item.get("category")) for item in condition_results}
    missing_condition_categories = sorted(required_condition_categories - exercised_categories)
    failed_conditions = [
        {"category": item.get("category"), "unit_id": item.get("unit_id")}
        for item in condition_results
        if not item.get("passed")
    ]
    condition_check = {
        "passed": not missing_condition_categories and not failed_conditions,
        "required_categories": sorted(required_condition_categories),
        "categories_exercised": sorted(exercised_categories),
        "missing_categories": missing_condition_categories,
        "failed_conditions": failed_conditions,
        "zero_omissions": not failed_conditions,
    }
    hydration_failures = sum(
        int(record.get("quality", {}).get("hydrate_verification_failures", 0))
        for record in completed_records
    )
    hydration_check = {"passed": bool(completed_records) and hydration_failures == 0, "verification_failures": hydration_failures}
    blocking_gap_total = sum(
        int(record.get("coverage", {}).get("blocking_gap_count", 0))
        for record in completed_records
    )
    expected_probe_gap_total = sum(
        int(record.get("coverage", {}).get("expected_probe_blocking_gap_count", 0))
        for record in completed_records
    )
    unexpected_blocking_gap_total = sum(
        int(record.get("coverage", {}).get("unexpected_blocking_gap_count", 0))
        for record in completed_records
    )
    blocking_gap_scenarios = [
        record["id"]
        for record in completed_records
        if record.get("coverage", {}).get("acceptance_blocking")
    ]
    blocking_gap_check = {
        "passed": unexpected_blocking_gap_total == 0,
        "total": blocking_gap_total,
        "expected_probe_total": expected_probe_gap_total,
        "unexpected_total": unexpected_blocking_gap_total,
        "scenarios": blocking_gap_scenarios,
    }
    future_selected = sum(
        int(record.get("quality", {}).get("future_information_selected", 0))
        for record in completed_records
    )
    future_check = {
        "passed": future_selected == 0 and "future" in exercised_categories,
        "selected_after_cutoff": future_selected,
        "future_probe_exercised": "future" in exercised_categories,
        "future_information_backfill": False,
    }
    degraded_scenarios = [
        record["id"]
        for record in completed_records
        if record.get("quality", {}).get("projection_degraded")
    ]
    projection_check = {
        "passed": bool(completed_records) and not degraded_scenarios,
        "degraded_scenarios": degraded_scenarios,
        "degradation_rate": len(degraded_scenarios) / len(completed_records) if completed_records else 1.0,
    }
    missing_semantic_scenarios = [
        record["id"]
        for record in scenario_records
        if not record.get("semantic_comparisons")
    ]
    semantic_comparisons = [
        comparison
        for record in scenario_records
        for comparison in record.get("semantic_comparisons", [])
    ]
    failed_semantic_comparisons = [
        {
            "scenario_id": record["id"],
            "unit_id": comparison.get("unit_id"),
        }
        for record in scenario_records
        for comparison in record.get("semantic_comparisons", [])
        if not comparison.get("passed")
    ]
    semantic_check = {
        "passed": not missing_semantic_scenarios and not failed_semantic_comparisons,
        "missing_scenarios": missing_semantic_scenarios,
        "comparison_total": len(semantic_comparisons),
        "failed_comparisons": failed_semantic_comparisons,
    }

    targets = manifest.get("targets") if isinstance(manifest.get("targets"), dict) else {}
    trace_metrics, measurement_check = _measurement_metrics(
        measurement_trace, measurement_trace_path.resolve()
    )
    baseline_raw = trace_metrics.get(("baseline", "raw_tool_payload_characters"))
    baseline_context = trace_metrics.get(("baseline", "main_context_characters"))
    baseline_tokens = trace_metrics.get(("baseline", "main_context_peak_tokens"))
    raw_payload_characters = trace_metrics.get(("candidate", "raw_tool_payload_characters"))
    candidate_context_characters = trace_metrics.get(("candidate", "main_context_characters"))
    candidate_peak_tokens = trace_metrics.get(("candidate", "main_context_peak_tokens"))
    target_raw_reduction = _ratio(targets.get("min_raw_tool_payload_reduction_ratio"))
    target_context_ratio = _ratio(targets.get("max_main_context_ratio"))
    target_peak_tokens = _positive_number(targets.get("max_main_context_peak_tokens"))
    missing_targets = [
        name
        for name, value in (
            ("min_raw_tool_payload_reduction_ratio", target_raw_reduction),
            ("max_main_context_ratio", target_context_ratio),
            ("max_main_context_peak_tokens", target_peak_tokens),
        )
        if value is None
    ]
    main_context_proxy_characters = max(
        (
            int(record.get("quality", {}).get("context_proxy", {}).get("stable_reference_characters", 0))
            + int(record.get("quality", {}).get("verification_characters", 0))
            for record in completed_records
        ),
        default=0,
    )
    raw_reduction_ratio = (
        max(0.0, (baseline_raw - raw_payload_characters) / baseline_raw)
        if baseline_raw is not None and raw_payload_characters is not None
        else None
    )
    main_context_ratio = (
        candidate_context_characters / baseline_context
        if baseline_context is not None and candidate_context_characters is not None
        else None
    )
    proxy_metrics = {
        "status": (
            "observed_trace"
            if measurement_check["passed"] and not missing_targets
            else "incomplete"
        ),
        "measurement_trace_id": measurement_check["trace_id"],
        "baseline_raw_tool_payload_characters": baseline_raw,
        "raw_tool_payload_characters_entered": raw_payload_characters,
        "raw_tool_payload_reduction_ratio": raw_reduction_ratio,
        "baseline_main_context_characters": baseline_context,
        "observed_main_context_characters": candidate_context_characters,
        "main_context_peak_proxy_characters": main_context_proxy_characters,
        "main_context_ratio": main_context_ratio,
        "assembled_units": sum(int(record.get("quality", {}).get("assembled_units", 0)) for record in completed_records),
        "hydrate_units": sum(int(record.get("quality", {}).get("hydrate_units", 0)) for record in completed_records),
        "projection_degradation_rate": projection_check["degradation_rate"],
        "model_token_replay": {
            "available": candidate_peak_tokens is not None,
            "baseline_peak_tokens": baseline_tokens,
            "candidate_peak_tokens": candidate_peak_tokens,
            "release_target_peak_tokens": target_peak_tokens,
            "limitation": (
                None
                if candidate_peak_tokens is not None
                else "候选会话峰值 token 未提供冻结观测，不能完成发布验收。"
            ),
        },
    }
    context_peak_check = {
        "passed": (
            candidate_peak_tokens is not None
            and target_peak_tokens is not None
            and candidate_peak_tokens <= target_peak_tokens
        ),
        "observed_peak_tokens": candidate_peak_tokens,
        "maximum_peak_tokens": target_peak_tokens,
    }
    proxy_passed = (
        measurement_check["passed"]
        and not missing_targets
        and raw_reduction_ratio is not None
        and target_raw_reduction is not None
        and raw_reduction_ratio >= target_raw_reduction
        and main_context_ratio is not None
        and target_context_ratio is not None
        and main_context_ratio <= target_context_ratio
    )
    proxy_check = {
        "passed": proxy_passed,
        "measurement_trace_id": measurement_check["trace_id"],
        "missing_target_fields": missing_targets,
        "missing_observations": measurement_check["missing_observations"],
        "invalid_observations": measurement_check["invalid_observations"],
        "targets": {
            "min_raw_tool_payload_reduction_ratio": target_raw_reduction,
            "max_main_context_ratio": target_context_ratio,
            "max_main_context_peak_tokens": target_peak_tokens,
        },
    }
    fingerprint_after = _workspace_fingerprint(root)
    changed_paths = sorted(
        path
        for path in set(fingerprint_before) | set(fingerprint_after)
        if fingerprint_before.get(path) != fingerprint_after.get(path)
    )
    input_check = {"passed": not changed_paths, "changed_paths": changed_paths}
    checks = {
        "input_bindings": {"passed": True},
        "runtime_surface": runtime_surface_check,
        "old_baseline_provenance": old_baseline_check,
        "workflow_coverage": workflow_check,
        "contract_integrity": contract_check,
        "scenario_execution": execution_check,
        "required_recall": recall_check,
        "condition_coverage": condition_check,
        "hydration_integrity": hydration_check,
        "blocking_gaps": blocking_gap_check,
        "future_information": future_check,
        "projection_integrity": projection_check,
        "semantic_equivalence": semantic_check,
        "measurement_trace": measurement_check,
        "proxy_targets": proxy_check,
        "context_peak_tokens": context_peak_check,
        "input_read_only": input_check,
    }
    acceptance_complete = (
        not missing
        and not failed_contracts
        and len(completed_records) == len(scenarios)
        and not missing_condition_categories
        and not missing_targets
        and measurement_check["passed"]
        and not missing_semantic_scenarios
    )
    passed = acceptance_complete and all(check["passed"] for check in checks.values())
    report = {
        "schema_version": SCHEMA_VERSION,
        "status": "passed" if passed else "failed",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "workspace_root": str(root),
        "scenario_manifest": str(scenarios_path.resolve()),
        "inputs": input_records,
        "acceptance": {
            "status": "passed" if passed else "failed",
            "complete": acceptance_complete,
            "passed": passed,
            "future_information_backfill": False,
        },
        "checks": checks,
        "baseline": {
            "status": "observed_trace" if measurement_check["passed"] else "incomplete",
            "trace_id": measurement_check["trace_id"],
            "raw_tool_payload_characters": baseline_raw,
            "main_context_characters": baseline_context,
            "main_context_peak_tokens": baseline_tokens,
        },
        "candidate_observation": {
            "status": "observed_trace" if measurement_check["passed"] else "incomplete",
            "trace_id": measurement_check["trace_id"],
            "raw_tool_payload_characters": raw_payload_characters,
            "main_context_characters": candidate_context_characters,
            "main_context_peak_tokens": candidate_peak_tokens,
        },
        "proxy_metrics": proxy_metrics,
        "scenarios": scenario_records,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Replay frozen A-share research scenarios in an isolated workspace.")
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--scenarios", required=True, type=Path)
    parser.add_argument("--old-baseline", required=True, type=Path)
    parser.add_argument("--migration-report", required=True, type=Path)
    parser.add_argument("--measurement-trace", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        report = replay(
            args.workspace,
            args.scenarios,
            args.old_baseline,
            args.migration_report,
            args.measurement_trace,
            args.output,
        )
        print(
            json.dumps(
                {
                    "status": report["status"],
                    "report": str(args.output.resolve()),
                    "acceptance_complete": report["acceptance"]["complete"],
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
        return 0 if report["acceptance"]["passed"] else 1
    except (OSError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        print(f"shadow_replay_workspace: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
