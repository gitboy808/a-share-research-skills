from __future__ import annotations

import json
import hashlib
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
CONTEXT_CLI = REPO_ROOT / ".agents/skills/a-share/shared/scripts/context_workspace.py"


def write_fixture_workspace(root: Path) -> None:
    (root / "证据包/2026-08").mkdir(parents=True)
    (root / "对象档案/个股").mkdir(parents=True)
    (root / "策略库").mkdir(parents=True)
    for name in ("CONTEXT.md", "研究规则.md", "经验库.md", "当前判断.md", "观察池.md"):
        (root / name).write_text(f"# {name}\n", encoding="utf-8")
    (root / "对象档案/索引.md").write_text("# 对象档案索引\n", encoding="utf-8")
    (root / "策略库/索引.md").write_text("# 策略索引\n", encoding="utf-8")
    (root / "证据包/2026-08/EVI-20260808-001.md").write_text(
        """---
schema_version: "a-share-workspace-v3"
artifact_type: "evidence_package"
id: "EVI-20260808-001"
status: "complete"
information_cutoff: "2026-08-08T09:00:00+08:00"
created_at: "2026-08-08T09:01:00+08:00"
objects: "个股:测试公司(600001)"
---

# 测试证据包

### EVI-20260808-001#001｜主营事实

- **事实陈述**：测试公司已披露主营业务为测试设备。
- **状态**：已确认
- **关联对象 / 档案字段**：个股:测试公司(600001) / business
- **数据交易日**：2026-08-07
- **过期条件 / 下次复核**：重大公告或 2026-08-31

### EVI-20260808-001#002｜市场事实

- **事实陈述**：A 股市场成交保持活跃。
- **状态**：多源印证
- **关联对象 / 档案字段**：市场:A股 / market_state
- **数据交易日**：2026-08-07
- **过期条件 / 下次复核**：下一个交易日
""",
        encoding="utf-8",
    )


class ContextWorkspaceSeamsTest(unittest.TestCase):
    def test_assemble_and_hydrate_return_coverage_gaps_and_stable_references(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_fixture_workspace(root)
            run_manifest = {
                "schema_version": "a-share-workspace-v3",
                "run_id": "RUN-20260808-001",
                "workspace_root": str(root),
                "workflow": "investigate",
                "stage": "research",
                "information_cutoff": "2026-08-08T09:00:00+08:00",
                "objects": ["个股:测试公司(600001)", "市场:A股"],
                "budget": {"soft_units": 1},
            }
            task_manifest = {
                "schema_version": "a-share-workspace-v3",
                "contract_id": "investigate.synthetic",
                "version": "1.0.0",
                "required_evidence": [
                    {
                        "requirement_id": "business",
                        "unit_type": "evidence_item",
                        "object": "个股:测试公司(600001)",
                        "field": "business",
                        "evidence_role": "primary",
                    },
                    {
                        "requirement_id": "market",
                        "unit_type": "evidence_item",
                        "object": "市场:A股",
                        "field": "market_state",
                        "evidence_role": "primary",
                    },
                ],
            }

            sys.path.insert(0, str(REPO_ROOT / ".agents/skills/a-share/shared"))
            from context import assemble, hydrate  # type: ignore[import-not-found]

            assembled = assemble(run_manifest, task_manifest)
            self.assertEqual(assembled["coverage"]["required_total"], 2)
            self.assertEqual(assembled["coverage"]["required_covered"], 2)
            self.assertEqual(assembled["gaps"], [])
            self.assertEqual(len(assembled["stable_references"]), 2)
            self.assertTrue(all("source_locator" in ref for ref in assembled["stable_references"]))
            self.assertTrue(all("事实陈述" not in json.dumps(ref, ensure_ascii=False) for ref in assembled["stable_references"]))

            hydrated = hydrate(assembled["stable_references"])
            self.assertEqual(len(hydrated["units"]), 2)
            self.assertTrue(all(unit["verification_text"] for unit in hydrated["units"]))
            self.assertTrue(any("测试公司已披露主营业务" in unit["verification_text"] for unit in hydrated["units"]))

    def test_cli_uses_compact_json_and_preserves_required_items_under_soft_budget(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_fixture_workspace(root)
            run_path = root / "run.json"
            task_path = root / "task.json"
            run_path.write_text(
                json.dumps(
                    {
                        "schema_version": "a-share-workspace-v3",
                        "run_id": "RUN-20260808-002",
                        "workspace_root": str(root),
                        "workflow": "investigate",
                        "information_cutoff": "2026-08-08T09:00:00+08:00",
                        "budget": {"soft_units": 0},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            task_path.write_text(
                json.dumps(
                    {
                        "schema_version": "a-share-workspace-v3",
                        "contract_id": "investigate.synthetic",
                        "version": "1.0.0",
                        "required_evidence": [
                            {
                                "requirement_id": "business",
                                "unit_type": "evidence_item",
                                "object": "个股:测试公司(600001)",
                                "field": "business",
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            result = subprocess.run(
                [
                    sys.executable,
                    str(CONTEXT_CLI),
                    "assemble",
                    "--run-manifest",
                    str(run_path),
                    "--task-evidence",
                    str(task_path),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["coverage"]["required_covered"], 1)
            self.assertEqual(payload["gaps"], [])
            self.assertLessEqual(len(result.stdout.splitlines()), 1)

            refs_path = root / "refs.json"
            refs_path.write_text(json.dumps(payload["stable_references"], ensure_ascii=False), encoding="utf-8")
            hydrated = subprocess.run(
                [
                    sys.executable,
                    str(CONTEXT_CLI),
                    "hydrate",
                    "--references",
                    str(refs_path),
                    "--root",
                    str(root),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(hydrated.returncode, 0, hydrated.stderr)
            self.assertTrue(json.loads(hydrated.stdout)["units"][0]["verification_text"])

    def test_source_payload_is_external_and_hydrate_returns_only_bounded_excerpt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            from context import FileSourcePayloadStore, hydrate  # type: ignore[import-not-found]

            store = FileSourcePayloadStore(root)
            reference = store.put(
                "line 1\nline 2\nline 3\n",
                run_id="RUN-20260808-003",
                source_uri="https://example.invalid/source",
                acquired_at="2026-08-08T09:00:00+08:00",
            )
            stable_reference = {
                "ref": "atom:PAYLOAD-001",
                "unit_id": "PAYLOAD-001",
                "unit_type": "evidence_item",
                "source_locator": {
                    "kind": "source_payload",
                    **reference,
                    "start_line": 2,
                    "end_line": 2,
                },
                "workspace_root": str(root),
            }
            hydrated = hydrate([stable_reference], source_payload_store=store)
            self.assertEqual(hydrated["units"][0]["verification_text"], "line 2")
            self.assertEqual(hydrated["quality"]["source_payload_externalized"], 1)

    def test_projection_rebuilds_after_fact_hash_changes_without_using_stale_text(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_fixture_workspace(root)
            run_manifest = {
                "schema_version": "a-share-workspace-v3",
                "run_id": "RUN-20260808-004",
                "workspace_root": str(root),
                "workflow": "investigate",
                "information_cutoff": "2026-08-08T09:00:00+08:00",
            }
            task_manifest = {
                "schema_version": "a-share-workspace-v3",
                "contract_id": "investigate.synthetic",
                "version": "1.0.0",
                "required_evidence": [
                    {
                        "requirement_id": "business",
                        "unit_type": "evidence_item",
                        "object": "个股:测试公司(600001)",
                        "field": "business",
                    }
                ],
            }
            sys.path.insert(0, str(REPO_ROOT / ".agents/skills/a-share/shared"))
            from context import assemble  # type: ignore[import-not-found]

            first = assemble(run_manifest, task_manifest)
            evidence = root / "证据包/2026-08/EVI-20260808-001.md"
            original = evidence.read_text(encoding="utf-8")
            evidence.write_text(original.replace("测试设备", "精密测试设备"), encoding="utf-8")
            second = assemble(run_manifest, task_manifest)
            self.assertFalse(second["projection"]["projection_degraded"])
            self.assertNotEqual(first["projection"].get("source_manifest_hash"), second["projection"].get("source_manifest_hash"))
            hydrated = __import__("context", fromlist=["hydrate"]).hydrate(second["stable_references"])
            self.assertIn("精密测试设备", hydrated["units"][0]["verification_text"])

    def test_conflict_is_a_recorded_noncovered_gap_and_allowed_unknown_is_not_silently_complete(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_fixture_workspace(root)
            evidence = root / "证据包/2026-08/EVI-20260808-001.md"
            evidence.write_text(
                evidence.read_text(encoding="utf-8").replace("多源印证", "冲突"),
                encoding="utf-8",
            )
            sys.path.insert(0, str(REPO_ROOT / ".agents/skills/a-share/shared"))
            from context import assemble  # type: ignore[import-not-found]

            run = {
                "workspace_root": str(root),
                "run_id": "RUN-20260808-005",
                "information_cutoff": "2026-08-08T09:00:00+08:00",
            }
            task = {
                "contract_id": "investigate.synthetic",
                "version": "1.0.0",
                "required_evidence": [
                    {
                        "requirement_id": "market",
                        "unit_type": "evidence_item",
                        "object": "市场:A股",
                        "field": "market_state",
                        "allow_unknown": True,
                    }
                ],
            }
            result = assemble(run, task)
            self.assertEqual(result["coverage"]["required_covered"], 0)
            self.assertEqual(result["gaps"][0]["reason"], "conflict_or_denial")
            self.assertFalse(result["gaps"][0]["blocking"])

    def test_allowed_unknown_remains_a_nonblocking_gap(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_fixture_workspace(root)
            evidence = root / "证据包/2026-08/EVI-20260808-001.md"
            evidence.write_text(
                evidence.read_text(encoding="utf-8").replace("多源印证", "未知"),
                encoding="utf-8",
            )
            sys.path.insert(0, str(REPO_ROOT / ".agents/skills/a-share/shared"))
            from context import assemble  # type: ignore[import-not-found]

            result = assemble(
                {
                    "workspace_root": str(root),
                    "run_id": "RUN-20260808-007",
                    "information_cutoff": "2026-08-08T09:00:00+08:00",
                },
                {
                    "contract_id": "investigate.synthetic",
                    "version": "1.0.0",
                    "required_evidence": [
                        {
                            "requirement_id": "market",
                            "unit_type": "evidence_item",
                            "object": "市场:A股",
                            "field": "market_state",
                            "allow_unknown": True,
                        }
                    ],
                },
            )
            self.assertEqual(result["coverage"]["required_covered"], 0)
            self.assertEqual(result["gaps"][0]["reason"], "unknown")
            self.assertFalse(result["gaps"][0]["blocking"])

    def test_future_snapshot_is_excluded_and_hydrate_rechecks_cutoff(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_fixture_workspace(root)
            evidence = root / "证据包/2026-08/EVI-20260808-001.md"
            evidence.write_text(
                evidence.read_text(encoding="utf-8").replace(
                    "- **事实陈述**：测试公司已披露主营业务为测试设备。",
                    "- **信息快照**：截至 2026-08-09T09:00:00+08:00\n- **事实陈述**：测试公司已披露主营业务为测试设备。",
                ),
                encoding="utf-8",
            )
            sys.path.insert(0, str(REPO_ROOT / ".agents/skills/a-share/shared"))
            from context import assemble, hydrate  # type: ignore[import-not-found]

            run = {
                "workspace_root": str(root),
                "run_id": "RUN-20260808-008",
                "information_cutoff": "2026-08-08T09:00:00+08:00",
            }
            task = {
                "contract_id": "investigate.synthetic",
                "version": "1.0.0",
                "required_evidence": [
                    {
                        "requirement_id": "business",
                        "unit_type": "evidence_item",
                        "object": "个股:测试公司(600001)",
                        "field": "business",
                    }
                ],
            }
            result = assemble(run, task)
            self.assertEqual(result["coverage"]["required_covered"], 0)
            self.assertEqual(result["gaps"][0]["reason"], "future_information")
            self.assertEqual(result["stable_references"], [])

            unrestricted = assemble({"workspace_root": str(root), "run_id": "RUN-20260808-009"}, task)
            reference = dict(unrestricted["stable_references"][0])
            reference["selection_cutoff"] = "2026-08-08T09:00:00+08:00"
            hydrated = hydrate([reference])
            self.assertEqual(hydrated["units"], [])
            self.assertIn("future", hydrated["missing_references"][0]["reason"])

    def test_projection_integrity_rebuilds_when_fts_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_fixture_workspace(root)
            sys.path.insert(0, str(REPO_ROOT / ".agents/skills/a-share/shared"))
            from context import assemble  # type: ignore[import-not-found]

            run = {
                "workspace_root": str(root),
                "run_id": "RUN-20260808-010",
                "information_cutoff": "2026-08-08T09:00:00+08:00",
            }
            task = {
                "contract_id": "investigate.synthetic",
                "version": "1.0.0",
                "required_evidence": [],
            }
            assemble(run, task)
            projection = root / ".context/projection.sqlite3"
            connection = sqlite3.connect(projection)
            try:
                connection.execute("DROP TABLE units_fts")
                connection.commit()
            finally:
                connection.close()
            rebuilt = assemble(run, task)
            self.assertTrue(rebuilt["projection"]["rebuilt"])
            self.assertFalse(rebuilt["projection"]["projection_degraded"])

    def test_relative_projection_path_is_resolved_inside_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_fixture_workspace(root)
            sys.path.insert(0, str(REPO_ROOT / ".agents/skills/a-share/shared"))
            from context import assemble  # type: ignore[import-not-found]

            result = assemble(
                {
                    "workspace_root": str(root),
                    "run_id": "RUN-20260808-011",
                    "projection_path": ".context/custom.sqlite3",
                },
                {"contract_id": "investigate.synthetic", "version": "1.0.0", "required_evidence": []},
            )
            self.assertEqual(Path(result["projection"]["path"]), root.resolve() / ".context/custom.sqlite3")

    def test_persistent_run_writes_a_machine_readable_workset_manifest_without_original_text(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_fixture_workspace(root)
            sys.path.insert(0, str(REPO_ROOT / ".agents/skills/a-share/shared"))
            from context import assemble  # type: ignore[import-not-found]

            result = assemble(
                {
                    "workspace_root": str(root),
                    "run_id": "RUN-20260808-006",
                    "workflow": "investigate",
                    "information_cutoff": "2026-08-08T09:00:00+08:00",
                    "persist_workset_manifest": True,
                },
                {
                    "contract_id": "investigate.synthetic",
                    "version": "1.0.0",
                    "required_evidence": [],
                },
            )
            manifest_path = Path(result["workset_manifest_path"])
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["artifact_type"], "workset_manifest")
            self.assertEqual(manifest["status"], "completed")
            self.assertNotIn("verification_text", manifest_path.read_text(encoding="utf-8"))
            self.assertNotIn("事实陈述", manifest_path.read_text(encoding="utf-8"))

    def test_migration_cli_requires_isolated_output_and_removes_legacy_runtime_directories(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            source = base / "old"
            output = base / "new"
            for relative in (
                "分析报告/2026-08/report.md",
                "调研报告/2026-08/investigation.md",
                "复盘报告/2026-08/review.md",
                "扫描报告/2026-08/scan.md",
                "证据包/2026-08/EVI-20260808-001.md",
                "判断日志/2026-08.md",
                "对象档案/个股/测试公司.md",
            ):
                path = source / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                content = "# 历史材料\n\n当时未记录的字段保持未知。\n"
                if relative == "判断日志/2026-08.md":
                    content = "# 判断日志\n\n## 历史批次\n\n- **J0803-01 v1**｜历史判断正文。\n"
                path.write_text(content, encoding="utf-8")
            for relative in (
                "CONTEXT.md",
                "研究规则.md",
                "经验库.md",
                "当前判断.md",
                "观察池.md",
                "对象档案/索引.md",
                "策略库/索引.md",
            ):
                path = source / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(f"# {relative}\n", encoding="utf-8")
            before = {
                path.relative_to(source).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in source.rglob("*")
                if path.is_file()
            }
            result = subprocess.run(
                [
                    sys.executable,
                    str(REPO_ROOT / "scripts/migrate_workspace.py"),
                    "--input",
                    str(source),
                    "--output",
                    str(output),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["status"], "completed")
            self.assertFalse((output / "分析报告").exists())
            self.assertFalse((output / "调研报告").exists())
            self.assertFalse((output / "复盘报告").exists())
            self.assertFalse((output / "扫描报告").exists())
            self.assertTrue((output / "报告/分析/2026-08/report.md").is_file())
            self.assertTrue((output / "迁移映射.json").is_file())
            report = json.loads((output / "迁移映射.json").read_text(encoding="utf-8"))
            self.assertEqual(report["acceptance"]["status"], "not_run")
            self.assertTrue((output / "判断日志/2026-08.md").read_text(encoding="utf-8").startswith("---\n"))
            self.assertTrue((output / "对象档案/个股/测试公司.md").read_text(encoding="utf-8").startswith("---\n"))
            self.assertTrue((output / "判断日志/迁移-2026-08.md").is_file())
            after = {
                path.relative_to(source).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in source.rglob("*")
                if path.is_file()
            }
            self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
