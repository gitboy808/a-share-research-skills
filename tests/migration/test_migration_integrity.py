from __future__ import annotations

import json
import hashlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.support.workspace_builders import (
    dossier_field,
    evidence_item,
    judgment_entry,
    write_dossier,
    write_evidence,
    write_judgments,
    write_strategy,
)
from tests.support.shadow_runtime import RUNTIME_PATHS


REPO_ROOT = Path(__file__).resolve().parents[2]
SUITE_ROOT = REPO_ROOT / ".agents/skills/a-share"


class MigrationIntegrityTests(unittest.TestCase):
    def _run_migration(self, files: dict[str, str]) -> tuple[Path, dict[str, object], tempfile.TemporaryDirectory[str]]:
        temporary = tempfile.TemporaryDirectory()
        base = Path(temporary.name)
        source = base / "source"
        output = base / "output"
        for relative, content in files.items():
            path = source / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
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
        report = json.loads((output / "迁移映射.json").read_text(encoding="utf-8"))
        return output, report, temporary

    def test_stable_reference_mapping_traces_old_locator_to_new_locator(self) -> None:
        output, report, temporary = self._run_migration(
            {
                "判断日志/2026-08.md": (
                    "# 判断日志\n\n"
                    "## 事件批次\n\n"
                    "- **J0803-01 v1**｜原始判断。\n"
                )
            }
        )
        self.addCleanup(temporary.cleanup)

        mappings = report["stable_reference_mapping"]
        reference = next(item for item in mappings if item.get("old_ref") == "J0803-01 v1")

        self.assertEqual(
            reference["old_source_locator"],
            {
                "path": "判断日志/2026-08.md",
                "start_line": 5,
                "end_line": 5,
                "source_group": "事件批次",
            },
        )
        self.assertRegex(reference["new_ref"], r"^J20260803-\d{3} v1$")
        self.assertEqual(reference["new_source_locator"]["path"], "判断日志/迁移-2026-08.md")
        generated = output / reference["new_source_locator"]["path"]
        generated_lines = generated.read_text(encoding="utf-8").splitlines()
        start = reference["new_source_locator"]["start_line"]
        self.assertEqual(generated_lines[start - 1], f"### {reference['new_ref']}")

    def test_shared_legacy_judgment_version_resolves_each_bare_reference(self) -> None:
        output, report, temporary = self._run_migration(
            {
                "判断日志/2026-08.md": (
                    "# 判断日志\n\n"
                    "## 同一批次\n\n"
                    "- **J0803-10 v1**｜条件判断甲。\n"
                    "- **J0803-11 v1**｜条件判断乙。\n"
                    "- **J0803-12 v1**｜基准判断。\n\n"
                    "### 批次复核\n\n"
                    "- （J0803-10、J0803-11、J0803-12 维持 v1 不变；J0803-12 升为基准判断）\n"
                )
            }
        )
        self.addCleanup(temporary.cleanup)

        declaration_refs = {
            item["old_ref"]: item
            for item in report["stable_reference_mapping"]
            if item["old_source_locator"]["start_line"] in {5, 6, 7}
        }
        shared_refs = [
            item
            for item in report["stable_reference_mapping"]
            if item["old_source_locator"]["start_line"] == 11
        ]

        self.assertEqual(
            {item["old_ref"] for item in shared_refs},
            {"J0803-10 v1", "J0803-11 v1", "J0803-12 v1"},
        )
        self.assertTrue(all(item.get("mapping_kind") == "shared_legacy_judgment_version" for item in shared_refs))
        for item in shared_refs:
            declaration = declaration_refs[item["old_ref"]]
            self.assertEqual(item["new_ref"], declaration["new_ref"])
            self.assertEqual(item["new_source_locator"], declaration["new_source_locator"])
            generated = output / item["new_source_locator"]["path"]
            self.assertIn(item["new_ref"], generated.read_text(encoding="utf-8"))

    def test_ambiguous_shared_legacy_version_is_quarantined_without_stopping_migration(self) -> None:
        output, report, temporary = self._run_migration(
            {
                "判断日志/2026-08.md": (
                    "# 判断日志\n\n"
                    "## 批次甲\n\n"
                    "- **J0803-01 v1**｜甲命题。\n\n"
                    "## 批次乙\n\n"
                    "- **J0803-01 v1**｜乙命题。\n\n"
                    "## 汇总\n\n"
                    "- J0803-01 维持 v1 不变。\n"
                )
            }
        )
        self.addCleanup(temporary.cleanup)

        reference = next(
            item
            for item in report["stable_reference_mapping"]
            if item["old_source_locator"]["start_line"] == 13
        )
        self.assertEqual(reference["old_ref"], "J0803-01 v1")
        self.assertEqual(reference["mapping_status"], "mapped_to_historical_audit")
        self.assertEqual(reference["mapping_kind"], "ambiguous_shared_legacy_judgment_version")
        self.assertEqual(len(reference["candidate_new_refs"]), 2)
        self.assertRegex(reference["new_ref"], r"^HIST-[0-9a-f]{16}$")
        self.assertIn("J0803-01 维持", (output / reference["new_source_locator"]["path"]).read_text(encoding="utf-8"))

    def test_bare_legacy_judgment_reference_without_explicit_version_is_quarantined(self) -> None:
        _output, report, temporary = self._run_migration(
            {
                "判断日志/2026-08.md": (
                    "# 判断日志\n\n"
                    "## 同一批次\n\n"
                    "- **J0803-10 v1**｜条件判断。\n\n"
                    "- J0803-10 维持不变。\n"
                )
            }
        )
        self.addCleanup(temporary.cleanup)

        unresolved = next(
            item
            for item in report["stable_reference_mapping"]
            if item["old_source_locator"]["start_line"] == 7
        )
        declaration = next(
            item
            for item in report["stable_reference_mapping"]
            if item["old_source_locator"]["start_line"] == 5
        )

        self.assertEqual(unresolved["mapping_status"], "mapped_to_historical_audit")
        self.assertEqual(unresolved["mapping_kind"], "unresolved_legacy_judgment_reference")
        self.assertRegex(unresolved["new_ref"], r"^HIST-[0-9a-f]{16}$")
        self.assertEqual(unresolved["candidate_new_refs"], [declaration["new_ref"]])
        self.assertNotEqual(unresolved["new_ref"], declaration["new_ref"])

    def test_unique_legacy_review_reference_maps_to_its_only_declared_version(self) -> None:
        _output, report, temporary = self._run_migration(
            {
                "判断日志/2026-08.md": (
                    "# 判断日志\n\n"
                    "## 原判断\n\n"
                    "- **J0803-01 v1**｜待验证命题。\n\n"
                    "## 复盘结果\n\n"
                    "- **J0803-01 ✅**｜命题验证成立。\n"
                )
            }
        )
        self.addCleanup(temporary.cleanup)

        declaration = next(
            item
            for item in report["stable_reference_mapping"]
            if item["old_source_locator"]["start_line"] == 5
        )
        review = next(
            item
            for item in report["stable_reference_mapping"]
            if item["old_source_locator"]["start_line"] == 9
        )

        self.assertEqual(review["old_ref"], "J0803-01")
        self.assertEqual(review["new_ref"], declaration["new_ref"])
        self.assertEqual(review["new_source_locator"], declaration["new_source_locator"])
        self.assertEqual(review["mapping_kind"], "legacy_judgment_status_reference")

    def test_ambiguous_legacy_review_reference_maps_only_to_historical_audit(self) -> None:
        output, report, temporary = self._run_migration(
            {
                "判断日志/2026-08.md": (
                    "# 判断日志\n\n"
                    "## 批次甲\n\n"
                    "- **J0803-15 v1**｜命题甲。\n\n"
                    "## 批次乙\n\n"
                    "- **J0803-15 v1**｜旧 ID 被复用于命题乙。\n\n"
                    "## 复盘结果\n\n"
                    "- **J0803-15 中间观察（未到期）**：方向暂时支持。\n"
                )
            }
        )
        self.addCleanup(temporary.cleanup)

        review = next(
            item
            for item in report["stable_reference_mapping"]
            if item["old_source_locator"]["start_line"] == 13
        )

        self.assertEqual(review["old_ref"], "J0803-15")
        self.assertEqual(review["mapping_status"], "mapped_to_historical_audit")
        self.assertEqual(review["mapping_kind"], "ambiguous_legacy_judgment_status_reference")
        self.assertRegex(review["new_ref"], r"^HIST-[0-9a-f]{16}$")
        self.assertEqual(len(review["candidate_new_refs"]), 2)
        locator = review["new_source_locator"]
        self.assertEqual(locator["path"], "判断日志/2026-08.md")
        self.assertIn("J0803-15 中间观察", (output / locator["path"]).read_text(encoding="utf-8"))

    def test_versioned_legacy_review_reference_maps_to_that_exact_version(self) -> None:
        _output, report, temporary = self._run_migration(
            {
                "判断日志/2026-08.md": (
                    "# 判断日志\n\n"
                    "## 原判断\n\n"
                    "- **J0803-09 v1**｜初始命题。\n"
                    "- **J0803-09 v2**｜收敛命题。\n\n"
                    "## 复盘结果\n\n"
                    "- **J0803-09 v2 ✅**｜收敛命题验证成立。\n"
                )
            }
        )
        self.addCleanup(temporary.cleanup)

        version_two = next(
            item
            for item in report["stable_reference_mapping"]
            if item["old_source_locator"]["start_line"] == 6
        )
        review = next(
            item
            for item in report["stable_reference_mapping"]
            if item["old_source_locator"]["start_line"] == 10
        )

        self.assertEqual(review["old_ref"], "J0803-09 v2")
        self.assertEqual(review["new_ref"], version_two["new_ref"])
        self.assertEqual(review["mapping_kind"], "legacy_judgment_status_reference")

    def test_existing_long_judgment_id_in_legacy_migration_table_remains_audit_only(self) -> None:
        _output, report, temporary = self._run_migration(
            {
                "判断日志/2026-08.md": (
                    "# 判断日志\n\n"
                    "## 旧判断\n\n"
                    "- **J0803-04 v1**｜旧命题。\n\n"
                    "## 旧版迁移表\n\n"
                    "| v2 ID | legacy source | 命题 |\n"
                    "|---|---|---|\n"
                    "| J20260805-004 | J0803-04 | 已迁移命题 |\n"
                )
            }
        )
        self.addCleanup(temporary.cleanup)

        current_id_reference = next(
            item
            for item in report["stable_reference_mapping"]
            if item["old_ref"] == "J20260805-004"
        )

        self.assertEqual(current_id_reference["mapping_status"], "mapped_to_historical_audit")
        self.assertEqual(current_id_reference["mapping_kind"], "unresolved_legacy_judgment_reference")
        self.assertRegex(current_id_reference["new_ref"], r"^HIST-[0-9a-f]{16}$")

    def test_judgment_versions_share_an_id_within_a_source_group_but_reused_ids_do_not(self) -> None:
        _output, report, temporary = self._run_migration(
            {
                "判断日志/2026-08.md": (
                    "# 判断日志\n\n"
                    "## 事件批次甲\n\n"
                    "- **J0803-01 v1**｜甲的初始判断。\n"
                    "- **J0803-01 v2**｜甲的后续判断。\n\n"
                    "## 事件批次乙\n\n"
                    "- **J0803-01 v1**｜乙重用了旧 ID。\n"
                    "- **J0803-01 v2**｜乙的后续判断。\n"
                )
            }
        )
        self.addCleanup(temporary.cleanup)

        mappings = [
            item
            for item in report["stable_reference_mapping"]
            if item["old_ref"].startswith("J0803-01")
        ]
        by_group: dict[str, list[dict[str, object]]] = {}
        for item in mappings:
            group = item["old_source_locator"]["source_group"]
            by_group.setdefault(group, []).append(item)

        self.assertEqual(set(by_group), {"事件批次甲", "事件批次乙"})
        group_ids = {
            group: {str(item["new_ref"]).rsplit(" v", 1)[0] for item in items}
            for group, items in by_group.items()
        }
        self.assertEqual(len(group_ids["事件批次甲"]), 1)
        self.assertEqual(len(group_ids["事件批次乙"]), 1)
        self.assertNotEqual(group_ids["事件批次甲"], group_ids["事件批次乙"])

    def test_repeated_version_in_one_source_group_starts_a_locator_disambiguated_chain(self) -> None:
        _output, report, temporary = self._run_migration(
            {
                "判断日志/2026-08.md": (
                    "# 判断日志\n\n"
                    "## 同一批次\n\n"
                    "- **J0803-01 v1**｜第一条链的初始判断。\n"
                    "- **J0803-01 v2**｜第一条链的后续判断。\n"
                    "- **J0803-01 v1**｜旧 ID 被再次用于另一命题。\n"
                    "- **J0803-01 v2**｜第二条链的后续判断。\n"
                )
            }
        )
        self.addCleanup(temporary.cleanup)

        mappings = sorted(
            (
                item
                for item in report["stable_reference_mapping"]
                if item["old_ref"].startswith("J0803-01")
            ),
            key=lambda item: item["old_source_locator"]["start_line"],
        )
        new_ids = [item["new_ref"].rsplit(" v", 1)[0] for item in mappings]
        self.assertEqual(new_ids[0], new_ids[1])
        self.assertEqual(new_ids[2], new_ids[3])
        self.assertNotEqual(new_ids[0], new_ids[2])

    def test_multiple_legacy_judgment_files_in_one_month_never_overwrite_derived_views(self) -> None:
        files = {
            f"判断日志/2026-08-{suffix}.md": (
                "# 判断日志\n\n"
                f"## 批次{suffix}\n\n"
                f"- **J080{index}-01 v1**｜第 {index} 份独立历史判断。\n"
            )
            for index, suffix in enumerate(("a", "b", "c"), start=1)
        }
        output, report, temporary = self._run_migration(files)
        self.addCleanup(temporary.cleanup)

        derived_paths = {
            item["new_path"]
            for item in report["mappings"]
            if item.get("derived_view")
        }
        self.assertEqual(len(derived_paths), 3)
        self.assertTrue(all((output / path).is_file() for path in derived_paths))
        combined = "\n".join((output / path).read_text(encoding="utf-8") for path in sorted(derived_paths))
        self.assertIn("第 1 份独立历史判断", combined)
        self.assertIn("第 2 份独立历史判断", combined)
        self.assertIn("第 3 份独立历史判断", combined)

    def test_reused_legacy_id_across_source_files_gets_globally_unique_new_judgment_ids(self) -> None:
        output, report, temporary = self._run_migration(
            {
                "判断日志/2026-08-a.md": (
                    "# 判断日志\n\n## 批次甲\n\n- **J0803-01 v1**｜甲命题。\n"
                ),
                "判断日志/2026-08-b.md": (
                    "# 判断日志\n\n## 批次乙\n\n- **J0803-01 v1**｜乙命题。\n"
                ),
            }
        )
        self.addCleanup(temporary.cleanup)

        mappings = [
            item
            for item in report["stable_reference_mapping"]
            if item.get("old_ref") == "J0803-01 v1"
        ]
        self.assertEqual(len(mappings), 2)
        self.assertEqual(len({item["new_ref"] for item in mappings}), 2)
        for item in mappings:
            locator = item["new_source_locator"]
            self.assertIn(item["new_ref"], (output / locator["path"]).read_text(encoding="utf-8"))

    def test_mixed_judgment_log_splits_provable_versions_live_and_keeps_incomplete_entries_audit_only(self) -> None:
        def entry(version: int, cutoff: str, proposition: str, upstream: str) -> str:
            return (
                f"### J20260807-001 v{version}\n\n"
                "- **Schema**：a-share-workspace-v3\n"
                "- **类型**：条件方向\n"
                "- **研究状态**：等待确认\n"
                "- **研究对象**：个股：测试股份（600000）\n"
                f"- **信息快照**：{cutoff}；冻结快照。\n"
                "- **判断周期**：短周期，至 2026-08-11。\n"
                f"- **原子命题**：{proposition}\n"
                f"- **上游判断**：{upstream}\n"
                "- **证据包 / 原子证据项**：EVI-20260807-001；#001—#002。\n"
                "- **价格纪律门**：等待；尚未确认。\n"
                "- **置信区间**：60%–69%。\n"
                "- **证伪条件**：窗口内跌破公共验证线。\n"
                "- **时限**：2026-08-11 收盘。\n"
                "- **核心跟踪指标**：公共验证线与主题广度。\n"
            )

        incomplete = (
            "### J20260808-002 v1\n\n"
            "- **类型**：方向\n"
            "- **研究状态**：等待确认\n"
            "- **研究对象**：个股：不完整样本（600001）\n"
            "- **信息快照**：2026-08-08 10:00 Asia/Shanghai。\n"
            "- **判断周期**：日内。\n"
            "- **原子命题**：该不完整样本只应留在迁移审计。\n"
            "- **证据包 / 原子证据项**：EVI-20260807-001#001。\n"
            "- **价格纪律门**：等待。\n"
            "- **置信区间**：55%–59%。\n"
            "- **证伪条件**：收盘未满足命题。\n"
            "- **时限**：2026-08-08 收盘。\n"
        )
        delta = (
            "### J20260807-001 v3\n\n"
            "- **上一版本**：J20260807-001 v2。\n"
            "- **新信息快照**：2026-08-07 11:30 Asia/Shanghai；午间冻结快照。\n"
            "- **类型 / 研究状态 / 周期**：弃权修订 / 弃权 / 日内至收盘。\n"
            "- **原子命题**：测试股份撤销原条件方向并等待重新取证。\n"
            "- **证据包 / 原子证据项**：EVI-20260807-001#001。\n"
            "- **价格纪律门**：否决；原条件未确认。\n"
            "- **置信区间**：不适用（弃权修订）。\n"
            "- **证伪条件 / 时限**：若重新取证后恢复确认则新建判断；2026-08-07 15:00。\n"
            "- **核心跟踪指标**：收盘验证线与重新取证状态。\n"
        )
        output, report, temporary = self._run_migration(
            {
                "证据包/EVI-20260807-001.md": (
                    "# 旧证据包\n\n### EVI-20260807-001#001\n\n"
                    "- **事实陈述**：旧证据没有 current 来源载荷。\n"
                ),
                "判断日志/2026-08.md": (
                    "# mixed 判断日志\n\n"
                    "## legacy 批次\n\n"
                    "- **J0803-01 v1**｜旧格式命题，只留审计。\n\n"
                    "## 可证明 v3 批次\n\n"
                    + entry(1, "2026-08-07 09:30 Asia/Shanghai", "测试股份等待价格确认。", "无。")
                    + "\n"
                    + entry(
                        2,
                        "2026-08-07 10:15 Asia/Shanghai",
                        "测试股份仍等待价格确认。",
                        "J20260807-001 v1；约束。",
                    )
                    + "\n"
                    + delta
                    + "\n## 不完整 v3 批次\n\n"
                    + incomplete
                ),
            }
        )
        self.addCleanup(temporary.cleanup)

        live_path = output / "判断日志/2026-08-live.md"
        live = live_path.read_text(encoding="utf-8")
        self.assertIn('artifact_type: "judgment_log"', live)
        self.assertIn('id: "JLOG-202608"', live)
        self.assertIn('authority: "judgment_fact_source"', live)
        self.assertIn("### J20260807-001 v1", live)
        self.assertIn("### J20260807-001 v2", live)
        self.assertIn("### J20260807-001 v3", live)
        self.assertIn("测试股份仍等待价格确认。", live)
        self.assertIn("J20260807-001 v1；约束。", live)
        self.assertIn("- **新信息快照**：2026-08-07 11:30 Asia/Shanghai；午间冻结快照。", live)
        self.assertIn("- **信息快照**：2026-08-07T11:30:00+08:00", live)
        self.assertIn(
            "- **迁移原信息快照**：2026-08-07 11:30 Asia/Shanghai；午间冻结快照。",
            live,
        )
        self.assertIn("- **类型**：弃权修订", live)
        self.assertIn("- **研究状态**：弃权", live)
        self.assertIn("- **状态**：弃权", live)
        self.assertGreaterEqual(live.count("- **状态**：等待确认"), 2)
        self.assertIn("- **判断周期**：日内至收盘。", live)
        self.assertIn("- **证伪条件**：若重新取证后恢复确认则新建判断", live)
        self.assertIn("- **时限**：2026-08-07 15:00。", live)
        self.assertIn("迁移继承审计", live)
        self.assertEqual(live.count("- **研究对象**：个股：测试股份（600000）"), 3)
        self.assertNotIn("J20260808-002", live)
        self.assertRegex(
            live,
            r"证据包 / 原子证据项\*\*：unknown—正式弃权；[^\n]+不计覆盖",
        )
        self.assertIn("迁移证据审计", live)
        self.assertIn("EVI-20260807-001；#001—#002", live)
        audit_relations: list[dict[str, str]] = []
        assembled_by_id: dict[str, dict[str, object]] = {}
        context_cli = REPO_ROOT / ".agents/skills/a-share/shared/scripts/context_workspace.py"
        for version in (1, 2, 3):
            unit_id = f"J20260807-001 v{version}"
            run_path = output.parent / f"run-{version}.json"
            task_path = output.parent / f"task-{version}.json"
            run_path.write_text(
                json.dumps(
                    {
                        "workspace_root": str(output),
                        "information_cutoff": "2026-08-07T16:00:00+08:00",
                    }
                ),
                encoding="utf-8",
            )
            task_path.write_text(
                json.dumps(
                    {
                        "contract_id": "test.migration-audit-relations",
                        "version": "1.0.0",
                        "required_evidence": [
                            {
                                "requirement_id": "judgment",
                                "unit_type": "judgment_version",
                                "unit_id": unit_id,
                                "eligibility_mode": "historical_as_of",
                                "cutoff_basis": "unit_snapshot",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            assembled_result = subprocess.run(
                [
                    sys.executable,
                    str(context_cli),
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
            self.assertEqual(assembled_result.returncode, 0, assembled_result.stderr)
            assembled = json.loads(assembled_result.stdout)
            assembled_by_id[unit_id] = assembled
            audit_relations.extend(
                relation
                for relation in assembled["audit_relations"]
                if relation.get("from") == unit_id
            )
        self.assertEqual(len(audit_relations), 5)
        self.assertEqual(
            {relation["to"] for relation in audit_relations},
            {"EVI-20260807-001#001", "EVI-20260807-001#002"},
        )
        assembled = assembled_by_id["J20260807-001 v1"]
        self.assertEqual(assembled["relations"], [])
        self.assertEqual(len(assembled["audit_relations"]), 2, assembled)
        self.assertFalse(
            any(gap.get("reason") == "missing_relation_target" for gap in assembled["gaps"])
        )
        audit = (output / "判断日志/2026-08.md").read_text(encoding="utf-8")
        self.assertIn("J20260808-002", audit)
        self.assertIn('authority: "migration_audit"', audit)
        live_mappings = [
            item
            for item in report["stable_reference_mapping"]
            if item.get("mapping_kind") == "provable_live_judgment_split"
        ]
        self.assertEqual(
            {item["old_ref"] for item in live_mappings},
            {"J20260807-001 v1", "J20260807-001 v2", "J20260807-001 v3"},
        )
        self.assertTrue(all(item["new_source_locator"]["path"] == "判断日志/2026-08-live.md" for item in live_mappings))
        self.assertEqual(report["summary"]["authoritative_atomization"]["judgment_items"], 3)
        self.assertEqual(report["summary"]["authoritative_atomization"]["evidence_items"], 0)
        self.assertEqual(report["summary"]["live_judgment_versions"], 3)
        self.assertEqual(report["summary"]["live_judgment_formal_evidence_gaps"], 3)
        rejection = next(
            item
            for item in report["live_judgment_rejections"]
            if item["reference"] == "J20260808-002 v1"
        )
        self.assertIn("missing_required_field:核心跟踪指标", rejection["reasons"])
        rejection_blocker = next(
            item
            for item in report["acceptance"]["blockers"]
            if item["code"] == "canonical_judgment_versions_quarantined"
        )
        self.assertEqual(rejection_blocker["count"], 1)
        blocker = next(
            item
            for item in report["acceptance"]["blockers"]
            if item["code"] == "live_judgments_missing_live_evidence"
        )
        self.assertEqual(blocker["count"], 3)

    def test_every_after_hash_matches_the_final_output_file(self) -> None:
        output, report, temporary = self._run_migration(
            {
                "分析报告/2026-08/事件分析.md": "# 事件分析\n\n原始判断正文。\n",
                "判断日志/2026-08.md": (
                    "# 判断日志\n\n"
                    "## 事件批次\n\n"
                    "- **J0803-01 v1**｜原始判断。\n"
                ),
            }
        )
        self.addCleanup(temporary.cleanup)

        self.assertGreater(len(report["mappings"]), 0)
        for mapping in report["mappings"]:
            final_path = output / mapping["new_path"]
            self.assertTrue(final_path.is_file(), mapping)
            final_hash = hashlib.sha256(final_path.read_bytes()).hexdigest()
            self.assertEqual(mapping["after_sha256"], final_hash, mapping)

    def test_legacy_report_name_collisions_allocate_unique_paths_without_overwrite(self) -> None:
        output, report, temporary = self._run_migration(
            {
                "报告/分析/2026-08/foo.md": "# 当前 foo\n\ncurrent sentinel\n",
                "报告/分析/2026-08/分析报告-foo.md": "# 当前 prefixed foo\n\nprefixed sentinel\n",
                "分析报告/2026-08/foo.md": "# 旧 foo\n\nlegacy sentinel\n",
            }
        )
        self.addCleanup(temporary.cleanup)

        contents = {
            path.read_text(encoding="utf-8")
            for path in (output / "报告/分析/2026-08").glob("*.md")
        }
        self.assertEqual(len(contents), 3)
        self.assertTrue(any("current sentinel" in text for text in contents))
        self.assertTrue(any("prefixed sentinel" in text for text in contents))
        self.assertTrue(any("legacy sentinel" in text for text in contents))
        legacy_mapping = next(
            item
            for item in report["mappings"]
            if item["old_path"] == "分析报告/2026-08/foo.md"
        )
        self.assertIn("legacy sentinel", (output / legacy_mapping["new_path"]).read_text(encoding="utf-8"))

    def test_migration_report_blocks_release_until_shadow_acceptance_runs(self) -> None:
        _output, report, temporary = self._run_migration(
            {"报告/分析/2026-08/事件分析.md": "# 事件分析\n\n历史正文。\n"}
        )
        self.addCleanup(temporary.cleanup)

        self.assertEqual(report["status"], "structural_migration_completed")
        self.assertEqual(report["overall_status"], "acceptance_pending")
        self.assertFalse(report["overall_passed"])
        self.assertFalse(report["release_ready"])
        self.assertEqual(report["acceptance"]["status"], "not_run")
        self.assertFalse(report["acceptance"]["complete"])

    def test_migration_report_seals_the_complete_copied_input_snapshot(self) -> None:
        output, report, temporary = self._run_migration(
            {
                "CONTEXT.md": "# frozen input\n",
                "报告/分析/source.md": "# source\n\nimmutable input sentinel\n",
                ".source-payloads/RUN-1/PAYLOAD-1.payload": "raw payload sentinel\n",
                ".context/projection.db": "ignored projection cache\n",
            }
        )
        self.addCleanup(temporary.cleanup)

        snapshot = report["input_snapshot"]
        paths = {item["path"] for item in snapshot["files"]}
        self.assertEqual(
            paths,
            {
                "CONTEXT.md",
                "报告/分析/source.md",
                ".source-payloads/RUN-1/PAYLOAD-1.payload",
            },
        )
        source_root = Path(report["input_root"])
        digest = hashlib.sha256()
        for item in sorted(snapshot["files"], key=lambda row: row["path"]):
            self.assertEqual(
                item["sha256"],
                hashlib.sha256((source_root / item["path"]).read_bytes()).hexdigest(),
            )
            digest.update(item["path"].encode("utf-8"))
            digest.update(b"\0")
            digest.update(item["sha256"].encode("ascii"))
            digest.update(b"\n")
        self.assertEqual(snapshot["sha256"], digest.hexdigest())
        self.assertNotIn("scripts/migrate_workspace.py", paths)
        self.assertTrue((output / "scripts/migrate_workspace.py").is_file())

    def test_migration_cli_marks_structural_completion_as_not_release_ready(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            source = base / "source"
            output = base / "output"
            source.mkdir()
            (source / "CONTEXT.md").write_text("# 历史工作区\n", encoding="utf-8")

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
            self.assertEqual(payload["overall_status"], "acceptance_pending")
            self.assertFalse(payload["overall_passed"])
            self.assertFalse(payload["release_ready"])

    def test_migration_preflight_rejects_symlink_that_escapes_input_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            source = base / "source"
            output = base / "output"
            outside = base / "private-source.md"
            source.mkdir()
            outside.write_text("PRIVATE_SENTINEL\n", encoding="utf-8")
            (source / "报告").mkdir()
            (source / "报告/private-link.md").symlink_to(outside)

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

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("symlink", result.stderr.lower())
            self.assertIn("escapes input workspace", result.stderr)
            self.assertFalse((output / "报告/private-link.md").exists())
            self.assertEqual(outside.read_text(encoding="utf-8"), "PRIVATE_SENTINEL\n")

    def test_existing_stable_references_keep_explicit_locators_across_report_moves(self) -> None:
        output, report, temporary = self._run_migration(
            {
                "分析报告/2026-08/事件分析.md": (
                    "# 事件分析\n\n"
                    "依据 EVI-20260801-001#1 形成历史判断。\n"
                )
            }
        )
        self.addCleanup(temporary.cleanup)

        reference = next(
            item
            for item in report["stable_reference_mapping"]
            if item.get("old_ref") == "EVI-20260801-001#1"
        )
        self.assertEqual(
            reference["old_source_locator"],
            {"path": "分析报告/2026-08/事件分析.md", "start_line": 3, "end_line": 3},
        )
        self.assertEqual(reference["new_ref"], "EVI-20260801-001#1")
        self.assertEqual(reference["mapping_status"], "mapped")
        new_locator = reference["new_source_locator"]
        self.assertEqual(new_locator["path"], "报告/分析/2026-08/事件分析.md")
        new_line = (output / new_locator["path"]).read_text(encoding="utf-8").splitlines()[
            new_locator["start_line"] - 1
        ]
        self.assertIn("EVI-20260801-001#1", new_line)

    def test_incomplete_legacy_evidence_is_quarantined_as_historical_audit(self) -> None:
        output, report, temporary = self._run_migration(
            {
                "证据包/旧资料.md": (
                    "# 旧证据\n\n"
                    "历史材料没有可证明的快照、来源权威或当前 schema 元数据。\n"
                )
            }
        )
        self.addCleanup(temporary.cleanup)

        mapping = next(
            item for item in report["mappings"] if item["old_path"] == "证据包/旧资料.md"
        )
        self.assertRegex(
            mapping["new_path"],
            r"^报告/迁移审计/证据包/迁移-旧资料(?:-\d{3})?\.md$",
        )
        self.assertFalse((output / "证据包/旧资料.md").exists())
        text = (output / mapping["new_path"]).read_text(encoding="utf-8")
        self.assertIn('artifact_type: "historical_record"', text)
        self.assertIn('record_kind: "evidence_package_migration_unresolved"', text)
        self.assertIn('authority: "migration_audit"', text)
        self.assertIn("历史材料没有可证明的快照", text)

    def test_references_to_quarantined_evidence_map_to_the_audit_record(self) -> None:
        output, report, temporary = self._run_migration(
            {
                "证据包/旧资料.md": (
                    "# 旧证据\n\n"
                    "### EVI-20260801-001#1\n\n"
                    "- **事实陈述**：历史证据缺少可信快照。\n"
                ),
                "分析报告/2026-08/旧分析.md": (
                    "# 旧分析\n\n依据 EVI-20260801-001#1 形成当时判断。\n"
                ),
            }
        )
        self.addCleanup(temporary.cleanup)

        evidence_mapping = next(
            item for item in report["mappings"] if item["old_path"] == "证据包/旧资料.md"
        )
        audit_text = (output / evidence_mapping["new_path"]).read_text(encoding="utf-8")
        audit_id = next(
            line.split(":", 1)[1].strip().strip('"')
            for line in audit_text.splitlines()
            if line.startswith("id:")
        )
        references = [
            item
            for item in report["stable_reference_mapping"]
            if item["old_ref"] == "EVI-20260801-001#1"
        ]
        self.assertEqual(len(references), 2)
        self.assertTrue(all(item["new_ref"] == audit_id for item in references))
        self.assertTrue(all(item["mapping_status"] == "mapped_to_historical_audit" for item in references))
        self.assertTrue(all(item["new_source_locator"]["path"] == evidence_mapping["new_path"] for item in references))

        self.assertEqual(report["summary"]["quarantined_evidence_packages"], 1)
        self.assertEqual(report["summary"]["audit_mapped_references"], 2)
        self.assertEqual(report["summary"]["ambiguous_reference_mappings"], 0)
        blocker_codes = {item["code"] for item in report["acceptance"]["blockers"]}
        self.assertIn("historical_evidence_quarantined", blocker_codes)
        self.assertIn("shadow_replay_not_run", blocker_codes)
        no_live_evidence = next(
            item
            for item in report["acceptance"]["blockers"]
            if item["code"] == "no_live_authoritative_evidence_items"
        )
        self.assertEqual(no_live_evidence["count"], 1)
        self.assertEqual(no_live_evidence["observed_live_evidence_items"], 0)
        self.assertEqual(
            report["summary"]["authoritative_atomization"]["evidence_items"],
            0,
        )

    def test_quarantined_evidence_declaration_wins_over_cross_package_mentions(self) -> None:
        output, report, temporary = self._run_migration(
            {
                "证据包/EVI-20260806-004.md": (
                    "# 旧证据包\n\n"
                    "### EVI-20260806-004#002\n\n"
                    "- **事实陈述**：煤炭篮子冻结事实。\n"
                ),
                "证据包/EVI-20260806-007.md": (
                    "# 旧证据包\n\n"
                    "### EVI-20260806-007#003\n\n"
                    "- **状态**：与 `EVI-20260806-004#002` 同源。\n"
                ),
                "判断日志/2026-08.md": (
                    "# 判断日志\n\n"
                    "- **J0806-01 v1**｜旧判断触发迁移视图。\n\n"
                    "### J20260806-005 v1\n\n"
                    "- **证据包 / 原子证据项**：受 `EVI-20260806-004#002—#008` 约束。\n"
                ),
            }
        )
        self.addCleanup(temporary.cleanup)

        declaration_mapping = next(
            item
            for item in report["mappings"]
            if item["old_path"] == "证据包/EVI-20260806-004.md"
        )
        audit_text = (output / declaration_mapping["new_path"]).read_text(encoding="utf-8")
        audit_id = next(
            line.split(":", 1)[1].strip().strip('"')
            for line in audit_text.splitlines()
            if line.startswith("id:")
        )
        references = [
            item
            for item in report["stable_reference_mapping"]
            if item["old_ref"] == "EVI-20260806-004#002"
        ]
        self.assertEqual(len(references), 3)
        self.assertTrue(all(item["new_ref"] == audit_id for item in references))
        self.assertTrue(
            all(item["mapping_kind"] == "evidence_package_migration_unresolved" for item in references)
        )

    def test_externalized_payloads_are_copied_but_excluded_from_reference_scanning(self) -> None:
        output, report, temporary = self._run_migration(
            {
                "报告/分析/2026-08/事件分析.md": "# 事件分析\n\n历史正文。\n",
                ".source-payloads/RUN-20260801-001/PAYLOAD-001.payload": (
                    "原始载荷中的 EVI-20260801-999#1 不属于文档事实源。\n"
                ),
                ".source-payloads/RUN-20260801-001/PAYLOAD-001.json": (
                    '{"payload_id":"PAYLOAD-001","acquired_at":"2026-08-01T09:00:00+08:00"}\n'
                ),
            }
        )
        self.addCleanup(temporary.cleanup)

        self.assertEqual(report["migration_status"], "completed")
        self.assertEqual(
            (output / ".source-payloads/RUN-20260801-001/PAYLOAD-001.payload").read_text(encoding="utf-8"),
            "原始载荷中的 EVI-20260801-999#1 不属于文档事实源。\n",
        )
        self.assertTrue((output / ".source-payloads/RUN-20260801-001/PAYLOAD-001.json").is_file())
        self.assertFalse(
            any(
                item.get("old_ref") == "EVI-20260801-999#1"
                for item in report["stable_reference_mapping"]
            )
        )

    def test_historical_run_gets_a_current_fail_closed_workset_manifest_without_invented_context(self) -> None:
        output, _report, temporary = self._run_migration(
            {
                "运行记录/2026-08/RUN-20260801-001.md": (
                    "# 历史运行记录 RUN-20260801-001\n\n"
                    "当时没有保存阶段工作集。\n"
                )
            }
        )
        self.addCleanup(temporary.cleanup)

        manifest_path = output / "运行记录/2026-08/RUN-20260801-001-historical-unknown-工作集清单.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["id"], "RUN-20260801-001-WORKSET-HISTORICAL-UNKNOWN")
        self.assertEqual(manifest["status"], "partial")
        self.assertEqual(manifest["workflow"], "historical")
        self.assertEqual(manifest["stage"], "unknown")
        self.assertEqual(manifest["strategy_version"], "unknown")
        self.assertEqual(
            manifest["migration_missing_fields"],
            [
                "task_contract",
                "stable_references",
                "relations",
                "verification",
                "coverage",
            ],
        )
        self.assertEqual(manifest["stable_references"], [])
        self.assertEqual(manifest["relations"], [])
        self.assertEqual(manifest["relation_checks"]["status"], "not_run")
        self.assertEqual(manifest["relation_checks"]["total"], 0)
        self.assertEqual(manifest["relation_checks"]["resolved"], 0)
        self.assertEqual(manifest["relation_checks"]["blocking_gaps"], 0)
        self.assertEqual(manifest["verification"]["status"], "not_run")
        self.assertEqual(manifest["coverage"]["status"], "unknown")
        self.assertEqual(manifest["coverage"]["required_total"], 0)
        self.assertEqual(manifest["coverage"]["required_covered"], 0)
        self.assertEqual(manifest["coverage"]["required_missing"], 0)
        self.assertEqual(manifest["coverage"]["coverage_ratio"], 0.0)
        self.assertTrue(manifest["coverage"]["blocking"])
        self.assertEqual(manifest["coverage"]["blocking_gap_count"], 1)
        self.assertEqual(manifest["coverage"]["requirements"], [])
        self.assertTrue(manifest["coverage"]["semantic_candidates_do_not_count"])
        self.assertTrue(manifest["gaps"][0]["blocking"])
        self.assertEqual(manifest["quality"]["assembled_units"], 0)
        self.assertEqual(manifest["quality"]["source_payload_bytes_in_workset"], 0)
        self.assertEqual(manifest["quality"]["hydrate_units"], 0)
        self.assertTrue(manifest["quality"]["projection_degraded"])
        self.assertEqual(manifest["quality"]["context_proxy"]["raw_tool_payload_characters_entered"], "unknown")
        self.assertFalse(manifest["quality"]["token_replay_available"])

    def test_current_run_without_a_recorded_workset_gets_a_gap_not_a_fabricated_sidecar(self) -> None:
        output, report, temporary = self._run_migration(
            {
                "运行记录/2026-08/RUN-20260801-004.md": (
                    "---\n"
                    'schema_version: "a-share-workspace-v3"\n'
                    'artifact_type: "run_record"\n'
                    'id: "RUN-20260801-004"\n'
                    'status: "partial"\n'
                    'created_at: "2026-08-01T09:00:00+08:00"\n'
                    'information_cutoff: "2026-08-01T10:00:00+08:00"\n'
                    'workflows: "investigate"\n'
                    "---\n"
                    "# 当前运行记录\n"
                )
            }
        )
        self.addCleanup(temporary.cleanup)

        self.assertEqual(list((output / "运行记录").rglob("*工作集清单.json")), [])
        self.assertEqual(
            report["run_workset_gaps"],
            [
                {
                    "run_id": "RUN-20260801-004",
                    "run_path": "运行记录/2026-08/RUN-20260801-004.md",
                    "reason": "current_run_missing_recorded_workset",
                    "blocking": True,
                }
            ],
        )
        self.assertEqual(report["summary"]["run_workset_gaps"], 1)

    def test_noncurrent_run_outside_the_narrow_unknown_time_exception_becomes_historical_audit(self) -> None:
        output, report, temporary = self._run_migration(
            {
                "运行记录/2026-08/RUN-20260801-005.md": (
                    "---\n"
                    'artifact_type: "run_record"\n'
                    'id: "RUN-20260801-005"\n'
                    'status: "partial"\n'
                    'created_at: "2026-08-01T09:00:00+08:00"\n'
                    'information_cutoff: "2026-08-01T10:00:00+08:00"\n'
                    'workflows: "investigate"\n'
                    "---\n"
                    "# 旧运行记录\n"
                )
            }
        )
        self.addCleanup(temporary.cleanup)

        mapping = next(
            item
            for item in report["mappings"]
            if item["old_path"] == "运行记录/2026-08/RUN-20260801-005.md"
        )
        self.assertRegex(
            mapping["new_path"],
            r"^报告/迁移审计/运行记录/迁移-RUN-20260801-005(?:-\d{3})?\.md$",
        )
        self.assertFalse((output / "运行记录/2026-08/RUN-20260801-005.md").exists())
        audit = (output / mapping["new_path"]).read_text(encoding="utf-8")
        self.assertIn('artifact_type: "historical_record"', audit)
        self.assertIn('record_kind: "run_record_migration_unresolved"', audit)
        self.assertIn('authority: "migration_audit"', audit)
        self.assertEqual(list((output / "运行记录").rglob("*工作集清单.json")), [])

    def test_legacy_workset_sidecar_demotes_its_run_to_a_current_fail_closed_audit_pair(self) -> None:
        legacy_sidecar = {
            "schema_version": "a-share-workspace-v3",
            "artifact_type": "workset_manifest",
            "id": "RUN-20260801-006-WORKSET",
            "status": "partial",
            "run_id": "RUN-20260801-006",
            "created_at": "当时未记录",
            "information_cutoff": "当时未记录",
            "stage": "当时未记录",
            "task_contract": {"contract_id": "当时未记录", "version": "当时未记录"},
            "stable_references": [],
            "coverage": {"status": "当时未记录"},
            "gaps": [{"reason": "historical workset was not recorded"}],
            "quality": {"status": "当时未记录", "token_replay_available": False},
        }
        output, report, temporary = self._run_migration(
            {
                "运行记录/2026-08/RUN-20260801-006.md": (
                    "---\n"
                    'schema_version: "a-share-workspace-v3"\n'
                    'artifact_type: "run_record"\n'
                    'id: "RUN-20260801-006"\n'
                    'status: "completed"\n'
                    'created_at: "2026-08-01T09:00:00+08:00"\n'
                    'information_cutoff: "2026-08-01T10:00:00+08:00"\n'
                    'workflows: "investigate"\n'
                    "---\n"
                    "# 旧运行记录\n"
                ),
                "运行记录/2026-08/RUN-20260801-006-工作集清单.json": (
                    json.dumps(legacy_sidecar, ensure_ascii=False) + "\n"
                ),
            }
        )
        self.addCleanup(temporary.cleanup)

        run_text = (output / "运行记录/2026-08/RUN-20260801-006.md").read_text(encoding="utf-8")
        self.assertIn('status: "partial"', run_text)
        self.assertIn('workflows: "historical"', run_text)
        self.assertIn("recorded_workset", run_text)
        self.assertFalse((output / "运行记录/2026-08/RUN-20260801-006-工作集清单.json").exists())
        workset_path = (
            output
            / "运行记录/2026-08/RUN-20260801-006-historical-unknown-工作集清单.json"
        )
        workset = json.loads(workset_path.read_text(encoding="utf-8"))
        self.assertEqual(workset["id"], "RUN-20260801-006-WORKSET-HISTORICAL-UNKNOWN")
        self.assertEqual(workset["workflow"], "historical")
        self.assertEqual(workset["verification"]["status"], "not_run")
        self.assertTrue(workset["coverage"]["blocking"])
        self.assertEqual(workset["coverage"]["required_total"], 0)
        self.assertEqual(report["legacy_workset_manifests"][0]["new_path"], workset_path.relative_to(output).as_posix())

    def test_historical_workset_retains_legacy_snapshot_without_making_it_hydratable(self) -> None:
        old_stable_references = ["atom:EVI-20260801-999#001", "atom:J20260801-999 v1"]
        old_relations = [
            {
                "from": "atom:J20260801-999 v1",
                "type": "supported_by",
                "to": "atom:EVI-20260801-999#001",
            }
        ]
        old_gaps = [{"reason": "legacy evidence was not frozen", "blocking": True}]
        old_coverage = {
            "required_total": 2,
            "required_covered": 1,
            "required_missing": 1,
            "coverage_ratio": 0.5,
        }
        legacy_sidecar = {
            "artifact_type": "workset_manifest",
            "run_id": "RUN-20260801-007",
            "stage": "analysis",
            "stable_references": old_stable_references,
            "relations": old_relations,
            "gaps": old_gaps,
            "coverage": old_coverage,
        }
        output, _report, temporary = self._run_migration(
            {
                "运行记录/2026-08/RUN-20260801-007.md": (
                    "---\n"
                    'schema_version: "a-share-workspace-v3"\n'
                    'artifact_type: "run_record"\n'
                    'id: "RUN-20260801-007"\n'
                    'status: "partial"\n'
                    'created_at: "当时未记录"\n'
                    'information_cutoff: "当时未记录"\n'
                    'workflows: "historical"\n'
                    'migration_missing_fields: ["created_at", "information_cutoff"]\n'
                    'migration_note: "旧工作集字段不完整。"\n'
                    "---\n# 旧运行记录\n"
                ),
                "运行记录/2026-08/RUN-20260801-007-工作集清单.json": (
                    json.dumps(legacy_sidecar, ensure_ascii=False) + "\n"
                ),
            }
        )
        self.addCleanup(temporary.cleanup)

        path = output / "运行记录/2026-08/RUN-20260801-007-historical-analysis-工作集清单.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["stable_references"], [])
        self.assertEqual(manifest["relations"], [])
        snapshot = manifest["legacy_snapshot"]
        self.assertEqual(snapshot["stable_references"], old_stable_references)
        self.assertEqual(snapshot["relations"], old_relations)
        self.assertEqual(snapshot["gaps"], old_gaps)
        self.assertEqual(snapshot["coverage"], old_coverage)
        self.assertIs(snapshot["hydrate_eligible"], False)
        canonical = json.dumps(
            {
                "stable_references": old_stable_references,
                "relations": old_relations,
                "gaps": old_gaps,
                "coverage": old_coverage,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        self.assertEqual(snapshot["canonical_sha256"], hashlib.sha256(canonical).hexdigest())

    def test_migration_installs_and_reports_the_executing_repository_runtime_surface(self) -> None:
        output, report, temporary = self._run_migration(
            {
                ".agents/skills/a-share/shared/schemas/artifacts.json": '{"stale": true}\n',
                "模板/工作集清单模板.md": "# 旧模板\n",
                "scripts/shadow_replay_workspace.py": "# old runtime\n",
                "docs/architecture.md": "# stale architecture\n",
                "docs/adr/0016-来源载荷外置并按需核验.md": "# stale ADR\n",
                "docs/adr/0001-纯文档双层架构.md": "# user-preserved old ADR\n",
            }
        )
        self.addCleanup(temporary.cleanup)

        for relative in (
            ".agents/skills/a-share/shared/schemas/artifacts.json",
            "模板/工作集清单模板.md",
            "scripts/shadow_replay_workspace.py",
            "docs/architecture.md",
            "docs/adr/0016-来源载荷外置并按需核验.md",
        ):
            self.assertEqual(
                (output / relative).read_bytes(),
                (REPO_ROOT / relative).read_bytes(),
            )
        surface = report["runtime_surface"]
        self.assertEqual(surface["schema_version"], "a-share-workspace-v3")
        self.assertRegex(surface["sha256"], r"^[0-9a-f]{64}$")
        self.assertGreater(len(surface["files"]), 0)
        surface_digest = hashlib.sha256()
        for item in sorted(surface["files"], key=lambda row: row["path"]):
            installed = output / item["path"]
            self.assertEqual(item["sha256"], hashlib.sha256(installed.read_bytes()).hexdigest())
            surface_digest.update(item["path"].encode("utf-8"))
            surface_digest.update(b"\0")
            surface_digest.update(item["sha256"].encode("ascii"))
            surface_digest.update(b"\n")
        self.assertEqual(surface["sha256"], surface_digest.hexdigest())
        replaced = {item["path"] for item in surface["replacements"]}
        self.assertIn(".agents/skills/a-share/shared/schemas/artifacts.json", replaced)
        self.assertIn("模板/工作集清单模板.md", replaced)
        self.assertIn("scripts/shadow_replay_workspace.py", replaced)
        self.assertIn("docs/architecture.md", replaced)
        self.assertIn("docs/adr/0016-来源载荷外置并按需核验.md", replaced)
        self.assertEqual(
            (output / "docs/adr/0001-纯文档双层架构.md").read_text(encoding="utf-8"),
            "# user-preserved old ADR\n",
        )

    def test_runtime_surface_preserves_unowned_custom_scripts(self) -> None:
        output, report, temporary = self._run_migration(
            {
                "scripts/custom_research_export.py": "# user-owned\nCUSTOM_SENTINEL = True\n",
                "scripts/shadow_replay_workspace.py": "# stale owned runtime\n",
            }
        )
        self.addCleanup(temporary.cleanup)

        custom = output / "scripts/custom_research_export.py"
        self.assertEqual(custom.read_text(encoding="utf-8"), "# user-owned\nCUSTOM_SENTINEL = True\n")
        self.assertEqual(
            (output / "scripts/shadow_replay_workspace.py").read_bytes(),
            (REPO_ROOT / "scripts/shadow_replay_workspace.py").read_bytes(),
        )
        self.assertNotIn(
            "scripts/custom_research_export.py",
            {item["path"] for item in report["runtime_surface"]["replacements"]},
        )
        self.assertNotIn("scripts", report["runtime_surface"]["installed_roots"])

    def test_migration_removes_legacy_claude_a_share_runtime_without_deleting_user_config(self) -> None:
        output, report, temporary = self._run_migration(
            {
                ".claude/skills/a-share/a-share-research/SKILL.md": "# stale duplicate runtime\n",
                ".claude/settings.json": '{"user_setting": true}\n',
            }
        )
        self.addCleanup(temporary.cleanup)

        self.assertFalse((output / ".claude/skills/a-share").exists())
        self.assertEqual(
            (output / ".claude/settings.json").read_text(encoding="utf-8"),
            '{"user_setting": true}\n',
        )
        removal = next(
            item
            for item in report["runtime_surface"]["replacements"]
            if item["path"] == ".claude/skills/a-share"
        )
        self.assertRegex(removal["before_sha256"], r"^[0-9a-f]{64}$")
        self.assertIsNone(removal["after_sha256"])
        self.assertEqual(removal["action"], "removed_legacy_compatibility_root")

    def test_known_stages_of_one_run_get_distinct_workset_manifest_files(self) -> None:
        def run_record(workflow: str, stage: str) -> str:
            return (
                "---\n"
                'schema_version: "a-share-workspace-v3"\n'
                'artifact_type: "run_record"\n'
                'id: "RUN-20260801-002"\n'
                'status: "partial"\n'
                'created_at: "当时未记录"\n'
                'information_cutoff: "当时未记录"\n'
                'workflows: "historical"\n'
                f'workflow: "{workflow}"\n'
                f'stage: "{stage}"\n'
                'migration_missing_fields: ["created_at", "information_cutoff", "workflows"]\n'
                'migration_note: "历史阶段没有保存工作集。"\n'
                "---\n"
                f"# {stage} 阶段运行记录\n"
            )

        output, _report, temporary = self._run_migration(
            {
                "运行记录/2026-08/RUN-20260801-002-research.md": run_record("investigate", "research"),
                "运行记录/2026-08/RUN-20260801-002-analysis.md": run_record("analyze", "analysis"),
            }
        )
        self.addCleanup(temporary.cleanup)

        research = output / "运行记录/2026-08/RUN-20260801-002-historical-research-工作集清单.json"
        analysis = output / "运行记录/2026-08/RUN-20260801-002-historical-analysis-工作集清单.json"
        self.assertTrue(research.is_file())
        self.assertTrue(analysis.is_file())
        self.assertNotEqual(
            json.loads(research.read_text(encoding="utf-8"))["id"],
            json.loads(analysis.read_text(encoding="utf-8"))["id"],
        )

    def test_existing_stage_workset_manifest_is_never_overwritten_by_migration(self) -> None:
        existing = {
            "schema_version": "a-share-workspace-v3",
            "artifact_type": "workset_manifest",
            "id": "RUN-20260801-003-WORKSET-INVESTIGATE-RESEARCH",
            "status": "partial",
            "run_id": "RUN-20260801-003",
            "workflow": "investigate",
            "stage": "research",
            "created_at": "2026-08-01T09:00:00+08:00",
            "information_cutoff": "2026-08-01T10:00:00+08:00",
            "task_contract": {"contract_id": "investigate.stock", "version": "1.0.0"},
            "strategy_version": "unknown",
            "projection": {"projection_degraded": False},
            "stable_references": [],
            "relations": [],
            "relation_checks": {"total": 0, "resolved": 0, "blocking_gaps": 0},
            "verification": {
                "status": "not_run",
                "required_unit_ids": [],
                "verified_unit_ids": [],
                "missing_references": [],
            },
            "coverage": {
                "required_total": 0,
                "required_covered": 0,
                "required_missing": 0,
                "coverage_ratio": 0.0,
                "blocking": True,
                "blocking_gap_count": 1,
                "requirements": [],
                "semantic_candidates_do_not_count": True,
            },
            "gaps": [{"reason": "not_instantiated", "blocking": True}],
            "quality": {
                "assembled_units": 0,
                "source_payload_bytes_in_workset": 0,
                "hydrate_units": 0,
                "projection_degraded": False,
                "context_proxy": {
                    "stable_reference_characters": 0,
                    "selected_source_characters": 0,
                    "indexed_source_characters": 0,
                    "raw_tool_payload_characters_entered": 0,
                    "token_replay_available": False,
                },
            },
            "sentinel": "preserve-existing-audit",
        }
        output, _report, temporary = self._run_migration(
            {
                "运行记录/2026-08/RUN-20260801-003.md": (
                    "---\n"
                    'schema_version: "a-share-workspace-v3"\n'
                    'artifact_type: "run_record"\n'
                    'id: "RUN-20260801-003"\n'
                    'status: "partial"\n'
                    'created_at: "2026-08-01T09:00:00+08:00"\n'
                    'information_cutoff: "2026-08-01T10:00:00+08:00"\n'
                    'workflows: "investigate"\n'
                    'workflow: "investigate"\n'
                    'stage: "research"\n'
                    "---\n# 历史运行\n"
                ),
                "运行记录/2026-08/RUN-20260801-003-investigate-research-工作集清单.json": (
                    json.dumps(existing, ensure_ascii=False) + "\n"
                ),
            }
        )
        self.addCleanup(temporary.cleanup)

        preserved = json.loads(
            (output / "运行记录/2026-08/RUN-20260801-003-investigate-research-工作集清单.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(preserved, existing)

    def test_legacy_strategy_filename_is_split_into_canonical_id_version_and_reference_mapping(self) -> None:
        output, report, temporary = self._run_migration(
            {
                "策略库/STR-ALPHA-v0.1.0.md": (
                    "# STR-ALPHA-v0.1.0\n\n"
                    "历史策略正文保持原样。\n"
                )
            }
        )
        self.addCleanup(temporary.cleanup)

        strategy_path = output / "策略库/STR-ALPHA-v0.1.0.md"
        text = strategy_path.read_text(encoding="utf-8")
        self.assertIn('artifact_type: "strategy_version"', text)
        self.assertIn('id: "STR-ALPHA"', text)
        self.assertIn('version: "0.1.0"', text)
        reference = next(
            item
            for item in report["stable_reference_mapping"]
            if item.get("old_ref") == "STR-ALPHA@0.1.0"
        )
        self.assertEqual(reference["new_ref"], "STR-ALPHA@0.1.0")
        self.assertEqual(reference["old_source_locator"]["path"], "策略库/STR-ALPHA-v0.1.0.md")
        self.assertEqual(reference["new_source_locator"]["path"], "策略库/STR-ALPHA-v0.1.0.md")
        self.assertEqual(reference["mapping_status"], "mapped")

    def test_strategy_identity_proven_only_by_filename_uses_a_path_locator(self) -> None:
        output, report, temporary = self._run_migration(
            {
                "策略库/STR-FILENAME-v0.1.0.md": (
                    "# 历史策略参数\n\n"
                    "正文没有重复记录旧策略 ID。\n"
                )
            }
        )
        self.addCleanup(temporary.cleanup)

        text = (output / "策略库/STR-FILENAME-v0.1.0.md").read_text(encoding="utf-8")
        self.assertIn('id: "STR-FILENAME"', text)
        self.assertIn('version: "0.1.0"', text)
        reference = next(
            item
            for item in report["stable_reference_mapping"]
            if item.get("old_ref") == "STR-FILENAME@0.1.0"
        )
        self.assertEqual(
            reference["old_source_locator"],
            {
                "path": "策略库/STR-FILENAME-v0.1.0.md",
                "locator_kind": "path_identity",
            },
        )
        self.assertEqual(reference["new_source_locator"]["path"], "策略库/STR-FILENAME-v0.1.0.md")

    def test_strategy_without_a_provable_version_becomes_non_authoritative_migration_audit(self) -> None:
        output, report, temporary = self._run_migration(
            {
                "策略库/STR-ALPHA.md": (
                    "# STR-ALPHA\n\n"
                    "旧文件没有记录版本，不能补猜。\n"
                )
            }
        )
        self.addCleanup(temporary.cleanup)

        migration = next(
            item for item in report["mappings"] if item.get("old_path") == "策略库/STR-ALPHA.md"
        )
        self.assertRegex(
            migration["new_path"],
            r"^报告/迁移审计/策略库/迁移-STR-ALPHA(?:-\d{3})?\.md$",
        )
        self.assertFalse((output / "策略库/STR-ALPHA.md").exists())
        text = (output / migration["new_path"]).read_text(encoding="utf-8")
        self.assertIn('artifact_type: "historical_record"', text)
        self.assertIn('status: "historical"', text)
        self.assertIn('record_kind: "strategy_version_migration_unresolved"', text)
        self.assertIn('authority: "migration_audit"', text)
        self.assertIn("旧文件没有记录版本，不能补猜。", text)
        self.assertFalse(
            any(item.get("old_ref", "").startswith("STR-ALPHA@") for item in report["stable_reference_mapping"])
        )

    def test_conflicting_strategy_version_signals_remain_non_authoritative(self) -> None:
        output, report, temporary = self._run_migration(
            {
                "策略库/STR-ALPHA-v0.1.0.md": (
                    "---\n"
                    'id: "STR-ALPHA-v0.1.0"\n'
                    'version: "0.2.0"\n'
                    "---\n"
                    "# STR-ALPHA-v0.1.0\n\n"
                    "冲突版本只能保留审计。\n"
                )
            }
        )
        self.addCleanup(temporary.cleanup)

        migration = next(
            item
            for item in report["mappings"]
            if item.get("old_path") == "策略库/STR-ALPHA-v0.1.0.md"
        )
        text = (output / migration["new_path"]).read_text(encoding="utf-8")
        self.assertIn('artifact_type: "historical_record"', text)
        self.assertIn('authority: "migration_audit"', text)
        self.assertIn("无法从文件名、正文或元数据证明", text)

    def test_non_initial_strategy_without_a_resolvable_previous_version_is_non_authoritative(self) -> None:
        output, report, temporary = self._run_migration(
            {
                "策略库/STR-ALPHA-v0.2.0.md": (
                    "# STR-ALPHA-v0.2.0\n\n"
                    "历史快照没有记录前序版本，不能补造版本链。\n"
                )
            }
        )
        self.addCleanup(temporary.cleanup)

        migration = next(
            item
            for item in report["mappings"]
            if item.get("old_path") == "策略库/STR-ALPHA-v0.2.0.md"
        )
        text = (output / migration["new_path"]).read_text(encoding="utf-8")
        self.assertIn('artifact_type: "historical_record"', text)
        self.assertIn('record_kind: "strategy_version_migration_unresolved"', text)
        self.assertIn('authority: "migration_audit"', text)
        self.assertIn("previous_version", text)
        reference = next(
            item
            for item in report["stable_reference_mapping"]
            if item.get("old_ref") == "STR-ALPHA@0.2.0"
        )
        self.assertRegex(reference["new_ref"], r"^HIST-STRATEGY-[0-9a-f]{16}$")
        self.assertEqual(reference["mapping_status"], "mapped_to_historical_audit")
        self.assertEqual(reference["mapping_kind"], "strategy_version_migration_unresolved")

    def test_all_historical_records_are_explicit_non_authoritative_migration_audits(self) -> None:
        output, _report, temporary = self._run_migration(
            {
                "报告/分析/2026-08/旧报告.md": "# 旧报告\n\n历史正文。\n",
                "判断日志/2026-08.md": (
                    "# 判断日志\n\n## 历史批次\n\n- **J0803-01 v1**｜历史判断。\n"
                ),
            }
        )
        self.addCleanup(temporary.cleanup)

        historical_files = [
            output / "报告/分析/2026-08/旧报告.md",
            output / "判断日志/2026-08.md",
            output / "判断日志/迁移-2026-08.md",
        ]
        for path in historical_files:
            text = path.read_text(encoding="utf-8")
            self.assertIn('artifact_type: "historical_record"', text)
            self.assertIn('authority: "migration_audit"', text)
            self.assertIn("migration_missing_fields:", text)
            self.assertIn("migration_note:", text)

    def test_strategy_references_inside_moved_reports_are_mapped_in_canonical_form(self) -> None:
        output, report, temporary = self._run_migration(
            {
                "分析报告/2026-08/策略复盘.md": (
                    "# 策略复盘\n\n"
                    "当时使用 STR-ALPHA-v0.1.0 形成判断。\n"
                )
            }
        )
        self.addCleanup(temporary.cleanup)

        reference = next(
            item
            for item in report["stable_reference_mapping"]
            if item.get("old_ref") == "STR-ALPHA@0.1.0"
        )
        self.assertEqual(reference["new_ref"], "STR-ALPHA@0.1.0")
        self.assertEqual(reference["old_source_locator"]["path"], "分析报告/2026-08/策略复盘.md")
        self.assertEqual(reference["new_source_locator"]["path"], "报告/分析/2026-08/策略复盘.md")
        line = (output / reference["new_source_locator"]["path"]).read_text(encoding="utf-8").splitlines()[
            reference["new_source_locator"]["start_line"] - 1
        ]
        self.assertIn("STR-ALPHA-v0.1.0", line)


class ShadowReplayTests(unittest.TestCase):
    _TEST_SESSION_ID = "shadow-session-test-001"
    _RUNTIME_PATHS = RUNTIME_PATHS

    @staticmethod
    def _sha256(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    @classmethod
    def _file_manifest(cls, root: Path, paths: tuple[Path, ...] | None = None) -> list[dict[str, str]]:
        candidates: list[Path] = []
        for relative in paths or (Path("."),):
            source = root / relative
            candidates.extend([source] if source.is_file() else sorted(source.rglob("*")))
        return [
            {"path": path.relative_to(root).as_posix(), "sha256": cls._sha256(path)}
            for path in sorted(set(candidates))
            if path.is_file()
            and not any(
                part in {".git", ".context", "__pycache__", ".DS_Store"}
                for part in path.relative_to(root).parts
            )
        ]

    @staticmethod
    def _manifest_sha256(files: list[dict[str, str]]) -> str:
        digest = hashlib.sha256()
        for item in sorted(files, key=lambda value: value["path"]):
            digest.update(item["path"].encode("utf-8"))
            digest.update(b"\0")
            digest.update(item["sha256"].encode("ascii"))
            digest.update(b"\n")
        return digest.hexdigest()

    @classmethod
    def _install_runtime(cls, workspace: Path) -> tuple[list[dict[str, str]], str]:
        for relative in cls._RUNTIME_PATHS:
            source = REPO_ROOT / relative
            destination = workspace / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            if source.is_dir():
                shutil.copytree(
                    source,
                    destination,
                    dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("__pycache__", ".DS_Store"),
                )
            else:
                shutil.copy2(source, destination)
        files = cls._file_manifest(REPO_ROOT, cls._RUNTIME_PATHS)
        return files, cls._manifest_sha256(files)

    @classmethod
    def _run_bound_shadow(
        cls,
        base: Path,
        workspace: Path,
        scenario_path: Path,
        report_path: Path,
    ) -> subprocess.CompletedProcess[str]:
        legacy = json.loads(scenario_path.read_text(encoding="utf-8"))
        runtime_files, runtime_hash = cls._install_runtime(workspace)
        migration_id = "MIG-SHADOW-TEST-001"
        old_workspace = base / "frozen-old-workspace"
        old_workspace.mkdir(exist_ok=True)
        semantic_units: list[dict[str, object]] = []
        execution_artifacts: list[dict[str, object]] = []
        for scenario in legacy.get("scenarios", []):
            expectations = scenario.get("expectations") if isinstance(scenario.get("expectations"), dict) else {}
            frozen = expectations.pop("semantic_units", [])
            selected_unit_ids: list[str] = []
            for index, item in enumerate(frozen, start=1):
                old = item["old"]
                selected_unit_ids.append(item["unit_id"])
                relative = f"baseline/{scenario['id']}-{index}.md"
                source = old_workspace / relative
                source.parent.mkdir(parents=True, exist_ok=True)
                relation_lines: list[str] = []
                for relation in old.get("relations", []):
                    relation_type = relation.get("type")
                    target = relation.get("to")
                    if relation_type in {"supported_by", "historically_referenced_evidence"}:
                        label = (
                            "迁移证据审计"
                            if relation_type == "historically_referenced_evidence"
                            else "证据包 / 原子证据项"
                        )
                        relation_lines.append(f"- **{label}**：{target}\n")
                    elif relation_type in {
                        "upstream_judgment",
                        "constrained_by",
                        "exception_to",
                        "supported_by_judgment",
                    }:
                        marker = {
                            "constrained_by": "约束",
                            "exception_to": "例外",
                            "supported_by_judgment": "支持",
                        }.get(relation_type, "上游")
                        relation_lines.append(f"- **上游判断**：{target}；{marker}\n")
                    elif relation_type == "uses_strategy":
                        relation_lines.append(f"- **策略版本**：{target}\n")
                source_text = (
                    f"## {item['unit_id']}\n"
                    f"- **事实陈述**：{old['proposition']}\n"
                    f"- **状态**：{old['status']}\n"
                    f"- **信息快照**：{old['information_cutoff']}\n"
                    + "".join(relation_lines)
                )
                source.write_text(source_text, encoding="utf-8")
                fields = {
                    "proposition": old["proposition"],
                    "information_cutoff": old["information_cutoff"],
                    "status": old["status"],
                    "relations": old.get("relations", []),
                }
                semantic_units.append(
                    {
                        "scenario_id": scenario["id"],
                        "unit_id": item["unit_id"],
                        "source_locator": {
                            "path": relative,
                            "start_line": 1,
                            "end_line": len(source_text.rstrip("\n").splitlines()),
                            "source_sha256": cls._sha256(source),
                            "content_sha256": hashlib.sha256(source_text.rstrip("\n").encode("utf-8")).hexdigest(),
                        },
                        "fields": fields,
                        "fields_sha256": hashlib.sha256(
                            json.dumps(fields, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
                        ).hexdigest(),
                    }
                )
            execution_fields = {
                "workflow": scenario.get("workflow"),
                "stage": scenario.get("stage"),
                "contract": scenario.get("contract"),
                "selector": {
                    "object_type": scenario.get("object_type"),
                    "objects": scenario.get("objects", []),
                    "handoff": scenario.get("handoff", {}),
                },
                "required_unit_ids": list(selected_unit_ids),
                "selected_unit_ids": list(selected_unit_ids),
            }
            execution_relative = f"baseline/{scenario['id']}-execution.json"
            execution_source = old_workspace / execution_relative
            execution_source.parent.mkdir(parents=True, exist_ok=True)
            execution_encoded = json.dumps(
                execution_fields,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            execution_source.write_text(execution_encoded + "\n", encoding="utf-8")
            execution_artifacts.append(
                {
                    "scenario_id": scenario["id"],
                    "source_locator": {
                        "path": execution_relative,
                        "start_line": 1,
                        "end_line": 1,
                        "source_sha256": cls._sha256(execution_source),
                        "content_sha256": hashlib.sha256(
                            execution_encoded.encode("utf-8")
                        ).hexdigest(),
                    },
                    "fields": execution_fields,
                    "fields_sha256": hashlib.sha256(
                        json.dumps(
                            execution_fields,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        ).encode("utf-8")
                    ).hexdigest(),
                }
            )
        marker = old_workspace / "README.md"
        marker.write_text("# Frozen old workspace\n", encoding="utf-8")
        input_files = cls._file_manifest(old_workspace)
        input_hash = cls._manifest_sha256(input_files)
        baseline_path = base / "frozen-old-baseline.json"
        baseline_path.write_text(
            json.dumps(
                {
                    "schema_version": "a-share-shadow-baseline-v1",
                    "session_id": cls._TEST_SESSION_ID,
                    "migration_id": migration_id,
                    "captured_at": "2026-08-01T00:00:00+08:00",
                    "workspace_root": str(old_workspace),
                    "workspace_sha256": input_hash,
                    "files": input_files,
                    "semantic_units": semantic_units,
                    "execution_artifacts": execution_artifacts,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        migration_report = base / "migration-report.json"
        migration_report.write_text(
            json.dumps(
                {
                    "schema_version": "a-share-workspace-v3",
                    "migration_id": migration_id,
                    "status": "structural_migration_completed",
                    "migration_status": "completed",
                    "input_root": str(old_workspace),
                    "input_snapshot": {"sha256": input_hash, "files": input_files},
                    "output_root": str(workspace),
                    "runtime_surface": {
                        "schema_version": "a-share-workspace-v3",
                        "sha256": runtime_hash,
                        "installed_roots": [path.as_posix() for path in cls._RUNTIME_PATHS],
                        "files": runtime_files,
                    },
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        baseline_metrics = legacy.pop("baseline", {})
        candidate_metrics = legacy.pop("candidate_observation", {})
        metric_sources = {
            "raw_tool_payload_characters": ("characters", "tool_payload_counter"),
            "main_context_characters": ("characters", "context_character_counter"),
            "main_context_peak_tokens": ("tokens", "model_context_telemetry"),
        }
        events: list[dict[str, object]] = []
        for phase, metrics in (("baseline", baseline_metrics), ("candidate", candidate_metrics)):
            if phase == "candidate" and "main_context_characters" not in metrics and baseline_metrics.get("main_context_characters") and metrics:
                metrics["main_context_characters"] = int(baseline_metrics["main_context_characters"] * 0.4)
            for metric, (unit, source_kind) in metric_sources.items():
                if metric not in metrics:
                    continue
                events.append(
                    {
                        "event_id": f"OBS-{len(events) + 1:03d}",
                        "session_id": cls._TEST_SESSION_ID,
                        "phase": phase,
                        "metric": metric,
                        "value": metrics[metric],
                        "unit": unit,
                        "source_kind": source_kind,
                        "observation_status": "observed",
                        "observed_at": "2026-08-01T12:00:00+08:00",
                    }
                )
        trace_id = "TRACE-SHADOW-TEST-001"
        raw_lines = [
            json.dumps(
                {
                    "event_id": event["event_id"],
                    "trace_id": trace_id,
                    "session_id": event["session_id"],
                    "migration_id": migration_id,
                    "phase": event["phase"],
                    "metric": event["metric"],
                    "value": event["value"],
                    "unit": event["unit"],
                    "source_kind": event["source_kind"],
                    "observation_status": event["observation_status"],
                    "observed_at": event["observed_at"],
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            for event in events
        ]
        telemetry_export = base / "measurement-telemetry.jsonl"
        telemetry_export.write_text(
            "\n".join(raw_lines) + ("\n" if raw_lines else ""), encoding="utf-8"
        )
        telemetry_hash = cls._sha256(telemetry_export)
        for line_number, (event, raw_line) in enumerate(zip(events, raw_lines), start=1):
            event["source_locator"] = {
                "path": telemetry_export.name,
                "start_line": line_number,
                "end_line": line_number,
                "source_sha256": telemetry_hash,
                "content_sha256": hashlib.sha256(raw_line.encode("utf-8")).hexdigest(),
            }
        trace_path = base / "measurement-trace.json"
        trace_path.write_text(
            json.dumps(
                {
                    "schema_version": "a-share-shadow-measurement-trace-v1",
                    "trace_id": trace_id,
                    "session_id": cls._TEST_SESSION_ID,
                    "migration_id": migration_id,
                    "observed_at": "2026-08-01T12:00:00+08:00",
                    "events": events,
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        workspace_files = cls._file_manifest(workspace)
        legacy.update(
            {
                "schema_version": "a-share-shadow-replay-v2",
                "session_id": cls._TEST_SESSION_ID,
                "migration_id": migration_id,
                "bindings": {
                    "old_baseline": {"path": str(baseline_path), "sha256": cls._sha256(baseline_path), "session_id": cls._TEST_SESSION_ID},
                    "migration_report": {"path": str(migration_report), "sha256": cls._sha256(migration_report), "session_id": cls._TEST_SESSION_ID},
                    "new_workspace": {"path": str(workspace), "sha256": cls._manifest_sha256(workspace_files), "session_id": cls._TEST_SESSION_ID},
                    "measurement_trace": {"path": str(trace_path), "sha256": cls._sha256(trace_path), "session_id": cls._TEST_SESSION_ID},
                },
            }
        )
        scenario_path.write_text(json.dumps(legacy, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        return subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "scripts/shadow_replay_workspace.py"),
                "--workspace",
                str(workspace),
                "--scenarios",
                str(scenario_path),
                "--old-baseline",
                str(baseline_path),
                "--migration-report",
                str(migration_report),
                "--measurement-trace",
                str(trace_path),
                "--output",
                str(report_path),
            ],
            check=False,
            capture_output=True,
            text=True,
        )

    @staticmethod
    def _write_scan_evidence(workspace: Path) -> None:
        write_evidence(
            workspace,
            "EVI-20260801-001",
            [
                evidence_item(
                    "EVI-20260801-001#1",
                    fact="市场状态已核验。",
                    object_name="market:A股",
                    field="market_state",
                    cutoff="2026-08-01T09:00:00+08:00",
                ),
                evidence_item(
                    "EVI-20260801-001#2",
                    fact="热度确认已核验。",
                    object_name="market:A股",
                    field="heat_confirmation",
                    cutoff="2026-08-01T09:00:00+08:00",
                    role="confirmation",
                ),
                evidence_item(
                    "EVI-20260801-001#3",
                    fact="市场结构确认项已核验。",
                    object_name="market:A股",
                    field="market_state",
                    cutoff="2026-08-01T09:00:00+08:00",
                    role="confirmation",
                ),
            ],
            cutoff="2026-08-01T09:00:00+08:00",
            status="confirmed",
            objects="market:A股",
        )

    @staticmethod
    def _write_risk_probe_evidence(workspace: Path) -> None:
        cutoff = "2026-08-01T09:00:00+08:00"
        rows = (
            ("1", "反向证据已核验但未触发否决。", "counterevidence", "已确认", "veto", cutoff, None),
            ("2", "来源之间存在口径冲突。", "conflict_probe", "冲突", "veto", cutoff, None),
            ("3", "该事实已被正式否证。", "denial_probe", "已否证", "veto", cutoff, None),
            ("4", "该行情事实已经超过复核时点。", "expiry_probe", "已确认", "primary", cutoff, "2026-08-01T09:30:00+08:00"),
            ("5", "该信息位于冻结快照之后。", "future_probe", "已确认", "primary", "2026-08-01T11:00:00+08:00", None),
            ("6", "对象事实已在冻结快照内核验。", "verified_fact", "已确认", "primary", cutoff, None),
        )
        write_evidence(
            workspace,
            "EVI-20260801-002",
            [
                evidence_item(
                    f"EVI-20260801-002#{number}",
                    fact=fact,
                    object_name="stock:测试股份",
                    field=field,
                    status=status,
                    role=role,
                    cutoff=item_cutoff,
                    valid_until=valid_until,
                )
                for number, fact, field, status, role, item_cutoff, valid_until in rows
            ],
            cutoff=cutoff,
            status="confirmed",
            objects="stock:测试股份",
        )
        write_dossier(
            workspace,
            [
                dossier_field(
                    "current-summary",
                    value="当前对象摘要已核验。",
                    verified_at=cutoff,
                    source_refs="EVI-20260801-002#6",
                )
            ],
            dossier_id="DOS-STOCK-TEST",
            object_name="stock:测试股份",
            cutoff=cutoff,
        )
        write_strategy(
            workspace,
            "STR-SHADOW-TEST",
            version="0.1.0",
            status="trial",
            cutoff=cutoff,
        )
        write_judgments(
            workspace,
            [
                judgment_entry(
                    "J20260801-001 v1",
                    proposition="测试股份当前判断链。",
                    research_status="等待确认",
                    object_name="stock:测试股份",
                    cutoff="2026-08-01T09:30:00+08:00",
                    deadline="2026-08-02T15:00:00+08:00",
                )
            ],
            cutoff="2026-08-01T09:30:00+08:00",
        )

    @staticmethod
    def _write_cross_workflow_evidence(workspace: Path) -> None:
        evidence = workspace / "证据包/2026-08/EVI-20260801-003.md"
        evidence.parent.mkdir(parents=True, exist_ok=True)
        evidence.write_text(
            "---\n"
            'schema_version: "a-share-workspace-v3"\n'
            'artifact_type: "evidence_package"\n'
            'id: "EVI-20260801-003"\n'
            'status: "confirmed"\n'
            'information_cutoff: "2026-08-01T09:00:00+08:00"\n'
            'created_at: "2026-08-01T09:00:00+08:00"\n'
            "objects: []\n"
            "---\n"
            "# 跨流程冻结证据\n\n"
            "## EVI-20260801-003#1\n\n"
            "- **事实陈述**：个股业务兑现已由原始来源核验。\n"
            "- **关联对象/档案字段**：stock:测试股份 / business\n"
            "- **状态**：已确认\n"
            "- **信息快照**：2026-08-01T09:00:00+08:00\n"
            "- **证据角色**：primary\n\n"
            "## EVI-20260801-003#2\n\n"
            "- **事实陈述**：原判断快照证据已冻结。\n"
            "- **关联对象/档案字段**：stock:测试股份 / original_snapshot\n"
            "- **状态**：已确认\n"
            "- **信息快照**：2026-08-01T09:00:00+08:00\n"
            "- **证据角色**：primary\n\n"
            "## EVI-20260801-003#3\n\n"
            "- **事实陈述**：判断窗口结果数据已核验。\n"
            "- **关联对象/档案字段**：stock:测试股份 / outcome\n"
            "- **状态**：已确认\n"
            "- **信息快照**：2026-08-01T15:30:00+08:00\n"
            "- **证据角色**：confirmation\n\n"
            "## EVI-20260801-003#4\n\n"
            "- **事实陈述**：事件本体已由正式来源核验。\n"
            "- **关联对象/档案字段**：event:测试事件 / event_fact\n"
            "- **状态**：已确认\n"
            "- **信息快照**：2026-08-01T09:00:00+08:00\n"
            "- **证据角色**：primary\n\n"
            "## EVI-20260801-003#5\n\n"
            "- **事实陈述**：事件前市场预期已冻结。\n"
            "- **关联对象/档案字段**：event:测试事件 / prior_expectation\n"
            "- **状态**：已确认\n"
            "- **信息快照**：2026-08-01T09:00:00+08:00\n"
            "- **证据角色**：primary\n\n"
            "## EVI-20260801-003#6\n\n"
            "- **事实陈述**：事件后的价格接受度已核验。\n"
            "- **关联对象/档案字段**：event:测试事件 / price_acceptance\n"
            "- **状态**：已确认\n"
            "- **信息快照**：2026-08-01T09:30:00+08:00\n"
            "- **证据角色**：confirmation\n",
            encoding="utf-8",
        )
        judgment = workspace / "判断日志/2026-08.md"
        judgment.parent.mkdir(parents=True, exist_ok=True)
        judgment.write_text(
            "---\n"
            'schema_version: "a-share-workspace-v3"\n'
            'artifact_type: "judgment_log"\n'
            'id: "JLOG-202608"\n'
            'status: "active"\n'
            'created_at: "2026-08-01T10:00:00+08:00"\n'
            'information_cutoff: "2026-08-01T10:00:00+08:00"\n'
            'record_kind: "judgment_log"\n'
            'stage: "analysis"\n'
            'authority: "judgment_log"\n'
            'objects: ["stock:测试股份"]\n'
            "---\n"
            "# 冻结判断\n\n"
            "## J20260801-001 v1\n\n"
            "- **原子命题**：测试股份在窗口内保持相对强势。\n"
            "- **状态**：进行中\n"
            "- **信息快照**：2026-08-01T10:00:00+08:00\n",
            encoding="utf-8",
        )

    def test_incomplete_workflow_suite_writes_a_failed_report_and_returns_nonzero(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            workspace = base / "shadow-workspace"
            workspace.mkdir()
            (workspace / "CONTEXT.md").write_text("# 临时隔离工作区\n", encoding="utf-8")
            scenario_path = base / "scenarios.json"
            scenario_path.write_text(
                json.dumps(
                    {
                        "schema_version": "a-share-shadow-replay-v1",
                        "baseline": {
                            "raw_tool_payload_characters": 1000,
                            "main_context_peak_tokens": 200000,
                        },
                        "scenarios": [],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            report_path = base / "shadow-report.json"

            result = self._run_bound_shadow(base, workspace, scenario_path, report_path)

            self.assertEqual(result.returncode, 1, result.stderr)
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(report["status"], "failed")
            self.assertFalse(report["acceptance"]["complete"])
            coverage = report["checks"]["workflow_coverage"]
            self.assertFalse(coverage["passed"])
            self.assertEqual(
                set(coverage["missing"]),
                {"scan", "investigate", "analyze", "event", "review"},
            )

    def test_unobserved_raw_payload_metric_cannot_satisfy_the_proxy_gate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            workspace = base / "shadow-workspace"
            workspace.mkdir()
            scenario_path = base / "scenarios.json"
            scenario_path.write_text(
                json.dumps(
                    {
                        "schema_version": "a-share-shadow-replay-v1",
                        "baseline": {
                            "raw_tool_payload_characters": 100000,
                            "main_context_characters": 200000,
                            "main_context_peak_tokens": 200000,
                        },
                        "targets": {
                            "min_raw_tool_payload_reduction_ratio": 0.8,
                            "max_main_context_ratio": 0.52,
                            "max_main_context_peak_tokens": 100000,
                        },
                        "scenarios": [],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            report_path = base / "shadow-report.json"

            result = self._run_bound_shadow(base, workspace, scenario_path, report_path)

            self.assertEqual(result.returncode, 1, result.stderr)
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertFalse(report["checks"]["proxy_targets"]["passed"])
            self.assertIsNone(report["proxy_metrics"]["raw_tool_payload_characters_entered"])
            self.assertIn(
                {"phase": "candidate", "metric": "raw_tool_payload_characters"},
                report["checks"]["proxy_targets"]["missing_observations"],
            )

    def test_candidate_context_peak_above_declared_limit_fails_the_hard_gate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            workspace = base / "shadow-workspace"
            workspace.mkdir()
            scenario_path = base / "scenarios.json"
            scenario_path.write_text(
                json.dumps(
                    {
                        "schema_version": "a-share-shadow-replay-v1",
                        "baseline": {
                            "raw_tool_payload_characters": 100000,
                            "main_context_characters": 200000,
                            "main_context_peak_tokens": 200000,
                        },
                        "candidate_observation": {
                            "raw_tool_payload_characters": 1000,
                            "main_context_peak_tokens": 100001,
                        },
                        "targets": {
                            "min_raw_tool_payload_reduction_ratio": 0.8,
                            "max_main_context_ratio": 0.52,
                            "max_main_context_peak_tokens": 100000,
                        },
                        "scenarios": [],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            report_path = base / "shadow-report.json"

            result = self._run_bound_shadow(base, workspace, scenario_path, report_path)

            self.assertEqual(result.returncode, 1, result.stderr)
            report = json.loads(report_path.read_text(encoding="utf-8"))
            gate = report["checks"]["context_peak_tokens"]
            self.assertFalse(gate["passed"])
            self.assertEqual(gate["observed_peak_tokens"], 100001)
            self.assertEqual(gate["maximum_peak_tokens"], 100000)

    def test_every_scenario_must_bind_to_a_repository_versioned_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            workspace = base / "shadow-workspace"
            workspace.mkdir()
            (workspace / "CONTEXT.md").write_text("# 临时隔离工作区\n", encoding="utf-8")
            scenarios = [
                {
                    "id": "scan",
                    "case_type": "scan",
                    "workflow": "scan",
                    "stage": "scan",
                    "contract": "scan-v1.json",
                },
                {
                    "id": "investigate",
                    "case_type": "investigate",
                    "workflow": "investigate",
                    "stage": "research",
                    "contract": "investigate-stock-v1.json",
                    "object_type": "stock",
                },
                {
                    "id": "analyze",
                    "case_type": "analyze",
                    "workflow": "analyze",
                    "stage": "analysis",
                    "contract": "analyze-v1.json",
                },
                {
                    "id": "event",
                    "case_type": "event",
                    "workflow": "investigate",
                    "stage": "research",
                    "contract": "does-not-exist.json",
                    "object_type": "event",
                },
                {
                    "id": "review",
                    "case_type": "review",
                    "workflow": "review",
                    "stage": "review",
                    "contract": "review-v1.json",
                },
            ]
            scenario_path = base / "scenarios.json"
            scenario_path.write_text(
                json.dumps(
                    {
                        "schema_version": "a-share-shadow-replay-v1",
                        "baseline": {
                            "raw_tool_payload_characters": 1000,
                            "main_context_peak_tokens": 200000,
                        },
                        "scenarios": scenarios,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            report_path = base / "shadow-report.json"

            result = self._run_bound_shadow(base, workspace, scenario_path, report_path)

            self.assertEqual(result.returncode, 1, result.stderr)
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertTrue(report["checks"]["workflow_coverage"]["passed"])
            contract_check = report["checks"]["contract_integrity"]
            self.assertFalse(contract_check["passed"])
            self.assertEqual(contract_check["failed_scenarios"], ["event"])
            self.assertIn("not found", report["scenarios"][3]["contract"]["reason"])

    def test_case_type_cannot_be_satisfied_by_relabeling_an_unrelated_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            workspace = base / "shadow-workspace"
            workspace.mkdir()
            scenario_path = base / "scenarios.json"
            scenario_path.write_text(
                json.dumps(
                    {
                        "schema_version": "a-share-shadow-replay-v1",
                        "baseline": {
                            "raw_tool_payload_characters": 1000,
                            "main_context_characters": 1000,
                            "main_context_peak_tokens": 200000,
                        },
                        "targets": {
                            "min_raw_tool_payload_reduction_ratio": 0.8,
                            "max_main_context_ratio": 0.52,
                            "max_main_context_peak_tokens": 100000,
                        },
                        "scenarios": [
                            {
                                "id": "fake-event",
                                "case_type": "event",
                                "workflow": "scan",
                                "stage": "scan",
                                "contract": "scan-v1.json",
                                "object_type": "market",
                                "objects": ["market:A股"],
                                "information_cutoff": "2026-08-01T10:00:00+08:00",
                                "expectations": {"conditions": []},
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            report_path = base / "report.json"

            result = self._run_bound_shadow(base, workspace, scenario_path, report_path)

            self.assertEqual(result.returncode, 1, result.stderr)
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertFalse(report["checks"]["contract_integrity"]["passed"])
            self.assertIn("case_type 'event'", report["scenarios"][0]["contract"]["reason"])

    def test_valid_scenario_executes_contract_assembly_and_hydration_even_when_suite_is_incomplete(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            workspace = base / "shadow-workspace"
            workspace.mkdir()
            self._write_scan_evidence(workspace)
            scenario_path = base / "scenarios.json"
            scenario_path.write_text(
                json.dumps(
                    {
                        "schema_version": "a-share-shadow-replay-v1",
                        "baseline": {
                            "raw_tool_payload_characters": 100000,
                            "main_context_characters": 100000,
                            "main_context_peak_tokens": 200000,
                        },
                        "scenarios": [
                            {
                                "id": "scan-market",
                                "case_type": "scan",
                                "workflow": "scan",
                                "stage": "scan",
                                "contract": "scan-v1.json",
                                "object_type": "market",
                                "objects": ["market:A股"],
                                "information_cutoff": "2026-08-01T10:00:00+08:00",
                                "expectations": {"conditions": []},
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            report_path = base / "shadow-report.json"

            result = self._run_bound_shadow(base, workspace, scenario_path, report_path)

            self.assertEqual(result.returncode, 1, result.stderr)
            report = json.loads(report_path.read_text(encoding="utf-8"))
            scenario = report["scenarios"][0]
            self.assertEqual(scenario["status"], "passed")
            self.assertEqual(scenario["coverage"]["required_recall_ratio"], 1.0)
            self.assertEqual(scenario["coverage"]["required_total"], 2)
            self.assertEqual(scenario["quality"]["hydrate_verification_failures"], 0)
            self.assertEqual(scenario["quality"]["future_information_selected"], 0)
            semantic_check = report["checks"]["semantic_equivalence"]
            self.assertFalse(semantic_check["passed"])
            self.assertEqual(semantic_check["missing_scenarios"], ["scan-market"])

    def test_authoritative_parse_gap_is_reported_and_blocks_a_scenario_with_a_covered_floor(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            workspace = base / "shadow-workspace"
            workspace.mkdir()
            self._write_scan_evidence(workspace)
            broken = workspace / "证据包/2026-08/EVI-20260801-BROKEN.md"
            broken.write_text(
                "---\n"
                'schema_version: "a-share-workspace-v3"\n'
                'artifact_type: "evidence_package"\n'
                'id: "EVI-20260801-BROKEN"\n'
                'status: "complete"\n'
                "\n# 缺少 frontmatter 结束标记，可能隐藏否证证据\n",
                encoding="utf-8",
            )
            scenario_path = base / "scenarios.json"
            scenario_path.write_text(
                json.dumps(
                    {
                        "schema_version": "a-share-shadow-replay-v1",
                        "baseline": {
                            "raw_tool_payload_characters": 100000,
                            "main_context_characters": 100000,
                            "main_context_peak_tokens": 200000,
                        },
                        "targets": {
                            "min_raw_tool_payload_reduction_ratio": 0.8,
                            "max_main_context_ratio": 0.52,
                            "max_main_context_peak_tokens": 100000,
                        },
                        "scenarios": [
                            {
                                "id": "scan-parse-gap",
                                "case_type": "scan",
                                "workflow": "scan",
                                "stage": "scan",
                                "contract": "scan-v1.json",
                                "object_type": "market",
                                "objects": ["market:A股"],
                                "information_cutoff": "2026-08-01T10:00:00+08:00",
                                "expectations": {"conditions": []},
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            report_path = base / "shadow-report.json"

            result = self._run_bound_shadow(base, workspace, scenario_path, report_path)

            self.assertEqual(result.returncode, 1, result.stderr)
            report = json.loads(report_path.read_text(encoding="utf-8"))
            scenario = report["scenarios"][0]
            self.assertEqual(scenario["coverage"]["base_required_eligible"], 2)
            self.assertTrue(scenario["coverage"]["blocking"])
            self.assertEqual(scenario["coverage"]["blocking_gap_count"], 1)
            self.assertEqual(
                scenario["coverage"]["blocking_gaps"][0]["reason"],
                "authoritative_document_unparseable",
            )
            self.assertEqual(scenario["status"], "failed")
            self.assertFalse(report["checks"]["blocking_gaps"]["passed"])
            self.assertEqual(report["checks"]["blocking_gaps"]["total"], 1)

    def test_risk_probes_distinguish_conflict_denial_expiry_and_future_information(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            workspace = base / "shadow-workspace"
            workspace.mkdir()
            self._write_scan_evidence(workspace)
            self._write_risk_probe_evidence(workspace)
            conditions = [
                {"category": "conflict", "unit_id": "EVI-20260801-002#2"},
                {"category": "denial", "unit_id": "EVI-20260801-002#3"},
                {"category": "expired", "unit_id": "EVI-20260801-002#4"},
                {"category": "future", "unit_id": "EVI-20260801-002#5"},
            ]
            scenario_path = base / "scenarios.json"
            scenario_path.write_text(
                json.dumps(
                    {
                        "schema_version": "a-share-shadow-replay-v1",
                        "baseline": {
                            "raw_tool_payload_characters": 100000,
                            "main_context_characters": 100000,
                            "main_context_peak_tokens": 200000,
                        },
                        "scenarios": [
                            {
                                "id": "analyze-risk-probes",
                                "case_type": "analyze",
                                "workflow": "analyze",
                                "stage": "analysis",
                                "contract": "analyze-v1.json",
                                "strategy_version": "STR-SHADOW-TEST@0.1.0",
                                "object_type": "stock",
                                "objects": ["stock:测试股份"],
                                "handoff": {
                                    "evidence_ids": [
                                        "EVI-20260801-001#1",
                                        "EVI-20260801-001#2",
                                        "EVI-20260801-002#1",
                                        "EVI-20260801-002#2",
                                        "EVI-20260801-002#3",
                                        "EVI-20260801-002#4",
                                        "EVI-20260801-002#5",
                                        "EVI-20260801-002#6",
                                    ]
                                },
                                "information_cutoff": "2026-08-01T10:00:00+08:00",
                                "expectations": {
                                    "conditions": conditions,
                                    "semantic_units": [
                                        {
                                            "unit_id": "EVI-20260801-002#1",
                                            "old": {
                                                "proposition": "反向证据已核验但未触发否决。",
                                                "information_cutoff": "2026-08-01T09:00:00+08:00",
                                                "status": "已确认",
                                                "relations": [],
                                            },
                                        }
                                    ],
                                },
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            report_path = base / "shadow-report.json"

            result = self._run_bound_shadow(base, workspace, scenario_path, report_path)

            self.assertEqual(result.returncode, 1, result.stderr)
            report = json.loads(report_path.read_text(encoding="utf-8"))
            scenario = report["scenarios"][0]
            self.assertEqual(scenario["status"], "passed", scenario)
            self.assertEqual(scenario["coverage"]["blocking_gap_count"], 4)
            self.assertEqual(scenario["coverage"]["expected_probe_blocking_gap_count"], 4)
            self.assertEqual(scenario["coverage"]["unexpected_blocking_gap_count"], 0)
            self.assertEqual({item["category"] for item in scenario["conditions"]}, {"conflict", "denial", "expired", "future"})
            self.assertTrue(all(item["passed"] for item in scenario["conditions"]))
            self.assertTrue(all(item["excluded"] for item in scenario["conditions"]))
            self.assertFalse(any(item["selected"] for item in scenario["conditions"]))
            self.assertEqual(scenario["quality"]["future_information_selected"], 0)
            self.assertTrue(report["checks"]["semantic_equivalence"]["passed"])

    def test_complete_shadow_replay_passes_only_with_accuracy_and_proxy_gates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            workspace = base / "shadow-workspace"
            workspace.mkdir()
            self._write_scan_evidence(workspace)
            self._write_risk_probe_evidence(workspace)
            self._write_cross_workflow_evidence(workspace)
            self._install_runtime(workspace)
            before = {
                path.relative_to(workspace).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in workspace.rglob("*")
                if path.is_file()
            }
            risk_conditions = [
                {"category": "conflict", "unit_id": "EVI-20260801-002#2"},
                {"category": "denial", "unit_id": "EVI-20260801-002#3"},
                {"category": "expired", "unit_id": "EVI-20260801-002#4"},
                {"category": "future", "unit_id": "EVI-20260801-002#5"},
            ]
            scenarios = [
                {
                    "id": "scan-market",
                    "case_type": "scan",
                    "workflow": "scan",
                    "stage": "scan",
                    "contract": "scan-v1.json",
                    "object_type": "market",
                    "objects": ["market:A股"],
                    "information_cutoff": "2026-08-01T10:00:00+08:00",
                    "expectations": {
                        "conditions": [],
                        "semantic_units": [
                            {
                                "unit_id": "EVI-20260801-001#1",
                                "old": {
                                    "proposition": "市场状态已核验。",
                                    "information_cutoff": "2026-08-01T09:00:00+08:00",
                                    "status": "已确认",
                                    "relations": [],
                                },
                            }
                        ],
                    },
                },
                {
                    "id": "investigate-stock",
                    "case_type": "investigate",
                    "workflow": "investigate",
                    "stage": "research",
                    "contract": "investigate-stock-v1.json",
                    "object_type": "stock",
                    "objects": ["stock:测试股份"],
                    "information_cutoff": "2026-08-01T10:00:00+08:00",
                    "expectations": {
                        "conditions": [],
                        "semantic_units": [
                            {
                                "unit_id": "EVI-20260801-003#1",
                                "old": {
                                    "proposition": "个股业务兑现已由原始来源核验。",
                                    "information_cutoff": "2026-08-01T09:00:00+08:00",
                                    "status": "已确认",
                                    "relations": [],
                                },
                            }
                        ],
                    },
                },
                {
                    "id": "analyze-stock",
                    "case_type": "analyze",
                    "workflow": "analyze",
                    "stage": "analysis",
                    "contract": "analyze-v1.json",
                    "strategy_version": "STR-SHADOW-TEST@0.1.0",
                    "object_type": "stock",
                    "objects": ["stock:测试股份"],
                    "handoff": {
                        "evidence_ids": [
                            "EVI-20260801-002#1",
                            "EVI-20260801-002#2",
                            "EVI-20260801-002#3",
                            "EVI-20260801-002#4",
                            "EVI-20260801-002#5",
                            "EVI-20260801-002#6",
                        ]
                    },
                    "information_cutoff": "2026-08-01T10:00:00+08:00",
                    "expectations": {
                        "conditions": risk_conditions,
                        "semantic_units": [
                            {
                                "unit_id": "EVI-20260801-002#1",
                                "old": {
                                    "proposition": "反向证据已核验但未触发否决。",
                                    "information_cutoff": "2026-08-01T09:00:00+08:00",
                                    "status": "已确认",
                                    "relations": [],
                                },
                            }
                        ],
                    },
                },
                {
                    "id": "investigate-event",
                    "case_type": "event",
                    "workflow": "investigate",
                    "stage": "research",
                    "contract": "investigate-event-v1.json",
                    "object_type": "event",
                    "objects": ["event:测试事件"],
                    "information_cutoff": "2026-08-01T10:00:00+08:00",
                    "expectations": {
                        "conditions": [],
                        "semantic_units": [
                            {
                                "unit_id": "EVI-20260801-003#4",
                                "old": {
                                    "proposition": "事件本体已由正式来源核验。",
                                    "information_cutoff": "2026-08-01T09:00:00+08:00",
                                    "status": "已确认",
                                    "relations": [],
                                },
                            }
                        ],
                    },
                },
                {
                    "id": "review-stock",
                    "case_type": "review",
                    "workflow": "review",
                    "stage": "review",
                    "contract": "review-v1.json",
                    "object_type": "stock",
                    "objects": ["stock:测试股份"],
                    "handoff": {
                        "judgment_ids": ["J20260801-001 v1"],
                        "evidence_ids": ["EVI-20260801-003#2", "EVI-20260801-003#3"],
                    },
                    "information_cutoff": "2026-08-01T16:00:00+08:00",
                    "expectations": {
                        "conditions": [],
                        "semantic_units": [
                            {
                                "unit_id": "J20260801-001 v1",
                                "old": {
                                    "proposition": "测试股份在窗口内保持相对强势。",
                                    "information_cutoff": "2026-08-01T10:00:00+08:00",
                                    "status": "进行中",
                                    "relations": [],
                                },
                            }
                        ],
                    },
                },
            ]
            scenario_path = base / "scenarios.json"
            scenario_path.write_text(
                json.dumps(
                    {
                        "schema_version": "a-share-shadow-replay-v1",
                        "baseline": {
                            "raw_tool_payload_characters": 100000,
                            "main_context_characters": 200000,
                            "main_context_peak_tokens": 200000,
                        },
                        "candidate_observation": {
                            "raw_tool_payload_characters": 10000,
                            "main_context_peak_tokens": 100000,
                        },
                        "targets": {
                            "min_raw_tool_payload_reduction_ratio": 0.8,
                            "max_main_context_ratio": 0.52,
                            "max_main_context_peak_tokens": 100000,
                        },
                        "scenarios": scenarios,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            report_path = base / "shadow-report.json"

            result = self._run_bound_shadow(base, workspace, scenario_path, report_path)

            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(report["status"], "passed")
            self.assertTrue(report["acceptance"]["complete"])
            self.assertTrue(report["checks"]["required_recall"]["passed"])
            self.assertEqual(report["checks"]["required_recall"]["ratio"], 1.0)
            condition_check = report["checks"]["condition_coverage"]
            self.assertTrue(condition_check["passed"])
            self.assertEqual(set(condition_check["categories_exercised"]), {"conflict", "denial", "expired", "future"})
            self.assertEqual(report["checks"]["blocking_gaps"]["expected_probe_total"], 4)
            self.assertEqual(report["checks"]["blocking_gaps"]["unexpected_total"], 0)
            self.assertTrue(report["checks"]["blocking_gaps"]["passed"])
            self.assertTrue(report["checks"]["proxy_targets"]["passed"])
            self.assertGreaterEqual(report["proxy_metrics"]["raw_tool_payload_reduction_ratio"], 0.8)
            self.assertLessEqual(report["proxy_metrics"]["main_context_ratio"], 0.52)
            self.assertTrue(report["proxy_metrics"]["model_token_replay"]["available"])
            self.assertEqual(report["proxy_metrics"]["model_token_replay"]["candidate_peak_tokens"], 100000)
            self.assertTrue(report["checks"]["semantic_equivalence"]["passed"])
            self.assertTrue(report["checks"]["input_read_only"]["passed"])
            after = {
                path.relative_to(workspace).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in workspace.rglob("*")
                if path.is_file()
            }
            self.assertEqual(before, after)


class SuiteManifestMigrationCliTests(unittest.TestCase):
    def test_migration_clis_resolve_from_the_suite_root(self) -> None:
        values: dict[str, str] = {}
        for line in (SUITE_ROOT / "shared/suite-manifest.yaml").read_text(encoding="utf-8").splitlines():
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            if key in {"migration_cli", "shadow_replay_cli"}:
                values[key] = value.strip().strip('"\'')

        self.assertEqual(set(values), {"migration_cli", "shadow_replay_cli"})
        for key, relative in values.items():
            resolved = (SUITE_ROOT / relative).resolve()
            self.assertTrue(resolved.is_file(), f"{key} does not resolve: {resolved}")


if __name__ == "__main__":
    unittest.main()
