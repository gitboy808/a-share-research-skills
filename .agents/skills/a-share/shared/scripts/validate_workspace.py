#!/usr/bin/env python3
"""Read-only validation for the workspace-bound A-share skill suite."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import date, datetime
from pathlib import Path


SCHEMA_VERSION = "a-share-workspace-v3"
REQUIRED_PATHS = [
    "AGENTS.md",
    "CONTEXT.md",
    "研究规则.md",
    "经验库.md",
    "当前判断.md",
    "观察池.md",
    "对象档案/索引.md",
    "策略库/索引.md",
    "模板/工作集清单模板.md",
    ".agents/skills/a-share/shared/context/__init__.py",
    ".agents/skills/a-share/shared/scripts/context_workspace.py",
]
ARTIFACT_DIRS = ["证据包", "策略库", "运行记录"]
HISTORICAL_RECORD_DIRS = ["判断日志", "对象档案", "报告", "周收敛", "观察日志"]
LEGACY_RUNTIME_DIRECTORIES = ["分析报告", "调研报告", "复盘报告", "扫描报告"]
SKILLS = [
    "a-share-research",
    "a-share-scan",
    "a-share-investigate",
    "a-share-analyze",
    "a-share-review",
    "a-share-meta-review",
]
LIVE_TIMESTAMP_ARTIFACTS = {
    "evidence_package",
    "run_record",
    "presentation_report",
    "judgment_log",
    "object_dossier",
    "meta_review",
    "observation_log",
    "workset_manifest",
}
TIMESTAMP_PATTERN = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?(?:Z|[+-][0-9]{2}:[0-9]{2})$"
)


def find_root(start: Path) -> Path:
    for candidate in [start.resolve(), *start.resolve().parents]:
        if (candidate / "CONTEXT.md").is_file() and (candidate / "研究规则.md").is_file():
            return candidate
    raise ValueError("research workspace markers not found")


def parse_frontmatter(path: Path) -> dict[str, str] | None:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        return None
    end = text.find("\n---\n", 4)
    if end < 0:
        return None
    result: dict[str, str] = {}
    for raw in text[4:end].splitlines():
        if not raw.strip() or raw.lstrip().startswith("#") or ":" not in raw:
            continue
        key, value = raw.split(":", 1)
        result[key.strip()] = value.strip().strip('"\'')
    return result


def parse_timezone_timestamp(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not TIMESTAMP_PATTERN.fullmatch(text):
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


def has_valid_review_date(value: str) -> bool:
    for candidate in re.findall(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        try:
            date.fromisoformat(candidate)
        except ValueError:
            continue
        return True
    return False


def migration_missing_fields(values: dict[str, object]) -> set[str]:
    raw = values.get("migration_missing_fields")
    if isinstance(raw, list):
        return {str(item).strip() for item in raw if str(item).strip()}
    text = str(raw or "").strip()
    if not text:
        return set()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, list):
        return {str(item).strip() for item in parsed if str(item).strip()}
    return {item.strip() for item in re.split(r"[,;、]", text) if item.strip()}


def is_historical_migration_run_record(values: dict[str, object]) -> bool:
    missing = migration_missing_fields(values)
    unknown_timestamp_fields = {
        field
        for field in ("created_at", "information_cutoff")
        if values.get(field) == "当时未记录"
    }
    return (
        values.get("status") == "partial"
        and values.get("workflows") == "historical"
        and isinstance(values.get("migration_note"), str)
        and bool(str(values.get("migration_note")).strip())
        and bool(missing)
        and unknown_timestamp_fields <= missing
    )


def is_historical_migration_workset(values: dict[str, object]) -> bool:
    verification = values.get("verification")
    coverage = values.get("coverage")
    gaps = values.get("gaps")
    quality = values.get("quality")
    legacy_snapshot = values.get("legacy_snapshot")
    if not all(
        isinstance(item, dict)
        for item in (verification, coverage, quality, legacy_snapshot)
    ) or not isinstance(gaps, list):
        return False
    blocking_gaps = sum(
        1 for gap in gaps if isinstance(gap, dict) and gap.get("blocking") is True
    )
    return (
        values.get("workflow") == "historical"
        and values.get("status") == "partial"
        and verification.get("status") == "not_run"
        and isinstance(values.get("migration_note"), str)
        and bool(str(values.get("migration_note")).strip())
        and coverage.get("required_total") == 0
        and coverage.get("required_covered") == 0
        and coverage.get("required_missing") == 0
        and coverage.get("coverage_ratio") == 0.0
        and coverage.get("blocking") is True
        and coverage.get("blocking_gap_count") == blocking_gaps
        and blocking_gaps > 0
        and quality.get("projection_degraded") is True
        and values.get("stable_references") == []
        and values.get("relations") == []
        and verification.get("required_unit_ids") == []
        and verification.get("verified_unit_ids") == []
        and verification.get("missing_references") == []
        and legacy_snapshot.get("hydrate_eligible") is False
    )


def validate_artifact_timestamps(
    relative: Path,
    artifact_type: str | None,
    values: dict[str, object],
    errors: list[str],
) -> None:
    if artifact_type == "historical_record":
        for field in ("created_at", "information_cutoff"):
            value = values.get(field)
            if value and value != "当时未记录" and parse_timezone_timestamp(value) is None:
                errors.append(
                    f"{relative}: historical {field} must be ISO-8601 with timezone or 当时未记录"
                )
        return
    if artifact_type not in LIVE_TIMESTAMP_ARTIFACTS:
        return
    controlled_historical_unknown = (
        artifact_type == "run_record" and is_historical_migration_run_record(values)
    ) or (
        artifact_type == "workset_manifest" and is_historical_migration_workset(values)
    )
    for field in ("created_at", "information_cutoff"):
        value = values.get(field)
        if value and parse_timezone_timestamp(value) is None and not (
            value == "当时未记录" and controlled_historical_unknown
        ):
            errors.append(f"{relative}: {field} must be ISO-8601 with timezone")


def check_markdown_links(root: Path, path: Path) -> list[str]:
    errors: list[str] = []
    text = path.read_text(encoding="utf-8")
    for target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", text):
        clean = target.strip("<>").split("#", 1)[0]
        if not clean or re.match(r"^[a-z]+://", clean) or clean.startswith("/"):
            continue
        resolved = (path.parent / clean).resolve()
        if root not in [resolved, *resolved.parents]:
            errors.append(f"{path.relative_to(root)}: link escapes workspace: {target}")
        elif not resolved.exists():
            errors.append(f"{path.relative_to(root)}: broken link: {target}")
    return errors


def validate_artifact_header(
    *,
    root: Path,
    path: Path,
    directory: str,
    values: dict[str, str],
    schemas: dict[str, dict[str, object]],
    allowed: dict[str, list[str]],
    errors: list[str],
) -> tuple[str, dict[str, object]] | None:
    """Apply schema/registry fields shared by every Markdown artifact."""

    relative = path.relative_to(root)
    artifact_type = values.get("artifact_type", "")
    schema = schemas.get(artifact_type)
    if schema is None:
        errors.append(f"{relative}: unknown artifact_type {artifact_type}")
        return None
    directories = schema.get("directories", [])
    if directories and directory not in directories:
        errors.append(f"{relative}: unsupported artifact_type {artifact_type} for {directory}")
        return None
    for key in schema.get("required", []):
        if not values.get(str(key)):
            errors.append(f"{relative}: missing frontmatter field {key}")
    if values.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"{relative}: unsupported schema_version")
    validate_artifact_timestamps(relative, artifact_type, values, errors)
    if values.get("status") not in allowed.get(artifact_type, []):
        errors.append(f"{relative}: invalid status {values.get('status')}")
    pattern_fields = (("id", "id_pattern"), ("version", "version_pattern"))
    for field, schema_key in pattern_fields:
        pattern = schema.get(schema_key)
        if pattern and not re.fullmatch(str(pattern), values.get(field, "")):
            errors.append(f"{relative}: invalid {artifact_type} {field} {values.get(field)}")
    if schema.get("filename_from_id"):
        expected = f"{values.get('id', '')}.md"
        if path.name != expected:
            errors.append(f"{relative}: filename must be {expected}")
    for field in ("stage", "authority", "record_kind", "write_stages", "field_authority"):
        expected = schema.get(field)
        if expected and values.get(field) != expected:
            errors.append(f"{relative}: invalid {field} {values.get(field)}")
    stages = schema.get("record_kind_stages", {})
    record_kind = values.get("record_kind")
    if stages and record_kind not in stages:
        errors.append(f"{relative}: invalid record_kind {record_kind}")
    elif stages and values.get("stage") != stages[record_kind]:
        errors.append(f"{relative}: stage {values.get('stage')} does not match {record_kind}")
    return artifact_type, schema


def validate_workspace(root_input: Path) -> dict[str, object]:
    """Validate one workspace without selecting or hydrating a workset."""

    errors: list[str] = []
    warnings: list[str] = []
    try:
        root = find_root(root_input)
    except ValueError as exc:
        errors.append(str(exc))
        root = root_input.resolve()

    for rel in REQUIRED_PATHS:
        if not (root / rel).exists():
            errors.append(f"missing required path: {rel}")

    if (root / ".agents/skills/fenxi").exists():
        errors.append("legacy skill directory still exists: .agents/skills/fenxi")
    for directory in LEGACY_RUNTIME_DIRECTORIES:
        if (root / directory).exists():
            errors.append(f"legacy runtime directory still exists: {directory}")

    suite_root = root / ".agents/skills/a-share"
    for skill_name in SKILLS:
        skill_dir = suite_root / skill_name
        skill_md = skill_dir / "SKILL.md"
        openai_yaml = skill_dir / "agents/openai.yaml"
        if not skill_md.exists():
            errors.append(f"missing skill: {skill_name}/SKILL.md")
            continue
        frontmatter = parse_frontmatter(skill_md)
        if frontmatter is None:
            errors.append(f"{skill_name}: invalid SKILL.md frontmatter")
        else:
            if frontmatter.get("name") != skill_name:
                errors.append(f"{skill_name}: frontmatter name mismatch")
            if not frontmatter.get("description"):
                errors.append(f"{skill_name}: missing description")
            unexpected = set(frontmatter) - {"name", "description"}
            if unexpected:
                errors.append(f"{skill_name}: unexpected frontmatter keys {sorted(unexpected)}")
        if not openai_yaml.exists():
            errors.append(f"{skill_name}: missing agents/openai.yaml")
        else:
            metadata = openai_yaml.read_text(encoding="utf-8")
            expected_policy = "true" if skill_name == "a-share-research" else "false"
            if f"allow_implicit_invocation: {expected_policy}" not in metadata:
                errors.append(f"{skill_name}: incorrect implicit invocation policy")
            if f"${skill_name}" not in metadata:
                errors.append(f"{skill_name}: default_prompt does not name the skill")
        if "TODO" in skill_md.read_text(encoding="utf-8"):
            errors.append(f"{skill_name}: unresolved TODO")

    context_root = suite_root / "shared/context"
    instantiate_task_evidence_runtime = None
    normalize_instantiated_requirements_runtime = None
    if context_root.is_dir():
        for path in context_root.glob("*.py"):
            if "TODO" in path.read_text(encoding="utf-8"):
                errors.append(f"{path.relative_to(root)}: unresolved TODO")
        shared_runtime_root = str(suite_root / "shared")
        if shared_runtime_root not in sys.path:
            sys.path.insert(0, shared_runtime_root)
        try:
            from context.contracts import (  # type: ignore[import-not-found]
                instantiate_task_evidence as instantiate_task_evidence_runtime,
                normalize_instantiated_requirements as normalize_instantiated_requirements_runtime,
            )
        except (ImportError, OSError, SyntaxError, ValueError):
            instantiate_task_evidence_runtime = None
            normalize_instantiated_requirements_runtime = None

    rule_file = root / "研究规则.md"
    if rule_file.exists():
        rule_count = len(re.findall(r"^- \*\*R\d{2}\b", rule_file.read_text(encoding="utf-8"), re.M))
        if rule_count > 20:
            errors.append(f"research rules exceed 20: {rule_count}")

    lesson_file = root / "经验库.md"
    if lesson_file.exists():
        lesson_count = len(re.findall(r"^### L\d+\b", lesson_file.read_text(encoding="utf-8"), re.M))
        if lesson_count > 50:
            errors.append(f"market lessons exceed 50: {lesson_count}")

    observation_file = root / "观察池.md"
    if observation_file.exists():
        candidate_count = len(re.findall(r"^### C\d{8}-\d{3}\b", observation_file.read_text(encoding="utf-8"), re.M))
        if candidate_count > 20:
            errors.append(f"active observation candidates exceed 20: {candidate_count}")

    schema_path = root / ".agents/skills/a-share/shared/schemas/artifacts.json"
    schemas = {}
    allowed = {}
    definition: dict[str, object] = {}
    contract_schema_versions = {"a-share-task-contract-v1"}
    if schema_path.exists():
        definition = json.loads(schema_path.read_text(encoding="utf-8"))
        schemas = definition.get("artifacts", {})
        allowed = definition.get("allowed_status", {})
        contract_schema_versions = set(
            definition.get("task_contract", {}).get("accepted_schema_versions", contract_schema_versions)
        )
    else:
        errors.append("missing artifact schema")

    contracts_root = suite_root / "shared/contracts"
    registered_task_contracts: dict[tuple[str, str], dict[str, object]] = {}
    registered_task_contract_paths: dict[tuple[str, str], Path] = {}
    registered_task_contract_hashes: dict[tuple[str, str], str] = {}
    if contracts_root.exists():
        task_contract_required = definition.get("task_contract", {}).get(
            "required", ["schema_version", "contract_id", "version", "workflow", "stage", "required_evidence"]
        )
        for path in sorted(contracts_root.glob("*.json")):
            try:
                contract = json.loads(path.read_text(encoding="utf-8"))
                for key in task_contract_required:
                    if key not in contract or contract[key] in (None, ""):
                        errors.append(f"{path.relative_to(root)}: task contract missing {key}")
                if contract.get("schema_version") not in contract_schema_versions:
                    errors.append(f"{path.relative_to(root)}: unsupported task contract schema")
                requirements = contract.get("required_evidence")
                if not isinstance(requirements, list):
                    errors.append(f"{path.relative_to(root)}: required_evidence must be a list")
                elif not requirements:
                    errors.append(
                        f"{path.relative_to(root)}: required_evidence must contain at least one requirement"
                    )
                else:
                    if not any(
                        isinstance(requirement, dict) and requirement.get("required", True) is not False
                        for requirement in requirements
                    ):
                        errors.append(
                            f"{path.relative_to(root)}: required_evidence must contain at least one required requirement"
                        )
                    seen_requirements: set[str] = set()
                    for requirement in requirements:
                        requirement_id = str(requirement.get("requirement_id", "")) if isinstance(requirement, dict) else ""
                        if not requirement_id:
                            errors.append(f"{path.relative_to(root)}: requirement missing requirement_id")
                        elif requirement_id in seen_requirements:
                            errors.append(f"{path.relative_to(root)}: duplicate requirement_id {requirement_id}")
                        seen_requirements.add(requirement_id)
                        if isinstance(requirement, dict) and requirement.get(
                            "eligibility_mode"
                        ) not in {
                            "prospective_current",
                            "historical_as_of",
                            "calibration_window",
                        }:
                            errors.append(
                                f"{path.relative_to(root)}: requirement {requirement_id} requires a supported eligibility_mode"
                            )
                registration_valid = (
                    contract.get("schema_version") in contract_schema_versions
                    and all(
                        key in contract and contract[key] not in (None, "")
                        for key in task_contract_required
                    )
                    and isinstance(requirements, list)
                    and bool(requirements)
                    and any(
                        isinstance(requirement, dict)
                        and requirement.get("required", True) is not False
                        for requirement in requirements
                    )
                    and all(
                        isinstance(requirement, dict)
                        and bool(str(requirement.get("requirement_id", "")).strip())
                        and requirement.get("eligibility_mode")
                        in {
                            "prospective_current",
                            "historical_as_of",
                            "calibration_window",
                        }
                        for requirement in requirements
                    )
                    and len(
                        {
                            str(requirement["requirement_id"])
                            for requirement in requirements
                            if isinstance(requirement, dict) and requirement.get("requirement_id")
                        }
                    )
                    == len(requirements)
                )
                if registration_valid:
                    contract_key = (str(contract["contract_id"]), str(contract["version"]))
                    if contract_key in registered_task_contracts:
                        errors.append(
                            f"{path.relative_to(root)}: duplicate registered task contract {contract_key[0]}@{contract_key[1]}"
                        )
                    else:
                        registered_task_contracts[contract_key] = contract
                        registered_task_contract_paths[contract_key] = path
                        registered_task_contract_hashes[contract_key] = hashlib.sha256(
                            path.read_bytes()
                        ).hexdigest()
            except (OSError, json.JSONDecodeError, TypeError):
                errors.append(f"{path.relative_to(root)}: invalid task contract JSON")

    declared: dict[tuple[str, str, str], Path] = {}
    strategy_declarations: dict[tuple[str, str], tuple[Path, dict[str, str]]] = {}
    evidence_atomic_declarations: dict[str, Path] = {}
    stable_references: set[str] = set()
    pending_source_references: list[tuple[Path, str, str]] = []
    pending_judgment_evidence_references: list[tuple[Path, str, str]] = []
    historical_migration_runs: dict[str, Path] = {}
    for directory in ARTIFACT_DIRS:
        base = root / directory
        if not base.exists():
            errors.append(f"missing artifact directory: {directory}")
            continue
        for path in sorted(base.rglob("*.md")):
            if path.name in {"索引.md", "README.md"}:
                continue
            frontmatter = parse_frontmatter(path)
            if frontmatter is None:
                errors.append(f"{path.relative_to(root)}: authoritative artifact missing frontmatter")
                continue
            header = validate_artifact_header(
                root=root,
                path=path,
                directory=directory,
                values=frontmatter,
                schemas=schemas,
                allowed=allowed,
                errors=errors,
            )
            if header is None:
                continue
            artifact_type, artifact_schema = header
            if artifact_type == "run_record" and frontmatter.get("workflows") == "historical":
                run_id = str(frontmatter.get("id", ""))
                historical_migration_runs[run_id] = path
                if not is_historical_migration_run_record(frontmatter):
                    errors.append(
                        f"{path.relative_to(root)}: historical migration run must remain partial with explicit migration note and missing fields"
                    )
            key = (artifact_type, frontmatter.get("id", ""), frontmatter.get("version", ""))
            if key in declared:
                errors.append(f"duplicate declaration {key}: {declared[key].relative_to(root)} and {path.relative_to(root)}")
            declared[key] = path
            if artifact_type == "strategy_version":
                strategy_declarations[(frontmatter.get("id", ""), frontmatter.get("version", ""))] = (
                    path,
                    frontmatter,
                )
                stable_references.add(
                    f"atom:{frontmatter.get('id', '')}@{frontmatter.get('version', '')}"
                )
            atomic_schema = schemas[artifact_type].get("atomic_items", {})
            if atomic_schema:
                text = path.read_text(encoding="utf-8")
                headings = list(
                    re.finditer(
                        atomic_schema.get(
                            "heading_pattern", r"^###\s+(EVI-[A-Za-z0-9-]+#\d+)\s*$"
                        ),
                        text,
                        re.MULTILINE,
                    )
                )
                for index, heading in enumerate(headings):
                    atomic_id = heading.group(1)
                    if artifact_type == "evidence_package" and atomic_id in evidence_atomic_declarations:
                        errors.append(
                            f"{path.relative_to(root)}: duplicate evidence atomic id {atomic_id}; first declared in {evidence_atomic_declarations[atomic_id].relative_to(root)}"
                        )
                    elif artifact_type == "evidence_package":
                        evidence_atomic_declarations[atomic_id] = path
                    stable_references.add(f"atom:{atomic_id}")
                    end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
                    section = text[heading.end() : end]
                    fields = {
                        match.group(1).strip(): match.group(2).strip()
                        for match in re.finditer(
                            r"^-[ \t]+\*\*([^*]+)\*\*[：:][ \t]*(.*?)[ \t]*$", section, re.MULTILINE
                        )
                    }
                    for field in atomic_schema.get("required_fields", []):
                        if not fields.get(field):
                            errors.append(
                                f"{path.relative_to(root)}: {heading.group(1)} missing atomic field {field}"
                            )
                    status_field = atomic_schema.get("status_field")
                    evidence_status = fields.get(status_field, "")
                    allowed_atomic_statuses = set(atomic_schema.get("allowed_statuses", []))
                    if evidence_status and allowed_atomic_statuses and evidence_status not in allowed_atomic_statuses:
                        errors.append(
                            f"{path.relative_to(root)}: {heading.group(1)} invalid evidence status {evidence_status}"
                        )
                    expiry_field = atomic_schema.get("expiry_field")
                    expiry_value = fields.get(expiry_field, "")
                    if expiry_value and not has_valid_review_date(expiry_value):
                        errors.append(
                            f"{path.relative_to(root)}: {heading.group(1)} expiry/review must contain a valid YYYY-MM-DD date"
                        )
                    source_group_field = atomic_schema.get("source_group_field")
                    source_group_value = fields.get(source_group_field, "")
                    source_group_pattern = atomic_schema.get("source_group_pattern")
                    if source_group_value and source_group_pattern and not re.fullmatch(
                        source_group_pattern, source_group_value
                    ):
                        errors.append(
                            f"{path.relative_to(root)}: {heading.group(1)} invalid source group id {source_group_value}"
                        )
                    locator_field = atomic_schema.get("source_locator_field")
                    locator_value = fields.get(locator_field, "")
                    if locator_value:
                        try:
                            locator = json.loads(locator_value.strip("`"))
                        except (json.JSONDecodeError, TypeError):
                            errors.append(
                                f"{path.relative_to(root)}: {heading.group(1)} source locator must be structured JSON"
                            )
                        else:
                            is_payload_locator = isinstance(locator, dict) and (
                                locator.get("kind") == "source_payload" or "payload_id" in locator
                            )
                            is_url_locator = isinstance(locator, dict) and (
                                locator.get("kind") == "remote_url" or "url" in locator
                            )
                            if not is_payload_locator and not is_url_locator:
                                errors.append(
                                    f"{path.relative_to(root)}: {heading.group(1)} source locator is missing payload or URL coordinates"
                                )
                            elif is_payload_locator:
                                required_locator_fields = atomic_schema.get(
                                    "payload_locator_required",
                                    [
                                        "kind",
                                        "payload_id",
                                        "path",
                                        "sha256",
                                        "byte_length",
                                        "acquired_at",
                                        "line_start",
                                        "line_end",
                                    ],
                                )
                                for field in required_locator_fields:
                                    if field not in locator or locator[field] in (None, ""):
                                        errors.append(
                                            f"{path.relative_to(root)}: {heading.group(1)} payload locator missing {field}"
                                        )
                                if locator.get("kind") != "source_payload":
                                    errors.append(
                                        f"{path.relative_to(root)}: {heading.group(1)} payload locator kind must be source_payload"
                                    )
                                payload_id = str(locator.get("payload_id", ""))
                                if not re.fullmatch(r"[A-Za-z0-9._-]+", payload_id) or payload_id in {".", ".."}:
                                    errors.append(
                                        f"{path.relative_to(root)}: {heading.group(1)} invalid payload_id"
                                    )
                                sha256 = str(locator.get("sha256", ""))
                                byte_length = locator.get("byte_length")
                                line_start = locator.get("line_start")
                                line_end = locator.get("line_end")
                                if (
                                    not re.fullmatch(r"[0-9a-fA-F]{64}", sha256)
                                    or isinstance(byte_length, bool)
                                    or not isinstance(byte_length, int)
                                    or byte_length < 0
                                    or isinstance(line_start, bool)
                                    or not isinstance(line_start, int)
                                    or isinstance(line_end, bool)
                                    or not isinstance(line_end, int)
                                    or line_start < 1
                                    or line_end < line_start
                                ):
                                    errors.append(
                                        f"{path.relative_to(root)}: {heading.group(1)} invalid payload source coordinates"
                                    )
                                acquired_at = parse_timezone_timestamp(locator.get("acquired_at"))
                                if locator.get("acquired_at") and acquired_at is None:
                                    errors.append(
                                        f"{path.relative_to(root)}: {heading.group(1)} payload acquired_at must be ISO-8601 with timezone"
                                    )
                                information_cutoff = parse_timezone_timestamp(
                                    frontmatter.get("information_cutoff")
                                )
                                if acquired_at and information_cutoff and acquired_at > information_cutoff:
                                    errors.append(
                                        f"{path.relative_to(root)}: {heading.group(1)} payload acquired_at is after information_cutoff"
                                    )
                                relative_source = str(locator.get("path", ""))
                                if relative_source:
                                    relative_source_path = Path(relative_source)
                                    if (
                                        not relative_source_path.parts
                                        or relative_source_path.parts[0] != ".source-payloads"
                                        or relative_source_path.name != f"{payload_id}.payload"
                                    ):
                                        errors.append(
                                            f"{path.relative_to(root)}: {heading.group(1)} payload source must be externalized under .source-payloads and match payload_id"
                                        )
                                    source_path = (root / relative_source).resolve()
                                    if root not in [source_path, *source_path.parents]:
                                        errors.append(
                                            f"{path.relative_to(root)}: {heading.group(1)} payload source escapes workspace"
                                        )
                                    elif not source_path.is_file():
                                        errors.append(
                                            f"{path.relative_to(root)}: {heading.group(1)} payload source does not exist: {relative_source}"
                                        )
                                    else:
                                        metadata_relative = relative_source_path.with_suffix(".json")
                                        metadata_path = root / metadata_relative
                                        if not metadata_path.is_file():
                                            errors.append(
                                                f"{path.relative_to(root)}: {heading.group(1)} payload metadata sidecar does not exist: {metadata_relative.as_posix()}"
                                            )
                                        else:
                                            try:
                                                payload_metadata = json.loads(
                                                    metadata_path.read_text(encoding="utf-8")
                                                )
                                            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                                                errors.append(
                                                    f"{path.relative_to(root)}: {heading.group(1)} invalid payload metadata sidecar: {metadata_relative.as_posix()}"
                                                )
                                            else:
                                                if not isinstance(payload_metadata, dict):
                                                    errors.append(
                                                        f"{path.relative_to(root)}: {heading.group(1)} invalid payload metadata sidecar: {metadata_relative.as_posix()}"
                                                    )
                                                else:
                                                    if (
                                                        payload_metadata.get("kind") != "source_payload"
                                                        or payload_metadata.get("payload_id")
                                                        != locator.get("payload_id")
                                                        or payload_metadata.get("path") != relative_source
                                                    ):
                                                        errors.append(
                                                            f"{path.relative_to(root)}: {heading.group(1)} payload metadata identity does not match locator"
                                                        )
                                                    if payload_metadata.get("acquired_at") != locator.get(
                                                        "acquired_at"
                                                    ):
                                                        errors.append(
                                                            f"{path.relative_to(root)}: {heading.group(1)} payload metadata acquired_at does not match locator"
                                                        )
                                                    if payload_metadata.get("sha256") != locator.get(
                                                        "sha256"
                                                    ):
                                                        errors.append(
                                                            f"{path.relative_to(root)}: {heading.group(1)} payload metadata sha256 does not match locator or payload"
                                                        )
                                                    if payload_metadata.get("byte_length") != locator.get(
                                                        "byte_length"
                                                    ):
                                                        errors.append(
                                                            f"{path.relative_to(root)}: {heading.group(1)} payload metadata byte_length does not match locator or payload"
                                                        )
                                        content = source_path.read_bytes()
                                        if re.fullmatch(r"[0-9a-fA-F]{64}", sha256) and hashlib.sha256(
                                            content
                                        ).hexdigest() != sha256.lower():
                                            errors.append(
                                                f"{path.relative_to(root)}: {heading.group(1)} payload source sha256 mismatch"
                                            )
                                        if isinstance(byte_length, int) and not isinstance(
                                            byte_length, bool
                                        ) and len(content) != byte_length:
                                            errors.append(
                                                f"{path.relative_to(root)}: {heading.group(1)} payload source byte_length mismatch"
                                            )
                                        try:
                                            source_text = content.decode("utf-8")
                                        except UnicodeDecodeError:
                                            errors.append(
                                                f"{path.relative_to(root)}: {heading.group(1)} payload source must be UTF-8 text"
                                            )
                                        else:
                                            if isinstance(line_end, int) and not isinstance(
                                                line_end, bool
                                            ) and line_end > len(source_text.splitlines()):
                                                errors.append(
                                                    f"{path.relative_to(root)}: {heading.group(1)} payload line range exceeds source"
                                                )
                            else:
                                url = str(locator.get("url", ""))
                                anchor = str(locator.get("anchor", "")).strip()
                                if not re.match(r"^https?://", url) or not anchor:
                                    errors.append(
                                        f"{path.relative_to(root)}: {heading.group(1)} invalid URL source coordinates"
                                    )
                            payload_required_statuses = set(
                                atomic_schema.get("payload_required_statuses", [])
                            )
                            remote_url_statuses = set(atomic_schema.get("remote_url_statuses", []))
                            if evidence_status in payload_required_statuses and not is_payload_locator:
                                errors.append(
                                    f"{path.relative_to(root)}: {heading.group(1)} status {evidence_status} requires a source_payload locator"
                                )
                            if is_url_locator and evidence_status and evidence_status not in remote_url_statuses:
                                if evidence_status not in payload_required_statuses:
                                    errors.append(
                                        f"{path.relative_to(root)}: {heading.group(1)} URL locator is allowed only for unconfirmed evidence"
                                    )

    strategy_schema = schemas.get("strategy_version", {})
    initial_strategy_version = strategy_schema.get("initial_version")
    for (strategy_id, version), (path, frontmatter) in sorted(strategy_declarations.items()):
        expected_filename = f"{strategy_id}-v{version}.md"
        if path.name != expected_filename:
            errors.append(f"{path.relative_to(root)}: strategy filename must be {expected_filename}")
        if version == initial_strategy_version:
            continue
        previous_version = frontmatter.get("previous_version")
        if not previous_version:
            errors.append(f"{path.relative_to(root)}: non-initial strategy version missing previous_version")
        elif (strategy_id, previous_version) not in strategy_declarations:
            errors.append(
                f"{path.relative_to(root)}: previous strategy version does not resolve: {strategy_id}@{previous_version}"
            )
        elif tuple(map(int, previous_version.split("."))) >= tuple(map(int, version.split("."))):
            errors.append(
                f"{path.relative_to(root)}: previous strategy version must be earlier: {strategy_id}@{previous_version}"
            )

    judgment_versions: dict[str, list[tuple[int, Path]]] = {}
    observation_versions: dict[str, list[tuple[int, Path]]] = {}
    for directory in HISTORICAL_RECORD_DIRS:
        base = root / directory
        if not base.exists():
            continue
        for path in base.rglob("*.md"):
            if path.name in {"索引.md", "README.md"}:
                continue
            frontmatter = parse_frontmatter(path)
            if frontmatter is None:
                errors.append(f"{path.relative_to(root)}: historical record missing frontmatter")
                continue
            header = validate_artifact_header(
                root=root,
                path=path,
                directory=directory,
                values=frontmatter,
                schemas=schemas,
                allowed=allowed,
                errors=errors,
            )
            if header is None:
                continue
            artifact_type, artifact_schema = header
            if "source_refs" in artifact_schema.get("required", []):
                pending_source_references.append(
                    (
                        path,
                        frontmatter.get("source_refs", ""),
                        frontmatter.get("source_refs_unknown_reason", ""),
                    )
                )
            atomic_schema = artifact_schema.get("atomic_items", {})
            if atomic_schema:
                text = path.read_text(encoding="utf-8")
                atomic_headings = list(
                    re.finditer(atomic_schema.get("heading_pattern", r"$^"), text, re.MULTILINE)
                )
                for index, atomic_heading in enumerate(atomic_headings):
                    end = (
                        atomic_headings[index + 1].start()
                        if index + 1 < len(atomic_headings)
                        else len(text)
                    )
                    section = text[atomic_heading.end() : end]
                    fields = {
                        match.group(1).strip(): match.group(2).strip()
                        for match in re.finditer(
                            r"^-[ \t]+\*\*([^*]+)\*\*[：:][ \t]*(.*?)[ \t]*$",
                            section,
                            re.MULTILINE,
                        )
                    }
                    for field in atomic_schema.get("required_fields", []):
                        if not fields.get(field):
                            errors.append(
                                f"{path.relative_to(root)}: {atomic_heading.group(1)} missing atomic field {field}"
                            )
                    for field in atomic_schema.get("timestamp_fields", []):
                        value = fields.get(field, "")
                        timestamp = value.split("；", 1)[0].split(";", 1)[0].strip()
                        if value and parse_timezone_timestamp(timestamp) is None:
                            errors.append(
                                f"{path.relative_to(root)}: {atomic_heading.group(1)} {field} must be ISO-8601 with timezone"
                            )
                    result_status_field = atomic_schema.get("result_status_field")
                    result_recorded_at_field = atomic_schema.get(
                        "result_recorded_at_field"
                    )
                    result_status = fields.get(result_status_field, "")
                    result_recorded_at = fields.get(result_recorded_at_field, "")
                    if result_recorded_at and parse_timezone_timestamp(
                        result_recorded_at
                    ) is None:
                        errors.append(
                            f"{path.relative_to(root)}: {atomic_heading.group(1)} {result_recorded_at_field} must be ISO-8601 with timezone"
                        )
                    if (
                        result_status
                        in set(atomic_schema.get("terminal_result_statuses", []))
                        and not result_recorded_at
                    ):
                        errors.append(
                            f"{path.relative_to(root)}: {atomic_heading.group(1)} terminal {result_status_field} requires {result_recorded_at_field}"
                        )
                    evidence_reference_field = atomic_schema.get("evidence_reference_field")
                    evidence_reference_value = fields.get(evidence_reference_field, "")
                    if evidence_reference_value:
                        evidence_ids = re.findall(
                            r"EVI-[A-Za-z0-9-]+#[0-9]+", evidence_reference_value
                        )
                        explicit_abstention = re.match(
                            r"^unknown\s*[—:-]\s*正式弃权\s*[；;，,：:]\s*\S+",
                            evidence_reference_value,
                        )
                        if not evidence_ids and not explicit_abstention:
                            errors.append(
                                f"{path.relative_to(root)}: {atomic_heading.group(1)} evidence field must contain a stable EVI atomic reference or explicit unknown—正式弃权 gap"
                            )
                        for evidence_id in evidence_ids:
                            pending_judgment_evidence_references.append(
                                (path, atomic_heading.group(1), f"atom:{evidence_id}")
                            )
            if artifact_type == "judgment_log":
                text = path.read_text(encoding="utf-8")
                for judgment_id, version_text in re.findall(
                    r"^###\s+(J\d{8}-\d{3})\s+v(\d+)(?:\s*[｜|].+)?\s*$", text, re.MULTILINE
                ):
                    judgment_versions.setdefault(judgment_id, []).append((int(version_text), path))
                    stable_references.add(f"atom:{judgment_id} v{version_text}")
            elif artifact_type == "observation_log":
                text = path.read_text(encoding="utf-8")
                for candidate_id, version_text in re.findall(
                    r"^###\s+(C\d{8}-\d{3})\s+v(\d+)(?:\s*[｜|].+)?\s*$", text, re.MULTILINE
                ):
                    stable_references.add(f"atom:{candidate_id} v{version_text}")
                    observation_versions.setdefault(candidate_id, []).append((int(version_text), path))

    for judgment_id, declarations in sorted(judgment_versions.items()):
        versions = [version for version, _ in declarations]
        first_path = declarations[0][1]
        if len(versions) != len(set(versions)):
            errors.append(
                f"{first_path.relative_to(root)}: duplicate judgment version for {judgment_id}: {sorted(versions)}"
            )
            continue
        ordered = sorted(versions)
        expected = list(range(1, ordered[-1] + 1))
        if ordered != expected:
            errors.append(
                f"{first_path.relative_to(root)}: non-contiguous version chain for {judgment_id}: {ordered}"
            )

    for candidate_id, declarations in sorted(observation_versions.items()):
        versions = [version for version, _ in declarations]
        first_path = declarations[0][1]
        if len(versions) != len(set(versions)):
            errors.append(
                f"{first_path.relative_to(root)}: duplicate observation version for {candidate_id}: {sorted(versions)}"
            )
            continue
        ordered = sorted(versions)
        expected = list(range(1, ordered[-1] + 1))
        if ordered != expected:
            errors.append(
                f"{first_path.relative_to(root)}: non-contiguous version chain for {candidate_id}: {ordered}"
            )

    for path, source_refs, unknown_reason in pending_source_references:
        if source_refs == "unknown":
            if not unknown_reason:
                errors.append(f"{path.relative_to(root)}: unknown source_refs require source_refs_unknown_reason")
            continue
        refs = [item.strip() for item in re.split(r"[,;、]", source_refs) if item.strip()]
        for reference in refs:
            if not reference.startswith("atom:"):
                errors.append(f"{path.relative_to(root)}: source_refs must contain stable atom refs: {reference}")
            elif reference not in stable_references:
                errors.append(f"{path.relative_to(root)}: source reference does not resolve: {reference}")

    for path, judgment_id, evidence_reference in pending_judgment_evidence_references:
        if evidence_reference not in stable_references:
            errors.append(
                f"{path.relative_to(root)}: {judgment_id} evidence reference does not resolve: {evidence_reference}"
            )

    # Workset manifests are JSON sidecars next to persistent run records. They
    # contain only stable references and audit fields, never hydrated text.
    run_root = root / "运行记录"
    worksets_by_run: dict[str, list[tuple[Path, dict[str, object]]]] = {}
    if run_root.exists():
        workset_attempt_groups: dict[
            tuple[str, str, str], list[tuple[Path, int, str, object]]
        ] = {}
        for path in run_root.rglob("*.json"):
            try:
                manifest = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                errors.append(f"{path.relative_to(root)}: invalid JSON workset manifest")
                continue
            if manifest.get("artifact_type") != "workset_manifest":
                continue
            historical_workset = is_historical_migration_workset(manifest)
            worksets_by_run.setdefault(str(manifest.get("run_id", "")), []).append(
                (path, manifest)
            )
            for key in schemas.get("workset_manifest", {}).get("required", []):
                if historical_workset and key in {
                    "contract_instantiation",
                    "instantiated_requirements",
                    "instantiated_requirements_sha256",
                    "audit_references",
                    "audited_exclusions",
                }:
                    continue
                if key not in manifest or manifest[key] in (None, ""):
                    errors.append(f"{path.relative_to(root)}: missing workset field {key}")
            if manifest.get("schema_version") != SCHEMA_VERSION:
                errors.append(f"{path.relative_to(root)}: unsupported workset schema_version")
            validate_artifact_timestamps(
                path.relative_to(root), "workset_manifest", manifest, errors
            )
            if manifest.get("workflow") == "historical" and not historical_workset:
                errors.append(
                    f"{path.relative_to(root)}: historical migration workset must remain partial/not_run/blocking with zero coverage"
                )
            legacy_snapshot = manifest.get("legacy_snapshot")
            if manifest.get("workflow") == "historical":
                if not isinstance(legacy_snapshot, dict):
                    errors.append(
                        f"{path.relative_to(root)}: historical migration workset requires an immutable legacy_snapshot"
                    )
                else:
                    legacy_fields = (
                        "stable_references",
                        "relations",
                        "gaps",
                        "coverage",
                    )
                    if any(field not in legacy_snapshot for field in legacy_fields):
                        errors.append(
                            f"{path.relative_to(root)}: legacy_snapshot is missing historical audit fields"
                        )
                    if legacy_snapshot.get("hydrate_eligible") is not False:
                        errors.append(
                            f"{path.relative_to(root)}: legacy_snapshot must never be hydrate eligible"
                        )
                    legacy_hash = str(
                        legacy_snapshot.get("canonical_sha256") or ""
                    )
                    if not re.fullmatch(r"[0-9a-f]{64}", legacy_hash):
                        errors.append(
                            f"{path.relative_to(root)}: legacy_snapshot canonical_sha256 must be a lowercase SHA-256"
                        )
                    elif all(field in legacy_snapshot for field in legacy_fields):
                        canonical_legacy = json.dumps(
                            {
                                field: legacy_snapshot[field]
                                for field in legacy_fields
                            },
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        )
                        if hashlib.sha256(
                            canonical_legacy.encode("utf-8")
                        ).hexdigest() != legacy_hash:
                            errors.append(
                                f"{path.relative_to(root)}: legacy_snapshot canonical_sha256 mismatch"
                            )
            elif legacy_snapshot is not None:
                errors.append(
                    f"{path.relative_to(root)}: live workset must not contain legacy_snapshot"
                )
            if manifest.get("status") not in allowed.get("workset_manifest", []):
                errors.append(f"{path.relative_to(root)}: invalid workset status {manifest.get('status')}")
            workset_schema = schemas.get("workset_manifest", {})
            task_contract = manifest.get("task_contract")
            task_contract_fields = workset_schema.get("task_contract", {}).get(
                "required", ["contract_id", "version"]
            )
            identity_fields = ["contract_id", "version"]
            if not isinstance(task_contract, dict) or any(
                not isinstance(task_contract.get(field), str)
                or not task_contract[field].strip()
                for field in identity_fields
            ):
                errors.append(
                    f"{path.relative_to(root)}: task_contract requires non-empty contract_id and version"
                )
            elif not historical_workset:
                contract_key = (
                    str(task_contract["contract_id"]),
                    str(task_contract["version"]),
                )
                registered_contract = registered_task_contracts.get(contract_key)
                if registered_contract is None:
                    errors.append(
                        f"{path.relative_to(root)}: task_contract {contract_key[0]}@{contract_key[1]} is not registered"
                    )
                elif (
                    registered_contract.get("workflow") != manifest.get("workflow")
                    or registered_contract.get("stage") != manifest.get("stage")
                ):
                    errors.append(
                        f"{path.relative_to(root)}: task_contract {contract_key[0]}@{contract_key[1]} belongs to {registered_contract.get('workflow')}/{registered_contract.get('stage')}, not {manifest.get('workflow')}/{manifest.get('stage')}"
                    )
                else:
                    registered_path = registered_task_contract_paths[contract_key]
                    expected_registry_path = registered_path.relative_to(root).as_posix()
                    if task_contract.get("registry_path") != expected_registry_path:
                        errors.append(
                            f"{path.relative_to(root)}: task_contract registry_path does not match the registered contract file"
                        )
                    declared_contract_hash = str(task_contract.get("sha256") or "")
                    if not re.fullmatch(r"[0-9a-f]{64}", declared_contract_hash):
                        errors.append(
                            f"{path.relative_to(root)}: task_contract sha256 must be a lowercase SHA-256"
                        )
                    elif declared_contract_hash != registered_task_contract_hashes[
                        contract_key
                    ]:
                        errors.append(
                            f"{path.relative_to(root)}: task_contract sha256 does not match the registered contract file"
                        )

                    recomputed_requirements: list[dict[str, object]] | None = None
                    contract_instantiation = manifest.get(
                        "contract_instantiation"
                    )
                    instantiation_fields = workset_schema.get(
                        "contract_instantiation", {}
                    ).get(
                        "required",
                        [
                            "schema_version",
                            "workflow",
                            "stage",
                            "information_cutoff",
                            "objects",
                            "handoff",
                            "strategy_version",
                            "task_conditions",
                            "sha256",
                        ],
                    )
                    instantiation_valid = (
                        isinstance(contract_instantiation, dict)
                        and set(contract_instantiation) == set(instantiation_fields)
                        and all(
                            field in contract_instantiation
                            for field in instantiation_fields
                        )
                        and isinstance(
                            contract_instantiation.get("schema_version"), str
                        )
                        and isinstance(
                            contract_instantiation.get("workflow"), str
                        )
                        and isinstance(contract_instantiation.get("stage"), str)
                        and isinstance(
                            contract_instantiation.get("information_cutoff"), str
                        )
                        and isinstance(contract_instantiation.get("objects"), list)
                        and isinstance(contract_instantiation.get("handoff"), dict)
                        and isinstance(
                            contract_instantiation.get("task_conditions"), list
                        )
                        and all(
                            isinstance(condition, dict)
                            for condition in contract_instantiation.get(
                                "task_conditions", []
                            )
                        )
                    )
                    if not instantiation_valid:
                        errors.append(
                            f"{path.relative_to(root)}: contract_instantiation must contain the complete normalized recomputation inputs"
                        )
                    else:
                        instantiation_payload = {
                            field: contract_instantiation[field]
                            for field in instantiation_fields
                            if field != "sha256"
                        }
                        declared_instantiation_hash = str(
                            contract_instantiation.get("sha256") or ""
                        )
                        actual_instantiation_hash = hashlib.sha256(
                            json.dumps(
                                instantiation_payload,
                                ensure_ascii=False,
                                sort_keys=True,
                                separators=(",", ":"),
                            ).encode("utf-8")
                        ).hexdigest()
                        if not re.fullmatch(
                            r"[0-9a-f]{64}", declared_instantiation_hash
                        ):
                            errors.append(
                                f"{path.relative_to(root)}: contract_instantiation sha256 must be a lowercase SHA-256"
                            )
                        elif declared_instantiation_hash != actual_instantiation_hash:
                            errors.append(
                                f"{path.relative_to(root)}: contract_instantiation sha256 mismatch"
                            )
                        if (
                            contract_instantiation["schema_version"]
                            != SCHEMA_VERSION
                            or contract_instantiation["workflow"]
                            != manifest.get("workflow")
                            or contract_instantiation["stage"]
                            != manifest.get("stage")
                            or contract_instantiation["information_cutoff"]
                            != manifest.get("information_cutoff")
                        ):
                            errors.append(
                                f"{path.relative_to(root)}: contract_instantiation does not match the workset stage snapshot"
                            )
                        if (
                            instantiate_task_evidence_runtime is None
                            or normalize_instantiated_requirements_runtime is None
                        ):
                            errors.append(
                                f"{path.relative_to(root)}: contract_instantiation cannot be recomputed by the installed runtime"
                            )
                        else:
                            recompute_run = {
                                "schema_version": contract_instantiation[
                                    "schema_version"
                                ],
                                "workflow": contract_instantiation["workflow"],
                                "stage": contract_instantiation["stage"],
                                "information_cutoff": contract_instantiation[
                                    "information_cutoff"
                                ],
                                "calibration_window_start": contract_instantiation[
                                    "calibration_window_start"
                                ],
                                "objects": contract_instantiation["objects"],
                                "handoff": contract_instantiation["handoff"],
                                "strategy_version": contract_instantiation[
                                    "strategy_version"
                                ],
                                "task_contract": str(registered_path),
                            }
                            try:
                                recomputed_task = instantiate_task_evidence_runtime(
                                    recompute_run,
                                    contract_instantiation["task_conditions"],
                                    root,
                                )
                                recomputed_requirements = normalize_instantiated_requirements_runtime(
                                    recomputed_task.get("required_evidence", [])
                                )
                            except (OSError, TypeError, ValueError) as exc:
                                errors.append(
                                    f"{path.relative_to(root)}: contract_instantiation cannot be recomputed: {exc}"
                                )

                    instantiated = manifest.get("instantiated_requirements")
                    instantiated_hash = str(
                        manifest.get("instantiated_requirements_sha256") or ""
                    )
                    valid_instantiated = isinstance(instantiated, list) and bool(
                        instantiated
                    )
                    if not valid_instantiated or not all(
                        isinstance(requirement, dict)
                        and bool(str(requirement.get("requirement_id") or "").strip())
                        and all(
                            isinstance(requirement.get(field), list)
                            for field in ("unit_types", "objects", "fields", "roles")
                        )
                        and isinstance(requirement.get("required"), bool)
                        and isinstance(requirement.get("allow_unknown"), bool)
                        for requirement in (instantiated or [])
                    ):
                        errors.append(
                            f"{path.relative_to(root)}: instantiated_requirements must be a non-empty list of complete normalized requirements"
                        )
                    else:
                        requirement_ids = [
                            str(requirement["requirement_id"])
                            for requirement in instantiated
                        ]
                        if len(requirement_ids) != len(set(requirement_ids)):
                            errors.append(
                                f"{path.relative_to(root)}: instantiated_requirements contain duplicate requirement_id"
                            )
                        canonical_requirements = json.dumps(
                            instantiated,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        )
                        actual_instantiated_hash = hashlib.sha256(
                            canonical_requirements.encode("utf-8")
                        ).hexdigest()
                        if not re.fullmatch(r"[0-9a-f]{64}", instantiated_hash):
                            errors.append(
                                f"{path.relative_to(root)}: instantiated_requirements_sha256 must be a lowercase SHA-256"
                            )
                        elif instantiated_hash != actual_instantiated_hash:
                            errors.append(
                                f"{path.relative_to(root)}: instantiated_requirements_sha256 does not match instantiated_requirements"
                            )
                        if (
                            recomputed_requirements is not None
                            and instantiated != recomputed_requirements
                        ):
                            errors.append(
                                f"{path.relative_to(root)}: instantiated_requirements do not exactly match recomputation from contract_instantiation"
                            )

                        for base in registered_contract.get("required_evidence", []):
                            if not isinstance(base, dict):
                                continue
                            base_id = str(base.get("requirement_id") or "")
                            candidates = [
                                requirement
                                for requirement in instantiated
                                if str(
                                    requirement.get(
                                        "base_requirement_id",
                                        requirement.get("requirement_id", ""),
                                    )
                                )
                                == base_id
                            ]
                            if not candidates:
                                errors.append(
                                    f"{path.relative_to(root)}: instantiated requirements omit registered floor {base_id}"
                                )
                                continue
                            for requirement in candidates:
                                requirement_id = str(requirement["requirement_id"])
                                for selector, expected in base.items():
                                    if selector == "requirement_id":
                                        continue
                                    actual = requirement.get(selector)
                                    preserved = (
                                        selector in requirement and actual == expected
                                    )
                                    if selector == "required":
                                        preserved = isinstance(actual, bool) and (
                                            not bool(expected) or actual is True
                                        )
                                    elif selector == "allow_unknown":
                                        preserved = isinstance(actual, bool) and (
                                            (bool(expected) and actual in {True, False})
                                            or (not bool(expected) and actual is False)
                                        )
                                    elif selector == "min_source_groups" and isinstance(
                                        expected, (int, float)
                                    ):
                                        preserved = (
                                            isinstance(actual, (int, float))
                                            and not isinstance(actual, bool)
                                            and actual >= expected
                                        )
                                    elif selector == "max_age_days" and isinstance(
                                        expected, (int, float)
                                    ):
                                        preserved = (
                                            isinstance(actual, (int, float))
                                            and not isinstance(actual, bool)
                                            and actual <= expected
                                        )
                                    if not preserved:
                                        errors.append(
                                            f"{path.relative_to(root)}: instantiated requirement {requirement_id} does not preserve registered selector {selector}"
                                        )
            relation_checks = manifest.get("relation_checks")
            relation_check_fields = workset_schema.get("relation_checks", {}).get(
                "required", ["total", "resolved", "blocking_gaps"]
            )
            relation_counts_valid = isinstance(relation_checks, dict)
            if not relation_counts_valid:
                errors.append(f"{path.relative_to(root)}: relation_checks must be an object")
            else:
                for field in relation_check_fields:
                    value = relation_checks.get(field)
                    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                        errors.append(
                            f"{path.relative_to(root)}: relation_checks {field} must be a non-negative integer"
                        )
                        relation_counts_valid = False
                if relation_counts_valid and (
                    relation_checks["resolved"] + relation_checks["blocking_gaps"]
                    != relation_checks["total"]
                ):
                    errors.append(
                        f"{path.relative_to(root)}: relation_checks resolved + blocking_gaps must equal total"
                    )
            verification = manifest.get("verification")
            verification_fields = workset_schema.get("verification", {}).get(
                "required",
                ["status", "required_unit_ids", "verified_unit_ids", "missing_references"],
            )
            if not isinstance(verification, dict):
                errors.append(f"{path.relative_to(root)}: verification must be an object")
            else:
                for field in verification_fields:
                    if field not in verification:
                        errors.append(f"{path.relative_to(root)}: missing verification field {field}")
                if verification.get("status") not in {"not_run", "completed", "failed"}:
                    errors.append(f"{path.relative_to(root)}: invalid verification status")
                for field in ("required_unit_ids", "verified_unit_ids", "missing_references"):
                    if not isinstance(verification.get(field), list):
                        errors.append(f"{path.relative_to(root)}: verification {field} must be a list")
                required_ids = verification.get("required_unit_ids")
                verified_ids = verification.get("verified_unit_ids")
                missing_items = verification.get("missing_references")
                if (
                    verification.get("status") == "completed"
                    and isinstance(required_ids, list)
                    and isinstance(verified_ids, list)
                    and isinstance(missing_items, list)
                    and (
                        not required_ids
                        or set(map(str, verified_ids)) != set(map(str, required_ids))
                        or bool(missing_items)
                    )
                ):
                    errors.append(
                        f"{path.relative_to(root)}: completed verification requires every non-empty required_unit_id and no missing references"
                    )
            coverage = manifest.get("coverage")
            coverage_fields = workset_schema.get("coverage", {}).get(
                "required",
                [
                    "required_total",
                    "required_covered",
                    "required_missing",
                    "coverage_ratio",
                    "requirements",
                    "semantic_candidates_do_not_count",
                ],
            )
            if not isinstance(coverage, dict):
                errors.append(f"{path.relative_to(root)}: coverage must be an object")
            else:
                for field in coverage_fields:
                    if field not in coverage:
                        errors.append(f"{path.relative_to(root)}: missing coverage field {field}")
                count_fields = ("required_total", "required_covered", "required_missing")
                counts_valid = True
                for field in count_fields:
                    value = coverage.get(field)
                    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                        errors.append(
                            f"{path.relative_to(root)}: coverage {field} must be a non-negative integer"
                        )
                        counts_valid = False
                ratio = coverage.get("coverage_ratio")
                ratio_valid = (
                    not isinstance(ratio, bool)
                    and isinstance(ratio, (int, float))
                    and 0.0 <= float(ratio) <= 1.0
                )
                if not ratio_valid:
                    errors.append(
                        f"{path.relative_to(root)}: coverage_ratio must be a number between 0 and 1"
                    )
                requirements = coverage.get("requirements")
                if not isinstance(requirements, list):
                    errors.append(f"{path.relative_to(root)}: coverage requirements must be a list")
                elif counts_valid:
                    required_rows = [
                        row for row in requirements if isinstance(row, dict) and row.get("required") is True
                    ]
                    if len(required_rows) != coverage["required_total"]:
                        errors.append(
                            f"{path.relative_to(root)}: coverage requirements do not match required_total"
                        )
                if not historical_workset and isinstance(requirements, list):
                    mirrored_requirements = [
                        row.get("requirement")
                        for row in requirements
                        if isinstance(row, dict)
                    ]
                    if mirrored_requirements != manifest.get(
                        "instantiated_requirements"
                    ):
                        errors.append(
                            f"{path.relative_to(root)}: coverage requirement selectors do not match instantiated_requirements"
                        )
                    for row in requirements:
                        if not isinstance(row, dict) or not isinstance(
                            row.get("requirement"), dict
                        ):
                            continue
                        snapshot = row["requirement"]
                        for summary_field in (
                            "requirement_id",
                            "required",
                            "allow_unknown",
                        ):
                            if row.get(summary_field) != snapshot.get(
                                summary_field
                            ):
                                errors.append(
                                    f"{path.relative_to(root)}: coverage row {row.get('requirement_id')} summary does not match requirement selector {summary_field}"
                                )
                        if row.get("base_requirement_id") != snapshot.get(
                            "base_requirement_id", snapshot.get("requirement_id")
                        ):
                            errors.append(
                                f"{path.relative_to(root)}: coverage row {row.get('requirement_id')} summary does not match requirement selector base_requirement_id"
                            )
                if coverage.get("semantic_candidates_do_not_count") is not True:
                    errors.append(
                        f"{path.relative_to(root)}: semantic candidates must not count toward required coverage"
                    )
                if counts_valid:
                    required_total = coverage["required_total"]
                    required_covered = coverage["required_covered"]
                    required_missing = coverage["required_missing"]
                    if required_covered + required_missing != required_total:
                        errors.append(
                            f"{path.relative_to(root)}: coverage required_covered + required_missing must equal required_total"
                        )
                    expected_ratio = required_covered / required_total if required_total else 0.0
                    if ratio_valid and abs(float(ratio) - expected_ratio) > 1e-9:
                        errors.append(
                            f"{path.relative_to(root)}: coverage_ratio does not match required_covered / required_total"
                        )
                    if required_total == 0 and manifest.get("status") == "completed":
                        errors.append(
                            f"{path.relative_to(root)}: zero required evidence cannot have completed status"
                        )
                    elif required_missing and manifest.get("status") == "completed":
                        errors.append(
                            f"{path.relative_to(root)}: missing required evidence cannot have completed status"
                        )
                blocking = coverage.get("blocking")
                blocking_gap_count = coverage.get("blocking_gap_count")
                if not isinstance(blocking, bool):
                    errors.append(f"{path.relative_to(root)}: coverage blocking must be boolean")
                if (
                    isinstance(blocking_gap_count, bool)
                    or not isinstance(blocking_gap_count, int)
                    or blocking_gap_count < 0
                ):
                    errors.append(
                        f"{path.relative_to(root)}: coverage blocking_gap_count must be a non-negative integer"
                    )
                gaps = manifest.get("gaps")
                if isinstance(gaps, list) and isinstance(blocking_gap_count, int) and not isinstance(
                    blocking_gap_count, bool
                ):
                    actual_blocking_gaps = sum(
                        1 for gap in gaps if isinstance(gap, dict) and gap.get("blocking") is True
                    )
                    if blocking_gap_count != actual_blocking_gaps:
                        errors.append(
                            f"{path.relative_to(root)}: coverage blocking_gap_count does not match gaps"
                        )
                    if isinstance(blocking, bool) and blocking != bool(actual_blocking_gaps):
                        errors.append(
                            f"{path.relative_to(root)}: coverage blocking does not match gaps"
                        )
            quality = manifest.get("quality")
            quality_schema = workset_schema.get("quality", {})
            if not isinstance(quality, dict):
                errors.append(f"{path.relative_to(root)}: quality must be an object")
            else:
                for field in quality_schema.get("required", []):
                    if field not in quality:
                        errors.append(f"{path.relative_to(root)}: missing quality field {field}")
                for field in ("assembled_units", "source_payload_bytes_in_workset", "hydrate_units"):
                    value = quality.get(field)
                    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                        errors.append(
                            f"{path.relative_to(root)}: quality {field} must be a non-negative integer"
                        )
                if not isinstance(quality.get("projection_degraded"), bool):
                    errors.append(f"{path.relative_to(root)}: quality projection_degraded must be boolean")
                context_proxy = quality.get("context_proxy")
                if not isinstance(context_proxy, dict):
                    errors.append(f"{path.relative_to(root)}: quality context_proxy must be an object")
                else:
                    for field in quality_schema.get("context_proxy_required", []):
                        if field not in context_proxy:
                            errors.append(
                                f"{path.relative_to(root)}: missing context_proxy field {field}"
                            )
            id_pattern = workset_schema.get("id_pattern")
            if id_pattern and not re.fullmatch(id_pattern, str(manifest.get("id", ""))):
                errors.append(f"{path.relative_to(root)}: invalid workset id {manifest.get('id')}")
            workflow = str(manifest.get("workflow", ""))
            stage = str(manifest.get("stage", ""))
            slug_pattern = workset_schema.get("slug_pattern")
            if slug_pattern and (not re.fullmatch(slug_pattern, workflow) or not re.fullmatch(slug_pattern, stage)):
                errors.append(f"{path.relative_to(root)}: invalid workflow/stage slug")
            if workflow and stage and manifest.get("run_id"):
                raw_attempt = manifest.get("attempt", 1)
                attempt_valid = (
                    not isinstance(raw_attempt, bool)
                    and isinstance(raw_attempt, int)
                    and raw_attempt >= 1
                )
                if not attempt_valid:
                    errors.append(
                        f"{path.relative_to(root)}: workset attempt must be a positive integer"
                    )
                    attempt = 1
                else:
                    attempt = raw_attempt
                base_id = f"{manifest['run_id']}-WORKSET-{workflow.upper()}-{stage.upper()}"
                attempt_suffix = "" if attempt == 1 else f"-A{attempt:03d}"
                expected_id = f"{base_id}{attempt_suffix}"
                if manifest.get("id") != expected_id:
                    errors.append(f"{path.relative_to(root)}: workset id does not match workflow/stage; expected {expected_id}")
                filename_suffix = "" if attempt == 1 else f"-a{attempt:03d}"
                expected_filename = f"{manifest['run_id']}-{workflow}-{stage}{filename_suffix}-工作集清单.json"
                if path.name != expected_filename:
                    errors.append(
                        f"{path.relative_to(root)}: filename does not match workflow/stage; expected {expected_filename}"
                    )
                previous_manifest_id = manifest.get("previous_manifest_id")
                if attempt == 1 and previous_manifest_id not in (None, ""):
                    errors.append(
                        f"{path.relative_to(root)}: first workset attempt must not declare previous_manifest_id"
                    )
                elif attempt > 1 and (
                    not isinstance(previous_manifest_id, str)
                    or not previous_manifest_id.strip()
                ):
                    errors.append(
                        f"{path.relative_to(root)}: repeated workset attempt requires previous_manifest_id"
                    )
                if attempt_valid:
                    group_key = (str(manifest["run_id"]), workflow, stage)
                    workset_attempt_groups.setdefault(group_key, []).append(
                        (path, attempt, str(manifest.get("id", "")), previous_manifest_id)
                    )
            serialized = json.dumps(manifest, ensure_ascii=False)
            if "verification_text" in serialized or "事实陈述" in serialized:
                errors.append(f"{path.relative_to(root)}: workset manifest contains source payload text")
            references = manifest.get("stable_references", [])
            reference_ids: set[str] = set()
            if not isinstance(references, list):
                errors.append(f"{path.relative_to(root)}: stable_references must be a list")
            else:
                for reference in references:
                    if not isinstance(reference, dict) or not reference.get("ref") or not reference.get("unit_id"):
                        errors.append(f"{path.relative_to(root)}: malformed stable reference")
                        continue
                    expected_ref = f"atom:{reference['unit_id']}"
                    if reference["ref"] != expected_ref:
                        errors.append(
                            f"{path.relative_to(root)}: stable reference ref/unit_id mismatch: {reference['ref']}"
                        )
                    known_atomic_id = re.fullmatch(
                        r"(?:EVI-[0-9]{8}-[0-9]{3}#[0-9]+|J[0-9]{8}-[0-9]{3} v[0-9]+|C[0-9]{8}-[0-9]{3} v[0-9]+|STR-[A-Z0-9-]+@[0-9]+\.[0-9]+\.[0-9]+)",
                        str(reference["unit_id"]),
                    )
                    if known_atomic_id and reference["ref"] not in stable_references:
                        errors.append(
                            f"{path.relative_to(root)}: stable reference does not resolve: {reference['ref']}"
                        )
                    if reference["unit_id"] in reference_ids:
                        errors.append(f"{path.relative_to(root)}: duplicate stable unit {reference['unit_id']}")
                    reference_ids.add(reference["unit_id"])
                    locator = reference.get("source_locator")
                    if not isinstance(locator, dict) or not locator.get("path"):
                        errors.append(f"{path.relative_to(root)}: stable reference missing source locator")
                        continue
                    source_path = (root / str(locator["path"])).resolve()
                    if root not in [source_path, *source_path.parents]:
                        errors.append(f"{path.relative_to(root)}: stable reference locator escapes workspace")
                    elif not source_path.is_file():
                        errors.append(
                            f"{path.relative_to(root)}: stable reference locator does not exist: {locator['path']}"
                        )
                    locator_hash = str(locator.get("sha256", ""))
                    if not re.fullmatch(r"[0-9a-fA-F]{64}", locator_hash):
                        errors.append(f"{path.relative_to(root)}: stable reference locator missing sha256")
                    elif source_path.is_file():
                        actual_hash = hashlib.sha256(source_path.read_bytes()).hexdigest()
                        if actual_hash != locator_hash.lower():
                            errors.append(f"{path.relative_to(root)}: stable reference locator sha256 mismatch")
                audit_references = manifest.get("audit_references", [])
                audited_unit_ids: set[str] = set()
                if not isinstance(audit_references, list):
                    errors.append(
                        f"{path.relative_to(root)}: audit_references must be a list"
                    )
                else:
                    for audit_reference in audit_references:
                        if (
                            not isinstance(audit_reference, dict)
                            or not audit_reference.get("unit_id")
                            or audit_reference.get("hydrate_eligible") is not False
                            or not audit_reference.get("exclusion_reason")
                        ):
                            errors.append(
                                f"{path.relative_to(root)}: malformed audit-only reference"
                            )
                            continue
                        audit_unit_id = str(audit_reference["unit_id"])
                        if audit_unit_id in audited_unit_ids:
                            errors.append(
                                f"{path.relative_to(root)}: duplicate audit-only unit {audit_unit_id}"
                            )
                        audited_unit_ids.add(audit_unit_id)
                        if audit_unit_id in reference_ids:
                            errors.append(
                                f"{path.relative_to(root)}: unit cannot be both live and audit-only: {audit_unit_id}"
                            )
                        if audit_reference.get("ref") != f"atom:{audit_unit_id}":
                            errors.append(
                                f"{path.relative_to(root)}: audit reference ref/unit_id mismatch"
                            )
                        audit_locator = audit_reference.get("source_locator")
                        if not isinstance(audit_locator, dict) or not audit_locator.get(
                            "path"
                        ):
                            errors.append(
                                f"{path.relative_to(root)}: audit reference missing source locator"
                            )
                audited_exclusions = manifest.get("audited_exclusions", [])
                if not isinstance(audited_exclusions, list):
                    errors.append(
                        f"{path.relative_to(root)}: audited_exclusions must be a list"
                    )
                else:
                    exclusion_unit_ids = {
                        str(item.get("unit_id"))
                        for item in audited_exclusions
                        if isinstance(item, dict)
                        and item.get("unit_id")
                        and item.get("reason")
                        and item.get("source")
                    }
                    if exclusion_unit_ids != audited_unit_ids:
                        errors.append(
                            f"{path.relative_to(root)}: audit_references must exactly cover audited_exclusions"
                        )
                relations = manifest.get("relations", [])
                resolved_relation_keys: set[tuple[str, str, str]] = set()
                if not isinstance(relations, list):
                    errors.append(f"{path.relative_to(root)}: relations must be a list")
                else:
                    if relation_counts_valid and len(relations) != relation_checks["resolved"]:
                        errors.append(
                            f"{path.relative_to(root)}: relation_checks resolved does not match relations"
                        )
                    allowed_relation_types = set(workset_schema.get("relation_types", []))
                    for relation in relations:
                        if not isinstance(relation, dict) or not all(
                            relation.get(key) for key in ("from", "to", "type")
                        ):
                            errors.append(f"{path.relative_to(root)}: malformed relation")
                            continue
                        relation_key = (
                            str(relation["from"]),
                            str(relation["to"]),
                            str(relation["type"]),
                        )
                        if relation_key in resolved_relation_keys:
                            errors.append(
                                f"{path.relative_to(root)}: duplicate resolved relation: {relation['from']} -> {relation['to']} ({relation['type']})"
                            )
                        resolved_relation_keys.add(relation_key)
                        if relation["from"] not in reference_ids or relation["to"] not in reference_ids:
                            errors.append(
                                f"{path.relative_to(root)}: relation endpoint is absent from stable_references: {relation['from']} -> {relation['to']}"
                            )
                        if allowed_relation_types and relation["type"] not in allowed_relation_types:
                            errors.append(
                                f"{path.relative_to(root)}: unsupported relation type {relation['type']}"
                            )
                audit_relations = manifest.get("audit_relations", [])
                if not isinstance(audit_relations, list):
                    errors.append(f"{path.relative_to(root)}: audit_relations must be a list")
                else:
                    allowed_audit_relation_types = set(
                        workset_schema.get(
                            "audit_relation_types",
                            ["historically_referenced_evidence"],
                        )
                    )
                    seen_audit_relations: set[tuple[str, str, str]] = set()
                    for relation in audit_relations:
                        if not isinstance(relation, dict) or not all(
                            relation.get(key) for key in ("from", "to", "type")
                        ):
                            errors.append(f"{path.relative_to(root)}: malformed audit relation")
                            continue
                        relation_key = (
                            str(relation["from"]),
                            str(relation["to"]),
                            str(relation["type"]),
                        )
                        if relation_key in seen_audit_relations:
                            errors.append(
                                f"{path.relative_to(root)}: duplicate audit relation: {relation['from']} -> {relation['to']} ({relation['type']})"
                            )
                        seen_audit_relations.add(relation_key)
                        if relation["from"] not in reference_ids:
                            errors.append(
                                f"{path.relative_to(root)}: audit relation source is absent from stable_references: {relation['from']}"
                            )
                        if relation["type"] not in allowed_audit_relation_types:
                            errors.append(
                                f"{path.relative_to(root)}: unsupported audit relation type {relation['type']}"
                            )
                blocking_relation_keys: set[tuple[str, str, str]] = set()
                manifest_gaps = manifest.get("gaps")
                if not isinstance(manifest_gaps, list):
                    errors.append(f"{path.relative_to(root)}: gaps must be a list")
                else:
                    for gap in manifest_gaps:
                        if not isinstance(gap, dict) or gap.get("blocking") is not True:
                            continue
                        relation = gap.get("relation")
                        if relation is None:
                            continue
                        if not isinstance(relation, dict) or not all(
                            relation.get(key) for key in ("from", "to", "type")
                        ):
                            errors.append(
                                f"{path.relative_to(root)}: malformed blocking relation gap"
                            )
                            continue
                        relation_key = (
                            str(relation["from"]),
                            str(relation["to"]),
                            str(relation["type"]),
                        )
                        if relation_key in blocking_relation_keys:
                            errors.append(
                                f"{path.relative_to(root)}: duplicate blocking relation gap: {relation['from']} -> {relation['to']} ({relation['type']})"
                            )
                        blocking_relation_keys.add(relation_key)
                for relation_from, relation_to, relation_type in sorted(
                    resolved_relation_keys & blocking_relation_keys
                ):
                    errors.append(
                        f"{path.relative_to(root)}: relation edge cannot be both resolved and blocking: {relation_from} -> {relation_to} ({relation_type})"
                    )
                if relation_counts_valid:
                    if relation_checks["resolved"] != len(resolved_relation_keys):
                        errors.append(
                            f"{path.relative_to(root)}: relation_checks resolved does not match unique resolved relations"
                        )
                    if relation_checks["blocking_gaps"] != len(blocking_relation_keys):
                        errors.append(
                            f"{path.relative_to(root)}: relation_checks blocking_gaps does not match blocking relation gaps"
                        )
                    if relation_checks["total"] != len(
                        resolved_relation_keys | blocking_relation_keys
                    ):
                        errors.append(
                            f"{path.relative_to(root)}: relation_checks total does not match unique relation edges"
                        )
            if isinstance(verification, dict):
                required_unit_ids = verification.get("required_unit_ids")
                verified_unit_ids = verification.get("verified_unit_ids")
                missing_references = verification.get("missing_references")
                if isinstance(required_unit_ids, list) and set(map(str, required_unit_ids)) != reference_ids:
                    errors.append(
                        f"{path.relative_to(root)}: verification required_unit_ids do not match stable_references"
                    )
                if isinstance(verified_unit_ids, list) and not set(map(str, verified_unit_ids)) <= reference_ids:
                    errors.append(
                        f"{path.relative_to(root)}: verification contains an unknown verified unit"
                    )
                if isinstance(missing_references, list):
                    missing_unit_ids = {
                        str(item.get("unit_id"))
                        for item in missing_references
                        if isinstance(item, dict) and item.get("unit_id")
                    }
                    if not missing_unit_ids <= reference_ids:
                        errors.append(
                            f"{path.relative_to(root)}: verification contains an unknown missing unit"
                        )
            if isinstance(quality, dict) and isinstance(quality.get("assembled_units"), int):
                if quality["assembled_units"] != len(reference_ids):
                    errors.append(
                        f"{path.relative_to(root)}: quality assembled_units does not match stable_references"
                    )

        for (run_id, workflow, stage), attempts in workset_attempt_groups.items():
            ordered = sorted(attempts, key=lambda item: item[1])
            attempt_numbers = [item[1] for item in ordered]
            if attempt_numbers != list(range(1, len(ordered) + 1)):
                parent = ordered[0][0].parent.relative_to(root)
                errors.append(
                    f"{parent}: workset attempt chain {run_id}/{workflow}/{stage} must be contiguous from 1"
                )
                continue
            ids_by_attempt = {attempt: manifest_id for _, attempt, manifest_id, _ in ordered}
            for path, attempt, _, previous_manifest_id in ordered[1:]:
                expected_previous = ids_by_attempt[attempt - 1]
                if previous_manifest_id != expected_previous:
                    errors.append(
                        f"{path.relative_to(root)}: previous_manifest_id must resolve to immediate attempt {expected_previous}"
                    )

        for run_id, run_path in historical_migration_runs.items():
            associated = worksets_by_run.get(run_id, [])
            if not associated:
                errors.append(
                    f"{run_path.relative_to(root)}: historical migration run requires an associated fail-closed workset"
                )
                continue
            for manifest_path, manifest in associated:
                if not is_historical_migration_workset(manifest):
                    errors.append(
                        f"{manifest_path.relative_to(root)}: workset associated with historical migration run must be historical/not_run/blocking"
                    )
        for run_id, worksets in worksets_by_run.items():
            for manifest_path, manifest in worksets:
                if (
                    manifest.get("workflow") == "historical"
                    and run_id not in historical_migration_runs
                ):
                    errors.append(
                        f"{manifest_path.relative_to(root)}: historical migration workset must resolve to a historical migration run"
                    )

    active_files = [root / "AGENTS.md", root / "研究规则.md", *(root / "模板").glob("*.md")]
    active_files += list((root / ".agents/skills/a-share").rglob("SKILL.md"))
    forbidden = ["弃权不记分", "JMMDD-NN", "买/卖/持有/减仓/放弃"]
    for path in active_files:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        for token in forbidden:
            if token in text:
                errors.append(f"{path.relative_to(root)}: forbidden legacy token {token}")

    for path in [root / "AGENTS.md", root / "CONTEXT.md", root / "研究规则.md", *(root / "模板").glob("*.md"), *(root / "docs/adr").glob("*.md")]:
        if path.exists():
            errors.extend(check_markdown_links(root, path))

    return {"root": str(root), "errors": errors, "warnings": warnings}


def _print_payload(payload: dict[str, object], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        errors = payload["errors"]
        warnings = payload["warnings"]
        print(f"workspace: {payload['root']}")
        print(f"errors: {len(errors)}")
        for item in errors:
            print(f"ERROR {item}")
        print(f"warnings: {len(warnings)}")
        for item in warnings:
            print(f"WARN  {item}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    payload = validate_workspace(args.root)
    _print_payload(payload, as_json=args.json)
    return 1 if payload["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
