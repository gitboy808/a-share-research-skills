#!/usr/bin/env python3
"""Read-only validation for the workspace-bound A-share skill suite."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


SCHEMA_VERSION = "a-share-workspace-v3"
REQUIRED_PATHS = [
    "AGENTS.md",
    "CONTEXT.md",
    "研究规则.md",
    "经验库.md",
    "当前判断.md",
    "观察池.md",
    "对象档案/索引.md",
    "策略库/索引.md",
    "模板/工作集清单模板.md",
    ".agents/skills/a-share/shared/context/__init__.py",
    ".agents/skills/a-share/shared/scripts/context_workspace.py",
]
ARTIFACT_DIRS = ["证据包", "策略库", "运行记录"]
HISTORICAL_RECORD_DIRS = ["判断日志", "对象档案", "报告", "周收敛", "观察日志"]
LEGACY_RUNTIME_DIRECTORIES = ["分析报告", "调研报告", "复盘报告", "扫描报告"]
SKILLS = [
    "a-share-research",
    "a-share-scan",
    "a-share-investigate",
    "a-share-analyze",
    "a-share-review",
    "a-share-meta-review",
]


def find_root(start: Path) -> Path:
    for candidate in [start.resolve(), *start.resolve().parents]:
        if (candidate / "CONTEXT.md").is_file() and (candidate / "研究规则.md").is_file():
            return candidate
    raise ValueError("research workspace markers not found")


def parse_frontmatter(path: Path) -> dict[str, str] | None:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        return None
    end = text.find("\n---\n", 4)
    if end < 0:
        return None
    result: dict[str, str] = {}
    for raw in text[4:end].splitlines():
        if not raw.strip() or raw.lstrip().startswith("#") or ":" not in raw:
            continue
        key, value = raw.split(":", 1)
        result[key.strip()] = value.strip().strip('"\'')
    return result


def check_markdown_links(root: Path, path: Path) -> list[str]:
    errors: list[str] = []
    text = path.read_text(encoding="utf-8")
    for target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", text):
        clean = target.strip("<>").split("#", 1)[0]
        if not clean or re.match(r"^[a-z]+://", clean) or clean.startswith("/"):
            continue
        resolved = (path.parent / clean).resolve()
        if root not in [resolved, *resolved.parents]:
            errors.append(f"{path.relative_to(root)}: link escapes workspace: {target}")
        elif not resolved.exists():
            errors.append(f"{path.relative_to(root)}: broken link: {target}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    errors: list[str] = []
    warnings: list[str] = []
    try:
        root = find_root(args.root)
    except ValueError as exc:
        errors.append(str(exc))
        root = args.root.resolve()

    for rel in REQUIRED_PATHS:
        if not (root / rel).exists():
            errors.append(f"missing required path: {rel}")

    if (root / ".agents/skills/fenxi").exists():
        errors.append("legacy skill directory still exists: .agents/skills/fenxi")
    for directory in LEGACY_RUNTIME_DIRECTORIES:
        if (root / directory).exists():
            errors.append(f"legacy runtime directory still exists: {directory}")

    suite_root = root / ".agents/skills/a-share"
    for skill_name in SKILLS:
        skill_dir = suite_root / skill_name
        skill_md = skill_dir / "SKILL.md"
        openai_yaml = skill_dir / "agents/openai.yaml"
        if not skill_md.exists():
            errors.append(f"missing skill: {skill_name}/SKILL.md")
            continue
        frontmatter = parse_frontmatter(skill_md)
        if frontmatter is None:
            errors.append(f"{skill_name}: invalid SKILL.md frontmatter")
        else:
            if frontmatter.get("name") != skill_name:
                errors.append(f"{skill_name}: frontmatter name mismatch")
            if not frontmatter.get("description"):
                errors.append(f"{skill_name}: missing description")
            unexpected = set(frontmatter) - {"name", "description"}
            if unexpected:
                errors.append(f"{skill_name}: unexpected frontmatter keys {sorted(unexpected)}")
        if not openai_yaml.exists():
            errors.append(f"{skill_name}: missing agents/openai.yaml")
        else:
            metadata = openai_yaml.read_text(encoding="utf-8")
            expected_policy = "true" if skill_name == "a-share-research" else "false"
            if f"allow_implicit_invocation: {expected_policy}" not in metadata:
                errors.append(f"{skill_name}: incorrect implicit invocation policy")
            if f"${skill_name}" not in metadata:
                errors.append(f"{skill_name}: default_prompt does not name the skill")
        if "TODO" in skill_md.read_text(encoding="utf-8"):
            errors.append(f"{skill_name}: unresolved TODO")

    context_root = suite_root / "shared/context"
    if context_root.is_dir():
        for path in context_root.glob("*.py"):
            if "TODO" in path.read_text(encoding="utf-8"):
                errors.append(f"{path.relative_to(root)}: unresolved TODO")

    rule_file = root / "研究规则.md"
    if rule_file.exists():
        rule_count = len(re.findall(r"^- \*\*R\d{2}\b", rule_file.read_text(encoding="utf-8"), re.M))
        if rule_count > 20:
            errors.append(f"research rules exceed 20: {rule_count}")

    lesson_file = root / "经验库.md"
    if lesson_file.exists():
        lesson_count = len(re.findall(r"^### L\d+\b", lesson_file.read_text(encoding="utf-8"), re.M))
        if lesson_count > 50:
            errors.append(f"market lessons exceed 50: {lesson_count}")

    observation_file = root / "观察池.md"
    if observation_file.exists():
        candidate_count = len(re.findall(r"^### C\d{8}-\d{3}\b", observation_file.read_text(encoding="utf-8"), re.M))
        if candidate_count > 20:
            errors.append(f"active observation candidates exceed 20: {candidate_count}")

    schema_path = root / ".agents/skills/a-share/shared/schemas/artifacts.json"
    schemas = {}
    allowed = {}
    contract_schema_versions = {"a-share-task-contract-v1", SCHEMA_VERSION}
    if schema_path.exists():
        definition = json.loads(schema_path.read_text(encoding="utf-8"))
        schemas = definition.get("artifacts", {})
        allowed = definition.get("allowed_status", {})
        contract_schema_versions = set(
            definition.get("task_contract", {}).get("accepted_schema_versions", contract_schema_versions)
        )
    else:
        errors.append("missing artifact schema")

    contracts_root = suite_root / "shared/contracts"
    if contracts_root.exists():
        for path in sorted(contracts_root.glob("*.json")):
            try:
                contract = json.loads(path.read_text(encoding="utf-8"))
                if contract.get("schema_version") not in contract_schema_versions:
                    errors.append(f"{path.relative_to(root)}: unsupported task contract schema")
                if not contract.get("contract_id") or not contract.get("version"):
                    errors.append(f"{path.relative_to(root)}: task contract missing identity/version")
                requirements = contract.get("required_evidence")
                if not isinstance(requirements, list):
                    errors.append(f"{path.relative_to(root)}: required_evidence must be a list")
                else:
                    seen_requirements: set[str] = set()
                    for requirement in requirements:
                        requirement_id = str(requirement.get("requirement_id", "")) if isinstance(requirement, dict) else ""
                        if not requirement_id:
                            errors.append(f"{path.relative_to(root)}: requirement missing requirement_id")
                        elif requirement_id in seen_requirements:
                            errors.append(f"{path.relative_to(root)}: duplicate requirement_id {requirement_id}")
                        seen_requirements.add(requirement_id)
            except (OSError, json.JSONDecodeError, TypeError):
                errors.append(f"{path.relative_to(root)}: invalid task contract JSON")

    declared: dict[tuple[str, str, str], Path] = {}
    for directory in ARTIFACT_DIRS:
        base = root / directory
        if not base.exists():
            errors.append(f"missing artifact directory: {directory}")
            continue
        for path in base.rglob("*.md"):
            if path.name in {"索引.md", "README.md"}:
                continue
            frontmatter = parse_frontmatter(path)
            if frontmatter is None:
                warnings.append(f"legacy or aggregate artifact without frontmatter: {path.relative_to(root)}")
                continue
            artifact_type = frontmatter.get("artifact_type")
            if artifact_type not in schemas:
                warnings.append(f"unknown artifact_type in {path.relative_to(root)}: {artifact_type}")
                continue
            for key in schemas[artifact_type]["required"]:
                if not frontmatter.get(key):
                    errors.append(f"{path.relative_to(root)}: missing frontmatter field {key}")
            if frontmatter.get("schema_version") != SCHEMA_VERSION:
                errors.append(f"{path.relative_to(root)}: unsupported schema_version")
            status = frontmatter.get("status")
            if status not in allowed.get(artifact_type, []):
                errors.append(f"{path.relative_to(root)}: invalid status {status}")
            key = (artifact_type, frontmatter.get("id", ""), frontmatter.get("version", ""))
            if key in declared:
                errors.append(f"duplicate declaration {key}: {declared[key].relative_to(root)} and {path.relative_to(root)}")
            declared[key] = path

    for directory in HISTORICAL_RECORD_DIRS:
        base = root / directory
        if not base.exists():
            continue
        for path in base.rglob("*.md"):
            if path.name in {"索引.md", "README.md"}:
                continue
            frontmatter = parse_frontmatter(path)
            if frontmatter is None:
                errors.append(f"{path.relative_to(root)}: historical record missing frontmatter")
                continue
            if frontmatter.get("artifact_type") != "historical_record":
                errors.append(f"{path.relative_to(root)}: unsupported historical record artifact_type")
                continue
            for key in schemas.get("historical_record", {}).get("required", []):
                if not frontmatter.get(key):
                    errors.append(f"{path.relative_to(root)}: missing historical field {key}")
            if frontmatter.get("schema_version") != SCHEMA_VERSION:
                errors.append(f"{path.relative_to(root)}: unsupported historical schema_version")
            if frontmatter.get("status") not in allowed.get("historical_record", []):
                errors.append(f"{path.relative_to(root)}: invalid historical status {frontmatter.get('status')}")

    # Workset manifests are JSON sidecars next to persistent run records. They
    # contain only stable references and audit fields, never hydrated text.
    run_root = root / "运行记录"
    if run_root.exists():
        for path in run_root.rglob("*.json"):
            try:
                manifest = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                errors.append(f"{path.relative_to(root)}: invalid JSON workset manifest")
                continue
            if manifest.get("artifact_type") != "workset_manifest":
                continue
            for key in schemas.get("workset_manifest", {}).get("required", []):
                if key not in manifest or manifest[key] in (None, ""):
                    errors.append(f"{path.relative_to(root)}: missing workset field {key}")
            if manifest.get("schema_version") != SCHEMA_VERSION:
                errors.append(f"{path.relative_to(root)}: unsupported workset schema_version")
            if manifest.get("status") not in allowed.get("workset_manifest", []):
                errors.append(f"{path.relative_to(root)}: invalid workset status {manifest.get('status')}")
            serialized = json.dumps(manifest, ensure_ascii=False)
            if "verification_text" in serialized or "事实陈述" in serialized:
                errors.append(f"{path.relative_to(root)}: workset manifest contains source payload text")
            references = manifest.get("stable_references", [])
            if not isinstance(references, list):
                errors.append(f"{path.relative_to(root)}: stable_references must be a list")
            else:
                reference_ids: set[str] = set()
                for reference in references:
                    if not isinstance(reference, dict) or not reference.get("ref") or not reference.get("unit_id"):
                        errors.append(f"{path.relative_to(root)}: malformed stable reference")
                        continue
                    if reference["unit_id"] in reference_ids:
                        errors.append(f"{path.relative_to(root)}: duplicate stable unit {reference['unit_id']}")
                    reference_ids.add(reference["unit_id"])
                    locator = reference.get("source_locator")
                    if not isinstance(locator, dict) or not locator.get("path"):
                        errors.append(f"{path.relative_to(root)}: stable reference missing source locator")

    active_files = [root / "AGENTS.md", root / "研究规则.md", *(root / "模板").glob("*.md")]
    active_files += list((root / ".agents/skills/a-share").rglob("SKILL.md"))
    forbidden = ["弃权不记分", "JMMDD-NN", "买/卖/持有/减仓/放弃"]
    for path in active_files:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        for token in forbidden:
            if token in text:
                errors.append(f"{path.relative_to(root)}: forbidden legacy token {token}")

    for path in [root / "AGENTS.md", root / "CONTEXT.md", root / "研究规则.md", *(root / "模板").glob("*.md"), *(root / "docs/adr").glob("*.md")]:
        if path.exists():
            errors.extend(check_markdown_links(root, path))

    payload = {"root": str(root), "errors": errors, "warnings": warnings}
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"workspace: {root}")
        print(f"errors: {len(errors)}")
        for item in errors:
            print(f"ERROR {item}")
        print(f"warnings: {len(warnings)}")
        for item in warnings:
            print(f"WARN  {item}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
