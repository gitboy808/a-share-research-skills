"""Versioned task-contract loading and evidence-list instantiation."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .eligibility import ELIGIBILITY_MODES


WORKSPACE_SCHEMA = "a-share-workspace-v3"
CONTRACT_SCHEMA = "a-share-task-contract-v1"
SUPPORTED_CONTRACT_SCHEMAS = {CONTRACT_SCHEMA}
RESEARCH_WORKFLOWS = {"scan", "investigate", "analyze", "review", "meta-review"}
UNIT_TYPE_ALIASES = {
    "evidence": "evidence_item",
    "evidence_item": "evidence_item",
    "judgment": "judgment_version",
    "judgment_version": "judgment_version",
    "object": "object_field",
    "object_field": "object_field",
    "strategy": "strategy_version",
    "strategy_version": "strategy_version",
}


def _read_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"JSON object expected: {path}")
    return value


def _constraint_values(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if str(item).strip()]
    return [str(value)]


def normalize_instantiated_requirements(
    requirements: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return the exact selector form consumed by workset assembly.

    This pure function is shared by runtime persistence and read-only
    validation so a manifest cannot self-attest to a truncated binding set.
    """

    normalized: list[dict[str, Any]] = []
    for requirement in requirements:
        result = dict(requirement)
        if "unit_types" not in result:
            result["unit_types"] = _constraint_values(
                result.get("unit_type", result.get("kind"))
            )
        result["unit_types"] = [
            UNIT_TYPE_ALIASES.get(str(value), str(value))
            for value in result["unit_types"]
            if value
        ]
        if "objects" not in result:
            result["objects"] = _constraint_values(result.get("object"))
        if "fields" not in result:
            result["fields"] = _constraint_values(result.get("field"))
        if "roles" not in result:
            result["roles"] = _constraint_values(
                result.get("evidence_role", result.get("role"))
            )
        result["required"] = bool(result.get("required", True))
        result["allow_unknown"] = bool(result.get("allow_unknown", False))
        normalized.append(result)
    return normalized


def contract_directory() -> Path:
    return Path(__file__).resolve().parents[1] / "contracts"


def load_contract(reference: Any, workspace_root: Path | None = None) -> dict[str, Any]:
    """Load a contract by object, path, or repository contract id."""

    if isinstance(reference, dict):
        validate_contract(reference)
        return reference
    if reference is None:
        raise ValueError("task contract is missing")
    candidate = Path(str(reference))
    approved_roots = [contract_directory().resolve()]
    if workspace_root is not None:
        approved_roots.insert(0, (workspace_root / "contracts").resolve())

    def approved(path: Path) -> bool:
        resolved = path.resolve()
        return any(root in [resolved, *resolved.parents] for root in approved_roots)

    paths: list[Path] = []
    if candidate.is_absolute():
        if not approved(candidate):
            raise ValueError("task contract path is outside approved contract roots")
        paths.append(candidate)
    else:
        if workspace_root is not None:
            workspace_candidate = (
                workspace_root / candidate
                if candidate.parts and candidate.parts[0] == "contracts"
                else workspace_root / "contracts" / candidate
            )
            if approved(workspace_candidate):
                paths.append(workspace_candidate)
        paths.extend([contract_directory() / candidate, contract_directory() / f"{candidate}.json"])
    for path in paths:
        if approved(path) and path.is_file():
            contract = _read_json(path)
            validate_contract(contract)
            return contract
    contract_id = str(reference)
    matches: list[dict[str, Any]] = []
    directories = [contract_directory()]
    if workspace_root is not None:
        directories.insert(0, workspace_root / "contracts")
    seen: set[Path] = set()
    for directory in directories:
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.json")):
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            value = _read_json(path)
            if value.get("contract_id") == contract_id:
                validate_contract(value)
                matches.append(value)
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise ValueError(f"task contract id is ambiguous; select an explicit versioned file: {reference}")
    raise FileNotFoundError(f"task contract not found: {reference}")


def _load_registered_contract(reference: Any) -> dict[str, Any]:
    if isinstance(reference, dict):
        raise ValueError(
            "research and persistent runs require a registered repository shared/contracts reference"
        )
    candidate = Path(str(reference))
    if candidate.is_absolute():
        resolved = candidate.resolve()
        repository_root = contract_directory().resolve()
        if repository_root not in [resolved, *resolved.parents]:
            raise ValueError(
                "research and persistent runs require a registered repository shared/contracts reference"
            )
    contract = load_contract(reference)
    for requirement in contract.get("required_evidence", []):
        if requirement.get("eligibility_mode") not in ELIGIBILITY_MODES:
            raise ValueError(
                "registered task contract requirements require an explicit eligibility_mode"
            )
    return contract


def _registered_contract_provenance(contract: dict[str, Any]) -> dict[str, str]:
    matches: list[Path] = []
    for path in sorted(contract_directory().glob("*.json")):
        value = _read_json(path)
        if (
            value.get("contract_id") == contract.get("contract_id")
            and value.get("version") == contract.get("version")
        ):
            matches.append(path.resolve())
    if len(matches) != 1:
        raise ValueError(
            "registered task contract identity must resolve to exactly one versioned file"
        )
    path = matches[0]
    repository_root = Path(__file__).resolve().parents[5]
    try:
        relative = path.relative_to(repository_root).as_posix()
    except ValueError as exc:  # pragma: no cover - deployment invariant
        raise ValueError("registered task contract is outside the runtime surface") from exc
    return {
        "registry_path": relative,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def validate_contract(contract: dict[str, Any]) -> None:
    if contract.get("schema_version") not in SUPPORTED_CONTRACT_SCHEMAS:
        raise ValueError("unsupported task contract schema_version")
    if not contract.get("contract_id"):
        raise ValueError("task contract requires contract_id")
    if not contract.get("version"):
        raise ValueError("task contract requires version")
    if not contract.get("workflow"):
        raise ValueError("task contract requires workflow applicability")
    if not contract.get("stage"):
        raise ValueError("task contract requires stage applicability")
    requirements = contract.get("required_evidence", contract.get("requirements", []))
    if not isinstance(requirements, list):
        raise ValueError("task contract required_evidence must be a list")
    if not requirements:
        raise ValueError("task contract requires at least one evidence requirement")
    seen_requirement_ids: set[str] = set()
    for requirement in requirements:
        if not isinstance(requirement, dict) or not requirement.get("requirement_id"):
            raise ValueError("each task evidence requirement needs requirement_id")
        requirement_id = str(requirement["requirement_id"])
        if requirement_id in seen_requirement_ids:
            raise ValueError(f"task contract has duplicate requirement_id {requirement_id!r}")
        seen_requirement_ids.add(requirement_id)
        eligibility_mode = requirement.get("eligibility_mode")
        if eligibility_mode is not None and eligibility_mode not in ELIGIBILITY_MODES:
            raise ValueError(
                f"task requirement {requirement_id!r} has unsupported eligibility_mode"
            )
    if not any(requirement.get("required", True) for requirement in requirements):
        raise ValueError("task contract requires at least one required floor item")


def _contract_reference(run_manifest: dict[str, Any]) -> Any:
    return run_manifest.get("task_contract")


def _requires_contract(run_manifest: dict[str, Any]) -> bool:
    workflow = str(run_manifest.get("workflow") or "").strip().casefold().replace("_", "-")
    persistent = bool(run_manifest.get("persist_workset_manifest"))
    return workflow in RESEARCH_WORKFLOWS or persistent


def _normalise_name(value: Any) -> str:
    return str(value or "").strip().casefold().replace("_", "-")


def _object_type(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get("object_type", value.get("type", value.get("object", value.get("id"))))
    normalised = "".join(str(value or "").split()).casefold().replace("_", "")
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
        if normalised.startswith(prefix):
            return result
    return "unknown"


def _validate_contract_alignment(contract: dict[str, Any], run_manifest: dict[str, Any]) -> None:
    expected_workflow = _normalise_name(contract.get("workflow"))
    actual_workflow = _normalise_name(run_manifest.get("workflow"))
    if expected_workflow and expected_workflow != actual_workflow:
        raise ValueError(
            f"task contract workflow {contract['workflow']!r} does not match run workflow {run_manifest.get('workflow')!r}"
        )
    expected_stage = _normalise_name(contract.get("stage"))
    actual_stage = _normalise_name(run_manifest.get("stage"))
    if expected_stage and expected_stage != actual_stage:
        raise ValueError(
            f"task contract stage {contract['stage']!r} does not match run stage {run_manifest.get('stage')!r}"
        )
    expected_object_types = {
        _object_type(value) for value in contract.get("object_types", []) if _object_type(value) != "unknown"
    }
    if expected_object_types:
        run_objects = run_manifest.get("objects") or []
        if not isinstance(run_objects, (list, tuple, set)):
            run_objects = [run_objects]
        actual_object_types = {_object_type(value) for value in run_objects}
        if not actual_object_types or not actual_object_types.issubset(expected_object_types):
            raise ValueError(
                "task contract object_types "
                f"{sorted(expected_object_types)!r} do not match run objects {sorted(actual_object_types)!r}"
            )
    if expected_workflow in RESEARCH_WORKFLOWS:
        snapshot = run_manifest.get("information_cutoff")
        if not snapshot:
            raise ValueError("research workflow requires an information snapshot cutoff")
        try:
            parsed_snapshot = datetime.fromisoformat(str(snapshot).replace("Z", "+00:00"))
        except ValueError:
            raise ValueError("research workflow requires a valid information snapshot cutoff") from None
        if parsed_snapshot.tzinfo is None or parsed_snapshot.utcoffset() is None:
            raise ValueError("research workflow requires a valid information snapshot cutoff with timezone")


def _run_objects(run_manifest: dict[str, Any]) -> list[Any]:
    values = run_manifest.get("objects") or []
    return list(values) if isinstance(values, (list, tuple, set)) else [values]


def _instantiate_object_bindings(
    requirements: list[dict[str, Any]],
    contract: dict[str, Any] | None,
    run_manifest: dict[str, Any],
) -> list[dict[str, Any]]:
    contract_object_types = {
        _object_type(value) for value in (contract or {}).get("object_types", [])
    }
    expanded: list[dict[str, Any]] = []
    for requirement in requirements:
        if not requirement.get("bind_to_run_objects"):
            expanded.append(requirement)
            continue
        targets = [
            value
            for value in _run_objects(run_manifest)
            if not contract_object_types or _object_type(value) in contract_object_types
        ]
        if not targets:
            raise ValueError(
                f"task requirement {requirement.get('requirement_id')!r} has no matching run object"
            )
        for target in targets:
            bound = dict(requirement)
            base_id = str(bound["requirement_id"])
            bound["base_requirement_id"] = base_id
            bound["requirement_id"] = f"{base_id}@{target}"
            bound["object"] = str(target)
            bound["objects"] = [str(target)]
            bound["object_match"] = "exact"
            expanded.append(bound)
    return expanded


def _instantiate_handoff_bindings(
    requirements: list[dict[str, Any]], run_manifest: dict[str, Any]
) -> list[dict[str, Any]]:
    handoff = run_manifest.get("handoff")
    handoff = handoff if isinstance(handoff, dict) else {}
    bound_requirements: list[dict[str, Any]] = []
    for requirement in requirements:
        handoff_key = requirement.get("bind_to_handoff")
        if not handoff_key:
            bound_requirements.append(requirement)
            continue
        values = handoff.get(str(handoff_key)) or []
        if not isinstance(values, (list, tuple, set)):
            values = [values]
        stable_ids = [str(value).removeprefix("atom:") for value in values if str(value).strip()]
        if not stable_ids:
            raise ValueError(f"formal handoff requires non-empty {handoff_key}")
        bound = dict(requirement)
        bound["unit_ids"] = stable_ids
        bound_requirements.append(bound)
    return bound_requirements


def _instantiate_strategy_bindings(
    requirements: list[dict[str, Any]], run_manifest: dict[str, Any]
) -> list[dict[str, Any]]:
    values = run_manifest.get("strategy_version") or []
    if not isinstance(values, (list, tuple, set)):
        values = [values]
    stable_ids = [
        str(value).removeprefix("atom:") for value in values if str(value).strip()
    ]
    bound_requirements: list[dict[str, Any]] = []
    for requirement in requirements:
        if not requirement.get("bind_to_strategy_version"):
            bound_requirements.append(requirement)
            continue
        bound = dict(requirement)
        bound["unit_ids"] = stable_ids or ["__missing_strategy_version__"]
        bound_requirements.append(bound)
    return bound_requirements


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _merge_requirement(
    current: dict[str, Any], addition: dict[str, Any]
) -> dict[str, Any]:
    merged = dict(current)
    for key, value in addition.items():
        if key == "requirement_id":
            continue
        if key not in merged:
            merged[key] = value
        elif key == "allow_unknown" and isinstance(merged[key], bool) and isinstance(value, bool):
            if merged[key] is False and value is True:
                raise ValueError(
                    f"task requirement {current['requirement_id']!r} cannot weaken field 'allow_unknown'"
                )
            merged[key] = merged[key] and value
        elif key == "required" and isinstance(merged[key], bool) and isinstance(value, bool):
            if merged[key] is True and value is False:
                raise ValueError(
                    f"task requirement {current['requirement_id']!r} cannot weaken field 'required'"
                )
            merged[key] = merged[key] or value
        elif (
            key == "min_source_groups"
            and _is_number(merged[key])
            and _is_number(value)
        ):
            merged[key] = max(merged[key], value)
        elif (
            key == "max_age_days"
            and _is_number(merged[key])
            and _is_number(value)
        ):
            merged[key] = min(merged[key], value)
        elif merged[key] != value:
            raise ValueError(
                f"task requirement {current['requirement_id']!r} cannot safely merge field {key!r}"
            )
    return merged


def _normalise_requirement_constraints(requirement: dict[str, Any]) -> dict[str, Any]:
    normalised = dict(requirement)
    if "freshness" not in normalised:
        return normalised
    freshness = normalised.pop("freshness")
    if isinstance(freshness, dict):
        if set(freshness) != {"max_age_days"}:
            raise ValueError("task requirement freshness must contain only max_age_days")
        freshness = freshness["max_age_days"]
    if not _is_number(freshness):
        raise ValueError("task requirement freshness max_age_days must be numeric")
    if "max_age_days" in normalised:
        normalised = _merge_requirement(
            normalised,
            {
                "requirement_id": normalised["requirement_id"],
                "max_age_days": freshness,
            },
        )
    else:
        normalised["max_age_days"] = freshness
    return normalised


def instantiate_task_evidence(
    run_manifest: dict[str, Any],
    task_evidence: Any = None,
    workspace_root: Path | None = None,
) -> dict[str, Any]:
    """Return the immutable evidence requirements for this run.

    A caller-supplied evidence manifest may add requirements but never removes
    requirements already supplied by a versioned contract.  This is the
    enforcement point for the ADR-0025 non-compensation rule.
    """

    if _requires_contract(run_manifest) and run_manifest.get("schema_version") != WORKSPACE_SCHEMA:
        raise ValueError(
            f"research run manifest schema_version must be {WORKSPACE_SCHEMA!r}"
        )
    if isinstance(task_evidence, (str, Path)):
        task_evidence = _read_json(Path(task_evidence))
    contract_reference = _contract_reference(run_manifest)
    formal_contract_required = _requires_contract(run_manifest)
    task_evidence_contract = (
        task_evidence.get("contract") if isinstance(task_evidence, dict) else None
    )
    if formal_contract_required and isinstance(task_evidence_contract, dict):
        raise ValueError(
            "research and persistent runs require a registered repository shared/contracts reference"
        )
    if formal_contract_required and contract_reference is None:
        raise ValueError("task contract is required in the run manifest for research workflow")
    if (
        formal_contract_required
        and task_evidence_contract is not None
    ):
        raise ValueError(
            "research and persistent runs require exactly one registered repository shared/contracts reference"
        )
    if formal_contract_required and isinstance(contract_reference, dict):
        raise ValueError(
            "research and persistent runs require a registered repository shared/contracts reference"
        )
    contract: dict[str, Any] | None = None
    if contract_reference is not None:
        contract = (
            _load_registered_contract(contract_reference)
            if formal_contract_required
            else load_contract(contract_reference, workspace_root)
        )
    elif task_evidence_contract is not None:
        contract = load_contract(task_evidence_contract, workspace_root)

    if contract is not None:
        _validate_contract_alignment(contract, run_manifest)

    direct: dict[str, Any] = task_evidence if isinstance(task_evidence, dict) else {}
    if contract is None and _requires_contract(run_manifest):
        raise ValueError("task contract is required for research workflow")
    requirements: list[dict[str, Any]] = []
    if contract:
        requirements.extend(dict(item) for item in contract.get("required_evidence", contract.get("requirements", [])))
    if isinstance(task_evidence, list):
        requirements.extend(dict(item) for item in task_evidence)
    else:
        requirements.extend(
            dict(item)
            for item in direct.get("required_evidence", direct.get("requirements", []))
            if isinstance(item, dict)
        )
    # Conditions with the same id refine the registered requirement before
    # handoff/object expansion.  They must never become an unbound parallel
    # requirement or disappear behind first-write-wins deduplication.
    by_id: dict[str, dict[str, Any]] = {}
    for raw_item in requirements:
        item = _normalise_requirement_constraints(raw_item)
        requirement_id = str(item["requirement_id"])
        if requirement_id in by_id:
            by_id[requirement_id] = _merge_requirement(by_id[requirement_id], item)
        else:
            item.setdefault("required", True)
            item.setdefault("allow_unknown", False)
            by_id[requirement_id] = item
    requirements = _instantiate_handoff_bindings(list(by_id.values()), run_manifest)
    requirements = _instantiate_strategy_bindings(requirements, run_manifest)
    requirements = _instantiate_object_bindings(requirements, contract, run_manifest)

    result: dict[str, Any] = {
        "schema_version": WORKSPACE_SCHEMA,
        "contract_schema_version": contract.get("schema_version") if contract else None,
        "contract_id": contract.get("contract_id") if contract else direct.get("contract_id"),
        "version": contract.get("version") if contract else direct.get("version"),
        "strategy_version": run_manifest.get("strategy_version"),
        "required_evidence": requirements,
    }
    if formal_contract_required and contract is not None:
        result["contract_registry"] = _registered_contract_provenance(contract)
    if contract is None and not result["required_evidence"]:
        result["contract_status"] = "not_supplied"
    else:
        result["contract_status"] = "instantiated"
    return result
