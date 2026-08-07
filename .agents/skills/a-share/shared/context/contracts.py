"""Versioned task-contract loading and evidence-list instantiation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


WORKSPACE_SCHEMA = "a-share-workspace-v3"
CONTRACT_SCHEMA = "a-share-task-contract-v1"


def _read_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"JSON object expected: {path}")
    return value


def contract_directory() -> Path:
    return Path(__file__).resolve().parents[1] / "contracts"


def load_contract(reference: Any, workspace_root: Path | None = None) -> dict[str, Any]:
    """Load a contract by object, path, or repository contract id."""

    if isinstance(reference, dict):
        return reference
    if reference is None:
        raise ValueError("task contract is missing")
    candidate = Path(str(reference))
    paths: list[Path] = []
    if candidate.is_absolute():
        paths.append(candidate)
    else:
        if workspace_root is not None:
            paths.extend([workspace_root / candidate, workspace_root / "contracts" / candidate])
        paths.extend([contract_directory() / candidate, contract_directory() / f"{candidate}.json"])
    for path in paths:
        if path.is_file():
            contract = _read_json(path)
            validate_contract(contract)
            return contract
    raise FileNotFoundError(f"task contract not found: {reference}")


def validate_contract(contract: dict[str, Any]) -> None:
    if contract.get("schema_version") not in {CONTRACT_SCHEMA, WORKSPACE_SCHEMA}:
        raise ValueError("unsupported task contract schema_version")
    if not contract.get("contract_id"):
        raise ValueError("task contract requires contract_id")
    if not contract.get("version"):
        raise ValueError("task contract requires version")
    requirements = contract.get("required_evidence", contract.get("requirements", []))
    if not isinstance(requirements, list):
        raise ValueError("task contract required_evidence must be a list")
    for requirement in requirements:
        if not isinstance(requirement, dict) or not requirement.get("requirement_id"):
            raise ValueError("each task evidence requirement needs requirement_id")


def _contract_reference(run_manifest: dict[str, Any]) -> Any:
    for key in ("task_contract", "contract", "task_contract_ref"):
        if key in run_manifest:
            return run_manifest[key]
    return None


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

    if isinstance(task_evidence, (str, Path)):
        task_evidence = _read_json(Path(task_evidence))
    contract_reference = _contract_reference(run_manifest)
    contract: dict[str, Any] | None = None
    if contract_reference is not None:
        contract = load_contract(contract_reference, workspace_root)
    elif isinstance(task_evidence, dict) and task_evidence.get("contract") is not None:
        contract = load_contract(task_evidence["contract"], workspace_root)

    direct: dict[str, Any] = task_evidence if isinstance(task_evidence, dict) else {}
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

    # Preserve the first declaration of an id; a task can add conditions to an
    # existing item but cannot silently replace the contract's floor.
    by_id: dict[str, dict[str, Any]] = {}
    for item in requirements:
        item.setdefault("required", True)
        item.setdefault("allow_unknown", False)
        by_id.setdefault(str(item["requirement_id"]), item)

    result: dict[str, Any] = {
        "schema_version": WORKSPACE_SCHEMA,
        "contract_schema_version": contract.get("schema_version") if contract else None,
        "contract_id": contract.get("contract_id") if contract else direct.get("contract_id"),
        "version": contract.get("version") if contract else direct.get("version"),
        "strategy_version": run_manifest.get("strategy_version"),
        "required_evidence": list(by_id.values()),
    }
    if contract is None and not result["required_evidence"]:
        result["contract_status"] = "not_supplied"
    else:
        result["contract_status"] = "instantiated"
    return result
