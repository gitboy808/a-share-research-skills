#!/usr/bin/env python3
"""Create a private A-share research workspace from the public scaffold."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Initialize missing private workspace files without overwriting existing data."
    )
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args()

    root = args.root.resolve()
    if root == Path(root.anchor):
        raise SystemExit("refusing to initialize a filesystem root")

    project_root = Path(__file__).resolve().parents[1]
    scaffold = project_root / "scaffold"
    if not scaffold.is_dir():
        raise SystemExit(f"scaffold not found: {scaffold}")
    if not (root / "CONTEXT.md").is_file() or not (root / "研究规则.md").is_file():
        raise SystemExit("target is not an A-share research repository")

    created: list[Path] = []
    skipped: list[Path] = []
    for source in sorted(scaffold.rglob("*")):
        relative = source.relative_to(scaffold)
        target = root / relative
        if source.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            skipped.append(relative)
            continue
        shutil.copy2(source, target)
        created.append(relative)

    print(f"workspace: {root}")
    print(f"created: {len(created)}")
    for path in created:
        print(f"CREATE {path}")
    print(f"skipped: {len(skipped)}")
    for path in skipped:
        print(f"SKIP   {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
