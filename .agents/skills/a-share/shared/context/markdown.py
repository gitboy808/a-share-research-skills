"""Small, dependency-free Markdown readers used by the derived projection.

The parser deliberately extracts locations and metadata rather than treating a
Markdown document as a bag of chunks.  The document remains the authority;
these dictionaries are only an index representation and are safe to rebuild.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable

from .status import is_conflict_status


FRONTMATTER_MARKER = "---"
HEADING_RE = re.compile(r"^(#{2,6})\s+(.+?)\s*$")
ID_RE = re.compile(r"\b(?:EVI-[A-Za-z0-9-]+#\d+|J\d{8}-\d{3}(?:\s+v\d+)?|C\d{8}-\d{3}(?:\s+v\d+)?)\b")
RULE_RE = re.compile(r"^-\s+\*\*(R\d{2})\b[^*]*\*\*\s*:?(.*)$")


class MarkdownParseError(ValueError):
    """An authoritative Markdown document could not be parsed safely."""


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_text(value: str) -> str:
    return sha256_bytes(value.encode("utf-8"))


def _scalar(value: str) -> Any:
    value = value.strip()
    if not value:
        return ""
    if (value.startswith('"') and value.endswith('"')) or (
        value.startswith("'") and value.endswith("'")
    ):
        return value[1:-1]
    if value.startswith(("[", "{")):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    if value.lower() in {"true", "false"}:
        return value.lower() == "true"
    if value.lower() in {"null", "none", "~"}:
        return None
    return value


def parse_frontmatter(text: str) -> tuple[dict[str, Any], int]:
    """Return frontmatter and the first body line number (one-based)."""

    if not text.startswith(f"{FRONTMATTER_MARKER}\n"):
        return {}, 1
    end = text.find("\n---\n", 4)
    if end < 0:
        return {}, 1
    result: dict[str, Any] = {}
    for raw in text[4:end].splitlines():
        if not raw.strip() or raw.lstrip().startswith("#") or ":" not in raw:
            continue
        key, value = raw.split(":", 1)
        result[key.strip()] = _scalar(value)
    return result, text[: end + 5].count("\n") + 1


def split_sections(text: str) -> list[dict[str, Any]]:
    lines = text.splitlines()
    headings: list[tuple[int, str, str]] = []
    for index, line in enumerate(lines, start=1):
        match = HEADING_RE.match(line)
        if match:
            headings.append((index, match.group(1), match.group(2).strip()))
    sections: list[dict[str, Any]] = []
    for position, (start, level, title) in enumerate(headings):
        end = headings[position + 1][0] - 1 if position + 1 < len(headings) else len(lines)
        content = "\n".join(lines[start - 1 : end])
        sections.append(
            {
                "title": title,
                "level": len(level),
                "start_line": start,
                "end_line": end,
                "content": content,
            }
        )
    return sections


def _split_values(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value)
    return [part.strip() for part in re.split(r"[,;、\n]", text) if part.strip()]


def _evidence_atomic_references(value: Any) -> list[str]:
    """Expand canonical and legacy package-plus-``#NNN`` evidence refs."""

    text = str(value or "")
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


def _source_locations(values: list[str]) -> list[Any]:
    """Parse only explicit structured source coordinates.

    Invalid/free-text values remain visible for workspace validation but are
    never promoted to a payload locator by the projection.
    """

    locations: list[Any] = []
    for value in values:
        candidate = value.strip().strip("`")
        try:
            parsed = json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            locations.append(value)
            continue
        if not isinstance(parsed, dict):
            locations.append(value)
            continue
        locator = dict(parsed)
        if {"payload_id", "path", "sha256"} <= set(locator):
            locator["kind"] = "source_payload"
            if "line_start" in locator:
                locator["start_line"] = locator["line_start"]
            if "line_end" in locator:
                locator["end_line"] = locator["line_end"]
        elif {"url", "anchor"} <= set(locator):
            locator["kind"] = "remote_url"
        locations.append(locator)
    return locations


def _normal_key(key: str) -> str:
    key = re.sub(r"[：:]\s*$", "", key.strip().lower())
    key = re.sub(r"[\s_*`]+", "", key)
    return key


def body_fields(content: str) -> dict[str, list[str]]:
    fields: dict[str, list[str]] = {}
    pattern = re.compile(r"^\s*(?:[-*]\s*)?\*\*([^*]+)\*\*\s*[：:]\s*(.*?)\s*$")
    for line in content.splitlines():
        match = pattern.match(line)
        if not match:
            continue
        key = _normal_key(match.group(1))
        value = match.group(2).strip()
        if value:
            fields.setdefault(key, []).append(value)
    return fields


def _field_aliases(fields: dict[str, list[str]]) -> dict[str, list[str]]:
    aliases = {
        "事实陈述": "fact",
        "事实": "fact",
        "原子命题": "proposition",
        "关联对象/档案字段": "object_field",
        "对象类型/名称": "object",
        "研究对象": "object",
        "状态": "status",
        "研究状态": "status",
        "信息快照": "information_cutoff",
        "信息截止": "information_cutoff",
        "信息截止时间": "information_cutoff",
        "快照时间": "information_cutoff",
        "数据交易日": "market_date",
        "事件时间/市场交易日": "market_date",
        "过期条件/下次复核": "expiry",
        "证据角色": "evidence_role",
        "来源链": "source_chain",
        "来源组id": "source_group",
        "来源定位": "source_location",
        "上游判断": "upstream_judgment",
        "证据包/原子证据项": "evidence_reference",
        "迁移证据审计": "historical_evidence_reference",
        "历史证据引用": "historical_evidence_reference",
        "基础画像/战术修饰/策略版本": "strategy_reference",
        "策略版本": "strategy_reference",
        "结果状态": "result_status",
        "结果记录时间": "result_recorded_at",
        "时限": "valid_until",
        "事件失效时间": "invalidated_at",
        "终止时间": "terminated_at",
        "生命周期状态": "lifecycle_status",
        "字段状态": "lifecycle_status",
        "字段身份": "logical_id",
        "逻辑字段id": "logical_id",
        "逻辑主张id": "logical_id",
        "字段版本": "logical_version",
        "最后核实时间": "information_cutoff",
        "有效至/下次复核": "expiry",
        "来源引用": "evidence_reference",
        "替代": "supersedes",
        "supersedes": "supersedes",
    }
    result = dict(fields)
    for key, values in fields.items():
        target = aliases.get(key)
        if target and target not in result:
            result[target] = list(values)
    return result


def _object_and_fields(metadata: dict[str, Any], fields: dict[str, list[str]], path: Path) -> tuple[list[str], list[str]]:
    objects = _split_values(metadata.get("objects"))
    object_fields = fields.get("object_field", [])
    for value in object_fields:
        parts = re.split(r"\s*/\s*", value, maxsplit=1)
        left = parts[0]
        separator = "/" if len(parts) > 1 else ""
        right = parts[1] if len(parts) > 1 else ""
        if left.strip():
            objects.append(left.strip())
        if separator and right.strip():
            fields.setdefault("field", []).extend(_split_values(right))
    if not objects and "对象" in fields:
        objects.extend(fields["对象"])
    if not objects and "object" in fields:
        objects.extend(fields["object"])
    if not objects and "对象档案" in path.parts:
        objects.append(path.stem)
    normalized_objects = list(dict.fromkeys(item for item in objects if item))
    field_values: list[str] = []
    for key in ("field", "fields", "关联字段"):
        for value in fields.get(key, []):
            field_values.extend(_split_values(value))
    normalized_fields = list(dict.fromkeys(item for item in field_values if item))
    return normalized_objects, normalized_fields


def _authority(path: Path, metadata: dict[str, Any] | None = None) -> str:
    metadata = metadata or {}
    if (
        metadata.get("artifact_type") == "historical_record"
        or metadata.get("authority") == "migration_audit"
    ):
        return "migration_audit"
    parts = path.parts
    if "证据包" in parts:
        return "evidence_package"
    if "判断日志" in parts:
        return "judgment_log"
    if "对象档案" in parts:
        return "object_dossier"
    if "策略库" in parts:
        return "strategy_library"
    if path.name == "研究规则.md":
        return "research_rules"
    if path.name == "经验库.md":
        return "lesson_library"
    if "观察日志" in parts:
        return "observation_log"
    if path.name == "观察池.md":
        return "observation_view"
    if "运行记录" in parts:
        return "run_record"
    return "document"


def _unit_type(path: Path, section: dict[str, Any], metadata: dict[str, Any]) -> str | None:
    authority = _authority(path, metadata)
    title = str(section.get("title", ""))
    if authority == "evidence_package" and re.search(r"EVI-[A-Za-z0-9-]+#\d+", title):
        return "evidence_item"
    if authority == "judgment_log" and re.search(r"\bJ\d{8}-\d{3}", title):
        return "judgment_version"
    if authority == "lesson_library" and re.match(r"L\d+\b", title):
        return "lesson"
    if authority == "observation_log" and re.search(r"\bC\d{8}-\d{3}", title):
        return "observation_candidate"
    if authority == "research_rules" and re.match(r"R\d{2}\b", title):
        return "rule"
    if (
        authority == "object_dossier"
        and section.get("level", 0) >= 3
        and re.fullmatch(r"FIELD\s+[A-Za-z0-9._:-]+\s+v[A-Za-z0-9._-]+", title)
    ):
        return "object_field"
    if metadata.get("artifact_type") == "strategy_version" and section.get("level", 0) == 1:
        return "strategy_version"
    return None


def _stable_id(
    path: Path,
    section: dict[str, Any],
    metadata: dict[str, Any],
    unit_type: str,
    fields: dict[str, list[str]],
) -> str:
    title = str(section.get("title", ""))
    match = ID_RE.search(title)
    if match:
        value = match.group(0)
        if unit_type in {"judgment_version", "observation_candidate"}:
            return value
        return value.split(" v", 1)[0]
    if unit_type == "strategy_version" and metadata.get("id"):
        version = metadata.get("version")
        if not version:
            raise ValueError(f"strategy version is missing for {path.as_posix()}")
        return f"{metadata['id']}@{version}"
    if unit_type == "object_field" and metadata.get("id"):
        logical_id = str((fields.get("logical_id") or [""])[0]).strip()
        version = str((fields.get("logical_version") or [""])[0]).strip().removeprefix("v")
        if logical_id and version:
            if not re.fullmatch(r"[A-Za-z0-9._:-]+", logical_id):
                raise ValueError(f"object field identity is invalid for {path.as_posix()}")
            if not re.fullmatch(r"[A-Za-z0-9._-]+", version):
                raise ValueError(f"object field version is invalid for {path.as_posix()}")
            return f"{metadata['id']}#{logical_id}@{version}"
    digest = hashlib.sha256(f"{path.as_posix()}:{title}".encode("utf-8")).hexdigest()[:16]
    prefix = {
        "object_field": "OBJ",
        "rule": "RULE",
        "lesson": "LESSON",
        "observation_candidate": "OBS",
    }.get(unit_type, "UNIT")
    return f"{prefix}:{digest}"


def _section_for_document(path: Path, text: str, metadata: dict[str, Any]) -> list[dict[str, Any]]:
    sections = split_sections(text)
    if sections:
        return sections
    lines = text.splitlines()
    return [
        {
            "title": path.stem,
            "level": 1,
            "start_line": 1,
            "end_line": max(1, len(lines)),
            # Hydration reconstructs bounded Markdown with ``splitlines`` and
            # joins it without a terminal newline.  Use the same canonical
            # representation here so whole-document atoms hash identically.
            "content": "\n".join(lines),
        }
    ]


def extract_units(root: Path, path: Path, *, strict: bool = False) -> list[dict[str, Any]]:
    """Extract authoritative atomic units from one Markdown file."""

    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        if strict:
            relative = path.relative_to(root).as_posix()
            raise MarkdownParseError(f"cannot parse {relative}: {exc}") from exc
        return []
    relative = path.relative_to(root).as_posix()
    document_hash = sha256_text(text)
    metadata, _ = parse_frontmatter(text)
    if strict and text.startswith(f"{FRONTMATTER_MARKER}\n") and not metadata:
        raise MarkdownParseError(f"cannot parse frontmatter in {relative}")
    authority = _authority(Path(relative), metadata)
    units: list[dict[str, Any]] = []

    if authority == "research_rules":
        for index, line in enumerate(text.splitlines(), start=1):
            match = RULE_RE.match(line)
            if not match:
                continue
            title = f"{match.group(1)} {match.group(2).strip()}".strip()
            section = {
                "title": title,
                "level": 2,
                "start_line": index,
                "end_line": index,
                "content": line,
            }
            units.append(_make_unit(root, path, metadata, authority, "rule", section, document_hash))
        return units

    for section in _section_for_document(path, text, metadata):
        unit_type = _unit_type(path, section, metadata)
        if unit_type is None:
            continue
        units.append(_make_unit(root, path, metadata, authority, unit_type, section, document_hash))
    if (
        strict
        and authority == "evidence_package"
        and metadata.get("artifact_type") == "evidence_package"
        and not units
    ):
        raise MarkdownParseError(f"{relative} has no atomic evidence items")
    return units


def _make_unit(
    root: Path,
    path: Path,
    metadata: dict[str, Any],
    authority: str,
    unit_type: str,
    section: dict[str, Any],
    document_hash: str,
) -> dict[str, Any]:
    fields = _field_aliases(body_fields(str(section["content"])))
    objects, field_values = _object_and_fields(metadata, fields, path)
    unit_id = _stable_id(path, section, metadata, unit_type, fields)
    # A package's frontmatter describes the package; an atomic evidence item's
    # body status is more specific and must win for conflict/denial filtering.
    status = str((fields.get("status") or [metadata.get("status") or "unknown"])[0])
    roles = fields.get("evidence_role") or fields.get("role") or []
    if not roles and unit_type == "evidence_item":
        roles = ["veto"] if is_conflict_status(status) else ["primary"]
    cutoff_values = fields.get("information_cutoff") or fields.get("snapshot_cutoff") or []
    information_cutoff = cutoff_values[0] if cutoff_values else metadata.get("information_cutoff") or metadata.get("snapshot_cutoff")
    source_groups = [
        item
        for value in fields.get("source_group", [])
        for item in _split_values(value)
    ]
    source_locations = _source_locations(fields.get("source_location", []))
    text = str(section["content"])
    locator = {
        "kind": "markdown",
        "path": path.relative_to(root).as_posix(),
        "start_line": int(section["start_line"]),
        "end_line": int(section["end_line"]),
        "anchor": str(section["title"]),
        "sha256": document_hash,
    }
    relations: list[dict[str, str]] = []
    for value in fields.get("evidence_reference", []):
        for target in _evidence_atomic_references(value):
            relations.append({"from": unit_id, "to": target, "type": "supported_by"})
    for value in fields.get("historical_evidence_reference", []):
        for target in _evidence_atomic_references(value):
            relations.append(
                {
                    "from": unit_id,
                    "to": target,
                    "type": "historically_referenced_evidence",
                }
            )
    for value in fields.get("upstream_judgment", []):
        targets = re.findall(r"J\d{8}-\d{3}(?:\s+v\d+)?", value)
        relation_type = "upstream_judgment"
        if "约束" in value:
            relation_type = "constrained_by"
        elif "例外" in value:
            relation_type = "exception_to"
        elif "支持" in value:
            relation_type = "supported_by_judgment"
        relations.extend({"from": unit_id, "to": target, "type": relation_type} for target in targets)
    for value in fields.get("strategy_reference", []):
        for strategy_id, version in re.findall(
            r"\b(STR-[A-Za-z0-9-]+)(?:@|\s+v)([A-Za-z0-9._-]+)\b",
            value,
        ):
            relations.append({"from": unit_id, "to": f"{strategy_id}@{version}", "type": "uses_strategy"})
    for value in fields.get("supersedes", []):
        targets = re.findall(
            r"(?:EVI-[A-Za-z0-9-]+#\d+|J\d{8}-\d{3}(?:\s+v\d+)?|C\d{8}-\d{3}(?:\s+v\d+)?|STR-[A-Za-z0-9-]+@[A-Za-z0-9._-]+|DOS-[A-Za-z0-9-]+#[A-Za-z0-9._:-]+@[A-Za-z0-9._-]+)",
            value,
        )
        relations.extend(
            {"from": unit_id, "to": target, "type": "supersedes"}
            for target in targets
        )
    unique_relations = list(
        {
            (relation["from"], relation["to"], relation["type"]): relation
            for relation in relations
        }.values()
    )
    logical_id = (fields.get("logical_id") or [None])[0]
    logical_version = (fields.get("logical_version") or [None])[0]
    if unit_type in {"judgment_version", "observation_candidate"}:
        version_match = re.fullmatch(r"(.+?)\s+v(\d+)", unit_id)
        if version_match:
            logical_id = logical_id or version_match.group(1)
            logical_version = logical_version or version_match.group(2)
    elif unit_type == "strategy_version":
        logical_id = logical_id or metadata.get("id")
        logical_version = logical_version or metadata.get("version")
    return {
        "unit_id": unit_id,
        "unit_type": unit_type,
        "authority": authority,
        "document_path": path.relative_to(root).as_posix(),
        "information_cutoff": str(information_cutoff) if information_cutoff is not None else None,
        "status": status,
        "objects": list(dict.fromkeys(objects)),
        "fields": list(dict.fromkeys(field_values)),
        "evidence_roles": list(dict.fromkeys(str(item) for item in roles)),
        "source_groups": list(dict.fromkeys(source_groups)),
        "source_locations": source_locations,
        "verification_source_locator": next(
            (
                dict(source_location)
                for source_location in source_locations
                if isinstance(source_location, dict)
                and source_location.get("kind") == "source_payload"
            ),
            None,
        ),
        "relations": unique_relations,
        "expiry": list(fields.get("expiry", [])),
        "valid_until": (fields.get("valid_until") or fields.get("expiry") or [None])[0],
        "invalidated_at": (fields.get("invalidated_at") or [None])[0],
        "terminated_at": (fields.get("terminated_at") or [None])[0],
        "result_status": (fields.get("result_status") or [None])[0],
        "result_recorded_at": (fields.get("result_recorded_at") or [None])[0],
        "lifecycle_status": (fields.get("lifecycle_status") or [None])[0],
        "logical_id": logical_id,
        "logical_version": logical_version,
        "market_dates": list(fields.get("market_date", [])),
        "facts": list(fields.get("fact", [])),
        "source_locator": locator,
        "content": text,
        "content_sha256": sha256_text(text),
        "document_sha256": document_hash,
        "metadata": metadata,
    }


def iter_source_files(root: Path) -> Iterable[Path]:
    skipped_dirs = {".git", ".context", ".source-payloads", "模板", "scaffold"}
    for path in sorted(root.rglob("*.md")):
        if any(part in skipped_dirs for part in path.relative_to(root).parts):
            continue
        if "docs" in path.relative_to(root).parts and "adr" in path.relative_to(root).parts:
            continue
        yield path


def source_manifest(root: Path) -> tuple[list[dict[str, str]], str]:
    documents: list[dict[str, str]] = []
    for path in iter_source_files(root):
        try:
            digest = sha256_bytes(path.read_bytes())
        except OSError as exc:
            relative = path.relative_to(root).as_posix()
            raise MarkdownParseError(
                f"cannot read authoritative Markdown {relative}: {exc}"
            ) from exc
        documents.append({"path": path.relative_to(root).as_posix(), "sha256": digest})
    payload = json.dumps(documents, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return documents, sha256_text(payload)
