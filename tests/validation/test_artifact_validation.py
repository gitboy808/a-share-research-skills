from __future__ import annotations

import json
import hashlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
VALIDATOR = PROJECT_ROOT / ".agents/skills/a-share/shared/scripts/validate_workspace.py"


class WorkspaceArtifactValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.workspace = Path(self.temporary_directory.name) / "workspace"
        shutil.copytree(
            PROJECT_ROOT,
            self.workspace,
            ignore=shutil.ignore_patterns(".git", ".context", ".source-payloads", "__pycache__"),
        )

    def render_template(self, template_name: str, destination: str, replacements: dict[str, str]) -> Path:
        text = (self.workspace / "模板" / template_name).read_text(encoding="utf-8")
        for old, new in replacements.items():
            text = text.replace(old, new)
        target = self.workspace / destination
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        return target

    def validate(self) -> tuple[subprocess.CompletedProcess[str], dict[str, object]]:
        result = subprocess.run(
            [sys.executable, str(VALIDATOR), "--root", str(self.workspace), "--json"],
            cwd=PROJECT_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertTrue(result.stdout, result.stderr)
        return result, json.loads(result.stdout)

    def assert_valid_workspace(self, *, warnings: bool = True) -> dict[str, object]:
        result, payload = self.validate()
        self.assertEqual(result.returncode, 0, payload)
        self.assertEqual(payload["errors"], [])
        if warnings:
            self.assertEqual(payload["warnings"], [])
        return payload

    def assert_workspace_error(self, expected: str) -> dict[str, object]:
        result, payload = self.validate()
        self.assertEqual(result.returncode, 1, payload)
        self.assertIn(expected, payload["errors"])
        return payload

    def workset_template_manifest(
        self, replacements: dict[str, str] | None = None
    ) -> dict[str, object]:
        template = (self.workspace / "模板/工作集清单模板.md").read_text(encoding="utf-8")
        rendered = re.search(r"```json\n(.*?)\n```", template, re.DOTALL).group(1)
        values = {
            "YYYY-MM-DDTHH:mm:ss+08:00": "2026-08-09T12:15:00+08:00",
            "YYYYMMDD": "20260809",
            "NNN": "001",
            "WORKFLOW_UPPER": "INVESTIGATE",
            "STAGE_UPPER": "RESEARCH",
            "WORKFLOW_SLUG": "investigate",
            "STAGE_SLUG": "research",
        }
        values.update(replacements or {})
        for old, new in values.items():
            rendered = rendered.replace(old, new)
        return json.loads(rendered)

    def write_workset_manifest(
        self,
        manifest: dict[str, object],
        filename: str = "RUN-20260809-001-investigate-research-工作集清单.json",
    ) -> Path:
        target = self.workspace / "运行记录/2026-08" / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(manifest, ensure_ascii=False) + "\n", encoding="utf-8")
        return target

    def write_source_payload(self, relative_path: str, content: str = "verified source\n") -> tuple[str, int]:
        path = self.workspace / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return hashlib.sha256(path.read_bytes()).hexdigest(), len(path.read_bytes())

    def write_payload_sidecar(
        self,
        relative_path: str,
        *,
        payload_id: str,
        sha256: str,
        byte_length: int,
        acquired_at: str = "2026-08-09T11:30:00+08:00",
    ) -> None:
        path = (self.workspace / relative_path).with_suffix(".json")
        path.write_text(
            json.dumps(
                {
                    "kind": "source_payload",
                    "payload_id": payload_id,
                    "path": relative_path,
                    "sha256": sha256,
                    "byte_length": byte_length,
                    "acquired_at": acquired_at,
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )

    def render_confirmed_evidence(
        self,
        *,
        sidecar: bool = True,
        sidecar_overrides: dict[str, object] | None = None,
    ) -> tuple[Path, str, int]:
        source_path = ".source-payloads/RUN-20260809-001/PAYLOAD-20260809-001.payload"
        source_sha256, source_bytes = self.write_source_payload(
            source_path,
            "\n".join(f"line {index}" for index in range(1, 21)) + "\n",
        )
        metadata: dict[str, object] = {
            "payload_id": "PAYLOAD-20260809-001",
            "sha256": source_sha256,
            "byte_length": source_bytes,
            "acquired_at": "2026-08-09T11:30:00+08:00",
        }
        metadata.update(sidecar_overrides or {})
        sidecar_path = (self.workspace / source_path).with_suffix(".json")
        if sidecar:
            self.write_payload_sidecar(source_path, **metadata)  # type: ignore[arg-type]
        elif sidecar_path.exists():
            sidecar_path.unlink()
        evidence = self.render_template(
            "证据包模板.md",
            "证据包/2026-08/EVI-20260809-001.md",
            {
                "YYYY-MM-DDTHH:mm:ss+08:00": "2026-08-09T11:30:00+08:00",
                "YYYYMMDD": "20260809",
                "NNN": "001",
                "PAYLOAD-ID": "PAYLOAD-20260809-001",
                "SOURCE-PATH": source_path,
                "SHA256_HEX": source_sha256,
                "BYTE_LENGTH": str(source_bytes),
                "- **状态**：未证实": "- **状态**：已确认",
                "- **事件时间 / 市场交易日**：": "- **事件时间 / 市场交易日**：2026-08-09；2026-08-07",
                "- **数据口径**：": "- **数据口径**：人民币元；不复权",
                "- **关联对象 / 档案字段**：": "- **关联对象 / 档案字段**：个股:000001 / 主营业务",
                "- **过期条件 / 下次复核**：": "- **过期条件 / 下次复核**：下一份定期报告披露时；2026-10-31",
            },
        )
        return evidence, source_sha256, source_bytes

    def write_partial_runtime_workset(
        self, objects: list[str] | None = None
    ) -> Path:
        run_path = self.workspace / "runtime-workset-run.json"
        run_path.write_text(
            json.dumps(
                {
                    "schema_version": "a-share-workspace-v3",
                    "workspace_root": str(self.workspace),
                    "run_id": "RUN-20260809-001",
                    "workflow": "investigate",
                    "stage": "research",
                    "created_at": "2026-08-09T12:00:00+08:00",
                    "information_cutoff": "2026-08-09T12:00:00+08:00",
                    "objects": objects or ["市场:A股"],
                    "task_contract": "investigate-market-v1.json",
                    "persist_workset_manifest": True,
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        context_cli = (
            self.workspace
            / ".agents/skills/a-share/shared/scripts/context_workspace.py"
        )
        assembled = subprocess.run(
            [
                sys.executable,
                str(context_cli),
                "assemble",
                "--run-manifest",
                str(run_path),
            ],
            cwd=self.workspace,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(assembled.returncode, 0, assembled.stderr)
        return (
            self.workspace
            / "运行记录/2026-08/RUN-20260809-001-investigate-research-工作集清单.json"
        )

    def test_presentation_templates_are_valid_projections(self) -> None:
        cases = (
            ("分析报告模板.md", "001", "10:30:00", "EVI-20260809-001#001"),
            ("扫描报告模板.md", "002", "09:00:00", "C20260809-002 v1"),
            ("调研报告模板.md", "003", "09:30:00", "EVI-20260809-003#001"),
            ("盘后复盘模板.md", "004", "15:30:00", "J20260809-004 v1"),
        )
        for template, sequence, time, source_ref in cases:
            with self.subTest(template=template):
                self.render_template(
                    template,
                    f"报告/2026-08/RPT-20260809-{sequence}.md",
                    {
                        "YYYY-MM-DDTHH:mm:ss+08:00": f"2026-08-09T{time}+08:00",
                        "YYYY-MM-DD": "2026-08-09",
                        "YYYYMMDD": "20260809",
                        "NNN": sequence,
                        "研究对象": "测试公司",
                        f'source_refs: "atom:{source_ref}"': (
                            'source_refs: "unknown"\n'
                            'source_refs_unknown_reason: "synthetic template has no research input"'
                        ),
                    },
                )
                result, payload = self.validate()
                self.assertEqual(result.returncode, 0, payload)
                self.assertEqual(payload["errors"], [])
                self.assertEqual(payload["warnings"], [])

    def test_live_artifact_rejects_a_naive_created_at_timestamp(self) -> None:
        path = self.render_template(
            "分析报告模板.md",
            "报告/2026-08/RPT-20260809-001.md",
            {
                "YYYY-MM-DDTHH:mm:ss+08:00": "2026-08-09T10:30:00+08:00",
                "YYYY-MM-DD": "2026-08-09",
                "YYYYMMDD": "20260809",
                "NNN": "001",
                "研究对象": "测试公司",
                'source_refs: "atom:EVI-20260809-001#001"': 'source_refs: "unknown"\nsource_refs_unknown_reason: "synthetic template has no research input"',
            },
        )
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                'created_at: "2026-08-09T10:30:00+08:00"',
                'created_at: "2026-08-09T10:30:00"',
            ),
            encoding="utf-8",
        )

        self.assert_workspace_error(
            "报告/2026-08/RPT-20260809-001.md: created_at must be ISO-8601 with timezone"
        )

    def test_judgment_log_rendered_from_template_is_a_valid_judgment_source(self) -> None:
        self.render_template(
            "判断条目模板.md",
            "判断日志/2026-08.md",
            {
                "YYYY-MM-DDTHH:mm:ss+08:00": "2026-08-09T10:30:00+08:00",
                "YYYY-MM": "2026-08",
                "YYYYMMDD": "20260809",
                "YYYYMM": "202608",
                "NNN": "001",
                "- **原子命题**：": "- **原子命题**：当前证据不足，至观察时限前维持弃权",
                "- **证据包 / 原子证据项**：": "- **证据包 / 原子证据项**：unknown—正式弃权；证据缺口见下",
                "- **证伪条件**：": "- **证伪条件**：任务证据底线在截止前完整满足",
            },
        )

        self.assert_valid_workspace()

    def test_judgment_log_rejects_a_gap_in_an_atomic_judgment_version_chain(self) -> None:
        path = self.render_template(
            "判断条目模板.md",
            "判断日志/2026-08.md",
            {
                "YYYY-MM-DDTHH:mm:ss+08:00": "2026-08-09T10:30:00+08:00",
                "YYYY-MM": "2026-08",
                "YYYYMMDD": "20260809",
                "YYYYMM": "202608",
                "NNN": "001",
            },
        )
        path.write_text(path.read_text(encoding="utf-8") + "\n### J20260809-001 v3\n", encoding="utf-8")

        self.assert_workspace_error(
            "判断日志/2026-08.md: non-contiguous version chain for J20260809-001: [1, 3]"
        )

    def test_object_dossier_rendered_from_template_preserves_split_field_ownership(self) -> None:
        self.render_template(
            "对象档案模板.md",
            "对象档案/个股/000001-测试公司.md",
            {
                "YYYY-MM-DDTHH:mm:ss+08:00": "2026-08-09T11:00:00+08:00",
                "DOS-KIND-IDENTIFIER": "DOS-STOCK-000001",
                "KIND:OBJECT": "stock:000001-测试公司",
                "EVI-YYYYMMDD-NNN": "EVI-20260809-001",
                'source_refs: "atom:EVI-20260809-001#001"': 'source_refs: "unknown"\nsource_refs_unknown_reason: "new empty dossier has no verified field"',
            },
        )

        self.assert_valid_workspace()

    def test_meta_review_rendered_from_template_is_a_governance_record(self) -> None:
        self.render_template(
            "元复盘模板.md",
            "周收敛/2026-W32.md",
            {
                "YYYY-MM-DDTHH:mm:ss+08:00": "2026-08-09T18:00:00+08:00",
                "YYYY-Www": "2026-W32",
                "YYYYMMDD": "20260809",
                "NNN": "001",
                'source_refs: "atom:J20260809-001 v1"': 'source_refs: "unknown"\nsource_refs_unknown_reason: "synthetic template has no review window"',
            },
        )

        self.assert_valid_workspace()

    def test_observation_log_rendered_from_template_is_an_observation_source(self) -> None:
        self.render_template(
            "观察候选模板.md",
            "观察日志/2026-08.md",
            {
                "YYYY-MM-DDTHH:mm:ss+08:00": "2026-08-09T09:00:00+08:00",
                "YYYY-MM": "2026-08",
                "YYYYMMDD": "20260809",
                "YYYYMM": "202608",
                "NNN": "001",
                "- **热度—确认象限**：": "- **热度—确认象限**：低热度、早期确认",
                "- **确认触发**：": "- **确认触发**：相对强度升至动态基线 80 分位且内部广度改善",
                "- **失效条件**：": "- **失效条件**：相对强度跌破动态基线",
                "- **最大可接受价格位移**：": "- **最大可接受价格位移**：相对发现价不超过 3%",
                "- **预计半衰期 / 到期**：": "- **预计半衰期 / 到期**：2 个交易日；2026-08-11T15:00:00+08:00",
                "- **缺失证据**：": "- **缺失证据**：业务兑现尚待调研",
                "- **后续调研优先级**：": "- **后续调研优先级**：高",
            },
        )

        self.assert_valid_workspace()

    def test_evidence_item_rejects_unstructured_source_independence_and_locator(self) -> None:
        path = self.render_template(
            "证据包模板.md",
            "证据包/2026-08/EVI-20260809-001.md",
            {
                "YYYY-MM-DDTHH:mm:ss+08:00": "2026-08-09T11:30:00+08:00",
                "YYYYMMDD": "20260809",
                "NNN": "001",
            },
        )
        text = "\n".join(
            line
            for line in path.read_text(encoding="utf-8").splitlines()
            if not line.startswith("- **来源组 ID**") and not line.startswith("- **来源定位**")
        )
        path.write_text(text + "\n", encoding="utf-8")

        payload = self.assert_workspace_error(
            "证据包/2026-08/EVI-20260809-001.md: EVI-20260809-001#001 missing atomic field 来源组 ID"
        )
        self.assertIn(
            "证据包/2026-08/EVI-20260809-001.md: EVI-20260809-001#001 missing atomic field 来源定位",
            payload["errors"],
        )

    def test_evidence_package_rendered_from_template_has_auditable_source_structure(self) -> None:
        self.render_confirmed_evidence()

        self.assert_valid_workspace()

    def test_payload_locator_sidecar_mismatches_are_table_driven(self) -> None:
        cases = (
            (False, {}, "sidecar does not exist"),
            (True, {"acquired_at": "2026-08-09T10:00:00+08:00"}, "acquired_at does not match locator"),
            (True, {"sha256": "0" * 64}, "sha256 does not match locator or payload"),
            (True, {"byte_length": 999}, "byte_length does not match locator or payload"),
            (True, {"payload_id": "PAYLOAD-OTHER"}, "identity does not match locator"),
        )
        for sidecar, overrides, expected in cases:
            with self.subTest(expected=expected):
                self.render_confirmed_evidence(
                    sidecar=sidecar,
                    sidecar_overrides=overrides,
                )
                result, payload = self.validate()
                self.assertEqual(result.returncode, 1)
                self.assertTrue(
                    any(expected in error for error in payload["errors"]),
                    payload,
                )

    def test_evidence_status_must_be_one_controlled_atomic_value(self) -> None:
        path = self.render_template(
            "证据包模板.md",
            "证据包/2026-08/EVI-20260809-001.md",
            {
                "YYYY-MM-DDTHH:mm:ss+08:00": "2026-08-09T11:30:00+08:00",
                "YYYYMMDD": "20260809",
                "NNN": "001",
                "- **状态**：未证实": "- **状态**：已确认 / 未证实",
                "- **事件时间 / 市场交易日**：": "- **事件时间 / 市场交易日**：2026-08-09；2026-08-07",
                "- **数据口径**：": "- **数据口径**：人民币元；不复权",
                "- **关联对象 / 档案字段**：": "- **关联对象 / 档案字段**：个股:000001 / 主营业务",
                "- **过期条件 / 下次复核**：YYYY-MM-DD；达到复核条件": "- **过期条件 / 下次复核**：2026-10-31；下一份定期报告披露",
            },
        )

        self.assert_workspace_error(
            "证据包/2026-08/EVI-20260809-001.md: EVI-20260809-001#001 invalid evidence status 已确认 / 未证实"
        )

    def test_confirmed_evidence_rejects_a_remote_url_locator(self) -> None:
        path = self.render_template(
            "证据包模板.md",
            "证据包/2026-08/EVI-20260809-001.md",
            {
                "YYYY-MM-DDTHH:mm:ss+08:00": "2026-08-09T11:30:00+08:00",
                "YYYYMMDD": "20260809",
                "NNN": "001",
                "- **状态**：未证实": "- **状态**：已确认",
                "- **事件时间 / 市场交易日**：": "- **事件时间 / 市场交易日**：2026-08-09；2026-08-07",
                "- **数据口径**：": "- **数据口径**：人民币元；不复权",
                "- **关联对象 / 档案字段**：": "- **关联对象 / 档案字段**：个股:000001 / 主营业务",
                "- **过期条件 / 下次复核**：YYYY-MM-DD；达到复核条件": "- **过期条件 / 下次复核**：2026-10-31；下一份定期报告披露",
            },
        )
        text = re.sub(
            r"^- \*\*来源定位\*\*[：:].*$",
            '- **来源定位**：{"url":"https://example.com/source","anchor":"公告正文"}',
            path.read_text(encoding="utf-8"),
            flags=re.MULTILINE,
        )
        path.write_text(text, encoding="utf-8")

        self.assert_workspace_error(
            "证据包/2026-08/EVI-20260809-001.md: EVI-20260809-001#001 status 已确认 requires a source_payload locator"
        )

    def test_confirmed_evidence_payload_locator_requires_acquisition_time_and_real_payload(self) -> None:
        path = self.render_template(
            "证据包模板.md",
            "证据包/2026-08/EVI-20260809-001.md",
            {
                "YYYY-MM-DDTHH:mm:ss+08:00": "2026-08-09T11:30:00+08:00",
                "YYYYMMDD": "20260809",
                "NNN": "001",
                "PAYLOAD-ID": "PAYLOAD-20260809-001",
                "SOURCE-PATH": "payloads/missing.txt",
                "SHA256_HEX": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
                "BYTE_LENGTH": "20",
                "- **状态**：未证实": "- **状态**：已确认",
                "- **事件时间 / 市场交易日**：": "- **事件时间 / 市场交易日**：2026-08-09；2026-08-07",
                "- **数据口径**：": "- **数据口径**：人民币元；不复权",
                "- **关联对象 / 档案字段**：": "- **关联对象 / 档案字段**：个股:000001 / 主营业务",
                "- **过期条件 / 下次复核**：YYYY-MM-DD；达到复核条件": "- **过期条件 / 下次复核**：2026-10-31；下一份定期报告披露",
            },
        )
        text = re.sub(
            r',"acquired_at":"[^"]+"',
            "",
            path.read_text(encoding="utf-8"),
        )
        path.write_text(text, encoding="utf-8")

        payload = self.assert_workspace_error(
            "证据包/2026-08/EVI-20260809-001.md: EVI-20260809-001#001 payload locator missing acquired_at"
        )
        self.assertIn(
            "证据包/2026-08/EVI-20260809-001.md: EVI-20260809-001#001 payload source does not exist: payloads/missing.txt",
            payload["errors"],
        )

    def test_evidence_expiry_requires_a_parseable_review_date(self) -> None:
        path = self.render_template(
            "证据包模板.md",
            "证据包/2026-08/EVI-20260809-001.md",
            {
                "YYYY-MM-DDTHH:mm:ss+08:00": "2026-08-09T11:30:00+08:00",
                "YYYYMMDD": "20260809",
                "NNN": "001",
                "- **事件时间 / 市场交易日**：": "- **事件时间 / 市场交易日**：2026-08-09；2026-08-07",
                "- **数据口径**：": "- **数据口径**：人民币元；不复权",
                "- **关联对象 / 档案字段**：": "- **关联对象 / 档案字段**：个股:000001 / 主营业务",
                "- **过期条件 / 下次复核**：YYYY-MM-DD；达到复核条件": "- **过期条件 / 下次复核**：待定 / 下次再看",
            },
        )

        self.assert_workspace_error(
            "证据包/2026-08/EVI-20260809-001.md: EVI-20260809-001#001 expiry/review must contain a valid YYYY-MM-DD date"
        )

    def test_unconfirmed_evidence_may_keep_a_remote_url_without_covering(self) -> None:
        path = self.render_template(
            "证据包模板.md",
            "证据包/2026-08/EVI-20260809-001.md",
            {
                "YYYY-MM-DDTHH:mm:ss+08:00": "2026-08-09T11:30:00+08:00",
                "YYYY-MM-DD": "2026-10-31",
                "YYYYMMDD": "20260809",
                "NNN": "001",
                "- **事件时间 / 市场交易日**：": "- **事件时间 / 市场交易日**：2026-08-09；2026-08-07",
                "- **数据口径**：": "- **数据口径**：人民币元；不复权",
                "- **关联对象 / 档案字段**：": "- **关联对象 / 档案字段**：个股:000001 / 主营业务",
            },
        )
        text = re.sub(
            r"^- \*\*来源定位\*\*[：:].*$",
            '- **来源定位**：{"url":"https://example.com/source","anchor":"公告正文"}',
            path.read_text(encoding="utf-8"),
            flags=re.MULTILINE,
        )
        path.write_text(text, encoding="utf-8")

        self.assert_valid_workspace(warnings=False)

    def test_evidence_atomic_ids_must_be_unique_across_packages(self) -> None:
        for package_number in ("001", "002"):
            path = self.render_template(
                "证据包模板.md",
                f"证据包/2026-08/EVI-20260809-{package_number}.md",
                {
                    "YYYY-MM-DDTHH:mm:ss+08:00": "2026-08-09T11:30:00+08:00",
                    "YYYY-MM-DD": "2026-10-31",
                    "YYYYMMDD": "20260809",
                    "NNN": package_number,
                    "- **事件时间 / 市场交易日**：": "- **事件时间 / 市场交易日**：2026-08-09；2026-08-07",
                    "- **数据口径**：": "- **数据口径**：人民币元；不复权",
                    "- **关联对象 / 档案字段**：": "- **关联对象 / 档案字段**：个股:000001 / 主营业务",
                },
            )
            if package_number == "002":
                path.write_text(
                    path.read_text(encoding="utf-8").replace(
                        "### EVI-20260809-002#001", "### EVI-20260809-001#001"
                    ),
                    encoding="utf-8",
                )

        self.assert_workspace_error(
            "证据包/2026-08/EVI-20260809-002.md: duplicate evidence atomic id EVI-20260809-001#001; first declared in 证据包/2026-08/EVI-20260809-001.md"
        )

    def test_payload_locator_rejects_an_out_of_bounds_line_range(self) -> None:
        source_path = ".source-payloads/RUN-20260809-001/PAYLOAD-20260809-001.payload"
        source_sha256, source_bytes = self.write_source_payload(source_path, "only one line\n")
        self.render_template(
            "证据包模板.md",
            "证据包/2026-08/EVI-20260809-001.md",
            {
                "YYYY-MM-DDTHH:mm:ss+08:00": "2026-08-09T11:30:00+08:00",
                "YYYY-MM-DD": "2026-10-31",
                "YYYYMMDD": "20260809",
                "NNN": "001",
                "PAYLOAD-ID": "PAYLOAD-20260809-001",
                "SOURCE-PATH": source_path,
                "SHA256_HEX": source_sha256,
                "BYTE_LENGTH": str(source_bytes),
                '"line_start":1,"line_end":20': '"line_start":1,"line_end":2',
                "- **状态**：未证实": "- **状态**：已确认",
                "- **事件时间 / 市场交易日**：": "- **事件时间 / 市场交易日**：2026-08-09；2026-08-07",
                "- **数据口径**：": "- **数据口径**：人民币元；不复权",
                "- **关联对象 / 档案字段**：": "- **关联对象 / 档案字段**：个股:000001 / 主营业务",
            },
        )

        self.assert_workspace_error(
            "证据包/2026-08/EVI-20260809-001.md: EVI-20260809-001#001 payload line range exceeds source"
        )

    def test_payload_locator_rejects_a_non_utf8_source(self) -> None:
        source_path = self.workspace / ".source-payloads/RUN-20260809-001/PAYLOAD-20260809-001.payload"
        source_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.write_bytes(b"\xff\xfe\xfd")
        source_sha256 = hashlib.sha256(source_path.read_bytes()).hexdigest()
        self.render_template(
            "证据包模板.md",
            "证据包/2026-08/EVI-20260809-001.md",
            {
                "YYYY-MM-DDTHH:mm:ss+08:00": "2026-08-09T11:30:00+08:00",
                "YYYY-MM-DD": "2026-10-31",
                "YYYYMMDD": "20260809",
                "NNN": "001",
                "PAYLOAD-ID": "PAYLOAD-20260809-001",
                "SOURCE-PATH": ".source-payloads/RUN-20260809-001/PAYLOAD-20260809-001.payload",
                "SHA256_HEX": source_sha256,
                "BYTE_LENGTH": "3",
                '"line_start":1,"line_end":20': '"line_start":1,"line_end":1',
                "- **状态**：未证实": "- **状态**：已确认",
                "- **事件时间 / 市场交易日**：": "- **事件时间 / 市场交易日**：2026-08-09；2026-08-07",
                "- **数据口径**：": "- **数据口径**：人民币元；不复权",
                "- **关联对象 / 档案字段**：": "- **关联对象 / 档案字段**：个股:000001 / 主营业务",
            },
        )

        self.assert_workspace_error(
            "证据包/2026-08/EVI-20260809-001.md: EVI-20260809-001#001 payload source must be UTF-8 text"
        )

    def test_evidence_item_rejects_a_free_text_source_locator(self) -> None:
        path = self.render_template(
            "证据包模板.md",
            "证据包/2026-08/EVI-20260809-001.md",
            {
                "YYYY-MM-DDTHH:mm:ss+08:00": "2026-08-09T11:30:00+08:00",
                "YYYYMMDD": "20260809",
                "NNN": "001",
                "SOURCE-GROUP-ID": "SRCGRP-20260809-001-01",
                "PAYLOAD-ID": "PAYLOAD-20260809-001",
                "SOURCE-PATH": "payloads/PAYLOAD-20260809-001.txt",
                "SHA256_HEX": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
                "- **事件时间 / 市场交易日**：": "- **事件时间 / 市场交易日**：2026-08-09；2026-08-07",
                "- **数据口径**：": "- **数据口径**：人民币元；不复权",
                "- **关联对象 / 档案字段**：": "- **关联对象 / 档案字段**：个股:000001 / 主营业务",
                "- **过期条件 / 下次复核**：": "- **过期条件 / 下次复核**：下一份定期报告披露时；2026-10-31",
            },
        )
        text = re.sub(
            r"^- \*\*来源定位\*\*[：:].*$",
            "- **来源定位**：搜索结果摘要",
            path.read_text(encoding="utf-8"),
            flags=re.MULTILINE,
        )
        path.write_text(text, encoding="utf-8")

        self.assert_workspace_error(
            "证据包/2026-08/EVI-20260809-001.md: EVI-20260809-001#001 source locator must be structured JSON"
        )

    def test_evidence_item_rejects_a_free_text_source_group(self) -> None:
        path = self.render_template(
            "证据包模板.md",
            "证据包/2026-08/EVI-20260809-001.md",
            {
                "YYYY-MM-DDTHH:mm:ss+08:00": "2026-08-09T11:30:00+08:00",
                "YYYYMMDD": "20260809",
                "NNN": "001",
                "SOURCE-GROUP-ID": "SRCGRP-20260809-001-01",
                "PAYLOAD-ID": "PAYLOAD-20260809-001",
                "SOURCE-PATH": "payloads/PAYLOAD-20260809-001.txt",
                "SHA256_HEX": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
                "- **事件时间 / 市场交易日**：": "- **事件时间 / 市场交易日**：2026-08-09；2026-08-07",
                "- **数据口径**：": "- **数据口径**：人民币元；不复权",
                "- **关联对象 / 档案字段**：": "- **关联对象 / 档案字段**：个股:000001 / 主营业务",
                "- **过期条件 / 下次复核**：": "- **过期条件 / 下次复核**：下一份定期报告披露时；2026-10-31",
            },
        )
        text = re.sub(
            r"^- \*\*来源组 ID\*\*[：:].*$",
            "- **来源组 ID**：看起来是两个来源",
            path.read_text(encoding="utf-8"),
            flags=re.MULTILINE,
        )
        path.write_text(text, encoding="utf-8")

        self.assert_workspace_error(
            "证据包/2026-08/EVI-20260809-001.md: EVI-20260809-001#001 invalid source group id 看起来是两个来源"
        )

    def test_run_record_rendered_from_template_is_valid(self) -> None:
        self.render_template(
            "运行记录模板.md",
            "运行记录/2026-08/RUN-20260809-001.md",
            {
                "YYYY-MM-DDTHH:mm:ss+08:00": "2026-08-09T12:00:00+08:00",
                "YYYYMMDD": "20260809",
                "NNN": "001",
            },
        )

        self.assert_valid_workspace()

    def test_workset_manifest_rendered_from_template_is_valid_stage_audit_json(self) -> None:
        runtime_path = self.write_partial_runtime_workset()
        runtime_manifest = json.loads(runtime_path.read_text(encoding="utf-8"))
        template = (self.workspace / "模板/工作集清单模板.md").read_text(encoding="utf-8")
        match = re.search(r"```json\n(.*?)\n```", template, re.DOTALL)
        self.assertIsNotNone(match, "workset template must contain the canonical JSON artifact")
        rendered = match.group(1)
        for old, new in {
            "YYYY-MM-DDTHH:mm:ss+08:00": "2026-08-09T12:15:00+08:00",
            "YYYYMMDD": "20260809",
            "NNN": "001",
            "WORKFLOW_UPPER": "INVESTIGATE",
            "STAGE_UPPER": "RESEARCH",
            "WORKFLOW_SLUG": "investigate",
            "STAGE_SLUG": "research",
            "CONTRACT-ID": "investigate.market",
            "CONTRACT-VERSION": "1.0.0",
        }.items():
            rendered = rendered.replace(old, new)
        manifest = json.loads(rendered)
        for field in (
            "task_contract",
            "contract_instantiation",
            "instantiated_requirements",
            "instantiated_requirements_sha256",
            "coverage",
            "gaps",
            "projection",
            "quality",
            "verification",
        ):
            manifest[field] = runtime_manifest[field]
        manifest["information_cutoff"] = runtime_manifest["information_cutoff"]
        target = self.workspace / "运行记录/2026-08/RUN-20260809-001-investigate-research-工作集清单.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        self.assert_valid_workspace()

    def test_workset_manifest_rejects_same_version_contract_content_drift(self) -> None:
        manifest_path = self.write_partial_runtime_workset()
        contract_path = (
            self.workspace
            / ".agents/skills/a-share/shared/contracts/investigate-market-v1.json"
        )
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        contract["required_evidence"][0]["field"] = "forged_market_state"
        contract_path.write_text(
            json.dumps(contract, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        self.assert_workspace_error(
            "运行记录/2026-08/RUN-20260809-001-investigate-research-工作集清单.json: task_contract sha256 does not match the registered contract file"
        )

    def test_workset_manifest_rejects_a_rehashed_snapshot_with_stale_selectors(self) -> None:
        manifest_path = self.write_partial_runtime_workset()
        contract_path = (
            self.workspace
            / ".agents/skills/a-share/shared/contracts/investigate-market-v1.json"
        )
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        contract["required_evidence"][0]["field"] = "forged_market_state"
        contract_path.write_text(
            json.dumps(contract, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["task_contract"]["sha256"] = hashlib.sha256(
            contract_path.read_bytes()
        ).hexdigest()
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        self.assert_workspace_error(
            "运行记录/2026-08/RUN-20260809-001-investigate-research-工作集清单.json: instantiated requirement market-state@市场:A股 does not preserve registered selector field"
        )

    def test_workset_manifest_rejects_instantiated_requirement_hash_tampering(self) -> None:
        manifest_path = self.write_partial_runtime_workset()
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["instantiated_requirements"][0]["fields"] = ["forged"]
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        self.assert_workspace_error(
            "运行记录/2026-08/RUN-20260809-001-investigate-research-工作集清单.json: instantiated_requirements_sha256 does not match instantiated_requirements"
        )

    def test_workset_manifest_recomputes_every_bound_object_requirement(self) -> None:
        manifest_path = self.write_partial_runtime_workset(
            ["市场:A股", "市场:港股"]
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        omitted_id = "market-state@市场:港股"
        manifest["instantiated_requirements"] = [
            requirement
            for requirement in manifest["instantiated_requirements"]
            if requirement["requirement_id"] != omitted_id
        ]
        manifest["coverage"]["requirements"] = [
            row
            for row in manifest["coverage"]["requirements"]
            if row["requirement_id"] != omitted_id
        ]
        manifest["gaps"] = [
            gap
            for gap in manifest["gaps"]
            if gap.get("requirement_id") != omitted_id
        ]
        manifest["coverage"].update(
            {
                "required_total": 3,
                "required_covered": 0,
                "required_missing": 3,
                "coverage_ratio": 0.0,
                "blocking": True,
                "blocking_gap_count": 1,
            }
        )
        canonical = json.dumps(
            manifest["instantiated_requirements"],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        manifest["instantiated_requirements_sha256"] = hashlib.sha256(
            canonical.encode("utf-8")
        ).hexdigest()
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        self.assert_workspace_error(
            "运行记录/2026-08/RUN-20260809-001-investigate-research-工作集清单.json: instantiated_requirements do not exactly match recomputation from contract_instantiation"
        )

    def test_workset_template_does_not_claim_empty_coverage_is_complete(self) -> None:
        template = (self.workspace / "模板/工作集清单模板.md").read_text(encoding="utf-8")
        rendered = re.search(r"```json\n(.*?)\n```", template, re.DOTALL).group(1)
        manifest = json.loads(rendered)

        self.assertNotEqual(manifest["status"], "completed")
        self.assertEqual(manifest["coverage"]["required_total"], 0)
        self.assertEqual(manifest["coverage"]["required_covered"], 0)
        self.assertEqual(manifest["coverage"]["required_missing"], 0)
        self.assertEqual(manifest["coverage"]["coverage_ratio"], 0.0)
        self.assertTrue(any(gap.get("blocking") for gap in manifest["gaps"]))
        self.assertEqual(
            set(manifest["relation_checks"]), {"total", "resolved", "blocking_gaps"}
        )
        self.assertEqual(
            set(manifest["verification"]),
            {"status", "required_unit_ids", "verified_unit_ids", "missing_references"},
        )
        self.assertEqual(
            set(manifest["coverage"]),
            {
                "required_total",
                "required_covered",
                "required_missing",
                "coverage_ratio",
                "blocking",
                "blocking_gap_count",
                "requirements",
                "semantic_candidates_do_not_count",
            },
        )
        self.assertEqual(
            set(manifest["quality"]),
            {
                "assembled_units",
                "source_payload_bytes_in_workset",
                "hydrate_units",
                "projection_degraded",
                "context_proxy",
            },
        )

    def test_workset_manifest_rejects_an_empty_task_contract_reference(self) -> None:
        manifest = self.workset_template_manifest()
        manifest["task_contract"] = {"contract_id": "", "version": ""}
        self.write_workset_manifest(manifest)

        self.assert_workspace_error(
            "运行记录/2026-08/RUN-20260809-001-investigate-research-工作集清单.json: task_contract requires non-empty contract_id and version"
        )

    def test_workset_manifest_rejects_a_forged_nonregistered_task_contract(self) -> None:
        manifest = self.workset_template_manifest()
        manifest["task_contract"] = {
            "contract_id": "investigate.forged",
            "version": "9.9.9",
        }
        self.write_workset_manifest(manifest)

        self.assert_workspace_error(
            "运行记录/2026-08/RUN-20260809-001-investigate-research-工作集清单.json: task_contract investigate.forged@9.9.9 is not registered"
        )

    def test_workset_manifest_rejects_a_registered_contract_for_another_stage(self) -> None:
        manifest = self.workset_template_manifest()
        manifest["task_contract"] = {"contract_id": "analyze.default", "version": "1.0.0"}
        self.write_workset_manifest(manifest)

        self.assert_workspace_error(
            "运行记录/2026-08/RUN-20260809-001-investigate-research-工作集清单.json: task_contract analyze.default@1.0.0 belongs to analyze/analysis, not investigate/research"
        )

    def test_workset_manifest_rejects_inconsistent_coverage_arithmetic(self) -> None:
        manifest = self.workset_template_manifest()
        manifest["coverage"] = {
            "required_total": 2,
            "required_covered": 2,
            "required_missing": 1,
            "coverage_ratio": 0.5,
            "requirements": [],
            "semantic_candidates_do_not_count": True,
        }
        self.write_workset_manifest(manifest)

        payload = self.assert_workspace_error(
            "运行记录/2026-08/RUN-20260809-001-investigate-research-工作集清单.json: coverage required_covered + required_missing must equal required_total"
        )
        self.assertIn(
            "运行记录/2026-08/RUN-20260809-001-investigate-research-工作集清单.json: coverage_ratio does not match required_covered / required_total",
            payload["errors"],
        )

    def test_workset_manifest_rejects_completed_status_with_zero_requirements(self) -> None:
        manifest = self.workset_template_manifest()
        manifest["status"] = "completed"
        manifest["coverage"] = {
            "required_total": 0,
            "required_covered": 0,
            "required_missing": 0,
            "coverage_ratio": 0.0,
            "requirements": [],
            "semantic_candidates_do_not_count": True,
        }
        self.write_workset_manifest(manifest)

        self.assert_workspace_error(
            "运行记录/2026-08/RUN-20260809-001-investigate-research-工作集清单.json: zero required evidence cannot have completed status"
        )

    def test_workset_verification_cannot_complete_without_every_required_unit(self) -> None:
        manifest = self.workset_template_manifest(
            {"CONTRACT-ID": "investigate.market", "CONTRACT-VERSION": "1.0.0"}
        )
        manifest["verification"]["status"] = "completed"
        self.write_workset_manifest(manifest)

        self.assert_workspace_error(
            "运行记录/2026-08/RUN-20260809-001-investigate-research-工作集清单.json: completed verification requires every non-empty required_unit_id and no missing references"
        )

    def test_workset_manifest_rejects_a_naive_information_cutoff(self) -> None:
        manifest = self.workset_template_manifest()
        manifest["information_cutoff"] = "2026-08-09T12:15:00"
        self.write_workset_manifest(manifest)

        self.assert_workspace_error(
            "运行记录/2026-08/RUN-20260809-001-investigate-research-工作集清单.json: information_cutoff must be ISO-8601 with timezone"
        )

    def test_workset_manifest_filename_must_identify_its_exact_stage(self) -> None:
        self.write_workset_manifest(
            self.workset_template_manifest(),
            "RUN-20260809-001-investigate-analysis-工作集清单.json",
        )

        self.assert_workspace_error(
            "运行记录/2026-08/RUN-20260809-001-investigate-analysis-工作集清单.json: filename does not match workflow/stage; expected RUN-20260809-001-investigate-research-工作集清单.json"
        )

    def test_workset_manifest_accepts_a_monotonic_attempt_chain(self) -> None:
        first_path = self.write_partial_runtime_workset()
        first = json.loads(first_path.read_text(encoding="utf-8"))
        second = json.loads(json.dumps(first, ensure_ascii=False))
        second.update(
            {
                "id": "RUN-20260809-001-WORKSET-INVESTIGATE-RESEARCH-A002",
                "attempt": 2,
                "previous_manifest_id": first["id"],
                "created_at": "2026-08-09T12:20:00+08:00",
                "information_cutoff": "2026-08-09T12:20:00+08:00",
            }
        )
        second["contract_instantiation"][
            "information_cutoff"
        ] = second["information_cutoff"]
        instantiation_payload = {
            key: value
            for key, value in second["contract_instantiation"].items()
            if key != "sha256"
        }
        second["contract_instantiation"]["sha256"] = hashlib.sha256(
            json.dumps(
                instantiation_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        directory = self.workspace / "运行记录/2026-08"
        (directory / "RUN-20260809-001-investigate-research-a002-工作集清单.json").write_text(
            json.dumps(second, ensure_ascii=False) + "\n", encoding="utf-8"
        )

        self.assert_valid_workspace(warnings=False)

    def test_workset_manifest_rejects_a_skipped_or_misdirected_attempt_chain(self) -> None:
        first = self.workset_template_manifest()
        first["attempt"] = 1
        third = dict(first)
        third.update(
            {
                "id": "RUN-20260809-001-WORKSET-INVESTIGATE-RESEARCH-A003",
                "attempt": 3,
                "previous_manifest_id": first["id"],
            }
        )
        directory = self.workspace / "运行记录/2026-08"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "RUN-20260809-001-investigate-research-工作集清单.json").write_text(
            json.dumps(first, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        (directory / "RUN-20260809-001-investigate-research-a003-工作集清单.json").write_text(
            json.dumps(third, ensure_ascii=False) + "\n", encoding="utf-8"
        )

        self.assert_workspace_error(
            "运行记录/2026-08: workset attempt chain RUN-20260809-001/investigate/research must be contiguous from 1"
        )

    def test_strategy_version_rendered_from_template_is_valid(self) -> None:
        self.render_template(
            "策略版本模板.md",
            "策略库/基础画像/STR-BASE-TEST-v0.1.0.md",
            {
                "YYYY-MM-DDTHH:mm:ss+08:00": "2026-08-09T18:30:00+08:00",
                "STR-KIND-NAME": "STR-BASE-TEST",
            },
        )

        self.assert_valid_workspace()

    def test_non_initial_strategy_version_requires_a_declared_predecessor(self) -> None:
        self.render_template(
            "策略版本模板.md",
            "策略库/基础画像/STR-BASE-TEST-v0.2.0.md",
            {
                "YYYY-MM-DDTHH:mm:ss+08:00": "2026-08-09T18:30:00+08:00",
                "STR-KIND-NAME": "STR-BASE-TEST",
                'version: "0.1.0"': 'version: "0.2.0"',
            },
        )

        self.assert_workspace_error(
            "策略库/基础画像/STR-BASE-TEST-v0.2.0.md: non-initial strategy version missing previous_version"
        )

    def test_strategy_version_rejects_a_forward_predecessor(self) -> None:
        for version, previous_version in (("0.1.0", None), ("0.2.0", "0.3.0"), ("0.3.0", "0.1.0")):
            path = self.render_template(
                "策略版本模板.md",
                f"策略库/基础画像/STR-BASE-FORWARD-v{version}.md",
                {
                    "YYYY-MM-DDTHH:mm:ss+08:00": "2026-08-09T18:30:00+08:00",
                    "STR-KIND-NAME": "STR-BASE-FORWARD",
                    'version: "0.1.0"': f'version: "{version}"',
                },
            )
            if previous_version is not None:
                path.write_text(
                    path.read_text(encoding="utf-8").replace(
                        f'version: "{version}"',
                        f'version: "{version}"\nprevious_version: "{previous_version}"',
                    ),
                    encoding="utf-8",
                )

        self.assert_workspace_error(
            "策略库/基础画像/STR-BASE-FORWARD-v0.2.0.md: previous strategy version must be earlier: STR-BASE-FORWARD@0.3.0"
        )

    def test_strategy_version_rejects_a_predecessor_cycle(self) -> None:
        for version, previous_version in (("0.1.0", None), ("0.2.0", "0.3.0"), ("0.3.0", "0.2.0")):
            path = self.render_template(
                "策略版本模板.md",
                f"策略库/基础画像/STR-BASE-CYCLE-v{version}.md",
                {
                    "YYYY-MM-DDTHH:mm:ss+08:00": "2026-08-09T18:30:00+08:00",
                    "STR-KIND-NAME": "STR-BASE-CYCLE",
                    'version: "0.1.0"': f'version: "{version}"',
                },
            )
            if previous_version:
                path.write_text(
                    path.read_text(encoding="utf-8").replace(
                        f'version: "{version}"',
                        f'version: "{version}"\nprevious_version: "{previous_version}"',
                    ),
                    encoding="utf-8",
                )

        self.assert_workspace_error(
            "策略库/基础画像/STR-BASE-CYCLE-v0.2.0.md: previous strategy version must be earlier: STR-BASE-CYCLE@0.3.0"
        )

    def test_artifact_type_cannot_claim_a_different_workflow_directory(self) -> None:
        self.render_template(
            "运行记录模板.md",
            "证据包/2026-08/RUN-20260809-001.md",
            {
                "YYYY-MM-DDTHH:mm:ss+08:00": "2026-08-09T12:00:00+08:00",
                "YYYYMMDD": "20260809",
                "NNN": "001",
            },
        )

        self.assert_workspace_error(
            "证据包/2026-08/RUN-20260809-001.md: unsupported artifact_type run_record for 证据包"
        )

    def test_presentation_report_rejects_an_unresolved_stable_source_reference(self) -> None:
        self.render_template(
            "分析报告模板.md",
            "报告/2099-01/RPT-20990101-001.md",
            {
                "YYYY-MM-DDTHH:mm:ss+08:00": "2026-08-09T10:30:00+08:00",
                "YYYY-MM-DD": "2026-08-09",
                "YYYYMMDD": "20990101",
                "NNN": "001",
                "研究对象": "测试公司",
            },
        )

        self.assert_workspace_error(
            "报告/2099-01/RPT-20990101-001.md: source reference does not resolve: atom:EVI-20990101-001#001"
        )

    def test_workset_manifest_rejects_an_unresolved_atomic_reference(self) -> None:
        manifest = self.workset_template_manifest()
        manifest["stable_references"] = [
            {
                "ref": "atom:EVI-20990101-001#999",
                "unit_id": "EVI-20990101-001#999",
                "unit_type": "evidence_item",
                "authority": "evidence_package",
                "source_locator": {
                    "path": "证据包/2099-01/EVI-20990101-001.md",
                    "start_line": 12,
                    "end_line": 20,
                    "anchor": "EVI-20990101-001#999",
                    "sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
                },
                "selection_reasons": ["task_required"],
                "verification_status": "verified",
            }
        ]
        self.write_workset_manifest(manifest)

        self.assert_workspace_error(
            "运行记录/2026-08/RUN-20260809-001-investigate-research-工作集清单.json: stable reference does not resolve: atom:EVI-20990101-001#999"
        )

    def test_workset_manifest_rejects_a_relation_with_missing_endpoints(self) -> None:
        manifest = self.workset_template_manifest(
            {"WORKFLOW_UPPER": "ANALYZE", "WORKFLOW_SLUG": "analyze"}
        )
        manifest["relations"] = [
            {
                "from": "J20260809-001 v1",
                "to": "EVI-20260809-001#001",
                "type": "supported_by",
            }
        ]
        self.write_workset_manifest(
            manifest, "RUN-20260809-001-analyze-research-工作集清单.json"
        )

        self.assert_workspace_error(
            "运行记录/2026-08/RUN-20260809-001-analyze-research-工作集清单.json: relation endpoint is absent from stable_references: J20260809-001 v1 -> EVI-20260809-001#001"
        )

    def test_workset_manifest_rejects_a_relation_as_both_resolved_and_blocking(self) -> None:
        manifest = self.workset_template_manifest(
            {"WORKFLOW_UPPER": "ANALYZE", "WORKFLOW_SLUG": "analyze"}
        )
        source = self.workspace / "fixtures/relation-source.md"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("relation endpoints\n", encoding="utf-8")
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        manifest["stable_references"] = [
            {
                "ref": f"atom:{unit_id}",
                "unit_id": unit_id,
                "source_locator": {
                    "path": "fixtures/relation-source.md",
                    "sha256": digest,
                },
            }
            for unit_id in ("UNIT-A", "UNIT-B")
        ]
        relation = {"from": "UNIT-A", "to": "UNIT-B", "type": "supported_by"}
        manifest["relations"] = [relation]
        manifest["relation_checks"] = {"total": 2, "resolved": 1, "blocking_gaps": 1}
        manifest["gaps"] = [
            {
                "reason": "conflict",
                "blocking": True,
                "relation": relation,
            }
        ]
        manifest["coverage"].update({"blocking": True, "blocking_gap_count": 1})
        manifest["verification"]["required_unit_ids"] = ["UNIT-A", "UNIT-B"]
        manifest["quality"]["assembled_units"] = 2
        self.write_workset_manifest(
            manifest, "RUN-20260809-001-analyze-research-工作集清单.json"
        )

        self.assert_workspace_error(
            "运行记录/2026-08/RUN-20260809-001-analyze-research-工作集清单.json: relation edge cannot be both resolved and blocking: UNIT-A -> UNIT-B (supported_by)"
        )

    def test_task_contract_rejects_workspace_schema_as_contract_identity(self) -> None:
        contract = {
            "schema_version": "a-share-workspace-v3",
            "contract_id": "investigate.invalid-schema",
            "version": "1.0.0",
            "workflow": "investigate",
            "stage": "research",
            "object_types": ["stock"],
            "required_evidence": [],
        }
        path = self.workspace / ".agents/skills/a-share/shared/contracts/invalid-schema.json"
        path.write_text(json.dumps(contract, ensure_ascii=False) + "\n", encoding="utf-8")

        self.assert_workspace_error(
            ".agents/skills/a-share/shared/contracts/invalid-schema.json: unsupported task contract schema"
        )

    def test_evidence_package_cannot_claim_the_analysis_stage(self) -> None:
        path = self.render_template(
            "证据包模板.md",
            "证据包/2026-08/EVI-20260809-001.md",
            {
                "YYYY-MM-DDTHH:mm:ss+08:00": "2026-08-09T11:30:00+08:00",
                "YYYYMMDD": "20260809",
                "NNN": "001",
                "SOURCE-GROUP-ID": "SRCGRP-20260809-001-01",
                "PAYLOAD-ID": "PAYLOAD-20260809-001",
                "SOURCE-PATH": "payloads/PAYLOAD-20260809-001.txt",
                "SHA256_HEX": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
                "- **事件时间 / 市场交易日**：": "- **事件时间 / 市场交易日**：2026-08-09；2026-08-07",
                "- **数据口径**：": "- **数据口径**：人民币元；不复权",
                "- **关联对象 / 档案字段**：": "- **关联对象 / 档案字段**：个股:000001 / 主营业务",
                "- **过期条件 / 下次复核**：": "- **过期条件 / 下次复核**：下一份定期报告披露时；2026-10-31",
            },
        )
        text = path.read_text(encoding="utf-8").replace('stage: "investigate"', 'stage: "analyze"')
        path.write_text(text, encoding="utf-8")

        self.assert_workspace_error(
            "证据包/2026-08/EVI-20260809-001.md: invalid stage analyze"
        )

    def test_task_contract_requires_routing_identity_and_a_nonempty_evidence_floor(self) -> None:
        contract = {
            "schema_version": "a-share-task-contract-v1",
            "contract_id": "investigate.empty",
            "version": "1.0.0",
            "required_evidence": [],
        }
        path = self.workspace / ".agents/skills/a-share/shared/contracts/empty-contract.json"
        path.write_text(json.dumps(contract, ensure_ascii=False) + "\n", encoding="utf-8")

        payload = self.assert_workspace_error(
            ".agents/skills/a-share/shared/contracts/empty-contract.json: task contract missing workflow"
        )
        self.assertIn(
            ".agents/skills/a-share/shared/contracts/empty-contract.json: task contract missing stage",
            payload["errors"],
        )
        self.assertIn(
            ".agents/skills/a-share/shared/contracts/empty-contract.json: required_evidence must contain at least one requirement",
            payload["errors"],
        )

    def test_task_contract_requires_at_least_one_required_evidence_item(self) -> None:
        contract = {
            "schema_version": "a-share-task-contract-v1",
            "contract_id": "investigate.all-optional",
            "version": "1.0.0",
            "workflow": "investigate",
            "stage": "research",
            "required_evidence": [
                {
                    "requirement_id": "optional-context",
                    "unit_type": "evidence_item",
                    "required": False,
                }
            ],
        }
        path = self.workspace / ".agents/skills/a-share/shared/contracts/all-optional.json"
        path.write_text(json.dumps(contract, ensure_ascii=False) + "\n", encoding="utf-8")

        self.assert_workspace_error(
            ".agents/skills/a-share/shared/contracts/all-optional.json: required_evidence must contain at least one required requirement"
        )

    def test_titled_evidence_heading_still_receives_atomic_validation(self) -> None:
        path = self.render_template(
            "证据包模板.md",
            "证据包/2026-08/EVI-20260809-001.md",
            {
                "YYYY-MM-DDTHH:mm:ss+08:00": "2026-08-09T11:30:00+08:00",
                "YYYYMMDD": "20260809",
                "NNN": "001",
            },
        )
        text = path.read_text(encoding="utf-8").replace(
            "### EVI-20260809-001#001", "### EVI-20260809-001#001｜公司公告"
        )
        text = "\n".join(
            line
            for line in text.splitlines()
            if not line.startswith("- **来源组 ID**") and not line.startswith("- **来源定位**")
        )
        path.write_text(text + "\n", encoding="utf-8")

        self.assert_workspace_error(
            "证据包/2026-08/EVI-20260809-001.md: EVI-20260809-001#001 missing atomic field 来源组 ID"
        )

    def test_runtime_generated_stage_manifest_passes_workspace_validation(self) -> None:
        source_input = self.workspace / "source-input.txt"
        source_input.write_text(
            "\n".join(f"line {index}" for index in range(1, 21)) + "\n", encoding="utf-8"
        )
        source_cli = self.workspace / ".agents/skills/a-share/shared/scripts/source_payload_store.py"
        stored = subprocess.run(
            [
                sys.executable,
                str(source_cli),
                "put",
                "--root",
                str(self.workspace),
                "--run-id",
                "RUN-20260809-001",
                "--input-file",
                str(source_input),
                "--acquired-at",
                "2026-08-09T11:30:00+08:00",
            ],
            cwd=self.workspace,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(stored.returncode, 0, stored.stderr)
        source_locator = json.loads(stored.stdout)
        self.render_template(
            "证据包模板.md",
            "证据包/2026-08/EVI-20260809-001.md",
            {
                "YYYY-MM-DDTHH:mm:ss+08:00": "2026-08-09T11:30:00+08:00",
                "YYYYMMDD": "20260809",
                "NNN": "001",
                "PAYLOAD-ID": source_locator["payload_id"],
                "SOURCE-PATH": source_locator["path"],
                "SHA256_HEX": source_locator["sha256"],
                "BYTE_LENGTH": str(source_locator["byte_length"]),
                "- **状态**：未证实": "- **状态**：已确认",
                "- **事件时间 / 市场交易日**：": "- **事件时间 / 市场交易日**：2026-08-09；2026-08-07",
                "- **数据口径**：": "- **数据口径**：人民币元；不复权",
                "- **关联对象 / 档案字段**：": "- **关联对象 / 档案字段**：个股:000001 / 主营业务",
                "- **过期条件 / 下次复核**：": "- **过期条件 / 下次复核**：下一份定期报告披露时；2026-10-31",
            },
        )
        contract_path = self.workspace / ".agents/skills/a-share/shared/contracts/runtime-validation.json"
        contract_path.write_text(
            json.dumps(
                {
                    "schema_version": "a-share-task-contract-v1",
                    "contract_id": "investigate.runtime-validation",
                    "version": "1.0.0",
                    "workflow": "investigate",
                    "stage": "research",
                    "object_types": ["stock"],
                    "required_evidence": [
                        {
                            "requirement_id": "verified-fact",
                            "unit_type": "evidence_item",
                            "unit_id": "EVI-20260809-001#001",
                            "eligibility_mode": "prospective_current",
                            "required": True,
                            "allow_unknown": False,
                        }
                    ],
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        run_path = self.workspace / "runtime-run.json"
        run_path.write_text(
            json.dumps(
                {
                    "schema_version": "a-share-workspace-v3",
                    "workspace_root": str(self.workspace),
                    "run_id": "RUN-20260809-001",
                    "workflow": "investigate",
                    "stage": "research",
                    "information_cutoff": "2026-08-09T12:00:00+08:00",
                    "created_at": "2026-08-09T12:00:00+08:00",
                    "objects": ["个股:000001"],
                    "strategy_version": "STR-BASE-GROWTH@0.1.0",
                    "task_contract": str(contract_path),
                    "persist_workset_manifest": True,
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        context_cli = self.workspace / ".agents/skills/a-share/shared/scripts/context_workspace.py"
        assembled = subprocess.run(
            [sys.executable, str(context_cli), "assemble", "--run-manifest", str(run_path)],
            cwd=self.workspace,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(assembled.returncode, 0, assembled.stderr)

        manifest_path = (
            self.workspace
            / "运行记录/2026-08/RUN-20260809-001-investigate-research-工作集清单.json"
        )
        runtime_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        template_text = (self.workspace / "模板/工作集清单模板.md").read_text(encoding="utf-8")
        template_manifest = json.loads(
            re.search(r"```json\n(.*?)\n```", template_text, re.DOTALL).group(1)
        )
        for field in ("relation_checks", "verification", "coverage", "quality"):
            self.assertEqual(
                set(template_manifest[field]),
                set(runtime_manifest[field]),
                f"workset template drifted from runtime field {field}",
            )
        for field in ("task_contract", "contract_instantiation"):
            self.assertEqual(
                set(template_manifest[field]),
                set(runtime_manifest[field]),
                f"workset template drifted from runtime field {field}",
            )
        for field in (
            "instantiated_requirements",
            "instantiated_requirements_sha256",
        ):
            self.assertIn(field, template_manifest)

        self.assert_valid_workspace(warnings=False)

    def test_judgment_entry_requires_an_explicit_evidence_reference_or_abstention_gap(self) -> None:
        path = self.render_template(
            "判断条目模板.md",
            "判断日志/2026-08.md",
            {
                "YYYY-MM-DDTHH:mm:ss+08:00": "2026-08-09T10:30:00+08:00",
                "YYYY-MM": "2026-08",
                "YYYYMMDD": "20260809",
                "YYYYMM": "202608",
                "NNN": "001",
            },
        )
        text = "\n".join(
            line
            for line in path.read_text(encoding="utf-8").splitlines()
            if not line.startswith("- **证据包 / 原子证据项**")
        )
        path.write_text(text + "\n", encoding="utf-8")

        self.assert_workspace_error(
            "判断日志/2026-08.md: J20260809-001 v1 missing atomic field 证据包 / 原子证据项"
        )

    def test_judgment_entry_rejects_a_free_text_evidence_pointer(self) -> None:
        path = self.render_template(
            "判断条目模板.md",
            "判断日志/2026-08.md",
            {
                "YYYY-MM-DDTHH:mm:ss+08:00": "2026-08-09T10:30:00+08:00",
                "YYYY-MM": "2026-08",
                "YYYYMMDD": "20260809",
                "YYYYMM": "202608",
                "NNN": "001",
                "- **原子命题**：": "- **原子命题**：当前证据不足，至观察时限前维持弃权",
                "- **证据包 / 原子证据项**：": "- **证据包 / 原子证据项**：见分析报告附件",
                "- **证伪条件**：": "- **证伪条件**：任务证据底线在截止前完整满足",
            },
        )

        self.assert_workspace_error(
            "判断日志/2026-08.md: J20260809-001 v1 evidence field must contain a stable EVI atomic reference or explicit unknown—正式弃权 gap"
        )

    def test_judgment_entry_rejects_an_unresolved_evidence_reference(self) -> None:
        self.render_template(
            "判断条目模板.md",
            "判断日志/2026-08.md",
            {
                "YYYY-MM-DDTHH:mm:ss+08:00": "2026-08-09T10:30:00+08:00",
                "YYYY-MM": "2026-08",
                "YYYYMMDD": "20260809",
                "YYYYMM": "202608",
                "NNN": "001",
                "- **证据包 / 原子证据项**：": "- **证据包 / 原子证据项**：EVI-20990101-001#001",
            },
        )

        self.assert_workspace_error(
            "判断日志/2026-08.md: J20260809-001 v1 evidence reference does not resolve: atom:EVI-20990101-001#001"
        )

    def test_judgment_entry_requires_a_falsifiable_atomic_proposition(self) -> None:
        path = self.render_template(
            "判断条目模板.md",
            "判断日志/2026-08.md",
            {
                "YYYY-MM-DDTHH:mm:ss+08:00": "2026-08-09T10:30:00+08:00",
                "YYYY-MM": "2026-08",
                "YYYYMMDD": "20260809",
                "YYYYMM": "202608",
                "NNN": "001",
                "- **证据包 / 原子证据项**：": "- **证据包 / 原子证据项**：unknown—正式弃权；证据缺口见下",
                "- **证伪条件**：": "- **证伪条件**：等待信号出现前不形成方向判断",
            },
        )
        text = "\n".join(
            line for line in path.read_text(encoding="utf-8").splitlines() if not line.startswith("- **原子命题**")
        )
        path.write_text(text + "\n", encoding="utf-8")

        self.assert_workspace_error(
            "判断日志/2026-08.md: J20260809-001 v1 missing atomic field 原子命题"
        )

    def test_observation_candidate_requires_predeclared_confirmation_and_invalidation(self) -> None:
        path = self.render_template(
            "观察候选模板.md",
            "观察日志/2026-08.md",
            {
                "YYYY-MM-DDTHH:mm:ss+08:00": "2026-08-09T09:00:00+08:00",
                "YYYY-MM": "2026-08",
                "YYYYMMDD": "20260809",
                "YYYYMM": "202608",
                "NNN": "001",
                "- **失效条件**：": "- **失效条件**：相对强度跌破动态基线",
            },
        )
        text = "\n".join(
            line for line in path.read_text(encoding="utf-8").splitlines() if not line.startswith("- **确认触发**")
        )
        path.write_text(text + "\n", encoding="utf-8")

        self.assert_workspace_error(
            "观察日志/2026-08.md: C20260809-001 v1 missing atomic field 确认触发"
        )

    def test_observation_log_rejects_a_candidate_version_gap(self) -> None:
        path = self.render_template(
            "观察候选模板.md",
            "观察日志/2026-08.md",
            {
                "YYYY-MM-DDTHH:mm:ss+08:00": "2026-08-09T09:00:00+08:00",
                "YYYY-MM": "2026-08",
                "YYYYMMDD": "20260809",
                "YYYYMM": "202608",
                "NNN": "001",
            },
        )
        path.write_text(path.read_text(encoding="utf-8") + "\n### C20260809-001 v3\n", encoding="utf-8")

        self.assert_workspace_error(
            "观察日志/2026-08.md: non-contiguous version chain for C20260809-001: [1, 3]"
        )

    def test_presentation_report_filename_must_match_its_declared_id(self) -> None:
        self.render_template(
            "分析报告模板.md",
            "报告/2026-08/RPT-20260809-999.md",
            {
                "YYYY-MM-DDTHH:mm:ss+08:00": "2026-08-09T10:30:00+08:00",
                "YYYY-MM-DD": "2026-08-09",
                "YYYYMMDD": "20260809",
                "NNN": "001",
                "研究对象": "测试公司",
                'source_refs: "atom:EVI-20260809-001#001"': 'source_refs: "unknown"\nsource_refs_unknown_reason: "synthetic template has no research input"',
            },
        )

        self.assert_workspace_error(
            "报告/2026-08/RPT-20260809-999.md: filename must be RPT-20260809-001.md"
        )

    def test_authoritative_artifact_without_frontmatter_is_a_blocking_error(self) -> None:
        path = self.workspace / "证据包/2026-08/untyped-evidence.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# 未声明证据\n\n没有 schema 和权威类型。\n", encoding="utf-8")

        self.assert_workspace_error(
            "证据包/2026-08/untyped-evidence.md: authoritative artifact missing frontmatter"
        )


if __name__ == "__main__":
    unittest.main()
