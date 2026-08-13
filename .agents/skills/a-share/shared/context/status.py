"""Conservative status classification shared by parsing and assembly."""

from __future__ import annotations

import re
from typing import Any


_CONFLICT_STATUSES = frozenset({"冲突", "已否证", "否证", "conflict", "denied", "falsified", "否决"})
_UNKNOWN_STATUSES = frozenset({"unknown", "未知", "未证实", "不可取得", "当时未记录"})
_CONFIRMED_EVIDENCE_STATUSES = frozenset({"已确认", "多源印证", "confirmed", "verified"})


def normalise_status(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).casefold()


_NORMALISED_CONFLICT_MARKERS = frozenset(normalise_status(marker) for marker in _CONFLICT_STATUSES)
_NORMALISED_UNKNOWN_STATUSES = frozenset(normalise_status(marker) for marker in _UNKNOWN_STATUSES)
_NORMALISED_CONFIRMED_EVIDENCE_STATUSES = frozenset(
    normalise_status(marker) for marker in _CONFIRMED_EVIDENCE_STATUSES
)


def _contains_marker(value: str, marker: str) -> bool:
    if marker.isascii() and marker.isalpha():
        return re.search(rf"(?<![a-z]){re.escape(marker)}(?![a-z])", value) is not None
    return marker in value


def status_exclusion(value: Any) -> str | None:
    """Return the conservative exclusion class for a status, if any.

    Conflict/denial markers remain blocking when embedded in a compound
    status such as ``已确认 / 冲突（口径）``.  An unknown marker embedded in
    a longer status is also excluded: ``已确认 / 未证实`` combines different
    propositions and must be atomized before either proposition can satisfy
    an evidence requirement.
    """

    normalised = normalise_status(value)
    if not normalised:
        return None
    if any(_contains_marker(normalised, marker) for marker in _NORMALISED_CONFLICT_MARKERS):
        return "conflict_or_denial"
    if normalised in _NORMALISED_UNKNOWN_STATUSES:
        return "unknown"
    if any(_contains_marker(normalised, marker) for marker in _NORMALISED_UNKNOWN_STATUSES):
        return "mixed_status_requires_atomization"
    return None


def is_conflict_status(value: Any) -> bool:
    return status_exclusion(value) == "conflict_or_denial"


def unit_status_exclusion(unit_type: Any, value: Any) -> str | None:
    """Classify status conservatively for the atomic unit being selected."""

    exclusion = status_exclusion(value)
    if exclusion is not None:
        return exclusion
    if str(unit_type or "") == "evidence_item":
        if normalise_status(value) not in _NORMALISED_CONFIRMED_EVIDENCE_STATUSES:
            return "unrecognized_evidence_status"
    return None
