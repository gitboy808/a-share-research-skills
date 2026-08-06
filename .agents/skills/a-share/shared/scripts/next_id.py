#!/usr/bin/env python3
"""Print the next workspace artifact ID without modifying files."""

from __future__ import annotations

import argparse
import re
from datetime import datetime
from pathlib import Path


PATTERNS = {
    "J": r"J{date}-(\d{{3}})",
    "C": r"C{date}-(\d{{3}})",
    "EVI": r"EVI-{date}-(\d{{3}})",
    "RUN": r"RUN-{date}-(\d{{3}})",
}


def find_root(start: Path) -> Path:
    for candidate in [start.resolve(), *start.resolve().parents]:
        if (candidate / "CONTEXT.md").is_file() and (candidate / "研究规则.md").is_file():
            return candidate
    raise SystemExit("research workspace not found")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("kind", choices=PATTERNS)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--date", default=datetime.now().strftime("%Y%m%d"))
    args = parser.parse_args()

    if not re.fullmatch(r"\d{8}", args.date):
        raise SystemExit("--date must be YYYYMMDD")

    root = find_root(args.root)
    pattern = re.compile(PATTERNS[args.kind].format(date=args.date))
    highest = 0
    for path in root.rglob("*.md"):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        highest = max([highest, *(int(value) for value in pattern.findall(text))])

    prefix = args.kind if args.kind in {"J", "C"} else f"{args.kind}-"
    print(f"{prefix}{args.date}-{highest + 1:03d}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
