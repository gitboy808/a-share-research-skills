from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_ROOT = REPO_ROOT / ".agents/skills/a-share/shared"
if str(SHARED_ROOT) not in sys.path:
    sys.path.insert(0, str(SHARED_ROOT))

from context import assemble  # type: ignore[import-not-found]  # noqa: E402
from tests.support.workspace_builders import (  # noqa: E402
    evidence_item,
    judgment_entry,
    write_evidence,
    write_judgments,
)


OBJECT = "个股:测试公司(600001)"
CUTOFF = "2026-08-09T09:00:00+08:00"


def research_run(root: Path | str, **overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "workspace_root": str(root),
        "schema_version": "a-share-workspace-v3",
        "run_id": "RUN-20260809-CONTRACT",
        "workflow": "investigate",
        "stage": "research",
        "objects": [OBJECT],
        "information_cutoff": CUTOFF,
        "task_contract": "investigate-stock-v1",
    }
    value.update(overrides)
    return value


def inline_contract(
    requirements: list[dict[str, object]], **overrides: object
) -> dict[str, object]:
    value: dict[str, object] = {
        "schema_version": "a-share-task-contract-v1",
        "contract_id": "test.inline",
        "version": "1.0.0",
        "workflow": "test",
        "stage": "test",
        "object_types": ["stock"],
        "required_evidence": requirements,
    }
    value.update(overrides)
    return value


def task_requirement(result: dict[str, object], requirement_id: str) -> dict[str, object]:
    return next(
        item
        for item in result["task_evidence_manifest"]["required_evidence"]
        if item.get("base_requirement_id", item["requirement_id"]) == requirement_id
    )


class ContractAccuracyTest(unittest.TestCase):
    def test_research_run_accepts_contracts_only_from_the_repository_registry(self) -> None:
        weak = inline_contract(
            [{"requirement_id": "weak-self-declared-floor", "unit_type": "evidence_item"}],
            contract_id="investigate.stock",
            version="999.0.0",
            workflow="investigate",
            stage="research",
        )
        cases = (
            ("run-inline", lambda root: (research_run(root, task_contract=weak), None), "repository shared/contracts"),
            ("task-inline", lambda root: ({key: value for key, value in research_run(root).items() if key != "task_contract"}, {"contract": weak}), "repository shared/contracts"),
            ("secondary-inline", lambda root: (research_run(root), {"contract": weak}), "repository shared/contracts"),
            ("task-reference", lambda root: ({key: value for key, value in research_run(root).items() if key != "task_contract"}, {"contract": "investigate-stock-v1"}), "task contract is required in the run manifest"),
        )
        for label, arguments, message in cases:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                run, task = arguments(Path(directory))
                with self.assertRaisesRegex(ValueError, message):
                    assemble(run, task)

    def test_research_run_rejects_a_workspace_contract_that_masquerades_as_registered(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            forged = root / "contracts/investigate-stock-v1.json"
            forged.parent.mkdir(parents=True)
            forged.write_text(
                json.dumps(
                    inline_contract(
                        [{"requirement_id": "weak-workspace-floor", "unit_type": "evidence_item"}],
                        contract_id="investigate.stock",
                        workflow="investigate",
                        stage="research",
                    )
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "repository shared/contracts"):
                assemble(research_run(root, task_contract=str(forged)))

    def test_research_contract_id_ignores_a_workspace_contract_with_the_same_id(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            forged = root / "contracts/forged.json"
            forged.parent.mkdir(parents=True)
            forged.write_text(
                json.dumps(
                    inline_contract(
                        [{"requirement_id": "weak-workspace-floor", "unit_type": "evidence_item"}],
                        contract_id="investigate.stock",
                        version="999.0.0",
                        workflow="investigate",
                        stage="research",
                    )
                ),
                encoding="utf-8",
            )
            result = assemble(research_run(root, task_contract="investigate.stock"))
            task = result["task_evidence_manifest"]
            ids = {item.get("base_requirement_id", item["requirement_id"]) for item in task["required_evidence"]}
            self.assertEqual(task["version"], "1.0.0")
            self.assertIn("business-realization", ids)
            self.assertNotIn("weak-workspace-floor", ids)

    def test_inline_contract_shape_and_identity_fail_closed(self) -> None:
        cases = (
            ("duplicate", inline_contract([{"requirement_id": "fact", "unit_type": "evidence_item"}, {"requirement_id": "fact", "unit_type": "evidence_item"}]), "duplicate requirement_id"),
            ("all-optional", inline_contract([{"requirement_id": "fact", "unit_type": "evidence_item", "required": False}]), "required floor"),
            ("empty", inline_contract([]), "at least one"),
            ("missing-routing", {key: value for key, value in inline_contract([{"requirement_id": "fact", "unit_type": "evidence_item"}]).items() if key not in {"workflow", "stage"}}, "workflow"),
            ("wrong-schema", inline_contract([{"requirement_id": "fact", "unit_type": "evidence_item"}], schema_version="a-share-workspace-v3"), "unsupported task contract schema_version"),
        )
        for label, task_contract, message in cases:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                run = research_run(
                    directory,
                    workflow="test",
                    stage="test",
                    task_contract=task_contract,
                )
                with self.assertRaisesRegex(ValueError, message):
                    assemble(run)

    def test_contract_path_cannot_escape_approved_versioned_contract_roots(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            workspace = base / "workspace"
            workspace.mkdir()
            outside = base / "outside-contract.json"
            outside.write_text(
                json.dumps(inline_contract([{"requirement_id": "fact", "unit_type": "evidence_item"}])),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "approved contract roots"):
                assemble(research_run(workspace, workflow="test", stage="test", task_contract=str(outside)))

    def test_non_research_non_persistent_run_can_use_an_inline_test_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = assemble(
                {
                    "workspace_root": directory,
                    "run_id": "RUN-20260809-INLINE-TEST",
                    "workflow": "test",
                    "stage": "test",
                    "objects": [OBJECT],
                },
                {"contract": inline_contract([{"requirement_id": "fixture", "unit_type": "evidence_item"}])},
            )
            task = result["task_evidence_manifest"]
            self.assertEqual(task["contract_id"], "test.inline")
            self.assertEqual(task["contract_status"], "instantiated")

    def test_research_run_envelope_and_contract_compatibility_fail_closed(self) -> None:
        def missing(run: dict[str, object], field: str) -> dict[str, object]:
            run.pop(field)
            return run

        cases = (
            ("schema", missing(research_run("."), "schema_version"), "run manifest schema_version"),
            ("contract", missing(research_run("."), "task_contract"), "task contract is required"),
            ("workflow", research_run(".", workflow="analyze"), "workflow"),
            ("stage", research_run(".", stage="analysis"), "stage"),
            ("object", research_run(".", objects=["交易主题:错误对象"]), "object_types"),
            ("snapshot", missing(research_run("."), "information_cutoff"), "information snapshot"),
            ("invalid-snapshot", research_run(".", information_cutoff="当时未记录"), "valid information snapshot"),
        )
        for label, partial, message in cases:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                partial["workspace_root"] = directory
                with self.assertRaisesRegex(ValueError, message):
                    assemble(partial)

    def test_direct_task_evidence_cannot_replace_research_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = research_run(directory)
            run.pop("task_contract")
            direct = {
                "schema_version": "a-share-workspace-v3",
                "contract_id": "investigate.synthetic",
                "version": "1.0.0",
                "required_evidence": [{"requirement_id": "self-declared-floor", "unit_type": "evidence_item", "object": OBJECT}],
            }
            with self.assertRaisesRegex(ValueError, "task contract is required"):
                assemble(run, direct)

    def test_persistent_run_without_contract_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "task contract is required"):
                assemble(
                    {
                        "workspace_root": directory,
                        "schema_version": "a-share-workspace-v3",
                        "run_id": "RUN-20260809-PERSIST",
                        "persist_workset_manifest": True,
                        "information_cutoff": CUTOFF,
                    }
                )

    def test_zero_requirements_never_report_full_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = assemble(
                {"workspace_root": directory, "run_id": "RUN-20260809-EMPTY"},
                {"contract_id": "test.empty", "version": "1.0.0", "required_evidence": []},
            )
            self.assertEqual(result["coverage"]["coverage_ratio"], 0.0)

    def test_task_conditions_tighten_before_object_binding(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = assemble(
                research_run(directory),
                {"required_evidence": [{"requirement_id": "business-realization", "min_source_groups": 2}]},
            )
            item = task_requirement(result, "business-realization")
            self.assertEqual(item["requirement_id"], f"business-realization@{OBJECT}")
            self.assertEqual(item["field"], "business")
            self.assertEqual(item["min_source_groups"], 2)

    def test_task_conditions_merge_only_toward_stricter_bounds(self) -> None:
        cases = (
            ("allow-unknown", "market-structure", [{"requirement_id": "market-structure", "allow_unknown": False}], {"allow_unknown": False, "field": "market_state"}),
            ("source-groups", "business-realization", [{"requirement_id": "business-realization", "min_source_groups": 1}, {"requirement_id": "business-realization", "min_source_groups": 2}], {"min_source_groups": 2}),
            ("max-age", "market-structure", [{"requirement_id": "market-structure", "max_age_days": 30}, {"requirement_id": "market-structure", "max_age_days": 7}], {"max_age_days": 7}),
            ("freshness-alias", "market-structure", [{"requirement_id": "market-structure", "freshness": {"max_age_days": 30}}, {"requirement_id": "market-structure", "max_age_days": 7}], {"max_age_days": 7}),
        )
        for label, requirement_id, conditions, expected in cases:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                item = task_requirement(assemble(research_run(directory), {"required_evidence": conditions}), requirement_id)
                for key, value in expected.items():
                    self.assertEqual(item[key], value)
                if label == "freshness-alias":
                    self.assertNotIn("freshness", item)

    def test_task_conditions_reject_weakening_and_conflicting_selectors(self) -> None:
        cases = (
            ({"requirement_id": "business-realization", "required": False}, "cannot weaken field 'required'"),
            ({"requirement_id": "business-realization", "allow_unknown": True}, "cannot weaken field 'allow_unknown'"),
            ({"requirement_id": "business-realization", "field": "revenue"}, "cannot safely merge field 'field'"),
        )
        for condition, message in cases:
            with self.subTest(message=message), tempfile.TemporaryDirectory() as directory:
                with self.assertRaisesRegex(ValueError, message):
                    assemble(research_run(directory), {"required_evidence": [condition]})

    def test_contract_instantiates_object_bound_requirement_for_each_run_object(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_evidence(
                root,
                "EVI-20260809-001",
                [evidence_item("EVI-20260809-001#001", object_name="个股:甲公司(600001)")],
                objects="个股:甲公司(600001)",
            )
            result = assemble(research_run(root, objects=["个股:甲公司(600001)", "个股:乙公司(600002)"]))
            rows = [row for row in result["coverage"]["requirements"] if row.get("base_requirement_id") == "business-realization"]
            self.assertEqual([(row["object"], row["covered"]) for row in rows], [("个股:甲公司(600001)", True), ("个股:乙公司(600002)", False)])

    def test_resolver_selects_each_object_specific_investigation_contract_by_id(self) -> None:
        cases = (("investigate.industry", "产业链:先进封装"), ("investigate.theme", "交易主题:并购重组"), ("investigate.event", "事件:政策发布"))
        with tempfile.TemporaryDirectory() as directory:
            resolved = [
                assemble(research_run(directory, run_id=f"RUN-20260809-{index}", objects=[obj], task_contract=contract_id))["task_evidence_manifest"]["contract_id"]
                for index, (contract_id, obj) in enumerate(cases, start=1)
            ]
            self.assertEqual(resolved, [contract_id for contract_id, _ in cases])

    def test_market_contract_binds_its_floor_to_each_market_object(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = assemble(research_run(directory, objects=["市场:A股", "市场:港股"], task_contract="investigate.market"))
            objects = [row["object"] for row in result["coverage"]["requirements"] if row["base_requirement_id"] == "market-state"]
            self.assertEqual(objects, ["市场:A股", "市场:港股"])

    def test_analysis_coverage_uses_only_formally_handed_off_evidence_ids(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_evidence(
                root,
                "EVI-20260809-002",
                [
                    evidence_item("EVI-20260809-002#001", object_name="个股:甲公司(600001)"),
                    evidence_item("EVI-20260809-002#002", object_name="个股:甲公司(600001)", field="background", role="confirmation"),
                ],
            )
            result = assemble(
                research_run(
                    root,
                    workflow="analyze",
                    stage="analysis",
                    objects=["个股:甲公司(600001)"],
                    task_contract="analyze-v1",
                    handoff={"evidence_ids": ["EVI-20260809-002#002"]},
                )
            )
            row = next(item for item in result["coverage"]["requirements"] if item["base_requirement_id"] == "verified-object-facts")
            self.assertFalse(row["covered"])

    def test_review_coverage_uses_only_formally_handed_off_judgment_ids(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_evidence(root, "EVI-20260809-002", [evidence_item("EVI-20260809-002#002", field="background", role="confirmation")])
            write_judgments(root, [judgment_entry("J20260809-001 v1", object_name="个股:甲公司(600001)")])
            result = assemble(
                research_run(
                    root,
                    workflow="review",
                    stage="review",
                    objects=["个股:甲公司(600001)"],
                    information_cutoff="2026-08-09T15:30:00+08:00",
                    task_contract="review-v1",
                    handoff={"judgment_ids": ["J20260809-999 v1"], "evidence_ids": ["EVI-20260809-002#002"]},
                )
            )
            row = next(item for item in result["coverage"]["requirements"] if item["base_requirement_id"] == "original-judgment")
            self.assertFalse(row["covered"])


if __name__ == "__main__":
    unittest.main()
