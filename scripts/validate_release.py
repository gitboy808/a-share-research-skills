#!/usr/bin/env python3
"""Validate the public Git release surface, masking every security finding."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path

from security_scan import scan_files


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
BASE_REQUIRED = {
    "README.md",
    "LICENSE",
    "AGENTS.md",
    "CONTEXT.md",
    "研究规则.md",
    ".agents/skills/a-share/shared/suite-manifest.yaml",
}
LIVE_RUNTIME_ROOTS = (
    Path(".agents/skills/a-share"),
    Path("模板"),
    Path("scaffold"),
)
LIVE_FILES = {
    "scripts/init_workspace.py",
    "scripts/security_scan.py",
    "scripts/validate_deployment.py",
    "scripts/validate_release.py",
    "docs/architecture.md",
    "docs/adr/0029-以资格模式隔离当前分析与历史审计.md",
}
IGNORED_RUNTIME_PARTS = {"__pycache__", ".DS_Store"}
SUITE_PATH_KEYS = {
    "artifact_schema",
    "validation_script",
    "id_script",
    "context_cli",
}
REQUIRED_CONTRACT_ROLES = {
    ("scan", "scan", None),
    ("investigate", "research", "market"),
    ("investigate", "research", "stock"),
    ("investigate", "research", "industry"),
    ("investigate", "research", "theme"),
    ("investigate", "research", "event"),
    ("analyze", "analysis", None),
    ("review", "review", None),
    ("meta-review", "meta-review", None),
}


def _runtime_files(root: Path) -> set[str]:
    """Return every file required by the current v3 runtime."""

    required = {*BASE_REQUIRED, *LIVE_FILES}
    for relative_root in LIVE_RUNTIME_ROOTS:
        directory = root / relative_root
        if not directory.exists():
            continue
        for path in directory.rglob("*"):
            relative = path.relative_to(root)
            if any(part in IGNORED_RUNTIME_PARTS for part in relative.parts):
                continue
            if path.is_file() or path.is_symlink():
                required.add(relative.as_posix())
    return required


def _suite_references(root: Path) -> tuple[set[str], list[str]]:
    """Resolve declared suite entry points without depending on a YAML package."""

    suite_relative = Path(".agents/skills/a-share/shared/suite-manifest.yaml")
    suite_path = root / suite_relative
    if not suite_path.is_file():
        return set(), [f"missing suite manifest: {suite_relative.as_posix()}"]
    try:
        lines = suite_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return set(), [f"suite manifest is unreadable: {suite_relative.as_posix()}"]

    suite_root = suite_path.parent.parent
    references: set[str] = set()
    errors: list[str] = []
    in_skills = False
    for line in lines:
        if line.strip() == "skills:":
            in_skills = True
            continue
        if in_skills and line and not line.startswith("  "):
            in_skills = False
        if in_skills:
            match = re.fullmatch(r'  [a-z_]+:\s*"([^"]+)"\s*', line)
            if match:
                references.add(
                    (suite_root / match.group(1) / "SKILL.md").relative_to(root).as_posix()
                )
            continue
        match = re.fullmatch(r'([a-z_]+):\s*"([^"]+)"\s*', line)
        if match and match.group(1) in SUITE_PATH_KEYS:
            resolved = (suite_root / match.group(2)).resolve()
            try:
                references.add(resolved.relative_to(root).as_posix())
            except ValueError:
                errors.append(f"suite reference escapes repository: {match.group(1)}")
    for relative in sorted(references):
        if not (root / relative).is_file():
            errors.append(f"missing suite reference: {relative}")
    return references, errors


def _contract_role_errors(root: Path) -> list[str]:
    """Require one registered contract for every formal workflow/object role."""

    contract_root = root / ".agents/skills/a-share/shared/contracts"
    observed: set[tuple[str, str, str | None]] = set()
    errors: list[str] = []
    for path in sorted(contract_root.glob("*.json")):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            errors.append(f"invalid registered contract: {path.relative_to(root).as_posix()}")
            continue
        workflow = str(value.get("workflow") or "")
        stage = str(value.get("stage") or "")
        object_types = value.get("object_types") or [None]
        for object_type in object_types:
            observed.add((workflow, stage, str(object_type) if object_type is not None else None))
    for workflow, stage, object_type in sorted(
        REQUIRED_CONTRACT_ROLES, key=lambda item: (item[0], item[1], item[2] or "")
    ):
        if (workflow, stage, object_type) not in observed:
            suffix = f"/{object_type}" if object_type else ""
            errors.append(f"missing registered contract role: {workflow}/{stage}{suffix}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate files exposed by a public Git release.")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    root = args.root.resolve()
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    errors: list[str] = []
    if result.returncode != 0:
        errors.append("repository is not initialized with Git")
        tracked: set[str] = set()
    else:
        tracked = {item for item in result.stdout.split("\0") if item}

    required = _runtime_files(root)
    suite_references, suite_errors = _suite_references(root)
    required.update(suite_references)
    errors.extend(suite_errors)
    errors.extend(_contract_role_errors(root))
    for required in sorted(required - tracked):
        errors.append(f"missing required tracked file: {required}")

    for relative in sorted(tracked):
        path = Path(relative)
        if path.parts and path.parts[0] != "scaffold":
            if relative in PRIVATE_FILES or path.parts[0] in PRIVATE_DIRECTORIES:
                errors.append(f"private runtime path is tracked: {relative}")

    findings = scan_files(
        root,
        tracked,
        include_public_paths=True,
    )
    errors.extend(finding.message() for finding in findings)

    payload = {
        "mode": "public_release_v3",
        "root": str(root),
        "tracked_files": len(tracked),
        "errors": errors,
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"mode: {payload['mode']}")
        print(f"root: {root}")
        print(f"tracked files: {len(tracked)}")
        print(f"errors: {len(errors)}")
        for error in errors:
            print(f"ERROR {error}")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
