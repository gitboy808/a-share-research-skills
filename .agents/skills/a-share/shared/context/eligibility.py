"""Central eligibility semantics for live, historical, and calibration worksets.

This is an internal implementation module.  Callers continue to use only
``assemble`` and ``hydrate``; compiled policies are serialized into stable
references so hydration can repeat the same decision at the same cutoff.
"""

from __future__ import annotations

import re
from datetime import datetime, time, timedelta, timezone
from typing import Any, Iterable


ELIGIBILITY_MODEL_VERSION = "a-share-eligibility-v1"
ELIGIBILITY_MODES = frozenset(
    {"prospective_current", "historical_as_of", "calibration_window"}
)
TERMINAL_JUDGMENT_RESULTS = frozenset(
    {"未触发", "兑现", "证伪", "不可判定", "untriggered", "realized", "falsified", "indeterminate"}
)
RETIRED_STATUSES = frozenset({"retired", "已退役", "退役"})
TERMINATED_STATUSES = frozenset(
    {"closed", "ended", "terminated", "expired", "已结案", "已结束", "已终止", "已失效"}
)
TIME_BOUND_UNIT_TYPES = frozenset(
    {"evidence_item", "judgment_version", "observation_candidate", "object_field"}
)


def parse_datetime(value: Any, *, reference: datetime | None = None) -> datetime | None:
    """Parse a timezone-aware timestamp or a date at local end-of-day."""

    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    timestamp = re.search(
        r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:?\d{2})",
        text,
    )
    if timestamp:
        try:
            parsed = datetime.fromisoformat(timestamp.group(0).replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo and parsed.utcoffset() is not None else None
    date_match = re.search(r"\d{4}-\d{2}-\d{2}", text)
    if date_match is None:
        return None
    try:
        parsed_date = datetime.fromisoformat(date_match.group(0)).date()
    except ValueError:
        return None
    tz = reference.tzinfo if reference and reference.tzinfo else timezone.utc
    return datetime.combine(parsed_date, time.max, tzinfo=tz)


def compile_policy(
    requirement: dict[str, Any],
    run_manifest: dict[str, Any],
    units: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    """Compile a requirement's declarative eligibility mode for audit/replay."""

    mode = str(requirement.get("eligibility_mode") or "prospective_current")
    if mode not in ELIGIBILITY_MODES:
        raise ValueError(f"unsupported eligibility_mode {mode!r}")
    cutoff_basis = str(
        requirement.get("cutoff_basis")
        or ("run_cutoff" if mode != "historical_as_of" else "run_cutoff")
    )
    if cutoff_basis not in {"run_cutoff", "unit_snapshot", "judgment_snapshot"}:
        raise ValueError(f"unsupported eligibility cutoff_basis {cutoff_basis!r}")
    run_cutoff = run_manifest.get("information_cutoff") or run_manifest.get(
        "snapshot_cutoff"
    )
    judgment_ids = {
        str(value).removeprefix("atom:")
        for value in ((run_manifest.get("handoff") or {}).get("judgment_ids") or [])
    }
    judgment_cutoffs = {
        str(unit.get("unit_id")): str(unit.get("information_cutoff"))
        for unit in units
        if str(unit.get("unit_id")) in judgment_ids
        and unit.get("unit_type") == "judgment_version"
        and parse_datetime(unit.get("information_cutoff")) is not None
    }
    related_judgment_cutoffs: dict[str, list[str]] = {}
    for unit in units:
        unit_id = str(unit.get("unit_id") or "")
        judgment_cutoff = judgment_cutoffs.get(unit_id)
        if judgment_cutoff is None:
            continue
        for relation in unit.get("relations") or []:
            if not isinstance(relation, dict):
                continue
            target_id = str(relation.get("to") or "")
            if target_id:
                related_judgment_cutoffs.setdefault(target_id, []).append(
                    judgment_cutoff
                )
    window_start = run_manifest.get("calibration_window_start") or run_manifest.get(
        "window_start"
    )
    if mode == "calibration_window" and parse_datetime(
        window_start, reference=parse_datetime(run_cutoff)
    ) is None:
        raise ValueError("calibration_window requires a timezone-aware window_start")
    return {
        "model_version": ELIGIBILITY_MODEL_VERSION,
        "mode": mode,
        "cutoff_basis": cutoff_basis,
        "run_cutoff": run_cutoff,
        "window_start": window_start,
        "judgment_cutoffs": judgment_cutoffs,
        "related_judgment_cutoffs": related_judgment_cutoffs,
        "max_age_days": requirement.get("max_age_days"),
        "allowed_lifecycle_statuses": list(
            requirement.get("allowed_lifecycle_statuses") or []
        ),
    }


def effective_cutoff(unit: dict[str, Any], policy: dict[str, Any]) -> datetime | None:
    basis = policy.get("cutoff_basis")
    if basis == "unit_snapshot":
        return parse_datetime(unit.get("information_cutoff"))
    if basis == "judgment_snapshot":
        related_cutoffs = [
            parsed
            for parsed in (
                parse_datetime(value)
                for value in (policy.get("related_judgment_cutoffs") or {}).get(
                    str(unit.get("unit_id") or ""), []
                )
            )
            if parsed is not None
        ]
        if not related_cutoffs:
            related_cutoffs = [
                parsed
                for parsed in (
                    parse_datetime(value)
                    for value in (policy.get("judgment_cutoffs") or {}).values()
                )
                if parsed is not None
            ]
        return min(related_cutoffs) if related_cutoffs else None
    return parse_datetime(policy.get("run_cutoff"))


def eligibility_exclusion(
    unit: dict[str, Any], policy: dict[str, Any]
) -> str | None:
    """Return the audited exclusion reason, or ``None`` when eligible."""

    if policy.get("model_version") != ELIGIBILITY_MODEL_VERSION:
        return "unsupported_eligibility_model"
    mode = str(policy.get("mode") or "")
    if mode not in ELIGIBILITY_MODES:
        return "unsupported_eligibility_mode"
    cutoff = effective_cutoff(unit, policy)
    unit_time = parse_datetime(unit.get("information_cutoff"))
    if cutoff is None:
        return (
            "missing_eligibility_cutoff"
            if mode in {"historical_as_of", "calibration_window"}
            else None
        )
    if unit_time is None and unit.get("unit_type") in TIME_BOUND_UNIT_TYPES:
        return "invalid_information_cutoff"
    if unit_time is not None and unit_time > cutoff:
        return "future_information"
    verification_locator = unit.get("verification_source_locator")
    acquired_at = (
        parse_datetime(verification_locator.get("acquired_at"))
        if isinstance(verification_locator, dict)
        else None
    )
    if acquired_at is not None and acquired_at > cutoff:
        return "future_information"

    if mode == "calibration_window":
        window_start = parse_datetime(policy.get("window_start"), reference=cutoff)
        if window_start is None:
            return "missing_calibration_window"
        if unit_time is None:
            return "missing_calibration_timestamp"
        if unit_time < window_start:
            return "outside_calibration_window"
        return None

    if mode == "historical_as_of":
        return _time_exclusion(unit, cutoff, policy)

    result_status = str(unit.get("result_status") or "").strip().casefold()
    result_recorded_at = parse_datetime(
        unit.get("result_recorded_at"), reference=cutoff
    )
    terminal_is_known = result_recorded_at is None or result_recorded_at <= cutoff
    if (
        unit.get("unit_type") == "judgment_version"
        and terminal_is_known
        and result_status
        in {value.casefold() for value in TERMINAL_JUDGMENT_RESULTS}
    ):
        return "terminal_judgment"
    status = str(
        unit.get("lifecycle_status") or unit.get("status") or ""
    ).strip().casefold()
    if status in {value.casefold() for value in RETIRED_STATUSES}:
        return "retired"
    if status in {value.casefold() for value in TERMINATED_STATUSES}:
        return "terminated"
    if unit.get("unit_type") == "strategy_version":
        allowed = {
            str(value).strip().casefold()
            for value in policy.get("allowed_lifecycle_statuses", [])
        } or {"trial", "official"}
        if status and status not in allowed:
            return "limited" if status == "limited" else "ineligible_strategy_status"
    return _time_exclusion(unit, cutoff, policy)


def supersession_exclusion(
    unit: dict[str, Any],
    policy: dict[str, Any],
    units: Iterable[dict[str, Any]],
) -> str | None:
    """Exclude only explicit successor versions from a prospective view."""

    if policy.get("mode") != "prospective_current":
        return None
    cutoff = effective_cutoff(unit, policy)
    unit_id = str(unit.get("unit_id") or "")
    logical_id = str(unit.get("logical_id") or "").strip()
    version = _version_key(unit.get("logical_version"))
    for candidate in units:
        if candidate is unit:
            continue
        candidate_time = parse_datetime(candidate.get("information_cutoff"))
        if cutoff is not None and candidate_time is not None and candidate_time > cutoff:
            continue
        if any(
            isinstance(relation, dict)
            and relation.get("type") == "supersedes"
            and str(relation.get("to") or "") == unit_id
            for relation in candidate.get("relations", [])
        ):
            return "superseded"
        if not logical_id or str(candidate.get("logical_id") or "").strip() != logical_id:
            continue
        candidate_version = _version_key(candidate.get("logical_version"))
        if version is not None and candidate_version is not None and candidate_version > version:
            return "superseded"
    return None


def _version_key(value: Any) -> tuple[tuple[int, Any], ...] | None:
    text = str(value or "").strip().removeprefix("v")
    if not text:
        return None
    parts: list[tuple[int, Any]] = []
    for token in re.split(r"[._-]", text):
        parts.append((0, int(token)) if token.isdigit() else (1, token.casefold()))
    return tuple(parts)


def _time_exclusion(
    unit: dict[str, Any], cutoff: datetime, policy: dict[str, Any]
) -> str | None:
    invalidated_at = parse_datetime(unit.get("invalidated_at"), reference=cutoff)
    if invalidated_at is not None and invalidated_at <= cutoff:
        return "event_invalidated"
    terminated_at = parse_datetime(unit.get("terminated_at"), reference=cutoff)
    if terminated_at is not None and terminated_at <= cutoff:
        return "terminated"
    valid_until = parse_datetime(unit.get("valid_until"), reference=cutoff)
    if valid_until is not None and valid_until < cutoff:
        return "expired"
    max_age_days = policy.get("max_age_days")
    unit_time = parse_datetime(unit.get("information_cutoff"))
    if max_age_days is not None and unit_time is not None:
        try:
            if unit_time < cutoff - timedelta(days=float(max_age_days)):
                return "expired"
        except (TypeError, ValueError):
            return "invalid_max_age_days"
    return None
