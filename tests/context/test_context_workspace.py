from __future__ import annotations

import json
import hashlib
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.support.workspace_builders import (
    contract,
    evidence_item,
    run_manifest,
    write_evidence,
    write_text,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
CONTEXT_CLI = REPO_ROOT / ".agents/skills/a-share/shared/scripts/context_workspace.py"
SHARED_ROOT = REPO_ROOT / ".agents/skills/a-share/shared"
if str(SHARED_ROOT) not in sys.path:
    sys.path.insert(0, str(SHARED_ROOT))


TEST_CUTOFF = "2026-08-08T09:00:00+08:00"


def write_fixture_workspace(root: Path, *, market_evidence_role: str | None = None) -> None:
    for name in ("CONTEXT.md", "研究规则.md", "经验库.md", "当前判断.md", "观察池.md"):
        write_text(root, name, f"# {name}")
    write_text(root, "对象档案/索引.md", "# 对象档案索引")
    write_text(root, "策略库/索引.md", "# 策略索引")
    write_evidence(
        root,
        "EVI-20260808-001",
        [
            evidence_item(
                "EVI-20260808-001#001｜主营事实",
                fact="测试公司已披露主营业务为测试设备。",
                role=None,
            ),
            evidence_item(
                "EVI-20260808-001#002｜市场事实",
                fact="A 股市场成交保持活跃。",
                status="多源印证",
                object_name="市场:A股",
                field="market_state",
                role=market_evidence_role,
                valid_until="下一个交易日",
            ),
        ],
        cutoff=TEST_CUTOFF,
        objects="个股:测试公司(600001)",
    )


def market_task(*, allow_unknown: bool = True) -> dict[str, object]:
    return contract(
        [
            {
                "requirement_id": "market",
                "unit_type": "evidence_item",
                "object": "市场:A股",
                "field": "market_state",
                "allow_unknown": allow_unknown,
            }
        ],
        contract_id="investigate.synthetic",
    )


class ContextWorkspaceSeamsTest(unittest.TestCase):
    def test_assemble_and_hydrate_return_coverage_gaps_and_stable_references(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_fixture_workspace(root, market_evidence_role="confirmation")
            run_manifest = {
                "schema_version": "a-share-workspace-v3",
                "run_id": "RUN-20260808-001",
                "workspace_root": str(root),
                "workflow": "investigate",
                "stage": "research",
                "information_cutoff": "2026-08-08T09:00:00+08:00",
                "objects": ["个股:测试公司(600001)"],
                "budget": {"soft_units": 1},
                "task_contract": "investigate-stock-v1",
            }

            sys.path.insert(0, str(REPO_ROOT / ".agents/skills/a-share/shared"))
            from context import assemble, hydrate  # type: ignore[import-not-found]

            assembled = assemble(run_manifest)
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
            write_fixture_workspace(root, market_evidence_role="confirmation")
            run_path = root / "run.json"
            run_path.write_text(
                json.dumps(
                    {
                        "schema_version": "a-share-workspace-v3",
                        "run_id": "RUN-20260808-002",
                        "workspace_root": str(root),
                        "workflow": "investigate",
                        "stage": "research",
                        "information_cutoff": "2026-08-08T09:00:00+08:00",
                        "objects": ["个股:测试公司(600001)"],
                        "budget": {"soft_units": 0},
                        "task_contract": "investigate-stock-v1",
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
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["coverage"]["required_total"], 2)
            self.assertEqual(payload["coverage"]["required_covered"], 2)
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
            from context import hydrate  # type: ignore[import-not-found]
            from a_share_context.source_payload import FileSourcePayloadStore  # type: ignore[import-not-found]

            store = FileSourcePayloadStore(root)
            reference = store.put(
                "line 1\nline 2\nline 3\n",
                run_id="RUN-20260808-003",
                source_uri="https://example.invalid/source",
                acquired_at="2026-08-08T09:00:00+08:00",
            )
            payload_id = reference["payload_id"]
            stable_reference = {
                "ref": f"source-payload:{payload_id}",
                "unit_id": payload_id,
                "unit_type": "source_payload_candidate",
                "authority": "source_payload_store",
                "information_cutoff": "2026-08-08T09:00:00+08:00",
                "selection_cutoff": "2026-08-08T09:01:00+08:00",
                "status": "unverified",
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
            self.assertTrue(hydrated["units"][0]["verification_only"])
            self.assertEqual(hydrated["quality"]["source_payload_externalized"], 1)
            self.assertNotIn("workset_manifest_update", hydrated)

    def test_forged_evidence_id_cannot_hydrate_a_valid_source_payload(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            from context import hydrate  # type: ignore[import-not-found]
            from a_share_context.source_payload import FileSourcePayloadStore  # type: ignore[import-not-found]

            store = FileSourcePayloadStore(root)
            locator = store.put(
                "forged raw source\n",
                run_id="RUN-20260808-003",
                source_uri="https://example.invalid/forged",
                acquired_at="2026-08-08T09:00:00+08:00",
            )
            forged_reference = {
                "ref": "atom:EVI-20990101-999#1",
                "unit_id": "EVI-20990101-999#1",
                "unit_type": "evidence_item",
                "authority": "evidence_fact_source",
                "information_cutoff": "2026-08-08T09:00:00+08:00",
                "selection_cutoff": "2026-08-08T09:01:00+08:00",
                "status": "已确认",
                "source_locator": {
                    **locator,
                    "start_line": 1,
                    "end_line": 1,
                },
                "workspace_root": str(root),
            }

            hydrated = hydrate([forged_reference], source_payload_store=store)

            self.assertEqual(hydrated["units"], [])
            self.assertEqual(hydrated["quality"]["verification_failures"], 1)
            self.assertIn(
                "does not resolve to a canonical atomic unit",
                hydrated["missing_references"][0]["reason"],
            )

    def test_projection_rebuilds_after_fact_hash_changes_without_using_stale_text(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_fixture_workspace(root, market_evidence_role="confirmation")
            run_manifest = {
                "schema_version": "a-share-workspace-v3",
                "run_id": "RUN-20260808-004",
                "workspace_root": str(root),
                "workflow": "investigate",
                "stage": "research",
                "information_cutoff": "2026-08-08T09:00:00+08:00",
                "objects": ["个股:测试公司(600001)"],
                "task_contract": "investigate-stock-v1",
            }
            sys.path.insert(0, str(REPO_ROOT / ".agents/skills/a-share/shared"))
            from context import assemble, hydrate  # type: ignore[import-not-found]

            first = assemble(run_manifest)
            evidence = root / "证据包/2026-08/EVI-20260808-001.md"
            original = evidence.read_text(encoding="utf-8")
            evidence.write_text(original.replace("测试设备", "精密测试设备"), encoding="utf-8")
            second = assemble(run_manifest)
            self.assertFalse(second["projection"]["projection_degraded"])
            self.assertNotEqual(first["projection"].get("source_manifest_hash"), second["projection"].get("source_manifest_hash"))
            hydrated = __import__("context", fromlist=["hydrate"]).hydrate(second["stable_references"])
            self.assertIn("精密测试设备", hydrated["units"][0]["verification_text"])

    def test_noncovering_statuses_are_table_driven_and_conflicts_remain_vetoes(self) -> None:
        sys.path.insert(0, str(REPO_ROOT / ".agents/skills/a-share/shared"))
        from context import assemble, hydrate  # type: ignore[import-not-found]

        cases = (
            ("冲突", "conflict_or_denial", 1),
            ("已确认 / 冲突（解禁股数口径）", "conflict_or_denial", 1),
            ("未知", "unknown", 0),
        )
        for index, (status, reason, conflict_count) in enumerate(cases):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                write_fixture_workspace(root)
                source = root / "证据包/2026-08/EVI-20260808-001.md"
                source.write_text(
                    source.read_text(encoding="utf-8").replace("多源印证", status),
                    encoding="utf-8",
                )
                result = assemble(
                    run_manifest(
                        root,
                        run_id=f"RUN-20260808-STATUS-{index}",
                        information_cutoff=TEST_CUTOFF,
                    ),
                    market_task(),
                )
                self.assertEqual(result["coverage"]["required_covered"], 0)
                self.assertEqual(result["gaps"][0]["reason"], reason)
                self.assertFalse(result["gaps"][0]["blocking"])
                self.assertEqual(
                    result["coverage"]["requirements"][0]["conflict_count"],
                    conflict_count,
                )
                self.assertEqual(result["stable_references"], [])

                if conflict_count:
                    self.assertIn(
                        {
                            "unit_id": "EVI-20260808-001#002",
                            "requirement_id": "market",
                            "reason": reason,
                        },
                        result["selection_exclusions"],
                    )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_fixture_workspace(root)
            source = root / "证据包/2026-08/EVI-20260808-001.md"
            compound = "已确认 / 冲突（解禁股数口径）"
            source.write_text(
                source.read_text(encoding="utf-8").replace("多源印证", compound),
                encoding="utf-8",
            )
            hydrated = hydrate(
                {
                    "workspace_root": str(root),
                    "stable_references": [
                        {
                            "ref": "atom:EVI-20260808-001#002",
                            "unit_id": "EVI-20260808-001#002",
                            "unit_type": "evidence_item",
                            "information_cutoff": TEST_CUTOFF,
                            "status": compound,
                            "source_locator": {
                                "kind": "markdown",
                                "path": "证据包/2026-08/EVI-20260808-001.md",
                                "start_line": 1,
                                "end_line": len(source.read_text(encoding="utf-8").splitlines()),
                                "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                            },
                        }
                    ],
                }
            )
            self.assertEqual(hydrated["units"], [])
            self.assertIn("conflict_or_denial", hydrated["missing_references"][0]["reason"])

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

    def test_projection_path_cannot_escape_the_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "workspace"
            root.mkdir()
            outside = base / "outside.sqlite3"
            sys.path.insert(0, str(REPO_ROOT / ".agents/skills/a-share/shared"))
            from context import assemble  # type: ignore[import-not-found]

            with self.assertRaisesRegex(ValueError, "projection_path escapes workspace"):
                assemble(
                    {
                        "workspace_root": str(root),
                        "run_id": "RUN-20260808-PROJECTION-PATH",
                        "projection_path": str(outside),
                    },
                    {
                        "schema_version": "a-share-workspace-v3",
                        "contract_id": "test.projection-path",
                        "version": "1.0.0",
                        "required_evidence": [],
                    },
                )
            self.assertFalse(outside.exists())

    def test_persistent_run_writes_a_machine_readable_workset_manifest_without_original_text(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_fixture_workspace(root, market_evidence_role="confirmation")
            sys.path.insert(0, str(REPO_ROOT / ".agents/skills/a-share/shared"))
            from context import assemble  # type: ignore[import-not-found]

            result = assemble(
                {
                    "workspace_root": str(root),
                    "schema_version": "a-share-workspace-v3",
                    "run_id": "RUN-20260808-006",
                    "workflow": "investigate",
                    "stage": "research",
                    "information_cutoff": "2026-08-08T09:00:00+08:00",
                    "objects": ["个股:测试公司(600001)"],
                    "persist_workset_manifest": True,
                    "task_contract": "investigate-stock-v1",
                },
            )
            manifest_path = Path(result["workset_manifest_path"])
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["artifact_type"], "workset_manifest")
            self.assertEqual(manifest["status"], "completed")
            self.assertEqual(
                manifest["task_contract"]["registry_path"],
                ".agents/skills/a-share/shared/contracts/investigate-stock-v1.json",
            )
            self.assertEqual(
                manifest["task_contract"]["sha256"],
                hashlib.sha256(
                    (
                        REPO_ROOT
                        / ".agents/skills/a-share/shared/contracts/investigate-stock-v1.json"
                    ).read_bytes()
                ).hexdigest(),
            )
            self.assertEqual(
                manifest["contract_instantiation"]["objects"],
                ["个股:测试公司(600001)"],
            )
            instantiation_payload = {
                key: value
                for key, value in manifest["contract_instantiation"].items()
                if key != "sha256"
            }
            self.assertEqual(
                manifest["contract_instantiation"]["sha256"],
                hashlib.sha256(
                    json.dumps(
                        instantiation_payload,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                ).hexdigest(),
            )
            self.assertTrue(manifest["instantiated_requirements"])
            business_requirement = next(
                item
                for item in manifest["instantiated_requirements"]
                if item["base_requirement_id"] == "business-realization"
            )
            self.assertEqual(business_requirement["objects"], ["个股:测试公司(600001)"])
            self.assertEqual(business_requirement["unit_types"], ["evidence_item"])
            self.assertEqual(business_requirement["fields"], ["business"])
            self.assertEqual(business_requirement["roles"], ["primary"])
            canonical_requirements = json.dumps(
                manifest["instantiated_requirements"],
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            self.assertEqual(
                manifest["instantiated_requirements_sha256"],
                hashlib.sha256(canonical_requirements.encode("utf-8")).hexdigest(),
            )
            self.assertEqual(
                [
                    row["requirement"]
                    for row in manifest["coverage"]["requirements"]
                ],
                manifest["instantiated_requirements"],
            )
            self.assertNotIn("verification_text", manifest_path.read_text(encoding="utf-8"))
            self.assertNotIn("事实陈述", manifest_path.read_text(encoding="utf-8"))

if __name__ == "__main__":
    unittest.main()
