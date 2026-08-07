"""Rebuildable SQLite/FTS5 projection for the research workspace."""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from pathlib import Path
from typing import Any, Iterable

from .markdown import extract_units, iter_source_files, source_manifest


PROJECTION_SCHEMA_VERSION = "a-share-context-projection-v1"


class ProjectionError(RuntimeError):
    """Raised when a projection cannot be safely read or rebuilt."""


def default_projection_path(root: Path) -> Path:
    return root / ".context" / "projection.sqlite3"


def _connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(str(path))
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS documents (
            path TEXT PRIMARY KEY,
            sha256 TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS units (
            unit_id TEXT PRIMARY KEY,
            unit_type TEXT NOT NULL,
            authority TEXT NOT NULL,
            document_path TEXT NOT NULL,
            start_line INTEGER NOT NULL,
            end_line INTEGER NOT NULL,
            anchor TEXT NOT NULL,
            document_sha256 TEXT NOT NULL,
            content_sha256 TEXT NOT NULL,
            information_cutoff TEXT,
            status TEXT NOT NULL,
            objects_json TEXT NOT NULL,
            fields_json TEXT NOT NULL,
            roles_json TEXT NOT NULL,
            content TEXT NOT NULL,
            metadata_json TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS units_type_idx ON units(unit_type);
        CREATE INDEX IF NOT EXISTS units_document_idx ON units(document_path);
        CREATE VIRTUAL TABLE IF NOT EXISTS units_fts USING fts5(
            unit_id UNINDEXED,
            content,
            objects,
            fields,
            tokenize = 'unicode61'
        );
        """
    )


def _metadata(connection: sqlite3.Connection) -> dict[str, str]:
    return {row["key"]: row["value"] for row in connection.execute("SELECT key, value FROM metadata")}


def _write_unit(connection: sqlite3.Connection, unit: dict[str, Any]) -> None:
    locator = unit["source_locator"]
    metadata = dict(unit.get("metadata", {}))
    metadata["_derived"] = {
        "expiry": unit.get("expiry", []),
        "market_dates": unit.get("market_dates", []),
        "facts": unit.get("facts", []),
    }
    connection.execute(
        """
        INSERT INTO units (
            unit_id, unit_type, authority, document_path, start_line, end_line,
            anchor, document_sha256, content_sha256, information_cutoff, status,
            objects_json, fields_json, roles_json, content, metadata_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            unit["unit_id"],
            unit["unit_type"],
            unit["authority"],
            unit["document_path"],
            locator["start_line"],
            locator["end_line"],
            locator["anchor"],
            unit["document_sha256"],
            unit["content_sha256"],
            unit.get("information_cutoff"),
            unit.get("status", "unknown"),
            json.dumps(unit.get("objects", []), ensure_ascii=False, separators=(",", ":")),
            json.dumps(unit.get("fields", []), ensure_ascii=False, separators=(",", ":")),
            json.dumps(unit.get("evidence_roles", []), ensure_ascii=False, separators=(",", ":")),
            unit.get("content", ""),
            json.dumps(metadata, ensure_ascii=False, separators=(",", ":")),
        ),
    )
    connection.execute(
        "INSERT INTO units_fts(unit_id, content, objects, fields) VALUES (?, ?, ?, ?)",
        (
            unit["unit_id"],
            unit.get("content", ""),
            " ".join(unit.get("objects", [])),
            " ".join(unit.get("fields", [])),
        ),
    )


def rebuild(root: Path, destination: Path) -> dict[str, Any]:
    """Build a fresh projection atomically from Markdown facts."""

    root = root.resolve()
    destination = destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix="projection-", suffix=".sqlite3", dir=str(destination.parent))
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        documents, manifest_hash = source_manifest(root)
        connection = _connect(temporary)
        try:
            _schema(connection)
            connection.execute("DELETE FROM metadata")
            connection.execute("DELETE FROM documents")
            connection.execute("DELETE FROM units")
            connection.execute("DELETE FROM units_fts")
            connection.executemany(
                "INSERT INTO documents(path, sha256) VALUES (?, ?)",
                [(item["path"], item["sha256"]) for item in documents],
            )
            for path in iter_source_files(root):
                for unit in extract_units(root, path):
                    _write_unit(connection, unit)
            connection.executemany(
                "INSERT INTO metadata(key, value) VALUES (?, ?)",
                [
                    ("schema_version", PROJECTION_SCHEMA_VERSION),
                    ("source_manifest_hash", manifest_hash),
                    ("workspace_schema", "a-share-workspace-v3"),
                    ("unit_count", str(connection.execute("SELECT COUNT(*) FROM units").fetchone()[0])),
                ],
            )
            connection.commit()
        finally:
            connection.close()
        os.replace(temporary, destination)
    except Exception as exc:  # pragma: no cover - exercised through fallback seam
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise ProjectionError(f"projection rebuild failed: {exc}") from exc
    return {
        "schema_version": PROJECTION_SCHEMA_VERSION,
        "source_manifest_hash": manifest_hash,
        "path": str(destination),
        "rebuilt": True,
        "unit_count": len(list(_iter_units(root))),
    }


def _iter_units(root: Path) -> Iterable[dict[str, Any]]:
    for path in iter_source_files(root):
        yield from extract_units(root, path)


def _is_fresh(root: Path, path: Path) -> tuple[bool, dict[str, str]]:
    if not path.is_file():
        return False, {}
    try:
        connection = _connect(path)
        try:
            metadata = _metadata(connection)
            documents, manifest_hash = source_manifest(root)
            current_documents = {
                row["path"]: row["sha256"] for row in connection.execute("SELECT path, sha256 FROM documents")
            }
            expected_documents = {item["path"]: item["sha256"] for item in documents}
            stored_unit_count = int(metadata.get("unit_count", "-1"))
            unit_count = int(connection.execute("SELECT COUNT(*) FROM units").fetchone()[0])
            fts_count = int(connection.execute("SELECT COUNT(*) FROM units_fts").fetchone()[0])
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
            fresh = (
                metadata.get("schema_version") == PROJECTION_SCHEMA_VERSION
                and metadata.get("source_manifest_hash") == manifest_hash
                and current_documents == expected_documents
                and integrity == "ok"
                and stored_unit_count == unit_count == fts_count
            )
            return fresh, metadata
        finally:
            connection.close()
    except (sqlite3.Error, OSError, ValueError):
        return False, {}


def open_fresh(root: Path, path: Path | None = None) -> tuple[sqlite3.Connection | None, dict[str, Any]]:
    """Open a current projection or return a safe direct-read degradation."""

    root = root.resolve()
    destination = (path or default_projection_path(root)).resolve()
    fresh, metadata = _is_fresh(root, destination)
    if fresh:
        try:
            return _connect(destination), {
                "schema_version": PROJECTION_SCHEMA_VERSION,
                "path": str(destination),
                "projection_degraded": False,
                "rebuilt": False,
                "source_manifest_hash": metadata.get("source_manifest_hash"),
            }
        except sqlite3.Error:
            pass
    try:
        info = rebuild(root, destination)
        connection = _connect(destination)
        info.update({"projection_degraded": False})
        return connection, info
    except ProjectionError as exc:
        return None, {
            "schema_version": PROJECTION_SCHEMA_VERSION,
            "path": str(destination),
            "projection_degraded": True,
            "rebuilt": False,
            "reason": str(exc),
        }


def rows_to_units(rows: Iterable[sqlite3.Row]) -> list[dict[str, Any]]:
    units: list[dict[str, Any]] = []
    for row in rows:
        metadata = json.loads(row["metadata_json"])
        derived = metadata.pop("_derived", {})
        units.append(
            {
                "unit_id": row["unit_id"],
                "unit_type": row["unit_type"],
                "authority": row["authority"],
                "document_path": row["document_path"],
                "information_cutoff": row["information_cutoff"],
                "status": row["status"],
                "objects": json.loads(row["objects_json"]),
                "fields": json.loads(row["fields_json"]),
                "evidence_roles": json.loads(row["roles_json"]),
                "source_locator": {
                    "kind": "markdown",
                    "path": row["document_path"],
                    "start_line": row["start_line"],
                    "end_line": row["end_line"],
                    "anchor": row["anchor"],
                    "sha256": row["document_sha256"],
                },
                "content_sha256": row["content_sha256"],
                "document_sha256": row["document_sha256"],
                "content": row["content"],
                "metadata": metadata,
                "expiry": derived.get("expiry", []),
                "market_dates": derived.get("market_dates", []),
                "facts": derived.get("facts", []),
            }
        )
    return units


def all_units(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    return rows_to_units(connection.execute("SELECT * FROM units ORDER BY unit_id"))


def search_text(connection: sqlite3.Connection, query: str, limit: int = 20) -> list[dict[str, Any]]:
    if not query.strip():
        return []
    tokens = [token for token in query.split() if token]
    fts_query = " AND ".join('"' + token.replace('"', ' ') + '"' for token in tokens)
    try:
        rows = connection.execute(
            """
            SELECT units.* FROM units_fts
            JOIN units ON units.unit_id = units_fts.unit_id
            WHERE units_fts MATCH ?
            ORDER BY bm25(units_fts)
            LIMIT ?
            """,
            (fts_query, limit),
        )
        return rows_to_units(rows)
    except sqlite3.Error:
        # FTS5 is required by the production adapter, but direct search keeps
        # the seam useful on a Python build compiled without FTS5.
        pattern = "%" + "%".join(tokens) + "%"
        return rows_to_units(
            connection.execute("SELECT * FROM units WHERE content LIKE ? LIMIT ?", (pattern, limit))
        )
