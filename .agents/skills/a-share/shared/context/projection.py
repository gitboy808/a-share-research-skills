"""Rebuildable SQLite/FTS5 projection for the research workspace."""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from pathlib import Path
from typing import Any, Iterable

from .markdown import extract_units, source_manifest


PROJECTION_SCHEMA_VERSION = "a-share-context-projection-v1"
PROJECTION_IMPLEMENTATION_REVISION = "10"


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
            sha256 TEXT NOT NULL,
            parse_status TEXT NOT NULL,
            unit_count INTEGER NOT NULL,
            parse_error TEXT
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
        CREATE TABLE IF NOT EXISTS unit_source_groups (
            unit_id TEXT NOT NULL,
            source_group TEXT NOT NULL,
            PRIMARY KEY (unit_id, source_group),
            FOREIGN KEY (unit_id) REFERENCES units(unit_id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS unit_relations (
            from_unit_id TEXT NOT NULL,
            to_unit_id TEXT NOT NULL,
            relation_type TEXT NOT NULL,
            PRIMARY KEY (from_unit_id, to_unit_id, relation_type),
            FOREIGN KEY (from_unit_id) REFERENCES units(unit_id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS unit_relations_target_idx ON unit_relations(to_unit_id);
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
        "source_groups": unit.get("source_groups", []),
        "source_locations": unit.get("source_locations", []),
        "verification_source_locator": unit.get("verification_source_locator"),
        "relations": unit.get("relations", []),
        "valid_until": unit.get("valid_until"),
        "invalidated_at": unit.get("invalidated_at"),
        "terminated_at": unit.get("terminated_at"),
        "result_status": unit.get("result_status"),
        "result_recorded_at": unit.get("result_recorded_at"),
        "lifecycle_status": unit.get("lifecycle_status"),
        "logical_id": unit.get("logical_id"),
        "logical_version": unit.get("logical_version"),
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
    connection.executemany(
        "INSERT OR IGNORE INTO unit_source_groups(unit_id, source_group) VALUES (?, ?)",
        [
            (unit["unit_id"], str(source_group))
            for source_group in unit.get("source_groups", [])
            if str(source_group).strip()
        ],
    )
    connection.executemany(
        """
        INSERT OR IGNORE INTO unit_relations(from_unit_id, to_unit_id, relation_type)
        VALUES (?, ?, ?)
        """,
        [
            (
                unit["unit_id"],
                str(relation.get("to")),
                str(relation.get("type")),
            )
            for relation in unit.get("relations", [])
            if isinstance(relation, dict)
            and relation.get("to")
            and relation.get("type")
        ],
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
            connection.execute("DELETE FROM unit_relations")
            connection.execute("DELETE FROM unit_source_groups")
            connection.execute("DELETE FROM units")
            connection.execute("DELETE FROM units_fts")
            for document in documents:
                path = root / document["path"]
                parsed_units = extract_units(root, path, strict=True)
                connection.execute(
                    """
                    INSERT INTO documents(path, sha256, parse_status, unit_count, parse_error)
                    VALUES (?, ?, 'parsed', ?, NULL)
                    """,
                    (document["path"], document["sha256"], len(parsed_units)),
                )
                for unit in parsed_units:
                    _write_unit(connection, unit)
            unit_count = int(connection.execute("SELECT COUNT(*) FROM units").fetchone()[0])
            source_group_count = int(
                connection.execute("SELECT COUNT(*) FROM unit_source_groups").fetchone()[0]
            )
            relation_count = int(
                connection.execute("SELECT COUNT(*) FROM unit_relations").fetchone()[0]
            )
            connection.executemany(
                "INSERT INTO metadata(key, value) VALUES (?, ?)",
                [
                    ("schema_version", PROJECTION_SCHEMA_VERSION),
                    ("implementation_revision", PROJECTION_IMPLEMENTATION_REVISION),
                    ("source_manifest_hash", manifest_hash),
                    ("workspace_schema", "a-share-workspace-v3"),
                    ("unit_count", str(unit_count)),
                    ("document_count", str(len(documents))),
                    ("parsed_document_count", str(len(documents))),
                    ("source_group_count", str(source_group_count)),
                    ("relation_count", str(relation_count)),
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
        "implementation_revision": PROJECTION_IMPLEMENTATION_REVISION,
        "source_manifest_hash": manifest_hash,
        "path": str(destination),
        "rebuilt": True,
        "unit_count": unit_count,
        "document_count": len(documents),
        "parsed_document_count": len(documents),
        "source_group_count": source_group_count,
        "relation_count": relation_count,
    }


def _is_fresh(root: Path, path: Path) -> tuple[bool, dict[str, str]]:
    if not path.is_file():
        return False, {}
    try:
        connection = _connect(path)
        try:
            metadata = _metadata(connection)
            documents, manifest_hash = source_manifest(root)
            document_rows = list(
                connection.execute(
                    "SELECT path, sha256, parse_status, unit_count, parse_error FROM documents"
                )
            )
            current_documents = {row["path"]: row["sha256"] for row in document_rows}
            expected_documents = {item["path"]: item["sha256"] for item in documents}
            stored_unit_count = int(metadata.get("unit_count", "-1"))
            unit_count = int(connection.execute("SELECT COUNT(*) FROM units").fetchone()[0])
            fts_count = int(connection.execute("SELECT COUNT(*) FROM units_fts").fetchone()[0])
            unit_metadata_rows = [
                (row["unit_id"], json.loads(row["metadata_json"]).get("_derived", {}))
                for row in connection.execute(
                    "SELECT unit_id, metadata_json FROM units ORDER BY unit_id"
                )
            ]
            expected_source_groups = {
                (unit_id, str(source_group))
                for unit_id, derived in unit_metadata_rows
                for source_group in derived.get("source_groups", [])
                if str(source_group).strip()
            }
            stored_source_groups = {
                (row["unit_id"], row["source_group"])
                for row in connection.execute("SELECT unit_id, source_group FROM unit_source_groups")
            }
            expected_relations = {
                (unit_id, str(relation.get("to")), str(relation.get("type")))
                for unit_id, derived in unit_metadata_rows
                for relation in derived.get("relations", [])
                if isinstance(relation, dict) and relation.get("to") and relation.get("type")
            }
            stored_relations = {
                (row["from_unit_id"], row["to_unit_id"], row["relation_type"])
                for row in connection.execute(
                    "SELECT from_unit_id, to_unit_id, relation_type FROM unit_relations"
                )
            }
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
            fresh = (
                metadata.get("schema_version") == PROJECTION_SCHEMA_VERSION
                and metadata.get("implementation_revision") == PROJECTION_IMPLEMENTATION_REVISION
                and metadata.get("source_manifest_hash") == manifest_hash
                and current_documents == expected_documents
                and int(metadata.get("document_count", "-1")) == len(document_rows)
                and int(metadata.get("parsed_document_count", "-1")) == len(document_rows)
                and int(metadata.get("source_group_count", "-1")) == len(stored_source_groups)
                and int(metadata.get("relation_count", "-1")) == len(stored_relations)
                and all(
                    row["parse_status"] == "parsed"
                    and row["parse_error"] is None
                    and row["unit_count"] >= 0
                    for row in document_rows
                )
                and sum(row["unit_count"] for row in document_rows) == unit_count
                and stored_source_groups == expected_source_groups
                and stored_relations == expected_relations
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
                "implementation_revision": metadata.get("implementation_revision"),
                "path": str(destination),
                "projection_degraded": False,
                "rebuilt": False,
                "source_manifest_hash": metadata.get("source_manifest_hash"),
                "unit_count": int(metadata.get("unit_count", "0")),
                "document_count": int(metadata.get("document_count", "0")),
                "parsed_document_count": int(metadata.get("parsed_document_count", "0")),
                "source_group_count": int(metadata.get("source_group_count", "0")),
                "relation_count": int(metadata.get("relation_count", "0")),
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
            "implementation_revision": PROJECTION_IMPLEMENTATION_REVISION,
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
                "source_groups": derived.get("source_groups", []),
                "source_locations": derived.get("source_locations", []),
                "verification_source_locator": derived.get("verification_source_locator"),
                "relations": derived.get("relations", []),
                "valid_until": derived.get("valid_until"),
                "invalidated_at": derived.get("invalidated_at"),
                "terminated_at": derived.get("terminated_at"),
                "result_status": derived.get("result_status"),
                "result_recorded_at": derived.get("result_recorded_at"),
                "lifecycle_status": derived.get("lifecycle_status"),
                "logical_id": derived.get("logical_id"),
                "logical_version": derived.get("logical_version"),
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
