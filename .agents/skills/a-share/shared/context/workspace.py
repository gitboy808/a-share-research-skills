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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from .contracts import instantiate_task_evidence
from .markdown import extract_units, iter_source_files
from .projection import all_units, open_fresh, rows_to_units, search_text
from .source_payload import FileSourcePayloadStore, SourcePayloadStore


WORKSPACE_SCHEMA = "a-share-workspace-v3"
UNIT_TYPES = {
    "evidence": "evidence_item",
    "evidence_item": "evidence_item",
    "judgment": "judgment_version",
    "judgment_version": "judgment_version",
    "object": "object_field",
    "object_field": "object_field",
    "strategy": "strategy_version",
    "strategy_version": "strategy_version",
}
CONFLICT_STATUSES = {"冲突", "已否证", "否证", "conflict", "denied", "falsified", "否决"}
UNKNOWN_STATUSES = {"unknown", "未知", "未证实", "不可取得", "当时未记录"}


def _read_json(value: Any) -> Any:
    if isinstance(value, (str, Path)):
        with Path(value).open(encoding="utf-8") as handle:
            return json.load(handle)
    return value


def _root_from_manifest(run_manifest: dict[str, Any], explicit: Path | None = None) -> Path:
    if explicit is not None:
        return Path(explicit).resolve()
    for key in ("workspace_root", "root", "workspace"):
        if run_manifest.get(key):
            return Path(str(run_manifest[key])).resolve()
    raise ValueError("run manifest requires workspace_root")


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
    result = dict(requirement)
    if "unit_types" not in result:
        result["unit_types"] = _values(result.get("unit_type", result.get("kind")))
    result["unit_types"] = [UNIT_TYPES.get(str(value), str(value)) for value in result["unit_types"] if value]
    if "objects" not in result:
        result["objects"] = _values(result.get("object"))
    if "fields" not in result:
        result["fields"] = _values(result.get("field"))
    if "roles" not in result:
        result["roles"] = _values(result.get("evidence_role", result.get("role")))
    result["required"] = bool(result.get("required", True))
    result["allow_unknown"] = bool(result.get("allow_unknown", False))
    return result


def _parse_datetime(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    candidate = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(candidate)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        try:
            parsed = datetime.fromisoformat(candidate[:10])
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            return None


def _is_future(unit: dict[str, Any], cutoff: datetime | None) -> bool:
    unit_time = _parse_datetime(unit.get("information_cutoff"))
    return bool(unit_time and cutoff and unit_time > cutoff)


def _is_expired(unit: dict[str, Any], requirement: dict[str, Any], cutoff: datetime | None) -> bool:
    freshness = requirement.get("freshness", requirement.get("max_age_days"))
    max_age: Any = freshness.get("max_age_days") if isinstance(freshness, dict) else freshness
    if max_age is not None and cutoff is not None:
        unit_time = _parse_datetime(unit.get("information_cutoff"))
        try:
            if unit_time and unit_time < cutoff - timedelta(days=float(max_age)):
                return True
        except (TypeError, ValueError):
            pass
    expiry = " ".join(str(item) for item in unit.get("expiry", []))
    expiry = expiry or str((unit.get("metadata") or {}).get("expires_at") or "")
    if not expiry:
        expiry_values = (unit.get("metadata") or {}).get("expiry")
        expiry = str(expiry_values or "")
    if not expiry:
        return False
    dates = re.findall(r"\d{4}-\d{2}-\d{2}(?:T[^\s，。；;]+)?", expiry)
    if not dates or cutoff is None:
        return False
    expiry_time = _parse_datetime(dates[-1])
    return bool(expiry_time and expiry_time < cutoff)


def _matches(unit: dict[str, Any], requirement: dict[str, Any]) -> bool:
    requirement = _normal_requirement(requirement)
    if requirement.get("unit_types") and unit.get("unit_type") not in requirement["unit_types"]:
        return False
    if requirement.get("unit_id") and unit.get("unit_id") != requirement["unit_id"]:
        return False
    if requirement.get("unit_ids") and unit.get("unit_id") not in _values(requirement["unit_ids"]):
        return False
    if requirement.get("objects") and not _match_any(unit.get("objects", []), requirement["objects"]):
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
    for path in iter_source_files(root):
        units.extend(extract_units(root, path))
    projection_info = dict(projection_info)
    projection_info["direct_read"] = True
    projection_info["direct_unit_count"] = len(units)
    return units, projection_info


def _stable_reference(unit: dict[str, Any], root: Path, reasons: list[dict[str, Any]]) -> dict[str, Any]:
    locator = dict(unit["source_locator"])
    return {
        "ref": f"atom:{unit['unit_id']}",
        "unit_id": unit["unit_id"],
        "unit_type": unit["unit_type"],
        "authority": unit["authority"],
        "workspace_root": str(root),
        "document_path": unit["document_path"],
        "information_cutoff": unit.get("information_cutoff"),
        "status": unit.get("status"),
        "objects": list(unit.get("objects", [])),
        "fields": list(unit.get("fields", [])),
        "evidence_roles": list(unit.get("evidence_roles", [])),
        "source_locator": locator,
        "content_sha256": unit.get("content_sha256"),
        "selection_reasons": reasons,
    }


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
    root = _root_from_manifest(run)
    task = instantiate_task_evidence(run, task_evidence_manifest, root)
    projection_path = run.get("projection_path")
    projection = Path(str(projection_path)).resolve() if projection_path else None
    units, projection_info = _load_units(root, projection)
    by_id = {unit["unit_id"]: unit for unit in units}
    cutoff = _parse_datetime(run.get("information_cutoff") or run.get("snapshot_cutoff"))
    requirements = [_normal_requirement(item) for item in task.get("required_evidence", [])]
    selected: dict[str, list[dict[str, Any]]] = {}
    coverage_rows: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []

    for requirement in requirements:
        candidates = [unit for unit in units if _matches(unit, requirement)]
        future = [unit for unit in candidates if _is_future(unit, cutoff)]
        expired = [unit for unit in candidates if not _is_future(unit, cutoff) and _is_expired(unit, requirement, cutoff)]
        eligible = [
            unit
            for unit in candidates
            if unit not in future
            and unit not in expired
            and (_normalise(unit.get("status")) not in {_normalise(item) for item in CONFLICT_STATUSES})
            and (requirement.get("allow_unknown") or _normalise(unit.get("status")) not in {_normalise(item) for item in UNKNOWN_STATUSES})
        ]
        covered = bool(eligible)
        reason = "covered" if covered else "missing"
        if candidates and not eligible:
            statuses = {_normalise(unit.get("status")) for unit in candidates}
            if future and len(future) == len(candidates):
                reason = "future_information"
            elif expired and len(expired) == len(candidates):
                reason = "expired"
            elif statuses & {_normalise(item) for item in CONFLICT_STATUSES}:
                reason = "conflict_or_denial"
            elif statuses & {_normalise(item) for item in UNKNOWN_STATUSES}:
                reason = "unknown"
        row = {
            "requirement_id": requirement.get("requirement_id"),
            "required": requirement.get("required", True),
            "allow_unknown": requirement.get("allow_unknown", False),
            "covered": covered,
            "candidate_count": len(candidates),
            "eligible_count": len(eligible),
            "reason": reason,
        }
        coverage_rows.append(row)
        if candidates:
            for unit in candidates:
                selected.setdefault(unit["unit_id"], []).append(
                    {
                        "reason": "required_evidence",
                        "requirement_id": requirement.get("requirement_id"),
                        "eligible": unit in eligible,
                        "excluded": "future" if unit in future else "expired" if unit in expired else None,
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
    for unit in candidate_units:
        if unit["unit_id"] not in selected:
            selected[unit["unit_id"]] = [{"reason": "semantic_candidate", "eligible": True, "requirement_id": None}]

    soft_budget = (run.get("budget") or {}).get("soft_units")
    required_ids = [
        unit_id
        for unit_id, reasons in selected.items()
        if any(item.get("reason") == "required_evidence" for item in reasons)
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
        _stable_reference(by_id[unit_id], root, selected[unit_id])
        for unit_id in selected_ids
        if unit_id in by_id
    ]
    required_rows = [row for row in coverage_rows if row["required"]]
    covered_count = sum(1 for row in required_rows if row["covered"])
    coverage = {
        "required_total": len(required_rows),
        "required_covered": covered_count,
        "required_missing": len(required_rows) - covered_count,
        "coverage_ratio": (covered_count / len(required_rows)) if required_rows else 1.0,
        "requirements": coverage_rows,
        "semantic_candidates_do_not_count": True,
    }
    result: dict[str, Any] = {
        "schema_version": WORKSPACE_SCHEMA,
        "run_id": run.get("run_id"),
        "stage": run.get("stage"),
        "workflow": run.get("workflow"),
        "task_evidence_manifest": task,
        "workspace": references,
        "workset": references,
        "stable_references": references,
        "coverage": coverage,
        "gaps": gaps,
        "projection": projection_info,
        "semantic_adapter": adapter_info,
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
    if run.get("persist_workset_manifest") or run.get("persist_workset"):
        result["workset_manifest_path"] = str(persist_workset_manifest(result, run))
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
        references = value.get("stable_references", value.get("references", value.get("workspace", [])))
        root = value.get("workspace_root") or value.get("root")
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


def _resolve_reference_units(root: Path, references: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    need = [item for item in references if not item.get("source_locator", {}).get("path")]
    if not need:
        return {}
    units, _ = _load_units(root, None)
    return {unit["unit_id"]: unit for unit in units}


def hydrate(
    stable_references: Any,
    workspace_root: Path | str | None = None,
    source_payload_store: SourcePayloadStore | None = None,
) -> dict[str, Any]:
    """Resolve stable references to bounded, hash-checked verification text."""

    references, embedded_root = _ref_input(stable_references)
    root = Path(workspace_root).resolve() if workspace_root else embedded_root
    if root is None:
        for reference in references:
            locator = reference.get("source_locator", {})
            if locator.get("absolute_path"):
                root = Path(str(locator["absolute_path"])).resolve().parent
                break
    if root is None:
        raise ValueError("hydrate requires workspace_root when references do not embed one")
    fallback_units = _resolve_reference_units(root, references)
    units: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    for reference in references:
        unit_id = str(reference.get("unit_id") or str(reference.get("ref", "")).removeprefix("atom:"))
        locator = dict(reference.get("source_locator") or {})
        if not locator.get("path") and unit_id in fallback_units:
            locator = dict(fallback_units[unit_id]["source_locator"])
        try:
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
                if expected_hash and expected_hash != actual_hash:
                    raise ValueError("source document content hash changed")
                lines = text.splitlines()
                start = max(1, int(locator.get("start_line", 1)))
                end = min(len(lines), int(locator.get("end_line", len(lines))))
                if start > end:
                    raise ValueError("source locator has an empty range")
                verification_text = "\n".join(lines[start - 1 : end])
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
                    "unit_type": reference.get("unit_type"),
                    "authority": reference.get("authority"),
                    "document_path": reference.get("document_path") or verification_locator.get("path"),
                    "information_cutoff": reference.get("information_cutoff"),
                    "status": reference.get("status"),
                    "objects": reference.get("objects", []),
                    "fields": reference.get("fields", []),
                    "evidence_roles": reference.get("evidence_roles", []),
                    "verification_text": verification_text,
                    "verification_excerpt": verification_text,
                    "verification_locator": verification_locator,
                    "source_payload_externalized": verification_locator.get("kind") == "source_payload",
                }
            )
        except (KeyError, OSError, UnicodeDecodeError, ValueError, FileNotFoundError) as exc:
            missing.append({"ref": reference.get("ref", f"atom:{unit_id}"), "unit_id": unit_id, "reason": str(exc)})
    return {
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


def _sha256_text(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def persist_workset_manifest(result: dict[str, Any], run_manifest: dict[str, Any] | None = None, path: Path | None = None) -> Path:
    """Persist only the visualisation-ready assembly audit, never source text."""

    run = run_manifest or result
    root = _root_from_manifest(run)
    run_id = str(result.get("run_id") or run.get("run_id") or "RUN-unknown")
    cutoff = str(run.get("information_cutoff") or "")
    month_match = re.search(r"(\d{4}-\d{2})", cutoff)
    run_date_match = re.search(r"RUN-(\d{4})(\d{2})\d{2}", run_id)
    month = month_match.group(1) if month_match else f"{run_date_match.group(1)}-{run_date_match.group(2)}" if run_date_match else "unknown"
    destination = path or (root / "运行记录" / month / f"{run_id}-工作集清单.json")
    destination = Path(destination)
    if not destination.is_absolute():
        destination = root / destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema_version": WORKSPACE_SCHEMA,
        "artifact_type": "workset_manifest",
        "id": f"{run_id}-WORKSET",
        "status": "degraded" if result.get("projection", {}).get("projection_degraded") else "partial" if result.get("gaps") else "completed",
        "run_id": run_id,
        "created_at": run.get("created_at") or cutoff or "当时未记录",
        "information_cutoff": cutoff or "当时未记录",
        "stage": result.get("stage") or run.get("stage"),
        "task_contract": {
            "contract_id": (result.get("task_evidence_manifest") or {}).get("contract_id"),
            "version": (result.get("task_evidence_manifest") or {}).get("version"),
        },
        "projection": result.get("projection", {}),
        "stable_references": result.get("stable_references", []),
        "coverage": result.get("coverage", {}),
        "gaps": result.get("gaps", []),
        "semantic_adapter": result.get("semantic_adapter", {}),
        "budget": result.get("budget", {}),
        "quality": result.get("quality", {}),
    }
    # References intentionally contain locators and hashes only.  Reject an
    # accidental raw payload before writing the audit file.
    serialized = json.dumps(manifest, ensure_ascii=False, indent=2)
    if "事实陈述" in serialized or "verification_text" in serialized:
        raise ValueError("workset manifest must not contain verification payload text")
    destination.write_text(serialized + "\n", encoding="utf-8")
    return destination
