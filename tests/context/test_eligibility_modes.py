from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_ROOT = REPO_ROOT / ".agents/skills/a-share/shared"
VALIDATOR = SHARED_ROOT / "scripts/validate_workspace.py"
if str(SHARED_ROOT) not in sys.path:
    sys.path.insert(0, str(SHARED_ROOT))

from context import assemble, hydrate  # type: ignore[import-not-found]  # noqa: E402
from tests.support.workspace_builders import (  # noqa: E402
    contract,
    dossier_field,
    evidence_item,
    judgment_entry,
    run_manifest,
    write_dossier,
    write_evidence,
    write_judgments,
    write_strategy,
    write_text,
)


def requirement(
    requirement_id: str,
    unit_type: str,
    *,
    unit_id: str | None = None,
    mode: str = "prospective_current",
    **selectors: object,
) -> dict[str, object]:
    value: dict[str, object] = {
        "requirement_id": requirement_id,
        "unit_type": unit_type,
        "eligibility_mode": mode,
        **selectors,
    }
    if unit_id:
        value["unit_id"] = unit_id
    return value


class EligibilityModesTest(unittest.TestCase):
    def validate(self, workspace: Path) -> tuple[int, dict[str, object]]:
        result = subprocess.run(
            [sys.executable, str(VALIDATOR), "--root", str(workspace), "--json"],
            check=False,
            capture_output=True,
            text=True,
        )
        return result.returncode, json.loads(result.stdout)

    def render_judgment_template(
        self, workspace: Path, *, sequence: str, snapshot: str
    ) -> Path:
        text = (workspace / "模板/判断条目模板.md").read_text(encoding="utf-8")
        replacements = {
            "YYYY-MM-DDTHH:mm:ss+08:00": "2026-08-09T10:30:00+08:00",
            "YYYY-MM": "2026-08",
            "YYYYMMDD": "20260809",
            "YYYYMM": "202608",
            "NNN": sequence,
            "- **信息快照**：2026-08-09T10:30:00+08:00；数据交易日": f"- **信息快照**：{snapshot}；数据交易日",
            "- **研究状态**：弃权 / 规避 / 等待确认 / 研究条件成立 / 持仓逻辑失效": "- **研究状态**：弃权",
            "- **研究对象**：市场 / 产业链 / 交易主题 / 个股": "- **研究对象**：个股:测试公司(600001)",
            "- **原子命题**：": "- **原子命题**：当前证据不足，维持弃权。",
            "- **证据包 / 原子证据项**：": "- **证据包 / 原子证据项**：unknown—正式弃权；证据缺口见下",
            "- **证伪条件**：": "- **证伪条件**：任务证据底线完整满足。",
            "- **时限**：2026-08-09T10:30:00+08:00": "- **时限**：2026-08-10T15:00:00+08:00",
        }
        for old, new in replacements.items():
            text = text.replace(old, new)
        return write_text(workspace, "判断日志/2026-08.md", text)

    def test_judgment_template_validates_assembles_and_hydrates_with_strict_snapshot_time(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspace"
            shutil.copytree(
                REPO_ROOT,
                workspace,
                ignore=shutil.ignore_patterns(".git", ".context", ".source-payloads", "__pycache__"),
            )
            self.render_judgment_template(
                workspace, sequence="001", snapshot="2026-08-09T10:30:00+08:00"
            )
            code, payload = self.validate(workspace)
            self.assertEqual(code, 0, payload)
            assembled = assemble(
                run_manifest(
                    workspace,
                    run_id="RUN-20260809-TEMPLATE",
                    information_cutoff="2026-08-09T11:00:00+08:00",
                ),
                contract(
                    [requirement("judgment", "judgment_version", unit_id="J20260809-001 v1")]
                ),
            )
            self.assertEqual(assembled["coverage"]["required_covered"], 1)
            self.assertEqual(
                [unit["unit_id"] for unit in hydrate(assembled)["units"]],
                ["J20260809-001 v1"],
            )

    def test_workspace_validation_rejects_a_naive_atomic_judgment_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspace"
            shutil.copytree(
                REPO_ROOT,
                workspace,
                ignore=shutil.ignore_patterns(".git", ".context", ".source-payloads", "__pycache__"),
            )
            self.render_judgment_template(
                workspace, sequence="002", snapshot="2026-08-09T10:30:00"
            )
            code, payload = self.validate(workspace)
            self.assertEqual(code, 1)
            self.assertIn(
                "判断日志/2026-08.md: J20260809-002 v1 信息快照 must be ISO-8601 with timezone",
                payload["errors"],
            )

    def test_falsified_judgment_is_currently_excluded_but_historically_auditable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            write_judgments(
                workspace,
                [
                    judgment_entry(
                        "J20260809-003 v1",
                        cutoff="2026-08-09T10:30:00+08:00",
                        outcome="证伪",
                        outcome_at="2026-08-10T15:30:00+08:00",
                    )
                ],
            )
            run = run_manifest(workspace, run_id="RUN-20260811-LIFECYCLE")
            base = {"requirement_id": "judgment", "unit_type": "judgment_version", "unit_id": "J20260809-003 v1"}
            current = assemble(run, contract([{**base, "eligibility_mode": "prospective_current"}]))
            historical = assemble(
                run,
                contract(
                    [{**base, "eligibility_mode": "historical_as_of", "cutoff_basis": "unit_snapshot"}]
                ),
            )
            self.assertEqual(current["stable_references"], [])
            self.assertEqual(current["gaps"][0]["reason"], "terminal_judgment")
            self.assertEqual(
                [item["unit_id"] for item in historical["stable_references"]],
                ["J20260809-003 v1"],
            )
            self.assertEqual(
                [unit["unit_id"] for unit in hydrate(historical)["units"]],
                ["J20260809-003 v1"],
            )

    def test_review_uses_judgment_cutoff_for_original_evidence_and_review_cutoff_for_outcome(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            write_evidence(
                workspace,
                "EVI-20260809-REVIEW",
                [
                    evidence_item(
                        "EVI-20260809-REVIEW#001",
                        fact="原判断形成时可用的业务事实。",
                        cutoff="2026-08-09T09:00:00+08:00",
                    ),
                    evidence_item(
                        "EVI-20260809-REVIEW#002",
                        fact="复盘截止时的结果数据。",
                        cutoff="2026-08-12T15:30:00+08:00",
                        field="outcome",
                        role="confirmation",
                    ),
                ],
                cutoff="2026-08-09T09:00:00+08:00",
            )
            write_judgments(
                workspace,
                [
                    judgment_entry(
                        "J20260809-004 v1",
                        cutoff="2026-08-09T09:30:00+08:00",
                        evidence_ids=["EVI-20260809-REVIEW#001"],
                        outcome="证伪",
                        outcome_at="2026-08-10T15:30:00+08:00",
                    )
                ],
            )
            result = assemble(
                run_manifest(
                    workspace,
                    schema_version="a-share-workspace-v3",
                    run_id="RUN-20260812-REVIEW",
                    workflow="review",
                    stage="review",
                    objects=["个股:测试公司(600001)"],
                    information_cutoff="2026-08-12T15:30:00+08:00",
                    task_contract="review-v1",
                    handoff={
                        "judgment_ids": ["J20260809-004 v1"],
                        "evidence_ids": ["EVI-20260809-REVIEW#001", "EVI-20260809-REVIEW#002"],
                    },
                )
            )
            coverage = {row["base_requirement_id"]: row for row in result["coverage"]["requirements"]}
            self.assertTrue(all(coverage[key]["covered"] for key in ("original-judgment", "original-snapshot", "outcome-data")))
            self.assertEqual(hydrate(result)["missing_references"], [])

    def test_current_mode_selects_only_the_latest_logical_field_version(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            logical = "stock:600001:business-realization"
            write_evidence(
                workspace,
                "EVI-20260811-VERSIONS",
                [
                    evidence_item(
                        "EVI-20260811-VERSIONS#001",
                        fact="旧业务兑现等级为 E2。",
                        cutoff="2026-08-09T09:00:00+08:00",
                        logical_field_id=logical,
                        version=1,
                    ),
                    evidence_item(
                        "EVI-20260811-VERSIONS#002",
                        fact="新业务兑现等级为 E3。",
                        cutoff="2026-08-11T09:30:00+08:00",
                        logical_field_id=logical,
                        version=2,
                        supersedes="EVI-20260811-VERSIONS#001",
                    ),
                ],
                cutoff="2026-08-11T09:30:00+08:00",
            )
            run = run_manifest(workspace, run_id="RUN-20260811-VERSIONS")
            current = assemble(
                run,
                contract([requirement("business", "evidence_item", object="个股:测试公司(600001)", field="business")]),
            )
            historical = assemble(
                run,
                contract(
                    [
                        requirement(
                            "old-business",
                            "evidence_item",
                            unit_id="EVI-20260811-VERSIONS#001",
                            mode="historical_as_of",
                            cutoff_basis="unit_snapshot",
                        )
                    ]
                ),
            )
            self.assertEqual([item["unit_id"] for item in current["stable_references"]], ["EVI-20260811-VERSIONS#002"])
            self.assertIn(
                {"unit_id": "EVI-20260811-VERSIONS#001", "reason": "superseded", "source": "requirement", "requirement_id": "business"},
                current["audited_exclusions"],
            )
            self.assertEqual([item["unit_id"] for item in historical["stable_references"]], ["EVI-20260811-VERSIONS#001"])

    def test_invalidated_successor_does_not_resurrect_superseded_history(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            logical = "stock:600001:claim"
            write_evidence(
                workspace,
                "EVI-20260811-NO-REVIVAL",
                [
                    evidence_item("EVI-20260811-NO-REVIVAL#001", fact="旧版本。", logical_field_id=logical, version=1),
                    evidence_item(
                        "EVI-20260811-NO-REVIVAL#002",
                        fact="已经替代旧版本但随后事件失效。",
                        logical_field_id=logical,
                        version=2,
                        supersedes="EVI-20260811-NO-REVIVAL#001",
                        invalidated_at="2026-08-11T09:30:00+08:00",
                    ),
                ],
            )
            result = assemble(
                run_manifest(workspace),
                contract([requirement("claim", "evidence_item", object="个股:测试公司(600001)", field="business")]),
            )
            self.assertEqual(result["stable_references"], [])
            self.assertEqual(
                {item["unit_id"]: item["reason"] for item in result["audited_exclusions"]},
                {"EVI-20260811-NO-REVIVAL#001": "superseded", "EVI-20260811-NO-REVIVAL#002": "event_invalidated"},
            )

    def test_retired_lesson_and_strategy_never_become_current_analysis_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            write_strategy(workspace, "STR-ACTIVE")
            write_strategy(workspace, "STR-OLD", status="retired")
            write_text(
                workspace,
                "经验库.md",
                '''---
information_cutoff: "2026-08-01T09:00:00+08:00"
---
# 经验库
### L001 活跃教训
- **状态**：已验证
### L002 退役教训
- **状态**：已退役''',
            )
            result = assemble(
                run_manifest(workspace, run_id="RUN-20260811-RETIRED"),
                contract(
                    [
                        requirement("strategies", "strategy_version"),
                        requirement("lessons", "lesson"),
                    ]
                ),
            )
            selected = {item["unit_id"] for item in result["stable_references"]}
            self.assertIn("STR-ACTIVE@1.0.0", selected)
            self.assertNotIn("STR-OLD@1.0.0", selected)
            self.assertEqual({item["reason"] for item in result["audited_exclusions"]}, {"retired"})

    def test_partially_expired_dossier_excludes_only_the_expired_atomic_field(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            write_dossier(
                workspace,
                [
                    dossier_field("business", valid_until="2026-08-05T23:59:59+08:00"),
                    dossier_field("valuation-anchor"),
                ],
                cutoff="2026-08-11T09:00:00+08:00",
            )
            result = assemble(
                run_manifest(workspace, run_id="RUN-20260811-DOSSIER"),
                contract([requirement("dossier", "object_field", object="个股:测试公司(600001)")]),
            )
            self.assertEqual([item["unit_id"] for item in result["stable_references"]], ["DOS-STOCK-600001#valuation-anchor@1"])
            self.assertIn(
                {"unit_id": "DOS-STOCK-600001#business@1", "reason": "expired", "source": "requirement", "requirement_id": "dossier"},
                result["audited_exclusions"],
            )

    def test_semantic_hit_on_terminated_history_is_audited_not_selected(self) -> None:
        class SemanticHit:
            def search(self, query: str, limit: int = 10) -> list[dict[str, str]]:
                return [{"unit_id": "J20260809-005 v1"}]

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            write_judgments(
                workspace,
                [judgment_entry("J20260809-005 v1", outcome="未触发", outcome_at="2026-08-10T15:30:00+08:00")],
            )
            result = assemble(
                run_manifest(workspace, semantic_query="旧判断", semantic_adapter=SemanticHit()),
                contract([]),
            )
            self.assertEqual(result["stable_references"], [])
            self.assertIn(
                {"unit_id": "J20260809-005 v1", "reason": "terminal_judgment", "source": "semantic", "requirement_id": None},
                result["audited_exclusions"],
            )

    def test_analysis_contract_compiles_current_dossier_chain_exact_strategy_and_handoff(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = assemble(
                run_manifest(
                    Path(directory),
                    schema_version="a-share-workspace-v3",
                    run_id="RUN-20260811-ANALYZE-CONTRACT",
                    workflow="analyze",
                    stage="analysis",
                    objects=["个股:测试公司(600001)"],
                    task_contract="analyze-v1",
                    strategy_version="STR-STOCK-GROWTH@1.2.0",
                    handoff={"evidence_ids": ["EVI-20260811-001#001"]},
                )
            )
            requirements = {row["base_requirement_id"]: row["requirement"] for row in result["coverage"]["requirements"]}
            self.assertEqual(set(requirements), {"verified-object-facts", "counterevidence", "current-object-dossier", "active-judgment-chain", "exact-strategy-version"})
            self.assertEqual(requirements["exact-strategy-version"]["unit_ids"], ["STR-STOCK-GROWTH@1.2.0"])
            self.assertEqual(requirements["exact-strategy-version"]["allowed_lifecycle_statuses"], ["trial", "official", "limited"])
            self.assertTrue(all(item["eligibility_mode"] == "prospective_current" for item in requirements.values()))

    def test_review_process_snapshot_excludes_evidence_created_after_the_judgment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            write_evidence(
                workspace,
                "EVI-20260812-PROCESS",
                [
                    evidence_item("EVI-20260812-PROCESS#001", fact="原判断前证据。", cutoff="2026-08-09T09:00:00+08:00"),
                    evidence_item("EVI-20260812-PROCESS#002", fact="原判断后才出现的证据。", cutoff="2026-08-10T09:00:00+08:00"),
                ],
            )
            write_judgments(
                workspace,
                [judgment_entry("J20260809-006 v1", cutoff="2026-08-09T09:30:00+08:00", deadline=None)],
            )
            result = assemble(
                run_manifest(
                    workspace,
                    information_cutoff="2026-08-12T15:30:00+08:00",
                    handoff={"judgment_ids": ["J20260809-006 v1"]},
                ),
                contract(
                    [
                        requirement(
                            "original-evidence",
                            "evidence_item",
                            mode="historical_as_of",
                            object="个股:测试公司(600001)",
                            field="business",
                            cutoff_basis="judgment_snapshot",
                        )
                    ]
                ),
            )
            self.assertEqual([item["unit_id"] for item in result["stable_references"]], ["EVI-20260812-PROCESS#001"])
            self.assertIn("EVI-20260812-PROCESS#002", {item["unit_id"] for item in result["audited_exclusions"]})

    def test_event_invalidation_exits_current_before_scheduled_review(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            write_evidence(
                workspace,
                "EVI-20260811-INVALIDATED",
                [
                    evidence_item(
                        "EVI-20260811-INVALIDATED#001",
                        fact="该事实原计划月底复核。",
                        object_name="交易主题:测试主题",
                        field="catalyst_lifecycle",
                        invalidated_at="2026-08-11T09:30:00+08:00",
                    )
                ],
            )
            result = assemble(
                run_manifest(workspace),
                contract([requirement("catalyst", "evidence_item", unit_id="EVI-20260811-INVALIDATED#001")]),
            )
            self.assertEqual(result["stable_references"], [])
            self.assertEqual(result["gaps"][0]["reason"], "event_invalidated")

    def test_calibration_window_includes_terminated_samples_only_inside_the_window(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            write_judgments(
                workspace,
                [
                    judgment_entry("J20260801-001 v1", object_name="市场:A股", cutoff="2026-08-01T09:00:00+08:00", outcome="兑现", deadline=None),
                    judgment_entry("J20260808-001 v1", object_name="市场:A股", cutoff="2026-08-08T09:00:00+08:00", outcome="证伪", deadline=None),
                ],
                status="closed",
            )
            result = assemble(
                run_manifest(
                    workspace,
                    information_cutoff="2026-08-11T18:00:00+08:00",
                    calibration_window_start="2026-08-04T00:00:00+08:00",
                ),
                contract([requirement("samples", "judgment_version", mode="calibration_window")]),
            )
            self.assertEqual([item["unit_id"] for item in result["stable_references"]], ["J20260808-001 v1"])
            self.assertIn("outside_calibration_window", {item["reason"] for item in result["audited_exclusions"]})

    def test_stock_and_industry_replays_exclude_unrelated_closed_judgments(self) -> None:
        class ClosedHistoryAdapter:
            def search(self, query: str, limit: int = 10) -> list[dict[str, str]]:
                return [{"unit_id": "J20260801-101 v1"}, {"unit_id": "J20260801-102 v1"}]

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            write_evidence(
                workspace,
                "EVI-20260812-REPLAY",
                [
                    evidence_item("EVI-20260812-REPLAY#001", object_name="个股:合成股份(600123)", field="business"),
                    evidence_item("EVI-20260812-REPLAY#002", object_name="市场:A股", field="market_state", role="confirmation"),
                    evidence_item("EVI-20260812-REPLAY#003", object_name="产业链:合成链", field="industry_membership"),
                    evidence_item("EVI-20260812-REPLAY#004", object_name="产业链:合成链", field="industry_state", role="confirmation"),
                ],
            )
            write_judgments(
                workspace,
                [
                    judgment_entry("J20260801-101 v1", object_name="个股:无关旧股(600999)", outcome="证伪", deadline=None),
                    judgment_entry("J20260801-102 v1", object_name="产业链:无关旧链", outcome="未触发", deadline=None),
                ],
                status="closed",
            )
            cases = (
                ("stock", "个股:合成股份(600123)", "investigate-stock-v1", {"EVI-20260812-REPLAY#001", "EVI-20260812-REPLAY#002"}),
                ("industry", "产业链:合成链", "investigate-industry-v1", {"EVI-20260812-REPLAY#003", "EVI-20260812-REPLAY#004"}),
            )
            for suffix, target, task_contract, expected in cases:
                with self.subTest(suffix=suffix):
                    assembled = assemble(
                        run_manifest(
                            workspace,
                            schema_version="a-share-workspace-v3",
                            run_id=f"RUN-20260812-REPLAY-{suffix.upper()}",
                            workflow="investigate",
                            stage="research",
                            objects=[target],
                            task_contract=task_contract,
                            semantic_adapter=ClosedHistoryAdapter(),
                        )
                    )
                    self.assertEqual({item["unit_id"] for item in assembled["stable_references"]}, expected)
                    self.assertTrue({"J20260801-101 v1", "J20260801-102 v1"} <= {item["unit_id"] for item in assembled["audited_exclusions"]})
                    hydrated = hydrate(assembled)
                    self.assertEqual({item["unit_id"] for item in hydrated["units"]}, expected)
                    self.assertEqual(hydrated["missing_references"], [])


if __name__ == "__main__":
    unittest.main()
