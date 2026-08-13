from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.support.workspace_builders import evidence_item, write_evidence as write_package


REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_ROOT = REPO_ROOT / ".agents/skills/a-share/shared"
if str(SHARED_ROOT) not in sys.path:
    sys.path.insert(0, str(SHARED_ROOT))

from context import assemble, hydrate  # type: ignore[import-not-found]  # noqa: E402


CONTEXT_CLI = REPO_ROOT / ".agents/skills/a-share/shared/scripts/context_workspace.py"
INVESTIGATE_MARKET_CONTRACT = (
    REPO_ROOT / ".agents/skills/a-share/shared/contracts/investigate-market-v1.json"
)


def write_evidence(root: Path) -> None:
    write_package(
        root,
        "EVI-20260809-AUDIT",
        [
            evidence_item(
                "EVI-20260809-AUDIT#001｜市场状态",
                fact="市场状态已经核验。",
                object_name="市场:A股",
                field="market_state",
                source_group="SRCGRP-MARKET-001",
                source_locator="source:market#snapshot",
                valid_until=None,
            )
        ],
        cutoff="2026-08-09T08:30:00+08:00",
    )


def audit_run(root: Path, run_id: str, **overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "workspace_root": str(root),
        "schema_version": "a-share-workspace-v3",
        "run_id": run_id,
        "workflow": "investigate",
        "stage": "research",
        "created_at": "2026-08-09T09:00:00+08:00",
        "information_cutoff": "2026-08-09T09:00:00+08:00",
        "objects": ["市场:A股"],
        "task_contract": str(INVESTIGATE_MARKET_CONTRACT),
        "persist_workset_manifest": True,
    }
    value.update(overrides)
    return value


class WorksetAuditTest(unittest.TestCase):
    def test_forged_markdown_locator_cannot_verify_or_complete_a_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_evidence(root)
            assembled = assemble(audit_run(root, "RUN-20260809-FORGED"))
            manifest_path = Path(assembled["workset_manifest_path"])
            manifest_before = json.loads(manifest_path.read_text(encoding="utf-8"))
            forged_document = root / "报告/伪造核验.md"
            forged_document.parent.mkdir(parents=True)
            forged_document.write_text("任意文本不属于该原子研究单元。\n", encoding="utf-8")
            forged = dict(manifest_before["stable_references"][0])
            forged["source_locator"] = {
                "kind": "markdown",
                "path": "报告/伪造核验.md",
                "start_line": 1,
                "end_line": 1,
                "anchor": "伪造核验",
                "sha256": hashlib.sha256(forged_document.read_bytes()).hexdigest(),
            }

            result = hydrate(
                {
                    "workspace_root": str(root),
                    "workset_manifest_path": str(manifest_path),
                    "stable_references": [forged],
                }
            )

            self.assertEqual(result["units"], [])
            self.assertIn("canonical", result["missing_references"][0]["reason"])
            manifest_after = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest_after["verification"]["status"], "not_run")
            self.assertEqual(manifest_after["verification"]["verified_unit_ids"], [])

    def test_markdown_reference_requires_canonical_type_status_and_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_evidence(root)
            assembled = assemble(
                {
                    "workspace_root": str(root),
                    "run_id": "RUN-20260809-CANONICAL",
                    "information_cutoff": "2026-08-09T09:00:00+08:00",
                },
                {
                    "contract_id": "test.canonical-reference",
                    "version": "1.0.0",
                    "required_evidence": [
                        {
                            "requirement_id": "market-state",
                            "unit_id": "EVI-20260809-AUDIT#001",
                        }
                    ],
                },
            )
            canonical = assembled["stable_references"][0]
            mutations = {
                "missing content hash": lambda item: item.pop("content_sha256"),
                "missing document hash": lambda item: item["source_locator"].pop("sha256"),
                "different unit type": lambda item: item.__setitem__(
                    "unit_type", "judgment_version"
                ),
                "different eligible status": lambda item: item.__setitem__(
                    "status", "多源印证"
                ),
            }

            for label, mutate in mutations.items():
                with self.subTest(label=label):
                    candidate = json.loads(json.dumps(canonical, ensure_ascii=False))
                    mutate(candidate)
                    hydrated = hydrate([candidate])
                    self.assertEqual(hydrated["units"], [])
                    self.assertIn(
                        "canonical", hydrated["missing_references"][0]["reason"]
                    )

    def test_failed_reverification_revokes_a_previously_verified_unit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_evidence(root)
            assembled = assemble(audit_run(root, "RUN-20260809-REVERIFY"))
            manifest_path = Path(assembled["workset_manifest_path"])
            reference = json.loads(manifest_path.read_text(encoding="utf-8"))[
                "stable_references"
            ][0]
            request = {
                "workspace_root": str(root),
                "workset_manifest_path": str(manifest_path),
                "stable_references": [reference],
            }
            hydrate(request)
            self.assertEqual(
                json.loads(manifest_path.read_text(encoding="utf-8"))["verification"][
                    "verified_unit_ids"
                ],
                [reference["unit_id"]],
            )
            evidence_path = root / reference["document_path"]
            evidence_path.write_text(
                evidence_path.read_text(encoding="utf-8").replace(
                    "市场状态已经核验", "市场状态正文发生变化"
                ),
                encoding="utf-8",
            )

            hydrate(request)

            verification = json.loads(
                manifest_path.read_text(encoding="utf-8")
            )["verification"]
            self.assertEqual(verification["status"], "failed")
            self.assertEqual(verification["verified_unit_ids"], [])
            self.assertEqual(
                verification["missing_references"][0]["unit_id"],
                reference["unit_id"],
            )

    def test_repeating_the_same_run_stage_creates_a_new_audit_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_evidence(root)
            run = audit_run(
                root,
                "RUN-20260809-REPEAT",
                strategy_version="STR-MARKET@1.2.0",
            )

            first = assemble(run)
            first_path = Path(first["workset_manifest_path"])
            first_text = first_path.read_text(encoding="utf-8")
            second = assemble(run)
            second_path = Path(second["workset_manifest_path"])

            self.assertNotEqual(first_path, second_path)
            self.assertEqual(first_path.name, "RUN-20260809-REPEAT-investigate-research-工作集清单.json")
            self.assertEqual(
                second_path.name,
                "RUN-20260809-REPEAT-investigate-research-a002-工作集清单.json",
            )
            self.assertEqual(first_path.read_text(encoding="utf-8"), first_text)
            first_manifest = json.loads(first_text)
            second_manifest = json.loads(second_path.read_text(encoding="utf-8"))
            self.assertEqual(first_manifest["attempt"], 1)
            self.assertEqual(second_manifest["attempt"], 2)
            self.assertEqual(
                second_manifest["previous_manifest_id"], first_manifest["id"]
            )
            self.assertEqual(
                second_manifest["id"],
                "RUN-20260809-REPEAT-WORKSET-INVESTIGATE-RESEARCH-A002",
            )

    def test_hydrate_completes_manifest_only_after_every_required_unit_is_verified(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_evidence(root)
            evidence_path = root / "证据包/2026-08/EVI-20260809-AUDIT.md"
            evidence_path.write_text(
                evidence_path.read_text(encoding="utf-8")
                + """

### EVI-20260809-AUDIT#002｜市场状态复核

- **事实陈述**：第二个市场状态单元已经核验。
- **状态**：已确认
- **证据角色**：primary
- **来源组 ID**：SRCGRP-MARKET-002
- **来源定位**：source:market#snapshot-2
- **关联对象 / 档案字段**：市场:A股 / market_state
""",
                encoding="utf-8",
            )
            assembled = assemble(audit_run(root, "RUN-20260809-VERIFY"))
            manifest_path = Path(assembled["workset_manifest_path"])
            stable_references = json.loads(
                manifest_path.read_text(encoding="utf-8")
            )["stable_references"]
            self.assertEqual(len(stable_references), 2)

            hydrate(
                {
                    "workspace_root": str(root),
                    "workset_manifest_path": str(manifest_path),
                    "stable_references": [stable_references[0]],
                }
            )

            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["verification"]["status"], "not_run")
            self.assertEqual(
                manifest["verification"]["verified_unit_ids"],
                [stable_references[0]["unit_id"]],
            )
            self.assertEqual(manifest["verification"]["missing_references"], [])

            hydrate(
                {
                    "workspace_root": str(root),
                    "workset_manifest_path": str(manifest_path),
                    "stable_references": [stable_references[1]],
                }
            )

            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["verification"]["status"], "completed")
            self.assertEqual(
                manifest["verification"]["verified_unit_ids"],
                [item["unit_id"] for item in stable_references],
            )
            self.assertEqual(manifest["verification"]["missing_references"], [])

    def test_same_run_keeps_stage_manifests_and_hydrate_updates_verification(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_evidence(root)
            common = {
                "workspace_root": str(root),
                "schema_version": "a-share-workspace-v3",
                "run_id": "RUN-20260809-001",
                "information_cutoff": "2026-08-09T09:00:00+08:00",
                "objects": ["市场:A股"],
                "strategy_version": "STR-MARKET@1.2.0",
                "persist_workset_manifest": True,
            }

            research = assemble(
                {
                    **common,
                    "workflow": "investigate",
                    "stage": "research",
                    "task_contract": "investigate-market-v1",
                }
            )
            analysis = assemble(
                {
                    **common,
                    "workflow": "analyze",
                    "stage": "analysis",
                    "task_contract": "analyze-v1",
                    "handoff": {"evidence_ids": ["EVI-20260809-AUDIT#001"]},
                }
            )

            research_path = Path(research["workset_manifest_path"])
            analysis_path = Path(analysis["workset_manifest_path"])
            self.assertNotEqual(research_path, analysis_path)
            self.assertTrue(research_path.is_file())
            self.assertTrue(analysis_path.is_file())

            before = json.loads(research_path.read_text(encoding="utf-8"))
            self.assertEqual(before["verification"]["status"], "not_run")
            self.assertEqual(before["strategy_version"], "STR-MARKET@1.2.0")

            assembled_path = root / "research-assembled.json"
            assembled_path.write_text(json.dumps(research, ensure_ascii=False), encoding="utf-8")
            process = subprocess.run(
                [
                    sys.executable,
                    str(CONTEXT_CLI),
                    "hydrate",
                    "--references",
                    str(assembled_path),
                    "--root",
                    str(root),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(process.returncode, 0, process.stderr)
            hydrated = json.loads(process.stdout)
            self.assertEqual(hydrated["quality"]["hydrate_units"], 1)

            after_text = research_path.read_text(encoding="utf-8")
            after = json.loads(after_text)
            self.assertEqual(after["verification"]["status"], "completed")
            self.assertEqual(after["verification"]["verified_unit_ids"], ["EVI-20260809-AUDIT#001"])
            self.assertEqual(after["quality"]["hydrate_units"], 1)
            self.assertNotIn("verification_text", after_text)
            self.assertNotIn("市场状态已经核验", after_text)


if __name__ == "__main__":
    unittest.main()
