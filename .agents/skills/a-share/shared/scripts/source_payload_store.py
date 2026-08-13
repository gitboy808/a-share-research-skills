#!/usr/bin/env python3
"""Store raw research-source payloads outside phase worksets."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


SHARED_ROOT = Path(__file__).resolve().parents[1]
if str(SHARED_ROOT) not in sys.path:
    sys.path.insert(0, str(SHARED_ROOT))

from context.source_payload import FileSourcePayloadStore  # noqa: E402


def _dump(value: object) -> None:
    sys.stdout.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n")


def _compact_locator(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": record["kind"],
        "payload_id": record["payload_id"],
        "path": record["path"],
        "sha256": record["sha256"],
        "byte_length": record["byte_length"],
        "acquired_at": record["acquired_at"],
    }


def _read_reference(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError("source payload reference must be a JSON object")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Externalize and locate research source payloads.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    put_parser = subparsers.add_parser("put")
    put_parser.add_argument("--root", type=Path, required=True)
    put_parser.add_argument("--run-id", required=True)
    put_parser.add_argument("--input-file", type=Path, required=True)
    put_parser.add_argument("--source-uri")
    put_parser.add_argument("--acquired-at", required=True)
    put_parser.add_argument("--content-type")

    locate_parser = subparsers.add_parser("locate")
    locate_parser.add_argument("--root", type=Path, required=True)
    locate_parser.add_argument("--reference", type=Path, required=True)

    excerpt_parser = subparsers.add_parser("excerpt")
    excerpt_parser.add_argument("--root", type=Path, required=True)
    excerpt_parser.add_argument("--reference", type=Path, required=True)
    excerpt_parser.add_argument("--start-line", type=int, required=True)
    excerpt_parser.add_argument("--end-line", type=int, required=True)
    excerpt_parser.add_argument("--max-chars", type=int, default=4_000)

    args = parser.parse_args(argv)
    try:
        store = FileSourcePayloadStore(args.root)
        if args.command == "put":
            record = store.put(
                args.input_file.read_bytes(),
                run_id=args.run_id,
                source_uri=args.source_uri or "",
                acquired_at=args.acquired_at,
                content_type=args.content_type,
            )
        elif args.command == "locate":
            record = store.locate(_read_reference(args.reference))
        else:
            reference = _read_reference(args.reference)
            record = store.locate(reference)
            excerpt = store.excerpt(
                reference,
                start_line=args.start_line,
                end_line=args.end_line,
                max_chars=args.max_chars,
            )
            result = _compact_locator(record)
            result.update(
                {
                    "start_line": args.start_line,
                    "end_line": args.end_line,
                    "character_length": len(excerpt),
                    "excerpt": excerpt,
                }
            )
            _dump(result)
            return 0
        _dump(_compact_locator(record))
        return 0
    except (OSError, TypeError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"source_payload_store: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
