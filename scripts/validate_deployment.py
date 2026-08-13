#!/usr/bin/env python3
"""Read-only validation for a private, versioned A-share research deployment."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from security_scan import scan_files


SKIPPED_DIRECTORIES = {".git", ".context", "__pycache__"}


def deployment_files(root: Path) -> list[str]:
    paths: list[str] = []
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if any(part in SKIPPED_DIRECTORIES for part in relative.parts):
            continue
        if path.is_file():
            paths.append(relative.as_posix())
    return paths


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate private workspace structure and scan deployed files for credentials."
    )
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    root = args.root.resolve()
    workspace_validator = root / ".agents/skills/a-share/shared/scripts/validate_workspace.py"
    workspace_errors: list[str] = []
    workspace_warnings: list[str] = []
    if not workspace_validator.is_file():
        workspace_errors.append("missing private workspace validator")
    else:
        result = subprocess.run(
            [sys.executable, str(workspace_validator), "--root", str(root), "--json"],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
        )
        try:
            validation = json.loads(result.stdout)
        except json.JSONDecodeError:
            workspace_errors.append("private workspace validator returned invalid JSON")
        else:
            workspace_errors.extend(str(item) for item in validation.get("errors", []))
            workspace_warnings.extend(str(item) for item in validation.get("warnings", []))

    findings = scan_files(root, deployment_files(root), include_public_paths=False)
    security_errors = [finding.message() for finding in findings]
    payload = {
        "mode": "private_deployment",
        "root": str(root),
        "workspace_errors": workspace_errors,
        "security_errors": security_errors,
        "warnings": workspace_warnings,
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"mode: {payload['mode']}")
        print(f"root: {root}")
        print(f"workspace errors: {len(workspace_errors)}")
        for error in workspace_errors:
            print(f"ERROR {error}")
        print(f"security errors: {len(security_errors)}")
        for error in security_errors:
            print(f"ERROR {error}")
        print(f"warnings: {len(workspace_warnings)}")
        for warning in workspace_warnings:
            print(f"WARN  {warning}")
    return 1 if workspace_errors or security_errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
