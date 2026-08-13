"""The deep workset-assembly module.

The six skills depend on this module's two public operations only:

* :func:`assemble` selects atomic units and reports evidence coverage.
* :func:`hydrate` reopens bounded, hash-checked source locations for review.

Projection, authority mapping, freshness checks and optional semantic search
are intentionally kept below this boundary.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .contracts import (
    instantiate_task_evidence,
    normalize_instantiated_requirements,
)
from .eligibility import (
    compile_policy,
    effective_cutoff,
    eligibility_exclusion,
    supersession_exclusion,
)
from .markdown import MarkdownParseError, extract_units, iter_source_files
from .projection import all_units, open_fresh, rows_to_units, search_text
from .source_payload import FileSourcePayloadStore, SourcePayloadStore
from .status import unit_status_exclusion


WORKSPACE_SCHEMA = "a-share-workspace-v3"
SOURCE_GROUP_PATTERN = re.compile(r"SRCGRP-[A-Z0-9-]+")
def _read_json(value: Any) -> Any:
    if isinstance(value, (str, Path)):
        with Path(value).open(encoding="utf-8") as handle:
            return json.load(handle)
    return value


def _task_condition_requirements(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [dict(item) for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        conditions = value.get("required_evidence", value.get("requirements", []))
        if isinstance(conditions, list):
            return [dict(item) for item in conditions if isinstance(item, dict)]
    return []


def _root_from_manifest(run_manifest: dict[str, Any], explicit: Path | None = None) -> Path:
    if explicit is not None:
        return Path(explicit).resolve()
    if run_manifest.get("workspace_root"):
        return Path(str(run_manifest["workspace_root"])).resolve()
    raise ValueError("run manifest requires workspace_root")


def _reject_noncanonical_manifest_keys(run_manifest: dict[str, Any]) -> None:
    replacements = {
        "root": "workspace_root",
        "workspace": "workspace_root",
        "snapshot_cutoff": "information_cutoff",
        "contract": "task_contract",
        "task_contract_ref": "task_contract",
        "persist_workset": "persist_workset_manifest",
        "persistent_write": "persist_workset_manifest",
        "window_start": "calibration_window_start",
    }
    for key, replacement in replacements.items():
        if key in run_manifest:
            raise ValueError(f"run manifest uses removed key {key!r}; use {replacement}")


def _normalise(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).casefold()


def _values(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if str(item).strip()]
    return [str(value)]


def _match_any(actual: Iterable[Any], expected: Any) -> bool:
    actual_values = [_normalise(item) for item in actual]
    expected_values = [_normalise(item) for item in _values(expected)]
    return any(
        left == right or left in right or right in left
        for left in actual_values
        for right in expected_values
        if left and right
    )


def _object_type(value: str) -> str:
    value = _normalise(value)
    aliases = {
        "个股": "stock",
        "股票": "stock",
        "产业链": "industry",
        "交易主题": "theme",
        "市场": "market",
        "事件": "event",
        "stock": "stock",
        "industrychain": "industry",
        "industry": "industry",
        "theme": "theme",
        "market": "market",
        "event": "event",
    }
    for prefix, result in aliases.items():
        if value.startswith(prefix):
            return result
    return "unknown"


def _normal_requirement(requirement: dict[str, Any]) -> dict[str, Any]:
    return normalize_instantiated_requirements([requirement])[0]


def _parse_datetime(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    candidate = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(candidate)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        match = re.search(
            r"\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?(?:[+-]\d{2}:?\d{2}|Z)?)?",
            candidate,
        )
        if not match:
            return None
        try:
            parsed = datetime.fromisoformat(match.group(0).replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            return None


def _parse_snapshot_datetime(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    match = re.search(
        r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:?\d{2})",
        value.strip(),
    )
    if match is None:
        return None
    try:
        parsed = datetime.fromisoformat(match.group(0).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


def _has_valid_information_cutoff(unit: dict[str, Any]) -> bool:
    if unit.get("unit_type") not in {
        "evidence_item",
        "judgment_version",
        "observation_candidate",
        "object_field",
    }:
        return True
    return _parse_snapshot_datetime(unit.get("information_cutoff")) is not None


def _status_exclusion(unit: dict[str, Any]) -> str | None:
    return unit_status_exclusion(unit.get("unit_type"), unit.get("status"))


def _policy_status_exclusion(
    unit: dict[str, Any], policy: dict[str, Any]
) -> str | None:
    return (
        _status_exclusion(unit)
        if policy.get("mode") == "prospective_current"
        else None
    )


def _matches(unit: dict[str, Any], requirement: dict[str, Any]) -> bool:
    requirement = _normal_requirement(requirement)
    if requirement.get("unit_types") and unit.get("unit_type") not in requirement["unit_types"]:
        return False
    if requirement.get("unit_id") and unit.get("unit_id") != requirement["unit_id"]:
        return False
    if requirement.get("unit_ids") and unit.get("unit_id") not in _values(requirement["unit_ids"]):
        return False
    if requirement.get("objects"):
        if requirement.get("object_match") == "exact":
            actual_objects = {_normalise(item) for item in unit.get("objects", [])}
            expected_objects = {_normalise(item) for item in requirement["objects"]}
            if not actual_objects.intersection(expected_objects):
                return False
        elif not _match_any(unit.get("objects", []), requirement["objects"]):
            return False
    if requirement.get("object_type"):
        if not any(_object_type(str(item)) == _object_type(str(requirement["object_type"])) for item in unit.get("objects", [])):
            return False
    if requirement.get("fields") and not _match_any(unit.get("fields", []), requirement["fields"]):
        return False
    if requirement.get("roles") and not _match_any(unit.get("evidence_roles", []), requirement["roles"]):
        return False
    if requirement.get("status") and not _match_any([unit.get("status")], requirement["status"]):
        return False
    if requirement.get("authority") and not _match_any([unit.get("authority")], requirement["authority"]):
        return False
    return True


def _load_units(root: Path, projection_path: Path | None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    connection, projection_info = open_fresh(root, projection_path)
    if connection is not None:
        try:
            return all_units(connection), projection_info
        finally:
            connection.close()
    units: list[dict[str, Any]] = []
    parse_errors: list[dict[str, str]] = []
    for path in iter_source_files(root):
        try:
            units.extend(extract_units(root, path, strict=True))
        except (MarkdownParseError, ValueError) as exc:
            parse_errors.append(
                {
                    "document_path": path.relative_to(root).as_posix(),
                    "reason": str(exc),
                }
            )
    projection_info = dict(projection_info)
    projection_info["direct_read"] = True
    projection_info["direct_unit_count"] = len(units)
    projection_info["direct_parse_errors"] = parse_errors
    return units, projection_info


def _stable_reference(
    unit: dict[str, Any],
    root: Path,
    reasons: list[dict[str, Any]],
    selection_cutoff: Any = None,
) -> dict[str, Any]:
    canonical_locator = dict(unit["source_locator"])
    locator = dict(unit.get("verification_source_locator") or canonical_locator)
    eligibility = [
        dict(reason["eligibility"])
        for reason in reasons
        if isinstance(reason, dict) and isinstance(reason.get("eligibility"), dict)
    ]
    return {
        "ref": f"atom:{unit['unit_id']}",
        "unit_id": unit["unit_id"],
        "unit_type": unit["unit_type"],
        "authority": unit["authority"],
        "workspace_root": str(root),
        "document_path": unit["document_path"],
        "information_cutoff": unit.get("information_cutoff"),
        "selection_cutoff": selection_cutoff,
        "status": unit.get("status"),
        "objects": list(unit.get("objects", [])),
        "fields": list(unit.get("fields", [])),
        "evidence_roles": list(unit.get("evidence_roles", [])),
        "source_groups": list(unit.get("source_groups", [])),
        "source_locations": list(unit.get("source_locations", [])),
        "expiry": list(unit.get("expiry", [])),
        "valid_until": unit.get("valid_until"),
        "invalidated_at": unit.get("invalidated_at"),
        "terminated_at": unit.get("terminated_at"),
        "result_status": unit.get("result_status"),
        "result_recorded_at": unit.get("result_recorded_at"),
        "lifecycle_status": unit.get("lifecycle_status"),
        "logical_id": unit.get("logical_id"),
        "logical_version": unit.get("logical_version"),
        "relations": list(unit.get("relations", [])),
        "source_locator": locator,
        "canonical_source_locator": canonical_locator,
        "content_sha256": unit.get("content_sha256"),
        "eligibility": eligibility,
        "selection_reasons": reasons,
    }


def _audit_reference(
    unit: dict[str, Any], root: Path, reason: str
) -> dict[str, Any]:
    """Return a locator-only reference that is never eligible for hydration."""

    return {
        "ref": f"atom:{unit['unit_id']}",
        "unit_id": unit["unit_id"],
        "unit_type": unit["unit_type"],
        "workspace_root": str(root),
        "document_path": unit["document_path"],
        "source_locator": dict(unit["source_locator"]),
        "content_sha256": unit.get("content_sha256"),
        "logical_id": unit.get("logical_id"),
        "logical_version": unit.get("logical_version"),
        "exclusion_reason": reason,
        "hydrate_eligible": False,
    }


def _dependency_policies(
    source: dict[str, Any], reasons: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Freeze the source decision boundary for each explicit dependency edge."""

    compiled: list[dict[str, Any]] = []
    for reason in reasons:
        policy = reason.get("eligibility") if isinstance(reason, dict) else None
        if not isinstance(policy, dict):
            continue
        dependency_policy = dict(policy)
        cutoff = effective_cutoff(source, policy)
        dependency_policy["cutoff_basis"] = "run_cutoff"
        dependency_policy["run_cutoff"] = cutoff.isoformat() if cutoff else None
        compiled.append(dependency_policy)
    return compiled


def _call_semantic_adapter(adapter: Any, query: str, limit: int) -> tuple[list[Any], dict[str, Any]]:
    if adapter is None:
        return [], {"name": "none", "status": "disabled"}
    try:
        result = adapter.search(query, limit=limit)
        if isinstance(result, dict):
            hits = result.get("hits", result.get("results", []))
            info = {"name": result.get("adapter", type(adapter).__name__), "status": "used"}
        else:
            hits = result
            info = {"name": type(adapter).__name__, "status": "used"}
        return list(hits or []), info
    except Exception as exc:  # adapters are optional and never a hard dependency
        return [], {"name": type(adapter).__name__, "status": "degraded", "reason": str(exc)}


def assemble(run_manifest: dict[str, Any] | str | Path, task_evidence_manifest: Any = None) -> dict[str, Any]:
    """Assemble a bounded workset from a run manifest and evidence contract."""

    run = _read_json(run_manifest)
    if not isinstance(run, dict):
        raise ValueError("run manifest must be a JSON object")
    _reject_noncanonical_manifest_keys(run)
    root = _root_from_manifest(run)
    task_evidence_input = _read_json(task_evidence_manifest)
    task = instantiate_task_evidence(run, task_evidence_input, root)
    projection_path = run.get("projection_path")
    if projection_path:
        projection_candidate = Path(str(projection_path))
        projection = (
            projection_candidate.resolve()
            if projection_candidate.is_absolute()
            else (root / projection_candidate).resolve()
        )
        if root not in [projection, *projection.parents]:
            raise ValueError("projection_path escapes workspace")
    else:
        projection = None
    units, projection_info = _load_units(root, projection)
    unit_id_counts: dict[str, int] = {}
    for unit in units:
        unit_id_counts[unit["unit_id"]] = unit_id_counts.get(unit["unit_id"], 0) + 1
    ambiguous_ids = {unit_id for unit_id, count in unit_id_counts.items() if count > 1}
    if ambiguous_ids:
        projection_info = dict(projection_info)
        projection_info["projection_degraded"] = True
        projection_info["ambiguous_unit_ids"] = sorted(ambiguous_ids)
    by_id = {
        unit["unit_id"]: unit
        for unit in units
        if unit["unit_id"] not in ambiguous_ids
    }
    cutoff = _parse_datetime(run.get("information_cutoff"))
    requirements = [_normal_requirement(item) for item in task.get("required_evidence", [])]
    instantiated_requirements_sha256 = _sha256_text(_canonical_json(requirements))
    run_objects = run.get("objects") or []
    if not isinstance(run_objects, list):
        run_objects = list(run_objects) if isinstance(
            run_objects, (tuple, set)
        ) else [run_objects]
    contract_instantiation_payload = {
        "schema_version": run.get("schema_version"),
        "workflow": run.get("workflow"),
        "stage": run.get("stage"),
        "information_cutoff": run.get("information_cutoff"),
        "calibration_window_start": run.get("calibration_window_start"),
        "objects": run_objects,
        "handoff": dict(run.get("handoff") or {})
        if isinstance(run.get("handoff") or {}, dict)
        else {},
        "strategy_version": run.get("strategy_version"),
        "task_conditions": _task_condition_requirements(task_evidence_input),
    }
    contract_instantiation = {
        **contract_instantiation_payload,
        "sha256": _sha256_text(_canonical_json(contract_instantiation_payload)),
    }
    selected: dict[str, list[dict[str, Any]]] = {}
    selection_exclusions: list[dict[str, str | None]] = []
    audited_exclusions: list[dict[str, Any]] = []
    audited_units: dict[str, tuple[dict[str, Any], str]] = {}
    coverage_rows: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = [
        {
            "requirement_id": f"projection:{item['document_path']}",
            "reason": "authoritative_document_unparseable",
            "required": True,
            "allow_unknown": False,
            "blocking": True,
            "document_path": item["document_path"],
        }
        for item in projection_info.get("direct_parse_errors", [])
    ]
    for requirement in requirements:
        candidates = [unit for unit in units if _matches(unit, requirement)]
        eligibility_policy = compile_policy(requirement, run, units)
        eligibility_reasons = {
            unit["unit_id"]: eligibility_exclusion(unit, eligibility_policy)
            or supersession_exclusion(unit, eligibility_policy, units)
            for unit in candidates
        }
        status_reasons = {
            unit["unit_id"]: _policy_status_exclusion(unit, eligibility_policy)
            for unit in candidates
        }
        invalid_cutoffs = [unit for unit in candidates if not _has_valid_information_cutoff(unit)]
        future = [
            unit
            for unit in candidates
            if unit not in invalid_cutoffs
            and eligibility_reasons.get(unit["unit_id"]) == "future_information"
        ]
        expired = [
            unit
            for unit in candidates
            if unit not in invalid_cutoffs
            and eligibility_reasons.get(unit["unit_id"]) == "expired"
        ]
        ambiguous = [unit for unit in candidates if unit["unit_id"] in ambiguous_ids]
        eligible = [
            unit
            for unit in candidates
            if unit not in future
            and unit not in expired
            and unit not in invalid_cutoffs
            and unit not in ambiguous
            and eligibility_reasons.get(unit["unit_id"]) is None
            and status_reasons.get(unit["unit_id"]) is None
        ]
        try:
            min_source_groups = int(requirement.get("min_source_groups", 0) or 0)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"task requirement {requirement.get('requirement_id')!r} has invalid min_source_groups"
            ) from exc
        if min_source_groups < 0:
            raise ValueError(
                f"task requirement {requirement.get('requirement_id')!r} has invalid min_source_groups"
            )
        source_groups = sorted(
            {
                str(source_group)
                for unit in eligible
                for source_group in unit.get("source_groups", [])
                if SOURCE_GROUP_PATTERN.fullmatch(str(source_group).strip())
            }
        )
        covered = bool(eligible) and len(source_groups) >= min_source_groups
        reason = "covered" if covered else "missing"
        if eligible and len(source_groups) < min_source_groups:
            reason = "insufficient_source_independence"
        elif candidates and not eligible:
            statuses = {status_reasons.get(unit["unit_id"]) for unit in candidates}
            if ambiguous and len(ambiguous) == len(candidates):
                reason = "ambiguous_stable_reference"
            elif invalid_cutoffs and len(invalid_cutoffs) == len(candidates):
                reason = "invalid_information_cutoff"
            elif future and len(future) == len(candidates):
                reason = "future_information"
            elif expired and len(expired) == len(candidates):
                reason = "expired"
            elif len(
                {
                    eligibility_reasons.get(unit["unit_id"])
                    for unit in candidates
                    if eligibility_reasons.get(unit["unit_id"])
                }
            ) == 1:
                reason = str(
                    next(
                        iter(
                            {
                                eligibility_reasons.get(unit["unit_id"])
                                for unit in candidates
                                if eligibility_reasons.get(unit["unit_id"])
                            }
                        )
                    )
                )
            elif "conflict_or_denial" in statuses:
                reason = "conflict_or_denial"
            elif "unknown" in statuses:
                reason = "unknown"
            elif "mixed_status_requires_atomization" in statuses:
                reason = "mixed_status_requires_atomization"
            elif "unrecognized_evidence_status" in statuses:
                reason = "unrecognized_evidence_status"
        row = {
            "requirement_id": requirement.get("requirement_id"),
            "base_requirement_id": requirement.get("base_requirement_id", requirement.get("requirement_id")),
            "requirement": dict(requirement),
            "object": requirement.get("object"),
            "required": requirement.get("required", True),
            "allow_unknown": requirement.get("allow_unknown", False),
            "covered": covered,
            "candidate_count": len(candidates),
            "eligible_count": len(eligible),
            "source_group_count": len(source_groups),
            "source_groups": source_groups,
            "min_source_groups": min_source_groups,
            "future_count": len(future),
            "invalid_cutoff_count": len(invalid_cutoffs),
            "expired_count": len(expired),
            "ambiguous_count": len(ambiguous),
            "conflict_count": sum(1 for unit in candidates if status_reasons.get(unit["unit_id"]) == "conflict_or_denial"),
            "unknown_count": sum(1 for unit in candidates if status_reasons.get(unit["unit_id"]) == "unknown"),
            "reason": reason,
        }
        coverage_rows.append(row)
        if candidates:
            for unit in candidates:
                excluded = (
                    "invalid_information_cutoff"
                    if unit in invalid_cutoffs
                    else "future_information"
                    if unit in future
                    else "ambiguous_stable_reference"
                    if unit in ambiguous
                    else "expired"
                    if unit in expired
                    else eligibility_reasons.get(unit["unit_id"])
                    or status_reasons.get(unit["unit_id"])
                )
                if excluded:
                    selection_exclusions.append(
                        {
                            "unit_id": str(unit["unit_id"]),
                            "requirement_id": requirement.get("requirement_id"),
                            "reason": excluded,
                        }
                    )
                    audited_exclusions.append(
                        {
                            "unit_id": str(unit["unit_id"]),
                            "reason": str(excluded),
                            "source": "requirement",
                            "requirement_id": requirement.get("requirement_id"),
                        }
                    )
                    audited_units[str(unit["unit_id"])] = (unit, str(excluded))
                    continue
                selected.setdefault(unit["unit_id"], []).append(
                    {
                        "reason": "required_evidence",
                        "requirement_id": requirement.get("requirement_id"),
                        "eligible": True,
                        "excluded": None,
                        "eligibility": eligibility_policy,
                    }
                )
        if requirement.get("required", True) and not covered:
            gaps.append(
                {
                    "requirement_id": requirement.get("requirement_id"),
                    "reason": reason,
                    "required": True,
                    "allow_unknown": requirement.get("allow_unknown", False),
                    "blocking": not requirement.get("allow_unknown", False),
                    "candidate_count": len(candidates),
                    "future_count": len(future),
                    "invalid_cutoff_count": len(invalid_cutoffs),
                    "expired_count": len(expired),
                    "ambiguous_count": len(ambiguous),
                    "source_group_count": len(source_groups),
                    "min_source_groups": min_source_groups,
                }
            )

    # Structured requirements have priority.  Semantic/FTS expansion is
    # additive and therefore cannot make a missing requirement appear covered.
    query = str(run.get("semantic_query") or run.get("query") or "")
    query_parts = _values(run.get("keywords"))
    if query_parts:
        query = " ".join([query, *query_parts]).strip()
    semantic_adapter = run.get("semantic_adapter")
    adapter_hits, adapter_info = _call_semantic_adapter(semantic_adapter, query, int(run.get("semantic_limit", 10))) if semantic_adapter else ([], {"name": "none", "status": "disabled"})
    candidate_units: list[dict[str, Any]] = []
    if query:
        search_connection, _ = open_fresh(root, projection)
        if search_connection is not None:
            try:
                candidate_units.extend(search_text(search_connection, query, limit=int(run.get("semantic_limit", 10))))
            finally:
                search_connection.close()
        else:
            candidate_units.extend(_search_units(units, query))
    for hit in adapter_hits:
        unit_id = hit.get("unit_id") if isinstance(hit, dict) else str(hit)
        if unit_id in by_id:
            candidate_units.append(by_id[unit_id])
    semantic_exclusions: list[dict[str, str]] = []
    semantic_seen: set[str] = set()
    semantic_eligibility = compile_policy(
        {"eligibility_mode": "prospective_current"}, run, units
    )
    for unit in candidate_units:
        if unit["unit_id"] in semantic_seen:
            continue
        semantic_seen.add(unit["unit_id"])
        semantic_eligibility_reason = eligibility_exclusion(
            unit, semantic_eligibility
        )
        exclusion = (
            "ambiguous_stable_reference"
            if unit["unit_id"] in ambiguous_ids
            else "invalid_information_cutoff"
            if not _has_valid_information_cutoff(unit)
            else "future_information"
            if semantic_eligibility_reason == "future_information"
            else semantic_eligibility_reason
            or supersession_exclusion(unit, semantic_eligibility, units)
            or _status_exclusion(unit)
        )
        if exclusion:
            semantic_exclusions.append({"unit_id": unit["unit_id"], "reason": exclusion})
            audited_exclusions.append(
                {
                    "unit_id": unit["unit_id"],
                    "reason": exclusion,
                    "source": "semantic",
                    "requirement_id": None,
                }
            )
            audited_units[str(unit["unit_id"])] = (unit, str(exclusion))
            continue
        if unit["unit_id"] not in selected:
            selected[unit["unit_id"]] = [
                {
                    "reason": "semantic_candidate",
                    "eligible": True,
                    "requirement_id": None,
                    "eligibility": semantic_eligibility,
                }
            ]

    # Explicit dependency edges are facts carried by an atomic unit.  They
    # are never inferred from prose or semantic similarity.  Reachable
    # dependencies remain mandatory under a soft budget; an unresolved edge
    # becomes a blocking audit gap instead of silently disappearing.
    relations: list[dict[str, str]] = []
    audit_relations: list[dict[str, str]] = []
    relation_gaps: list[dict[str, Any]] = []
    relation_queue = list(selected)
    relation_seen: set[str] = set()
    relation_keys: set[tuple[str, str, str]] = set()
    while relation_queue:
        source_id = relation_queue.pop(0)
        if source_id in relation_seen or source_id not in by_id:
            continue
        relation_seen.add(source_id)
        for raw_relation in by_id[source_id].get("relations", []):
            if not isinstance(raw_relation, dict):
                continue
            relation = {
                "from": str(raw_relation.get("from") or source_id),
                "to": str(raw_relation.get("to") or ""),
                "type": str(raw_relation.get("type") or "related_to"),
            }
            if relation["from"] != source_id or not relation["to"]:
                continue
            relation_key = (relation["from"], relation["to"], relation["type"])
            if relation_key in relation_keys:
                continue
            if relation["type"] == "supersedes":
                audit_relations.append(relation)
                continue
            relation_keys.add(relation_key)
            target = by_id.get(relation["to"])
            gap_reason: str | None = None
            policies = _dependency_policies(by_id[source_id], selected[source_id])
            if relation["to"] in ambiguous_ids:
                gap_reason = "ambiguous_stable_reference"
            elif target is None:
                gap_reason = "missing_relation_target"
            elif not _has_valid_information_cutoff(target):
                gap_reason = "invalid_information_cutoff"
            else:
                eligibility_failures = [
                    reason
                    for policy in policies
                    if (
                        reason := eligibility_exclusion(target, policy)
                        or supersession_exclusion(target, policy, units)
                    )
                ]
                gap_reason = (
                    sorted(set(eligibility_failures))[0]
                    if eligibility_failures
                    else next(
                        (
                            reason
                            for policy in policies
                            if (
                                reason := _policy_status_exclusion(
                                    target, policy
                                )
                            )
                        ),
                        None,
                    )
                )
            if target is not None and gap_reason is None:
                relations.append(relation)
                selected.setdefault(target["unit_id"], []).extend(
                    {
                        "reason": "relation_dependency",
                        "source_unit_id": source_id,
                        "relation_type": relation["type"],
                        "eligible": True,
                        "excluded": None,
                        "eligibility": policy,
                    }
                    for policy in (policies or [compile_policy({}, run, units)])
                )
                relation_queue.append(target["unit_id"])
            if gap_reason:
                if target is not None:
                    audited_units[str(target["unit_id"])] = (
                        target,
                        str(gap_reason),
                    )
                    audited_exclusions.append(
                        {
                            "unit_id": str(target["unit_id"]),
                            "reason": str(gap_reason),
                            "source": "relation",
                            "requirement_id": None,
                        }
                    )
                relation_gaps.append(
                    {
                        "requirement_id": f"relation:{source_id}->{relation['to']}",
                        "reason": gap_reason,
                        "required": True,
                        "allow_unknown": False,
                        "blocking": True,
                        "relation": relation,
                    }
                )
    gaps.extend(relation_gaps)

    soft_budget = (run.get("budget") or {}).get("soft_units")
    required_ids = [
        unit_id
        for unit_id, reasons in selected.items()
        if any(item.get("reason") in {"required_evidence", "relation_dependency"} for item in reasons)
    ]
    required_id_set = set(required_ids)
    optional_ids = [unit_id for unit_id in selected if unit_id not in required_id_set]
    optional_before = len(optional_ids)
    if soft_budget is not None:
        try:
            allowed = max(0, int(soft_budget))
            optional_ids = optional_ids[:allowed]
        except (TypeError, ValueError):
            pass
    selected_ids = required_ids + optional_ids
    references = [
        _stable_reference(by_id[unit_id], root, selected[unit_id], run.get("information_cutoff"))
        for unit_id in selected_ids
        if unit_id in by_id
    ]
    required_rows = [row for row in coverage_rows if row["required"]]
    covered_count = sum(1 for row in required_rows if row["covered"])
    coverage = {
        "required_total": len(required_rows),
        "required_covered": covered_count,
        "required_missing": len(required_rows) - covered_count,
        "coverage_ratio": (covered_count / len(required_rows)) if required_rows else 0.0,
        "blocking": any(bool(gap.get("blocking")) for gap in gaps),
        "blocking_gap_count": sum(1 for gap in gaps if gap.get("blocking")),
        "requirements": coverage_rows,
        "semantic_candidates_do_not_count": True,
    }
    result: dict[str, Any] = {
        "schema_version": WORKSPACE_SCHEMA,
        "run_id": run.get("run_id"),
        "workspace_root": str(root),
        "stage": run.get("stage"),
        "workflow": run.get("workflow"),
        "task_evidence_manifest": task,
        "instantiated_requirements": requirements,
        "instantiated_requirements_sha256": instantiated_requirements_sha256,
        "contract_instantiation": contract_instantiation,
        "stable_references": references,
        "coverage": coverage,
        "gaps": gaps,
        "relations": relations,
        "audit_relations": audit_relations,
        "relation_checks": {
            "total": len(relation_keys),
            "resolved": len(relations),
            "blocking_gaps": len(relation_gaps),
        },
        "projection": projection_info,
        "semantic_adapter": adapter_info,
        "semantic_exclusions": semantic_exclusions,
        "selection_exclusions": selection_exclusions,
        "audited_exclusions": list(
            {
                (
                    item["unit_id"],
                    item["reason"],
                    item["source"],
                    item.get("requirement_id"),
                ): item
                for item in audited_exclusions
            }.values()
        ),
        "audit_references": [
            _audit_reference(unit, root, reason)
            for unit, reason in audited_units.values()
        ],
        "budget": {
            "soft_units": soft_budget,
            "required_units": len(required_ids),
            "optional_candidates_before_budget": optional_before,
            "optional_units_selected": len(optional_ids),
            "budget_is_soft": True,
            "required_items_preserved": True,
        },
        "quality": {
            "assembled_units": len(references),
            "source_payload_bytes_in_workset": 0,
            "hydrate_units": 0,
            "projection_degraded": bool(projection_info.get("projection_degraded")),
            "context_proxy": {
                "stable_reference_characters": len(json.dumps(references, ensure_ascii=False, separators=(",", ":"))),
                "selected_source_characters": sum(len(by_id[unit_id].get("content", "")) for unit_id in selected_ids if unit_id in by_id),
                "indexed_source_characters": sum(len(unit.get("content", "")) for unit in units),
                "raw_tool_payload_characters_entered": 0,
                "token_replay_available": False,
            },
        },
    }
    if run.get("persist_workset_manifest"):
        result["workset_manifest_path"] = str(_persist_workset_manifest(result, run))
    return result


def _search_units(units: list[dict[str, Any]], query: str) -> list[dict[str, Any]]:
    tokens = [_normalise(token) for token in query.split() if token.strip()]
    if not tokens:
        return []
    scored: list[tuple[int, dict[str, Any]]] = []
    for unit in units:
        haystack = _normalise(" ".join([unit.get("content", ""), *unit.get("objects", []), *unit.get("fields", [])]))
        score = sum(1 for token in tokens if token in haystack)
        if score:
            scored.append((score, unit))
    return [unit for _, unit in sorted(scored, key=lambda item: (-item[0], item[1]["unit_id"]))[:20]]


def _ref_input(stable_references: Any) -> tuple[list[dict[str, Any]], Path | None]:
    value = _read_json(stable_references)
    if isinstance(value, dict):
        if "stable_references" not in value:
            raise ValueError("hydrate envelope requires stable_references")
        references = value["stable_references"]
        root = value.get("workspace_root")
    else:
        references = value
        root = None
    if not isinstance(references, list):
        raise ValueError("stable references must be a list")
    normalised: list[dict[str, Any]] = []
    for item in references:
        if isinstance(item, str):
            normalised.append({"ref": item, "unit_id": item.removeprefix("atom:")})
        elif isinstance(item, dict):
            normalised.append(dict(item))
        else:
            raise ValueError("invalid stable reference")
    if not root:
        for item in normalised:
            if item.get("workspace_root"):
                root = item["workspace_root"]
                break
    return normalised, Path(str(root)).resolve() if root else None


def _safe_source_path(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    if root not in [path, *path.parents]:
        raise ValueError("source locator escapes workspace")
    return path


def _resolve_reference_units(
    root: Path, references: list[dict[str, Any]]
) -> tuple[dict[str, dict[str, Any]], set[str], list[dict[str, str]]]:
    # A caller-supplied Markdown locator is never itself authority.  Resolve
    # every requested id against the current document facts so an arbitrary
    # in-workspace file cannot masquerade as an atomic research unit.
    units, projection_info = _load_units(root, None)
    resolved: dict[str, dict[str, Any]] = {}
    ambiguous: set[str] = set()
    for unit in units:
        unit_id = str(unit["unit_id"])
        if unit_id in resolved:
            ambiguous.add(unit_id)
            resolved.pop(unit_id, None)
        elif unit_id not in ambiguous:
            resolved[unit_id] = unit
    return resolved, ambiguous, list(projection_info.get("direct_parse_errors", []))


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _validate_reference_against_canonical_unit(
    root: Path,
    reference: dict[str, Any],
    unit: dict[str, Any],
) -> None:
    unit_id = str(unit["unit_id"])
    if reference.get("ref") != f"atom:{unit_id}":
        raise ValueError("stable reference ref does not match canonical unit id")
    if str(reference.get("unit_id") or "") != unit_id:
        raise ValueError("stable reference unit_id does not match canonical unit")

    expected_fields = {
        "unit_type": unit.get("unit_type"),
        "authority": unit.get("authority"),
        "document_path": unit.get("document_path"),
        "information_cutoff": unit.get("information_cutoff"),
        "status": unit.get("status"),
        "objects": list(unit.get("objects", [])),
        "fields": list(unit.get("fields", [])),
        "evidence_roles": list(unit.get("evidence_roles", [])),
        "source_groups": list(unit.get("source_groups", [])),
        "source_locations": list(unit.get("source_locations", [])),
        "relations": list(unit.get("relations", [])),
        "expiry": list(unit.get("expiry", [])),
        "valid_until": unit.get("valid_until"),
        "invalidated_at": unit.get("invalidated_at"),
        "terminated_at": unit.get("terminated_at"),
        "result_status": unit.get("result_status"),
        "result_recorded_at": unit.get("result_recorded_at"),
        "lifecycle_status": unit.get("lifecycle_status"),
        "logical_id": unit.get("logical_id"),
        "logical_version": unit.get("logical_version"),
        "content_sha256": unit.get("content_sha256"),
    }
    for field, expected in expected_fields.items():
        if field not in reference:
            raise ValueError(f"stable reference is missing canonical {field}")
        if reference[field] != expected:
            raise ValueError(f"stable reference {field} does not match canonical unit")
    if not re.fullmatch(r"[0-9a-f]{64}", str(reference.get("content_sha256") or "")):
        raise ValueError("stable reference is missing canonical content hash")
    if "selection_cutoff" not in reference:
        raise ValueError("stable reference is missing selection_cutoff")
    if reference.get("workspace_root") != str(root):
        raise ValueError("stable reference workspace_root does not match canonical workspace")

    canonical_markdown_locator = dict(unit["source_locator"])
    if reference.get("canonical_source_locator") != canonical_markdown_locator:
        raise ValueError("stable reference canonical_source_locator does not match canonical unit")
    expected_locator = dict(
        unit.get("verification_source_locator") or canonical_markdown_locator
    )
    supplied_locator = reference.get("source_locator")
    if not isinstance(supplied_locator, dict) or supplied_locator != expected_locator:
        raise ValueError("stable reference source_locator does not match canonical unit")


def _validate_direct_source_payload_candidate(
    root: Path,
    reference: dict[str, Any],
    locator: dict[str, Any],
    manifest_path: Path | str | None,
) -> None:
    """Validate the deliberately non-authoritative pre-evidence payload seam.

    A raw payload must be inspectable before an evidence item exists, but it
    must not be able to impersonate one.  This discriminated reference shape
    is verification-only and is therefore forbidden in a persisted workset.
    """

    payload_id = str(locator.get("payload_id") or "")
    if not payload_id:
        raise ValueError("direct source payload candidate is missing payload_id")
    if manifest_path:
        raise ValueError("direct source payload candidate cannot belong to a workset manifest")
    if reference.get("ref") != f"source-payload:{payload_id}":
        raise ValueError("direct source payload candidate ref must match payload_id")
    if str(reference.get("unit_id") or "") != payload_id:
        raise ValueError("direct source payload candidate unit_id must match payload_id")
    if reference.get("unit_type") != "source_payload_candidate":
        raise ValueError("unresolved source payload must be a source_payload_candidate")
    if reference.get("authority") != "source_payload_store":
        raise ValueError("direct source payload candidate authority must be source_payload_store")
    if reference.get("status") != "unverified":
        raise ValueError("direct source payload candidate status must be unverified")
    supplied_root = reference.get("workspace_root")
    if not supplied_root or Path(str(supplied_root)).resolve() != root:
        raise ValueError("direct source payload candidate workspace_root does not match workspace")
    if locator.get("kind") != "source_payload":
        raise ValueError("direct source payload candidate requires a source_payload locator")

    information_cutoff = _parse_snapshot_datetime(reference.get("information_cutoff"))
    selection_cutoff = _parse_snapshot_datetime(reference.get("selection_cutoff"))
    acquired_at = _parse_snapshot_datetime(locator.get("acquired_at"))
    if information_cutoff is None:
        raise ValueError("direct source payload candidate requires a timezone-aware information cutoff")
    if selection_cutoff is None:
        raise ValueError("direct source payload candidate requires a timezone-aware selection cutoff")
    if acquired_at is None:
        raise ValueError("direct source payload candidate requires a timezone-aware acquired_at")
    if information_cutoff != acquired_at:
        raise ValueError("direct source payload candidate information cutoff must equal acquired_at")
    if information_cutoff > selection_cutoff or acquired_at > selection_cutoff:
        raise ValueError("future source payload candidate is outside the selection cutoff")

    if reference.get("canonical_source_locator") is not None:
        raise ValueError("direct source payload candidate cannot claim a canonical source locator")
    if reference.get("content_sha256") is not None:
        raise ValueError("direct source payload candidate cannot claim a canonical atomic content hash")
    for field in (
        "objects",
        "fields",
        "evidence_roles",
        "source_groups",
        "source_locations",
        "relations",
        "selection_reasons",
    ):
        if reference.get(field):
            raise ValueError(
                f"direct source payload candidate cannot claim canonical {field}"
            )


def _manifest_reference_map(
    root: Path, manifest_path: Path | str
) -> tuple[Path, dict[str, Any], dict[str, dict[str, Any]]]:
    destination = Path(manifest_path)
    destination = (
        destination.resolve()
        if destination.is_absolute()
        else (root / destination).resolve()
    )
    if root not in [destination, *destination.parents]:
        raise ValueError("workset manifest escapes workspace")
    with destination.open(encoding="utf-8") as handle:
        manifest = json.load(handle)
    if not isinstance(manifest, dict) or manifest.get("artifact_type") != "workset_manifest":
        raise ValueError("invalid workset manifest for verification update")
    references = manifest.get("stable_references")
    if references is None and manifest.get("workflow") == "historical":
        references = []
    if not isinstance(references, list):
        raise ValueError("workset manifest stable_references must be a list")
    by_id: dict[str, dict[str, Any]] = {}
    for reference in references:
        if not isinstance(reference, dict) or not reference.get("unit_id"):
            raise ValueError("workset manifest contains a malformed stable reference")
        unit_id = str(reference["unit_id"])
        if unit_id in by_id:
            raise ValueError("workset manifest contains duplicate stable references")
        by_id[unit_id] = reference
    return destination, manifest, by_id


def hydrate(
    stable_references: Any,
    workspace_root: Path | str | None = None,
    source_payload_store: SourcePayloadStore | None = None,
) -> dict[str, Any]:
    """Resolve stable references to bounded, hash-checked verification text."""

    reference_input = _read_json(stable_references)
    manifest_path = (
        reference_input.get("workset_manifest_path")
        if isinstance(reference_input, dict)
        else None
    )
    references, embedded_root = _ref_input(reference_input)
    root = Path(workspace_root).resolve() if workspace_root else embedded_root
    if root is None:
        for reference in references:
            locator = reference.get("source_locator", {})
            if locator.get("absolute_path"):
                root = Path(str(locator["absolute_path"])).resolve().parent
                break
    if root is None:
        raise ValueError("hydrate requires workspace_root when references do not embed one")
    manifest_references: dict[str, dict[str, Any]] = {}
    manifest_value: dict[str, Any] | None = None
    if manifest_path:
        try:
            _, manifest_value, manifest_references = _manifest_reference_map(
                root, manifest_path
            )
        except (OSError, ValueError, json.JSONDecodeError):
            # The updater reports the manifest failure below.  Hydration still
            # verifies each requested reference against document facts.
            manifest_references = {}
    if manifest_value is not None and manifest_value.get("workflow") == "historical":
        result: dict[str, Any] = {
            "schema_version": WORKSPACE_SCHEMA,
            "workspace_root": str(root),
            "units": [],
            "missing_references": [],
            "quality": {
                "hydrate_units": 0,
                "source_payload_externalized": 0,
                "verification_failures": 0,
                "verification_characters": 0,
                "token_replay_available": False,
            },
        }
        try:
            result["workset_manifest_update"] = _update_workset_manifest_verification(
                root,
                manifest_path,
                result,
                references,
            )
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            result["workset_manifest_update"] = {
                "status": "degraded",
                "reason": str(exc),
            }
        return result
    canonical_units, ambiguous_canonical_ids, canonical_parse_errors = (
        _resolve_reference_units(root, references)
    )
    units: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    for reference in references:
        effective_reference = dict(reference)
        unit_id = str(reference.get("unit_id") or str(reference.get("ref", "")).removeprefix("atom:"))
        locator = dict(reference.get("source_locator") or {})
        direct_payload_candidate = False
        selection_cutoff = _parse_datetime(reference.get("selection_cutoff"))
        exclusion_reasons = sorted(
            {
                str(reason.get("excluded") or "ineligible")
                for reason in reference.get("selection_reasons", [])
                if isinstance(reason, dict)
                and (reason.get("excluded") or reason.get("eligible") is False)
            }
        )
        try:
            if manifest_path:
                manifest_reference = manifest_references.get(unit_id)
                if manifest_reference is None or _canonical_json(reference) != _canonical_json(
                    manifest_reference
                ):
                    raise ValueError(
                        "requested reference does not match canonical workset manifest reference"
                    )
            if reference.get("hydrate_eligible") is False:
                raise ValueError("audit-only reference is not hydrate eligible")
            if exclusion_reasons:
                raise ValueError(
                    "audit-only reference is excluded from hydrate: "
                    + ", ".join(exclusion_reasons)
                )
            # Reject intrinsically ineligible caller metadata before resolving
            # it.  Canonical matching below is still mandatory before any
            # eligible reference can return text.
            if not _has_valid_information_cutoff(effective_reference):
                raise ValueError("invalid information cutoff")
            supplied_policies = reference.get("eligibility")
            if not isinstance(supplied_policies, list) or not supplied_policies:
                status_exclusion = _status_exclusion(effective_reference)
                if status_exclusion:
                    raise ValueError(
                        "audit-only reference status is excluded from hydrate: "
                        f"{status_exclusion}"
                    )
            if unit_id in ambiguous_canonical_ids:
                raise ValueError("ambiguous stable reference resolves to multiple atomic units")
            canonical_unit = canonical_units.get(unit_id)
            if locator.get("kind") != "source_payload":
                if canonical_parse_errors:
                    raise ValueError(
                        "canonical unit resolution is blocked by an unreadable authoritative document"
                    )
                if canonical_unit is None:
                    raise ValueError("stable reference does not resolve to a canonical atomic unit")
                _validate_reference_against_canonical_unit(
                    root, reference, canonical_unit
                )
                locator = dict(canonical_unit["source_locator"])
                effective_reference = dict(reference)
            elif canonical_unit is None:
                if reference.get("unit_type") != "source_payload_candidate":
                    if canonical_parse_errors:
                        raise ValueError(
                            "canonical unit resolution is blocked by an unreadable authoritative document"
                        )
                    raise ValueError(
                        "source payload evidence reference does not resolve to a canonical atomic unit"
                    )
                _validate_direct_source_payload_candidate(
                    root, reference, locator, manifest_path
                )
                direct_payload_candidate = True
            else:
                # Payload excerpts remain under the strict sidecar adapter,
                # while assembled payload references are also bound back to
                # their canonical Markdown evidence item.  Any parse failure
                # makes the authoritative id universe incomplete, so fail
                # closed even when this id happened to resolve.
                if canonical_parse_errors:
                    raise ValueError(
                        "canonical unit resolution is blocked by an unreadable authoritative document"
                    )
                _validate_reference_against_canonical_unit(
                    root, reference, canonical_unit
                )
                locator = dict(canonical_unit["verification_source_locator"])
                effective_reference = dict(reference)
            if canonical_unit is not None:
                eligibility_policies = reference.get("eligibility")
                if not isinstance(eligibility_policies, list) or not eligibility_policies:
                    eligibility_policies = [
                        {
                            "model_version": "a-share-eligibility-v1",
                            "mode": "prospective_current",
                            "cutoff_basis": "run_cutoff",
                            "run_cutoff": reference.get("selection_cutoff"),
                            "window_start": None,
                            "judgment_cutoffs": {},
                            "related_judgment_cutoffs": {},
                            "max_age_days": None,
                            "allowed_lifecycle_statuses": [],
                        }
                    ]
                eligibility_failures = [
                    reason
                    for policy in eligibility_policies
                    if (
                        reason := eligibility_exclusion(canonical_unit, policy)
                        or supersession_exclusion(
                            canonical_unit, policy, canonical_units.values()
                        )
                        or _policy_status_exclusion(canonical_unit, policy)
                    )
                ]
                if eligibility_failures:
                    raise ValueError(
                        "reference is no longer eligible: "
                        + ", ".join(sorted(set(eligibility_failures)))
                    )
            information_cutoff = _parse_datetime(
                effective_reference.get("information_cutoff")
            )
            source_acquired_at = _parse_datetime(locator.get("acquired_at"))
            if (
                selection_cutoff
                and (
                    (information_cutoff and information_cutoff > selection_cutoff)
                    or (source_acquired_at and source_acquired_at > selection_cutoff)
                )
            ):
                raise ValueError("future information is outside the selection cutoff")
            if locator.get("kind") == "source_payload":
                store = source_payload_store or FileSourcePayloadStore(root)
                verification_text = store.excerpt(
                    locator,
                    locator.get("start_line"),
                    locator.get("end_line"),
                )
                verification_locator = locator
            else:
                path = _safe_source_path(root, str(locator["path"]))
                text = path.read_text(encoding="utf-8")
                actual_hash = _sha256_text(text)
                expected_hash = locator.get("sha256")
                if not expected_hash:
                    raise ValueError("canonical source locator is missing sha256")
                if expected_hash != actual_hash:
                    raise ValueError("source document content hash changed")
                lines = text.splitlines()
                start = max(1, int(locator.get("start_line", 1)))
                end = min(len(lines), int(locator.get("end_line", len(lines))))
                if start > end:
                    raise ValueError("source locator has an empty range")
                verification_text = "\n".join(lines[start - 1 : end])
                if _sha256_text(verification_text) != effective_reference.get(
                    "content_sha256"
                ):
                    raise ValueError("canonical atomic unit content hash changed")
                verification_locator = {
                    "kind": "markdown",
                    "path": path.relative_to(root).as_posix(),
                    "start_line": start,
                    "end_line": end,
                    "anchor": locator.get("anchor"),
                    "sha256": actual_hash,
                }
            units.append(
                {
                    "unit_id": unit_id,
                    "unit_type": effective_reference.get("unit_type"),
                    "authority": effective_reference.get("authority"),
                    "document_path": effective_reference.get("document_path") or verification_locator.get("path"),
                    "information_cutoff": effective_reference.get("information_cutoff"),
                    "status": effective_reference.get("status"),
                    "objects": effective_reference.get("objects", []),
                    "fields": effective_reference.get("fields", []),
                    "evidence_roles": effective_reference.get("evidence_roles", []),
                    "source_groups": effective_reference.get("source_groups", []),
                    "source_locations": effective_reference.get("source_locations", []),
                    "relations": effective_reference.get("relations", []),
                    "verification_text": verification_text,
                    "verification_excerpt": verification_text,
                    "verification_locator": verification_locator,
                    "source_payload_externalized": verification_locator.get("kind") == "source_payload",
                    "verification_only": direct_payload_candidate,
                }
            )
        except (KeyError, OSError, UnicodeDecodeError, ValueError, FileNotFoundError) as exc:
            missing.append({"ref": reference.get("ref", f"atom:{unit_id}"), "unit_id": unit_id, "reason": str(exc)})
    result = {
        "schema_version": WORKSPACE_SCHEMA,
        "workspace_root": str(root),
        "units": units,
        "missing_references": missing,
        "quality": {
            "hydrate_units": len(units),
            "source_payload_externalized": sum(1 for unit in units if unit["source_payload_externalized"]),
            "verification_failures": len(missing),
            "verification_characters": sum(len(unit["verification_text"]) for unit in units),
            "token_replay_available": False,
        },
    }
    if manifest_path:
        try:
            result["workset_manifest_update"] = _update_workset_manifest_verification(
                root,
                manifest_path,
                result,
                references,
            )
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            result["workset_manifest_update"] = {
                "status": "degraded",
                "reason": str(exc),
            }
    return result


def _sha256_text(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _update_workset_manifest_verification(
    root: Path,
    manifest_path: Path | str,
    hydrated: dict[str, Any],
    requested_references: list[dict[str, Any]],
) -> dict[str, Any]:
    destination, manifest, manifest_references = _manifest_reference_map(
        root, manifest_path
    )
    for reference in requested_references:
        unit_id = str(reference.get("unit_id") or "")
        if unit_id not in manifest_references or _canonical_json(
            reference
        ) != _canonical_json(manifest_references[unit_id]):
            raise ValueError(
                "hydrate may update verification only for the manifest's canonical stable references"
            )
    verification = dict(manifest.get("verification") or {})
    required_unit_ids = list(manifest_references)
    if [str(item) for item in verification.get("required_unit_ids", [])] != required_unit_ids:
        raise ValueError(
            "workset verification required_unit_ids do not match manifest stable references"
        )
    previous_verified = [
        str(item) for item in verification.get("verified_unit_ids", [])
    ]
    if not set(previous_verified) <= set(required_unit_ids):
        raise ValueError("workset verification contains an unknown verified unit")
    newly_verified_unit_ids = [
        str(item.get("unit_id")) for item in hydrated.get("units", []) if item.get("unit_id")
    ]
    newly_missing = [
        {
            "ref": item.get("ref"),
            "unit_id": item.get("unit_id"),
            "reason": item.get("reason"),
        }
        for item in hydrated.get("missing_references", [])
    ]
    newly_missing_unit_ids = {
        str(item["unit_id"]) for item in newly_missing if item.get("unit_id")
    }
    if not (
        set(newly_verified_unit_ids) | newly_missing_unit_ids
    ) <= set(required_unit_ids):
        raise ValueError("hydrate result contains a unit outside the workset manifest")
    verified_unit_ids = list(
        dict.fromkeys(
            [
                *[
                    item
                    for item in previous_verified
                    if item not in newly_missing_unit_ids
                ],
                *newly_verified_unit_ids,
            ]
        )
    )
    verified_unit_id_set = set(verified_unit_ids)
    missing_by_unit_id = {
        str(item.get("unit_id")): dict(item)
        for item in verification.get("missing_references", [])
        if isinstance(item, dict) and item.get("unit_id")
    }
    for item in newly_missing:
        if item.get("unit_id"):
            missing_by_unit_id[str(item["unit_id"])] = item
    for unit_id in set(newly_verified_unit_ids):
        missing_by_unit_id.pop(unit_id, None)
    missing = list(missing_by_unit_id.values())
    all_required_verified = bool(required_unit_ids) and set(required_unit_ids) <= verified_unit_id_set
    verification.update(
        {
            "status": "failed" if missing else "completed" if all_required_verified else "not_run",
            "verified_unit_ids": verified_unit_ids,
            "missing_references": missing,
        }
    )
    manifest["verification"] = verification
    quality = dict(manifest.get("quality") or {})
    quality.update(hydrated.get("quality") or {})
    manifest["quality"] = quality
    if missing:
        manifest["status"] = "degraded"
    serialized = json.dumps(manifest, ensure_ascii=False, indent=2)
    if "verification_text" in serialized or "verification_excerpt" in serialized:
        raise ValueError("workset manifest must not contain verification payload text")
    destination.write_text(serialized + "\n", encoding="utf-8")
    return {"status": "updated", "path": str(destination)}


def _persist_workset_manifest(result: dict[str, Any], run_manifest: dict[str, Any] | None = None, path: Path | None = None) -> Path:
    """Persist only the visualisation-ready assembly audit, never source text."""

    run = run_manifest or result
    root = _root_from_manifest(run)
    run_id = str(result.get("run_id") or run.get("run_id") or "RUN-unknown")
    workflow = str(result.get("workflow") or run.get("workflow") or "").strip()
    stage = str(result.get("stage") or run.get("stage") or "").strip()
    if not workflow or not stage:
        raise ValueError("persistent workset requires workflow and stage")
    for label, value in (("run_id", run_id), ("workflow", workflow), ("stage", stage)):
        if not re.fullmatch(r"[A-Za-z0-9._-]+", value):
            raise ValueError(f"persistent workset {label} contains unsafe characters")
    cutoff = str(run.get("information_cutoff") or "")
    month_match = re.search(r"(\d{4}-\d{2})", cutoff)
    run_date_match = re.search(r"RUN-(\d{4})(\d{2})\d{2}", run_id)
    month = month_match.group(1) if month_match else f"{run_date_match.group(1)}-{run_date_match.group(2)}" if run_date_match else "unknown"
    base_destination = path or (
        root / "运行记录" / month / f"{run_id}-{workflow}-{stage}-工作集清单.json"
    )
    base_destination = Path(base_destination)
    if not base_destination.is_absolute():
        base_destination = root / base_destination
    base_destination.parent.mkdir(parents=True, exist_ok=True)

    base_id = f"{run_id}-WORKSET-{workflow.upper()}-{stage.upper()}"
    filename_suffix = "-工作集清单.json"
    if not base_destination.name.endswith(filename_suffix):
        raise ValueError("workset manifest path must use the canonical filename suffix")
    filename_stem = base_destination.name[: -len(filename_suffix)]

    def candidate_for(attempt: int) -> tuple[Path, str]:
        if attempt == 1:
            return base_destination, base_id
        suffix = f"-a{attempt:03d}"
        return (
            base_destination.with_name(f"{filename_stem}{suffix}{filename_suffix}"),
            f"{base_id}-A{attempt:03d}",
        )

    def existing_attempts() -> dict[int, tuple[Path, str]]:
        attempts: dict[int, tuple[Path, str]] = {}
        escaped_stem = re.escape(filename_stem)
        escaped_suffix = re.escape(filename_suffix)
        pattern = re.compile(rf"^{escaped_stem}(?:-a([0-9]{{3,}}))?{escaped_suffix}$")
        for existing in base_destination.parent.iterdir():
            match = pattern.fullmatch(existing.name)
            if not match:
                continue
            attempt = int(match.group(1) or "1")
            if attempt < 1 or attempt in attempts:
                raise ValueError("workset manifest attempt chain is ambiguous")
            try:
                previous = json.loads(existing.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError("workset manifest attempt chain is unreadable") from exc
            _, expected_id = candidate_for(attempt)
            if (
                not isinstance(previous, dict)
                or previous.get("artifact_type") != "workset_manifest"
                or previous.get("id") != expected_id
                or previous.get("run_id") != run_id
                or previous.get("workflow") != workflow
                or previous.get("stage") != stage
                or previous.get("attempt", attempt) != attempt
            ):
                raise ValueError("workset manifest attempt chain is invalid")
            attempts[attempt] = (existing, expected_id)
        if attempts and set(attempts) != set(range(1, max(attempts) + 1)):
            raise ValueError("workset manifest attempt chain is not contiguous")
        return attempts

    while True:
        attempts = existing_attempts()
        attempt = max(attempts, default=0) + 1
        destination, manifest_id = candidate_for(attempt)
        task_evidence_manifest = result.get("task_evidence_manifest") or {}
        contract_registry = task_evidence_manifest.get("contract_registry") or {}
        if not all(
            isinstance(contract_registry.get(field), str)
            and bool(contract_registry[field].strip())
            for field in ("registry_path", "sha256")
        ):
            raise ValueError(
                "persistent workset requires immutable registered task contract provenance"
            )
        contract_instantiation = result.get("contract_instantiation")
        if not isinstance(contract_instantiation, dict) or not isinstance(
            contract_instantiation.get("sha256"), str
        ):
            raise ValueError(
                "persistent workset requires immutable contract instantiation inputs"
            )
        manifest = {
            "schema_version": WORKSPACE_SCHEMA,
            "artifact_type": "workset_manifest",
            "id": manifest_id,
            "attempt": attempt,
            "status": "degraded" if result.get("projection", {}).get("projection_degraded") else "partial" if result.get("gaps") else "completed",
            "run_id": run_id,
            "created_at": run.get("created_at") or cutoff,
            "information_cutoff": cutoff,
            "workflow": workflow,
            "stage": stage,
            "task_contract": {
                "contract_id": task_evidence_manifest.get("contract_id"),
                "version": task_evidence_manifest.get("version"),
                "registry_path": contract_registry.get("registry_path"),
                "sha256": contract_registry.get("sha256"),
            },
            "instantiated_requirements": result.get(
                "instantiated_requirements", []
            ),
            "instantiated_requirements_sha256": result.get(
                "instantiated_requirements_sha256"
            ),
            "contract_instantiation": contract_instantiation,
            "strategy_version": (
                (result.get("task_evidence_manifest") or {}).get("strategy_version")
                or run.get("strategy_version")
                or "not_applicable"
            ),
            "projection": result.get("projection", {}),
            "stable_references": result.get("stable_references", []),
            "audit_references": result.get("audit_references", []),
            "audited_exclusions": result.get("audited_exclusions", []),
            "relations": result.get("relations", []),
            "audit_relations": result.get("audit_relations", []),
            "relation_checks": result.get("relation_checks", {}),
            "coverage": result.get("coverage", {}),
            "gaps": result.get("gaps", []),
            "semantic_adapter": result.get("semantic_adapter", {}),
            "budget": result.get("budget", {}),
            "quality": result.get("quality", {}),
            "verification": {
                "status": "not_run",
                "required_unit_ids": [
                    item.get("unit_id") for item in result.get("stable_references", [])
                ],
                "verified_unit_ids": [],
                "missing_references": [],
            },
        }
        if attempt > 1:
            manifest["previous_manifest_id"] = attempts[attempt - 1][1]
        # References intentionally contain locators and hashes only.  Reject an
        # accidental raw payload before writing the audit file.
        serialized = json.dumps(manifest, ensure_ascii=False, indent=2)
        if "事实陈述" in serialized or "verification_text" in serialized:
            raise ValueError("workset manifest must not contain verification payload text")
        try:
            with destination.open("x", encoding="utf-8") as handle:
                handle.write(serialized + "\n")
        except FileExistsError:
            if path is not None:
                raise ValueError("workset manifest path already exists")
            continue
        return destination
