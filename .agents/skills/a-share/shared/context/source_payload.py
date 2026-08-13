"""Externalized source-payload storage and bounded verification access."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any


DEFAULT_MAX_EXCERPT_CHARACTERS = 4_000
ABSOLUTE_MAX_EXCERPT_CHARACTERS = 20_000


def _normalise_acquired_at(value: Any) -> str:
    if value is None:
        raise ValueError("source payload acquired_at requires a timezone")
    text = str(value).strip()
    if not text:
        raise ValueError("source payload acquired_at requires a timezone")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("source payload acquired_at must be a valid timestamp with timezone") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("source payload acquired_at requires a timezone")
    return parsed.isoformat()


class SourcePayloadStore:
    """Boundary used by investigators to retain raw tool/source payloads.

    Implementations return metadata and locators.  Workset assembly only
    carries those locators; it never embeds the complete payload.
    """

    def put(self, payload: str | bytes, **metadata: Any) -> dict[str, Any]:  # pragma: no cover - interface
        raise NotImplementedError

    def locate(self, reference: dict[str, Any]) -> dict[str, Any]:  # pragma: no cover - interface
        raise NotImplementedError

    def excerpt(
        self,
        reference: dict[str, Any],
        start_line: int | None = None,
        end_line: int | None = None,
        max_chars: int = DEFAULT_MAX_EXCERPT_CHARACTERS,
    ) -> str:
        raise NotImplementedError  # pragma: no cover - interface


class FileSourcePayloadStore(SourcePayloadStore):
    """Store payloads outside the research workset in a private directory."""

    def __init__(self, root: Path, directory_name: str = ".source-payloads") -> None:
        self.root = Path(root).resolve()
        self.base = self.root / directory_name
        if self.root not in self.base.resolve().parents:
            raise ValueError("source payload store escapes workspace root")
        self.base.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _payload_id(payload: bytes, source_uri: str, acquired_at: str) -> str:
        digest = hashlib.sha256()
        digest.update(payload)
        digest.update(b"\0")
        digest.update(source_uri.encode("utf-8"))
        digest.update(b"\0")
        digest.update(acquired_at.encode("utf-8"))
        return f"PAY-{digest.hexdigest()[:24]}"

    def put(self, payload: str | bytes, **metadata: Any) -> dict[str, Any]:
        content = payload.encode("utf-8") if isinstance(payload, str) else bytes(payload)
        source_uri = str(metadata.get("source_uri", ""))
        acquired_at = _normalise_acquired_at(metadata.get("acquired_at"))
        payload_id = str(
            metadata.get("payload_id") or self._payload_id(content, source_uri, acquired_at)
        )
        if not re.fullmatch(r"[A-Za-z0-9._-]+", payload_id) or payload_id in {".", ".."}:
            raise ValueError("invalid payload_id")
        run_id = str(metadata.get("run_id", "unassigned"))
        if not re.fullmatch(r"[A-Za-z0-9._-]+", run_id) or run_id in {".", ".."}:
            raise ValueError("invalid run_id")
        directory = self.base / run_id
        directory.mkdir(parents=True, exist_ok=True)
        resolved_directory = directory.resolve()
        resolved_base = self.base.resolve()
        if resolved_base not in [resolved_directory, *resolved_directory.parents]:
            raise ValueError("source payload run directory escapes source payload store")
        data_path = directory / f"{payload_id}.payload"
        meta_path = directory / f"{payload_id}.json"
        if data_path.is_symlink():
            raise ValueError("source payload file symlink is not allowed")
        if meta_path.is_symlink():
            raise ValueError("source payload metadata file symlink is not allowed")
        record = {
            "payload_id": payload_id,
            "kind": "source_payload",
            "path": data_path.relative_to(self.root).as_posix(),
            "sha256": hashlib.sha256(content).hexdigest(),
            "byte_length": len(content),
            "run_id": run_id,
            "source_uri": source_uri,
            "acquired_at": acquired_at,
            "content_type": metadata.get("content_type"),
            "metadata": metadata.get("metadata", {}),
        }
        encoded_record = json.dumps(record, ensure_ascii=False, indent=2) + "\n"
        if data_path.exists() or meta_path.exists():
            try:
                existing_record = (
                    json.loads(meta_path.read_text(encoding="utf-8"))
                    if data_path.is_file() and meta_path.is_file()
                    else None
                )
            except (OSError, json.JSONDecodeError):
                existing_record = None
            if (
                data_path.is_file()
                and meta_path.is_file()
                and data_path.read_bytes() == content
                and existing_record == record
            ):
                return record
            raise ValueError("immutable source payload record already exists with different data")
        try:
            with data_path.open("xb") as handle:
                handle.write(content)
            with meta_path.open("x", encoding="utf-8") as handle:
                handle.write(encoded_record)
        except FileExistsError:
            raise ValueError("immutable source payload record appeared during write") from None
        return record

    def locate(self, reference: dict[str, Any]) -> dict[str, Any]:
        payload_id = str(reference.get("payload_id", ""))
        if (
            not payload_id
            or not re.fullmatch(r"[A-Za-z0-9._-]+", payload_id)
            or payload_id in {".", ".."}
        ):
            raise ValueError("invalid source payload reference")
        relative = reference.get("path")
        base = self.base.resolve()
        if relative:
            path = (self.root / str(relative)).resolve()
            if base not in [path, *path.parents]:
                raise ValueError("source payload reference escapes source payload store")
            if path.name != f"{payload_id}.payload":
                raise ValueError("source payload path does not match payload_id")
            if not path.is_file():
                raise FileNotFoundError(f"source payload not found: {payload_id}")
        else:
            candidates = list(self.base.rglob(f"{payload_id}.payload"))
            if len(candidates) != 1:
                raise FileNotFoundError(f"source payload not found: {payload_id}")
            path = candidates[0].resolve()
            if base not in [path, *path.parents]:
                raise ValueError("source payload reference escapes source payload store")
            if path.name != f"{payload_id}.payload":
                raise ValueError("source payload path does not match payload_id")
        content = path.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        if reference.get("sha256") and reference["sha256"] != digest:
            raise ValueError("source payload content hash changed")
        if reference.get("byte_length") is not None and int(reference["byte_length"]) != len(content):
            raise ValueError("source payload byte length changed")
        record = dict(reference)
        record["path"] = path.relative_to(self.root).as_posix()
        record["sha256"] = digest
        record["byte_length"] = len(content)
        acquired_at = _normalise_acquired_at(record.get("acquired_at"))
        meta_path = path.with_suffix(".json")
        if meta_path.is_symlink():
            raise ValueError("source payload metadata file symlink is not allowed")
        if not meta_path.is_file():
            raise FileNotFoundError(f"source payload metadata not found: {payload_id}")
        stored = json.loads(meta_path.read_text(encoding="utf-8"))
        if not isinstance(stored, dict):
            raise ValueError("source payload metadata must be a JSON object")
        if (
            stored.get("payload_id") != payload_id
            or stored.get("sha256") != digest
            or stored.get("byte_length") != len(content)
        ):
            raise ValueError("source payload content differs from stored metadata")
        stored_acquired_at = _normalise_acquired_at(stored.get("acquired_at"))
        if acquired_at != stored_acquired_at:
            raise ValueError("source payload acquired_at differs from stored metadata")
        record["acquired_at"] = acquired_at
        return record

    def excerpt(
        self,
        reference: dict[str, Any],
        start_line: int | None = None,
        end_line: int | None = None,
        max_chars: int = DEFAULT_MAX_EXCERPT_CHARACTERS,
    ) -> str:
        if start_line is None or end_line is None:
            raise ValueError("source payload excerpt requires explicit start_line and end_line")
        start = int(start_line)
        end = int(end_line)
        limit = int(max_chars)
        if start < 1 or end < start:
            raise ValueError("invalid source payload excerpt line range")
        if limit < 1 or limit > ABSOLUTE_MAX_EXCERPT_CHARACTERS:
            raise ValueError(
                f"source payload max_chars must be between 1 and {ABSOLUTE_MAX_EXCERPT_CHARACTERS}"
            )
        record = self.locate(reference)
        path = self.root / str(record["path"])
        content = path.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        if record.get("sha256") and record["sha256"] != digest:
            raise ValueError("source payload content hash changed")
        text = content.decode("utf-8")
        lines = text.splitlines()
        if end > len(lines):
            raise ValueError("source payload excerpt line range exceeds payload")
        excerpt = "\n".join(lines[start - 1 : end])
        if len(excerpt) > limit:
            raise ValueError("source payload excerpt exceeds max_chars; request a narrower range")
        return excerpt
