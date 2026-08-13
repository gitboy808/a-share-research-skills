from __future__ import annotations

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_ROOT = REPO_ROOT / ".agents/skills/a-share/shared"
if str(SHARED_ROOT) not in sys.path:
    sys.path.insert(0, str(SHARED_ROOT))

from context import assemble, hydrate  # type: ignore[import-not-found]  # noqa: E402
from a_share_context.source_payload import FileSourcePayloadStore  # type: ignore[import-not-found]  # noqa: E402
from tests.support.workspace_builders import (  # noqa: E402
    contract,
    evidence_item,
    judgment_entry,
    run_manifest as _run_manifest,
    write_evidence,
    write_judgments,
    write_strategy,
    write_text,
)


def required(unit_id: str, **selectors: object) -> dict[str, object]:
    return {"requirement_id": "required", "unit_id": unit_id, **selectors}


def run_manifest(root: Path, **overrides: object) -> dict[str, object]:
    overrides.setdefault("information_cutoff", "2026-08-09T09:00:00+08:00")
    return _run_manifest(root, **overrides)


class ProjectionAccuracyTest(unittest.TestCase):
    def test_canonical_judgment_research_status_overrides_container_status(self) -> None:
        for index, research_status in enumerate(("规避", "弃权", "等待确认"), start=1):
            with self.subTest(research_status=research_status), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                unit_id = f"J20260809-{index:03d} v1"
                write_judgments(root, [judgment_entry(unit_id, research_status=research_status)])
                result = assemble(
                    run_manifest(root, run_id=f"RUN-20260809-STATUS-{index}"),
                    contract([required(unit_id)]),
                )
                self.assertEqual(result["stable_references"][0]["status"], research_status)

    def test_unreadable_authoritative_markdown_degrades_projection_and_blocks_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_evidence(
                root,
                "EVI-20260809-READABLE",
                [evidence_item("EVI-20260809-READABLE#001", object_name="市场:A股", field="market_state")],
            )
            (root / "证据包/2026-08/EVI-20260809-BROKEN.md").symlink_to("missing-authoritative-target.md")
            result = assemble(run_manifest(root), contract([required("EVI-20260809-READABLE#001")]))
            expected = {
                "requirement_id": "projection:证据包/2026-08/EVI-20260809-BROKEN.md",
                "reason": "authoritative_document_unparseable",
                "required": True,
                "allow_unknown": False,
                "blocking": True,
                "document_path": "证据包/2026-08/EVI-20260809-BROKEN.md",
            }
            self.assertTrue(result["projection"]["projection_degraded"])
            self.assertIn(expected, result["gaps"])
            self.assertTrue(result["coverage"]["blocking"])

    def test_review_contract_matches_the_research_object_from_the_canonical_judgment_template(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            text = (REPO_ROOT / "模板/判断条目模板.md").read_text(encoding="utf-8")
            for old, new in {
                "JLOG-YYYYMM": "JLOG-202608",
                "YYYY-MM-DDTHH:mm:ss+08:00": "2026-08-09T09:00:00+08:00",
                "# 判断日志 · YYYY-MM": "# 判断日志 · 2026-08",
                "JYYYYMMDD-NNN": "J20260809-001",
                "- **研究对象**：市场 / 产业链 / 交易主题 / 个股": "- **研究对象**：stock:测试股份",
                "- **信息快照**：YYYY-MM-DD HH:mm Asia/Shanghai；数据交易日": "- **信息快照**：2026-08-09T09:00:00+08:00",
                "- **原子命题**：": "- **原子命题**：测试股份在冻结窗口内保持相对强势。",
            }.items():
                text = text.replace(old, new)
            write_text(root, "判断日志/2026-08.md", text)
            result = assemble(
                run_manifest(
                    root,
                    schema_version="a-share-workspace-v3",
                    run_id="RUN-20260809-001",
                    workflow="review",
                    stage="review",
                    task_contract="review-v1.json",
                    objects=["stock:测试股份"],
                    information_cutoff="2026-08-09T16:00:00+08:00",
                    handoff={"judgment_ids": ["J20260809-001 v1"], "evidence_ids": ["EVI-20260809-001#001"]},
                )
            )
            original = next(row for row in result["coverage"]["requirements"] if row["base_requirement_id"] == "original-judgment")
            self.assertEqual(original["candidate_count"], 1)
            self.assertTrue(original["covered"])

    def test_naive_evidence_snapshot_cannot_satisfy_a_requirement(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_evidence(
                root,
                "EVI-20260809-NAIVE-TIME",
                [evidence_item("EVI-20260809-NAIVE-TIME#001")],
                cutoff="2026-08-09T08:30:00",
            )
            result = assemble(run_manifest(root), contract([required("EVI-20260809-NAIVE-TIME#001", unit_type="evidence_item")]))
            self.assertEqual(result["coverage"]["required_covered"], 0)
            self.assertEqual(result["gaps"][0]["reason"], "invalid_information_cutoff")
            self.assertEqual(result["stable_references"], [])

    def test_hydrate_rejects_an_evidence_reference_with_a_naive_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = write_text(root, "证据包/2026-08/EVI-20260809-NAIVE-HYDRATE.md", "naive snapshot must not hydrate")
            result = hydrate(
                {
                    "workspace_root": str(root),
                    "stable_references": [
                        {
                            "ref": "atom:EVI-20260809-NAIVE-HYDRATE#001",
                            "unit_id": "EVI-20260809-NAIVE-HYDRATE#001",
                            "unit_type": "evidence_item",
                            "information_cutoff": "2026-08-09T08:30:00",
                            "source_locator": {
                                "kind": "markdown",
                                "path": "证据包/2026-08/EVI-20260809-NAIVE-HYDRATE.md",
                                "start_line": 1,
                                "end_line": 1,
                                "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                            },
                        }
                    ],
                }
            )
            self.assertEqual(result["units"], [])
            self.assertEqual(result["missing_references"][0]["reason"], "invalid information cutoff")

    def test_unrecognized_evidence_status_cannot_satisfy_a_requirement(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_evidence(
                root,
                "EVI-20260809-AMBIGUOUS-STATUS",
                [evidence_item("EVI-20260809-AMBIGUOUS-STATUS#001", status="部分确认")],
            )
            result = assemble(run_manifest(root), contract([required("EVI-20260809-AMBIGUOUS-STATUS#001")]))
            self.assertEqual(result["coverage"]["required_covered"], 0)
            self.assertEqual(result["gaps"][0]["reason"], "unrecognized_evidence_status")

    def test_unparseable_authoritative_document_is_a_blocking_gap_even_when_floor_is_covered(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_evidence(root, "EVI-20260809-VALID", [evidence_item("EVI-20260809-VALID#001")])
            write_text(
                root,
                "证据包/2026-08/EVI-20260809-BROKEN.md",
                '''---
schema_version: "a-share-workspace-v3"
artifact_type: "evidence_package"
id: "EVI-20260809-BROKEN"
status: "complete"
# 结构损坏且可能包含冲突证据''',
            )
            result = assemble(run_manifest(root), contract([required("EVI-20260809-VALID#001")]))
            self.assertEqual(result["coverage"]["required_covered"], 1)
            self.assertTrue(result["coverage"]["blocking"])
            self.assertIn("authoritative_document_unparseable", {gap["reason"] for gap in result["gaps"]})

    def test_evidence_source_locator_hydrates_the_original_payload_excerpt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload = FileSourcePayloadStore(root).put(
                "original line 1\noriginal line 2\noriginal line 3\n",
                run_id="RUN-20260809-SOURCE",
                source_uri="https://example.invalid/original",
                acquired_at="2026-08-09T08:45:00+08:00",
            )
            locator = {**payload, "line_start": 2, "line_end": 2}
            write_evidence(
                root,
                "EVI-20260809-PAYLOAD",
                [evidence_item("EVI-20260809-PAYLOAD#001", source_group="SRCGRP-ORIGINAL-001", source_locator=locator)],
            )
            assembled = assemble(run_manifest(root), contract([required("EVI-20260809-PAYLOAD#001")]))
            reference = assembled["stable_references"][0]
            self.assertEqual(reference["source_locator"]["kind"], "source_payload")
            self.assertEqual(reference["canonical_source_locator"]["path"], "证据包/2026-08/EVI-20260809-PAYLOAD.md")
            self.assertEqual(hydrate(assembled)["units"][0]["verification_text"], "original line 2")

    def test_payload_acquired_after_the_run_cutoff_is_future_information(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload = FileSourcePayloadStore(root).put(
                "future payload\n",
                run_id="RUN-20260809-FUTURE-PAYLOAD",
                acquired_at="2026-08-09T10:00:00+08:00",
            )
            write_evidence(
                root,
                "EVI-20260809-FUTURE-PAYLOAD",
                [evidence_item("EVI-20260809-FUTURE-PAYLOAD#001", source_group="SRCGRP-FUTURE-001", source_locator={**payload, "line_start": 1, "line_end": 1})],
            )
            result = assemble(run_manifest(root), contract([required("EVI-20260809-FUTURE-PAYLOAD#001")]))
            self.assertEqual(result["coverage"]["required_covered"], 0)
            self.assertEqual(result["gaps"][0]["reason"], "future_information")

    def test_compound_confirmed_and_unverified_status_never_counts_as_covered(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_evidence(root, "EVI-20260809-STATUS", [evidence_item("EVI-20260809-STATUS#001", status="已确认 / 未证实")])
            result = assemble(
                run_manifest(root),
                contract([{"requirement_id": "business", "unit_type": "evidence_item", "object": "个股:测试公司(600001)", "field": "business"}]),
            )
            self.assertEqual(result["coverage"]["required_covered"], 0)
            self.assertTrue(result["gaps"][0]["blocking"])

    def test_explicit_judgment_dependencies_and_source_groups_enter_the_workset(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_evidence(
                root,
                "EVI-20260809-REL",
                [evidence_item("EVI-20260809-REL#001", source_group="SRCGRP-OFFICIAL-001", source_locator="source:official-announcement#p1")],
            )
            write_judgments(root, [judgment_entry("J20260809-001 v1", evidence_ids=["EVI-20260809-REL#001"])])
            result = assemble(run_manifest(root), contract([required("J20260809-001 v1", unit_type="judgment_version")]))
            references = {item["unit_id"]: item for item in result["stable_references"]}
            self.assertEqual(set(references), {"J20260809-001 v1", "EVI-20260809-REL#001"})
            self.assertEqual(references["EVI-20260809-REL#001"]["source_groups"], ["SRCGRP-OFFICIAL-001"])
            self.assertIn({"from": "J20260809-001 v1", "to": "EVI-20260809-REL#001", "type": "supported_by"}, result["relations"])

    def test_missing_explicit_dependency_is_a_blocking_gap_not_a_dangling_edge(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_judgments(root, [judgment_entry("J20260809-002 v1", evidence_ids=["EVI-20260809-MISSING#001"])])
            result = assemble(run_manifest(root), contract([required("J20260809-002 v1", unit_type="judgment_version")]))
            gap = next(item for item in result["gaps"] if item["reason"] == "missing_relation_target")
            self.assertTrue(gap["blocking"])
            self.assertEqual(result["relations"], [])
            self.assertEqual(result["relation_checks"]["total"], 1)
            self.assertEqual(result["relation_checks"]["resolved"], 0)

    def test_excluded_dependency_is_blocking_and_never_also_counted_as_resolved(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_evidence(
                root,
                "EVI-20260809-CONFLICT",
                [evidence_item("EVI-20260809-CONFLICT#001", status="冲突", source_group="SRCGRP-CONFLICT-001")],
            )
            write_judgments(root, [judgment_entry("J20260809-003 v1", evidence_ids=["EVI-20260809-CONFLICT#001"])])
            result = assemble(run_manifest(root), contract([required("J20260809-003 v1", unit_type="judgment_version")]))
            self.assertEqual([item["unit_id"] for item in result["stable_references"]], ["J20260809-003 v1"])
            self.assertEqual(result["relations"], [])
            self.assertEqual(result["relation_checks"], {"total": 1, "resolved": 0, "blocking_gaps": 1})

    def test_expired_semantic_candidate_is_audited_but_never_selected(self) -> None:
        class ExpiredHit:
            def search(self, query: str, limit: int = 10) -> list[dict[str, str]]:
                return [{"unit_id": "EVI-20260809-EXPIRED#001"}]

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_evidence(
                root,
                "EVI-20260809-EXPIRED",
                [evidence_item("EVI-20260809-EXPIRED#001", fact="过期语义候选不应进入工作集。", object_name="市场:A股", field="market_state", valid_until="2026-08-02")],
                cutoff="2026-08-01T08:30:00+08:00",
            )
            result = assemble(
                run_manifest(
                    root,
                    semantic_query="过期语义候选",
                    semantic_adapter=ExpiredHit(),
                ),
                contract([]),
            )
            self.assertEqual(result["stable_references"], [])
            self.assertIn({"unit_id": "EVI-20260809-EXPIRED#001", "reason": "expired"}, result["semantic_exclusions"])

    def test_reposted_evidence_from_one_source_group_cannot_fake_independence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for index in (1, 2):
                write_evidence(
                    root,
                    f"EVI-20260809-SOURCE-{index}",
                    [evidence_item(f"EVI-20260809-SOURCE-{index}#001", source_group="SRCGRP-SAME-ANNOUNCEMENT")],
                )
            result = assemble(
                run_manifest(root),
                contract([{"requirement_id": "independent-business-sources", "unit_type": "evidence_item", "object": "个股:测试公司(600001)", "field": "business", "min_source_groups": 2}]),
            )
            row = result["coverage"]["requirements"][0]
            self.assertFalse(row["covered"])
            self.assertEqual(row["source_group_count"], 1)
            self.assertEqual(row["reason"], "insufficient_source_independence")

    def test_duplicate_atomic_ids_cannot_resolve_to_an_arbitrary_document(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for index in (1, 2):
                path = write_evidence(root, f"EVI-20260809-DUP-{index}", [evidence_item("EVI-20260809-DUP#001", fact=f"重复原子 ID 的第 {index} 份内容。")])
                path.rename(path.with_name(f"duplicate-{index}.md"))
            result = assemble(run_manifest(root), contract([required("EVI-20260809-DUP#001")]))
            self.assertEqual(result["coverage"]["required_covered"], 0)
            self.assertEqual(result["gaps"][0]["reason"], "ambiguous_stable_reference")
            hydrated = hydrate(
                [{"ref": "atom:EVI-20260809-DUP#001", "unit_id": "EVI-20260809-DUP#001", "selection_cutoff": "2026-08-09T09:00:00+08:00"}],
                workspace_root=root,
            )
            self.assertEqual(hydrated["units"], [])
            self.assertIn("ambiguous", hydrated["missing_references"][0]["reason"])

    def test_observation_pool_is_a_view_not_a_second_authoritative_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            candidate = '''### C20260809-001 v1
- **状态**：active
- **对象类型 / 名称**：交易主题:测试主题'''
            write_text(
                root,
                "观察日志/2026-08.md",
                f'''---
schema_version: "a-share-workspace-v3"
artifact_type: "observation_log"
id: "OLOG-202608"
status: "active"
information_cutoff: "2026-08-09T08:30:00+08:00"
---
# 观察日志
{candidate}''',
            )
            write_text(root, "观察池.md", f"# 当前观察池（派生视图）\n\n{candidate}")
            result = assemble(run_manifest(root), contract([required("C20260809-001 v1", unit_type="observation_candidate")]))
            self.assertFalse(result["projection"]["projection_degraded"])
            self.assertEqual(len(result["stable_references"]), 1)
            self.assertEqual(result["stable_references"][0]["document_path"], "观察日志/2026-08.md")

    def test_strategy_versions_have_distinct_stable_references(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for version in ("0.1.0", "0.2.0"):
                write_strategy(root, "STR-TEST", version=version, status="trial")
            result = assemble(
                run_manifest(root),
                contract([{"requirement_id": "strategy-versions", "unit_type": "strategy_version", "eligibility_mode": "historical_as_of", "cutoff_basis": "run_cutoff"}]),
            )
            self.assertFalse(result["projection"]["projection_degraded"])
            self.assertEqual({item["unit_id"] for item in result["stable_references"]}, {"STR-TEST@0.1.0", "STR-TEST@0.2.0"})

    def test_unreadable_fact_document_degrades_projection_instead_of_hiding_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "证据包/2026-08/EVI-invalid.md"
            path.parent.mkdir(parents=True)
            path.write_bytes(b"\xff\xfe\x00")
            result = assemble(run_manifest(root), contract([]))
            self.assertTrue(result["projection"]["projection_degraded"])
            self.assertTrue(result["projection"]["direct_read"])
            self.assertIn("证据包/2026-08/EVI-invalid.md", result["projection"]["reason"])

    def test_unparseable_evidence_structure_degrades_projection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_text(
                root,
                "证据包/2026-08/EVI-20260809-MALFORMED.md",
                '''---
schema_version: "a-share-workspace-v3"
artifact_type: "evidence_package"
id: "EVI-20260809-MALFORMED"
status: "complete"
information_cutoff: "2026-08-09T08:30:00+08:00"
---
# 结构损坏的证据包
## 这不是原子证据 ID
- **事实陈述**：解析器不能把它静默当成没有证据。''',
            )
            result = assemble(run_manifest(root), contract([]))
            self.assertTrue(result["projection"]["projection_degraded"])
            self.assertIn("has no atomic evidence items", result["projection"]["reason"])


if __name__ == "__main__":
    unittest.main()
