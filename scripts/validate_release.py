#!/usr/bin/env python3
"""Fail when a public commit includes private runtime data or obvious secrets."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path


PRIVATE_FILES = {"当前判断.md", "经验库.md", "观察池.md"}
PRIVATE_DIRECTORIES = {
    "判断日志",
    "观察日志",
    "证据包",
    "运行记录",
    "对象档案",
    "报告",
    "周收敛",
}
REQUIRED = {
    "README.md",
    "LICENSE",
    "AGENTS.md",
    "CONTEXT.md",
    "研究规则.md",
    "scripts/init_workspace.py",
    ".agents/skills/a-share/a-share-research/SKILL.md",
}
SECRET_PATTERNS = {
    "OpenAI-style API key": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    "GitHub token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    "private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "absolute macOS user path": re.compile(r"/" r"Users/[^/\s]+/"),
}


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print("ERROR repository is not initialized with Git")
        return 1

    tracked = {item for item in result.stdout.split("\0") if item}
    errors: list[str] = []
    for required in sorted(REQUIRED - tracked):
        errors.append(f"missing required tracked file: {required}")

    for relative in sorted(tracked):
        path = Path(relative)
        if path.parts and path.parts[0] != "scaffold":
            if relative in PRIVATE_FILES or path.parts[0] in PRIVATE_DIRECTORIES:
                errors.append(f"private runtime path is tracked: {relative}")
        if ".agents/skills/fenxi" in relative:
            errors.append(f"legacy skill is tracked: {relative}")

        full_path = root / relative
        if not full_path.is_file() or full_path.stat().st_size > 1_000_000:
            continue
        try:
            text = full_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for label, pattern in SECRET_PATTERNS.items():
            if pattern.search(text):
                errors.append(f"{label} found in tracked file: {relative}")

    print(f"tracked files: {len(tracked)}")
    print(f"errors: {len(errors)}")
    for error in errors:
        print(f"ERROR {error}")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
