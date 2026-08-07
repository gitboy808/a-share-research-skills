"""Externalized source-payload storage and bounded verification access."""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any


class SourcePayloadStore:
    """Boundary used by investigators to retain raw tool/source payloads.

    Implementations return metadata and locators.  Workset assembly only
    carries those locators; it never embeds the complete payload.
    """

    def put(self, payload: str | bytes, **metadata: Any) -> dict[str, Any]:  # pragma: no cover - interface
        raise NotImplementedError

    def locate(self, reference: dict[str, Any]) -> dict[str, Any]:  # pragma: no cover - interface
        raise NotImplementedError

    def excerpt(self, reference: dict[str, Any], start_line: int | None = None, end_line: int | None = None) -> str:
        raise NotImplementedError  # pragma: no cover - interface


class FileSourcePayloadStore(SourcePayloadStore):
    """Store payloads outside the research workset in a private directory."""

    def __init__(self, root: Path, directory_name: str = ".source-payloads") -> None:
        self.root = Path(root).resolve()
        self.base = self.root / directory_name
        self.base.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _payload_id(payload: bytes, source_uri: str = "") -> str:
        digest = hashlib.sha256(payload + source_uri.encode("utf-8")).hexdigest()
        return f"PAY-{digest[:24]}"

    def put(self, payload: str | bytes, **metadata: Any) -> dict[str, Any]:
        content = payload.encode("utf-8") if isinstance(payload, str) else bytes(payload)
        source_uri = str(metadata.get("source_uri", ""))
        payload_id = str(metadata.get("payload_id") or self._payload_id(content, source_uri))
        if not re.fullmatch(r"[A-Za-z0-9._-]+", payload_id):
            raise ValueError("payload_id contains unsafe characters")
        run_id = str(metadata.get("run_id", "unassigned"))
        if not re.fullmatch(r"[A-Za-z0-9._-]+", run_id):
            raise ValueError("run_id contains unsafe characters")
        directory = self.base / run_id
        directory.mkdir(parents=True, exist_ok=True)
        data_path = directory / f"{payload_id}.payload"
        meta_path = directory / f"{payload_id}.json"
        data_path.write_bytes(content)
        record = {
            "payload_id": payload_id,
            "kind": "source_payload",
            "path": data_path.relative_to(self.root).as_posix(),
            "sha256": hashlib.sha256(content).hexdigest(),
            "byte_length": len(content),
            "run_id": run_id,
            "source_uri": source_uri,
            "acquired_at": metadata.get("acquired_at"),
            "content_type": metadata.get("content_type"),
            "metadata": metadata.get("metadata", {}),
        }
        meta_path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return record

    def locate(self, reference: dict[str, Any]) -> dict[str, Any]:
        payload_id = str(reference.get("payload_id", ""))
        if not payload_id or not re.fullmatch(r"[A-Za-z0-9._-]+", payload_id):
            raise ValueError("invalid source payload reference")
        relative = reference.get("path")
        path = (self.root / str(relative)).resolve() if relative else None
        if path is None or self.root not in [path, *path.parents] or not path.is_file():
            candidates = list(self.base.rglob(f"{payload_id}.payload"))
            if len(candidates) != 1:
                raise FileNotFoundError(f"source payload not found: {payload_id}")
            path = candidates[0]
        record = dict(reference)
        record["path"] = path.relative_to(self.root).as_posix()
        record["sha256"] = record.get("sha256") or hashlib.sha256(path.read_bytes()).hexdigest()
        return record

    def excerpt(self, reference: dict[str, Any], start_line: int | None = None, end_line: int | None = None) -> str:
        record = self.locate(reference)
        path = self.root / str(record["path"])
        content = path.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        if record.get("sha256") and record["sha256"] != digest:
            raise ValueError("source payload content hash changed")
        text = content.decode("utf-8")
        lines = text.splitlines()
        start = max(1, int(start_line or 1))
        end = min(len(lines), int(end_line or len(lines)))
        return "\n".join(lines[start - 1 : end])
