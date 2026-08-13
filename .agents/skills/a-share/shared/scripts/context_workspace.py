#!/usr/bin/env python3
"""Compact JSON adapter for the internal workset assembly module."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


SHARED_ROOT = Path(__file__).resolve().parents[1]
if str(SHARED_ROOT) not in sys.path:
    sys.path.insert(0, str(SHARED_ROOT))

from context import assemble, hydrate  # noqa: E402


def _json_value(path: Path) -> object:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _dump(value: object) -> None:
    sys.stdout.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Assemble or hydrate an A-share research workset.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    assemble_parser = subparsers.add_parser("assemble")
    assemble_parser.add_argument("--run-manifest", required=True, type=Path)
    assemble_parser.add_argument("--task-evidence", type=Path)
    assemble_parser.add_argument("--root", type=Path)
    assemble_parser.add_argument("--projection-path", type=Path)

    hydrate_parser = subparsers.add_parser("hydrate")
    hydrate_parser.add_argument("--references", required=True, type=Path)
    hydrate_parser.add_argument("--root", type=Path)

    args = parser.parse_args(argv)
    try:
        if args.command == "assemble":
            run_manifest = _json_value(args.run_manifest)
            if args.root:
                run_manifest["workspace_root"] = str(args.root.resolve())
            if args.projection_path:
                run_manifest["projection_path"] = str(args.projection_path.resolve())
            task = _json_value(args.task_evidence) if args.task_evidence else None
            _dump(assemble(run_manifest, task))
        else:
            _dump(hydrate(_json_value(args.references), args.root))
        return 0
    except (OSError, TypeError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"context_workspace: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
