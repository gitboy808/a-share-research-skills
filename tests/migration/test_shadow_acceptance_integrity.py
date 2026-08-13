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
SHADOW_CLI = REPO_ROOT / "scripts/shadow_replay_workspace.py"
TEST_SESSION_ID = "shadow-session-test-001"
def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical_sha256(value: object) -> str:
    return _sha256_text(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def _tree_files(root: Path) -> list[dict[str, str]]:
    return [
        {"path": path.relative_to(root).as_posix(), "sha256": _sha256(path)}
        for path in sorted(root.rglob("*"))
        if path.is_file()
        and not any(part in {".git", ".context", "__pycache__"} for part in path.relative_to(root).parts)
    ]


def _tree_sha256(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if not path.is_file() or any(part in {".git", ".context", "__pycache__"} for part in relative.parts):
            continue
        digest.update(relative.as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(_sha256(path).encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def _runtime_surface_files(root: Path) -> list[dict[str, str]]:
    entries: list[tuple[str, str]] = []
    for relative in RUNTIME_PATHS:
        source = root / relative
        paths = [source] if source.is_file() else sorted(source.rglob("*"))
        entries.extend(
            (path.relative_to(root).as_posix(), _sha256(path))
            for path in paths
            if path.is_file() and not any(part in {"__pycache__", ".DS_Store"} for part in path.parts)
        )
    return [{"path": path, "sha256": file_hash} for path, file_hash in sorted(entries)]


def _runtime_surface_sha256(root: Path) -> str:
    digest = hashlib.sha256()
    for entry in _runtime_surface_files(root):
        path = entry["path"]
        file_hash = entry["sha256"]
        digest.update(path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(file_hash.encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


class ShadowAcceptanceIntegrityTests(unittest.TestCase):
    @staticmethod
    def _observed_trace_events() -> list[dict[str, object]]:
        values = {
            ("baseline", "raw_tool_payload_characters"): (100000, "characters", "tool_payload_counter"),
            ("candidate", "raw_tool_payload_characters"): (10000, "characters", "tool_payload_counter"),
            ("baseline", "main_context_characters"): (200000, "characters", "context_character_counter"),
            ("candidate", "main_context_characters"): (80000, "characters", "context_character_counter"),
            ("baseline", "main_context_peak_tokens"): (200000, "tokens", "model_context_telemetry"),
            ("candidate", "main_context_peak_tokens"): (100000, "tokens", "model_context_telemetry"),
        }
        return [
            {
                "event_id": f"OBS-{index:03d}",
                "session_id": TEST_SESSION_ID,
                "phase": phase,
                "metric": metric,
                "value": value,
                "unit": unit,
                "source_kind": source_kind,
                "observation_status": "observed",
                "observed_at": "2026-08-01T12:00:00+08:00",
            }
            for index, ((phase, metric), (value, unit, source_kind)) in enumerate(values.items(), start=1)
        ]

    def _bind_trace_events(
        self,
        inputs: dict[str, Path],
        events: list[dict[str, object]],
    ) -> list[dict[str, object]]:
        trace = json.loads(inputs["measurement_trace"].read_text(encoding="utf-8"))
        raw_lines = [
            json.dumps(
                {
                    "event_id": event["event_id"],
                    "trace_id": trace["trace_id"],
                    "session_id": event["session_id"],
                    "migration_id": trace["migration_id"],
                    "phase": event["phase"],
                    "metric": event["metric"],
                    "value": event["value"],
                    "unit": event["unit"],
                    "source_kind": event["source_kind"],
                    "observation_status": event["observation_status"],
                    "observed_at": event["observed_at"],
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            for event in events
        ]
        export = inputs["telemetry_export"]
        export.write_text("\n".join(raw_lines) + ("\n" if raw_lines else ""), encoding="utf-8")
        export_hash = _sha256(export)
        bound_events: list[dict[str, object]] = []
        for line_number, (event, raw_line) in enumerate(zip(events, raw_lines), start=1):
            bound = dict(event)
            bound["source_locator"] = {
                "path": export.name,
                "start_line": line_number,
                "end_line": line_number,
                "source_sha256": export_hash,
                "content_sha256": _sha256_text(raw_line),
            }
            bound_events.append(bound)
        trace["events"] = bound_events
        inputs["measurement_trace"].write_text(
            json.dumps(trace, sort_keys=True), encoding="utf-8"
        )
        self._refresh_binding(
            inputs["suite"], "measurement_trace", inputs["measurement_trace"]
        )
        return bound_events

    def _bind_old_execution_artifact(
        self,
        inputs: dict[str, Path],
        *,
        scenario_id: str,
        required_unit_ids: list[str],
        selected_unit_ids: list[str],
        workflow: str,
        stage: str,
        contract: str,
        selector: dict[str, object],
    ) -> None:
        fields = {
            "workflow": workflow,
            "stage": stage,
            "contract": contract,
            "selector": selector,
            "required_unit_ids": required_unit_ids,
            "selected_unit_ids": selected_unit_ids,
        }
        relative = Path("运行记录") / f"shadow-{scenario_id}.json"
        artifact = inputs["old_workspace"] / relative
        artifact.parent.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(
            fields,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        artifact.write_text(encoded + "\n", encoding="utf-8")
        baseline = json.loads(inputs["old_baseline"].read_text(encoding="utf-8"))
        records = [
            record
            for record in baseline.get("execution_artifacts", [])
            if record.get("scenario_id") != scenario_id
        ]
        records.append(
            {
                "scenario_id": scenario_id,
                "source_locator": {
                    "path": relative.as_posix(),
                    "start_line": 1,
                    "end_line": 1,
                    "source_sha256": _sha256(artifact),
                    "content_sha256": _sha256_text(encoded),
                },
                "fields": fields,
                "fields_sha256": _canonical_sha256(fields),
            }
        )
        baseline["execution_artifacts"] = records
        baseline["workspace_sha256"] = _tree_sha256(inputs["old_workspace"])
        baseline["files"] = _tree_files(inputs["old_workspace"])
        inputs["old_baseline"].write_text(
            json.dumps(baseline, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        self._refresh_binding(inputs["suite"], "old_baseline", inputs["old_baseline"])
        migration = json.loads(inputs["migration_report"].read_text(encoding="utf-8"))
        migration["input_snapshot"] = {
            "sha256": _tree_sha256(inputs["old_workspace"]),
            "files": _tree_files(inputs["old_workspace"]),
        }
        inputs["migration_report"].write_text(
            json.dumps(migration, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        self._refresh_binding(
            inputs["suite"], "migration_report", inputs["migration_report"]
        )

    def _bind_old_evidence_semantic(
        self,
        inputs: dict[str, Path],
        *,
        workspace: Path,
        relative_path: str,
        scenario_id: str,
        unit_id: str,
        proposition: str,
        information_cutoff: str,
        status: str,
        workflow: str,
        stage: str,
        contract: str,
        selector: dict[str, object],
    ) -> None:
        source = workspace / relative_path
        frozen = inputs["old_workspace"] / relative_path
        frozen.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, frozen)
        lines = frozen.read_text(encoding="utf-8").splitlines()
        start_line = lines.index(f"## {unit_id}") + 1
        end_line = next(
            (
                index
                for index in range(start_line, len(lines))
                if lines[index].startswith("## ")
            ),
            len(lines),
        )
        excerpt = "\n".join(lines[start_line - 1 : end_line])
        fields = {
            "proposition": proposition,
            "information_cutoff": information_cutoff,
            "status": status,
            "relations": [],
        }
        baseline = json.loads(inputs["old_baseline"].read_text(encoding="utf-8"))
        baseline["semantic_units"] = [
            item
            for item in baseline.get("semantic_units", [])
            if item.get("scenario_id") != scenario_id
        ]
        baseline["semantic_units"].append(
            {
                "scenario_id": scenario_id,
                "unit_id": unit_id,
                "source_locator": {
                    "path": relative_path,
                    "start_line": start_line,
                    "end_line": end_line,
                    "source_sha256": _sha256(frozen),
                    "content_sha256": _sha256_text(excerpt),
                },
                "fields": fields,
                "fields_sha256": _canonical_sha256(fields),
            }
        )
        inputs["old_baseline"].write_text(
            json.dumps(baseline, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        self._bind_old_execution_artifact(
            inputs,
            scenario_id=scenario_id,
            required_unit_ids=[unit_id],
            selected_unit_ids=[unit_id],
            workflow=workflow,
            stage=stage,
            contract=contract,
            selector=selector,
        )

    @staticmethod
    def _refresh_binding(suite_path: Path, name: str, path: Path) -> None:
        suite = json.loads(suite_path.read_text(encoding="utf-8"))
        suite["bindings"][name]["sha256"] = _tree_sha256(path) if path.is_dir() else _sha256(path)
        suite_path.write_text(json.dumps(suite, sort_keys=True), encoding="utf-8")

    def _bound_inputs(self, root: Path, workspace: Path) -> dict[str, Path]:
        for relative in RUNTIME_PATHS:
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

        migration_id = "MIG-SHADOW-BOUND-001"
        runtime_hash = _runtime_surface_sha256(REPO_ROOT)
        old_workspace = root / "frozen-old-workspace"
        old_workspace.mkdir()
        (old_workspace / "README.md").write_text("# Frozen old workspace\n", encoding="utf-8")
        old_baseline = root / "frozen-old-baseline.json"
        old_baseline.write_text(
            json.dumps(
                {
                    "schema_version": "a-share-shadow-baseline-v1",
                    "session_id": TEST_SESSION_ID,
                    "migration_id": migration_id,
                    "captured_at": "2026-08-01T00:00:00+08:00",
                    "workspace_root": str(old_workspace),
                    "workspace_sha256": _tree_sha256(old_workspace),
                    "files": _tree_files(old_workspace),
                    "semantic_units": [],
                    "execution_artifacts": [],
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        migration_report = root / "migration-report.json"
        migration_report.write_text(
            json.dumps(
                {
                    "schema_version": "a-share-workspace-v3",
                    "migration_id": migration_id,
                    "status": "structural_migration_completed",
                    "migration_status": "completed",
                    "input_root": str(old_workspace),
                    "input_snapshot": {
                        "sha256": _tree_sha256(old_workspace),
                        "files": _tree_files(old_workspace),
                    },
                    "output_root": str(workspace),
                    "runtime_surface": {
                        "schema_version": "a-share-workspace-v3",
                        "sha256": runtime_hash,
                        "installed_roots": [path.as_posix() for path in RUNTIME_PATHS],
                        "files": _runtime_surface_files(REPO_ROOT),
                    },
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        telemetry_export = root / "telemetry-export.jsonl"
        telemetry_export.write_text("", encoding="utf-8")
        measurement_trace = root / "measurement-trace.json"
        measurement_trace.write_text(
            json.dumps(
                {
                    "schema_version": "a-share-shadow-measurement-trace-v1",
                    "trace_id": "TRACE-BOUND-001",
                    "session_id": TEST_SESSION_ID,
                    "migration_id": migration_id,
                    "observed_at": "2026-08-01T12:00:00+08:00",
                    "events": [],
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        suite = root / "shadow-suite.json"
        suite.write_text(
            json.dumps(
                {
                    "schema_version": "a-share-shadow-replay-v2",
                    "session_id": TEST_SESSION_ID,
                    "migration_id": migration_id,
                    "bindings": {
                        "old_baseline": {
                            "path": str(old_baseline),
                            "sha256": _sha256(old_baseline),
                            "session_id": TEST_SESSION_ID,
                        },
                        "migration_report": {
                            "path": str(migration_report),
                            "sha256": _sha256(migration_report),
                            "session_id": TEST_SESSION_ID,
                        },
                        "new_workspace": {
                            "path": str(workspace),
                            "sha256": _tree_sha256(workspace),
                            "session_id": TEST_SESSION_ID,
                        },
                        "measurement_trace": {
                            "path": str(measurement_trace),
                            "sha256": _sha256(measurement_trace),
                            "session_id": TEST_SESSION_ID,
                        },
                    },
                    "targets": {
                        "min_raw_tool_payload_reduction_ratio": 0.8,
                        "max_main_context_ratio": 0.52,
                        "max_main_context_peak_tokens": 100000,
                    },
                    "scenarios": [],
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        return {
            "suite": suite,
            "old_baseline": old_baseline,
            "migration_report": migration_report,
            "measurement_trace": measurement_trace,
            "telemetry_export": telemetry_export,
            "old_workspace": old_workspace,
        }

    @staticmethod
    def _write_scan_evidence(workspace: Path) -> None:
        path = workspace / "证据包/2026-08/EVI-20260801-001.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "---\n"
            'schema_version: "a-share-workspace-v3"\n'
            'artifact_type: "evidence_package"\n'
            'id: "EVI-20260801-001"\n'
            'status: "complete"\n'
            'information_cutoff: "2026-08-01T09:00:00+08:00"\n'
            'created_at: "2026-08-01T09:00:00+08:00"\n'
            'objects: ["market:A股"]\n'
            'stage: "investigate"\n'
            'authority: "evidence_fact_source"\n'
            "---\n"
            "# 扫描冻结证据\n\n"
            "## EVI-20260801-001#1\n\n"
            "- **事实陈述**：市场状态已核验。\n"
            "- **关联对象/档案字段**：market:A股 / market_state\n"
            "- **状态**：已确认\n"
            "- **信息快照**：2026-08-01T09:00:00+08:00\n"
            "- **证据角色**：primary\n\n"
            "## EVI-20260801-001#2\n\n"
            "- **事实陈述**：热度确认已核验。\n"
            "- **关联对象/档案字段**：market:A股 / heat_confirmation\n"
            "- **状态**：已确认\n"
            "- **信息快照**：2026-08-01T09:00:00+08:00\n"
            "- **证据角色**：confirmation\n",
            encoding="utf-8",
        )

    @staticmethod
    def _write_analyze_risk_evidence(workspace: Path) -> None:
        cutoff = "2026-08-01T09:00:00+08:00"
        rows = (
            ("1", "对象事实已核验。", "verified_fact", "已确认", "primary"),
            ("2", "来源口径存在冲突。", "conflict_probe", "冲突", "veto"),
            ("3", "该事实已被正式否证。", "denial_probe", "已否证", "veto"),
            ("4", "反向证据已核验但未触发否决。", "counterevidence", "已确认", "veto"),
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
                    cutoff=cutoff,
                    heading_level=2,
                )
                for number, fact, field, status, role in rows
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
                    source_refs="EVI-20260801-002#1",
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

    def test_legacy_self_reported_manifest_cannot_use_the_shadow_cli(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            workspace.mkdir()
            legacy_suite = root / "legacy-shadow-suite.json"
            legacy_suite.write_text(
                json.dumps(
                    {
                        "schema_version": "a-share-shadow-replay-v1",
                        "baseline": {
                            "raw_tool_payload_characters": 100000,
                            "main_context_peak_tokens": 200000,
                        },
                        "candidate_observation": {
                            "raw_tool_payload_characters": 1,
                            "main_context_peak_tokens": 1,
                        },
                        "scenarios": [],
                    }
                ),
                encoding="utf-8",
            )
            report = root / "report.json"

            result = subprocess.run(
                [
                    sys.executable,
                    str(SHADOW_CLI),
                    "--workspace",
                    str(workspace),
                    "--scenarios",
                    str(legacy_suite),
                    "--output",
                    str(report),
                ],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn("--old-baseline", result.stderr)
            self.assertIn("--migration-report", result.stderr)
            self.assertIn("--measurement-trace", result.stderr)
            self.assertFalse(report.exists())

    def test_v1_manifest_is_rejected_even_when_new_artifact_arguments_are_supplied(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            workspace.mkdir()
            inputs = self._bound_inputs(root, workspace)
            suite = json.loads(inputs["suite"].read_text(encoding="utf-8"))
            suite["schema_version"] = "a-share-shadow-replay-v1"
            inputs["suite"].write_text(json.dumps(suite, sort_keys=True), encoding="utf-8")
            report = root / "report.json"

            result = subprocess.run(
                [
                    sys.executable,
                    str(SHADOW_CLI),
                    "--workspace",
                    str(workspace),
                    "--scenarios",
                    str(inputs["suite"]),
                    "--old-baseline",
                    str(inputs["old_baseline"]),
                    "--migration-report",
                    str(inputs["migration_report"]),
                    "--measurement-trace",
                    str(inputs["measurement_trace"]),
                    "--output",
                    str(report),
                ],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn("unsupported shadow replay schema_version", result.stderr)
            self.assertFalse(report.exists())

    def test_suite_binds_all_acceptance_inputs_by_path_hash_and_session(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            workspace.mkdir()
            inputs = self._bound_inputs(root, workspace)
            report = root / "report.json"

            result = subprocess.run(
                [
                    sys.executable,
                    str(SHADOW_CLI),
                    "--workspace",
                    str(workspace),
                    "--scenarios",
                    str(inputs["suite"]),
                    "--old-baseline",
                    str(inputs["old_baseline"]),
                    "--migration-report",
                    str(inputs["migration_report"]),
                    "--measurement-trace",
                    str(inputs["measurement_trace"]),
                    "--output",
                    str(report),
                ],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 1, result.stderr)
            payload = json.loads(report.read_text(encoding="utf-8"))
            self.assertTrue(payload["checks"]["input_bindings"]["passed"])
            self.assertEqual(payload["inputs"]["session_id"], TEST_SESSION_ID)
            for name in ("old_baseline", "migration_report", "new_workspace", "measurement_trace"):
                self.assertRegex(payload["inputs"][name]["sha256"], r"^[0-9a-f]{64}$")

    def test_migration_runtime_surface_matches_both_runner_and_output_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            workspace.mkdir()
            inputs = self._bound_inputs(root, workspace)
            report = root / "report.json"

            result = subprocess.run(
                [
                    sys.executable,
                    str(SHADOW_CLI),
                    "--workspace",
                    str(workspace),
                    "--scenarios",
                    str(inputs["suite"]),
                    "--old-baseline",
                    str(inputs["old_baseline"]),
                    "--migration-report",
                    str(inputs["migration_report"]),
                    "--measurement-trace",
                    str(inputs["measurement_trace"]),
                    "--output",
                    str(report),
                ],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 1, result.stderr)
            check = json.loads(report.read_text(encoding="utf-8"))["checks"]["runtime_surface"]
            self.assertTrue(check["passed"])
            self.assertEqual(check["runner_sha256"], check["installed_sha256"])
            self.assertEqual(check["workspace_sha256"], check["installed_sha256"])

    def test_release_metrics_are_derived_only_from_observed_trace_events(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            workspace.mkdir()
            inputs = self._bound_inputs(root, workspace)
            self._bind_trace_events(inputs, self._observed_trace_events())
            report = root / "report.json"

            result = subprocess.run(
                [
                    sys.executable,
                    str(SHADOW_CLI),
                    "--workspace",
                    str(workspace),
                    "--scenarios",
                    str(inputs["suite"]),
                    "--old-baseline",
                    str(inputs["old_baseline"]),
                    "--migration-report",
                    str(inputs["migration_report"]),
                    "--measurement-trace",
                    str(inputs["measurement_trace"]),
                    "--output",
                    str(report),
                ],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 1, result.stderr)
            payload = json.loads(report.read_text(encoding="utf-8"))
            self.assertTrue(payload["checks"]["measurement_trace"]["passed"])
            self.assertTrue(payload["checks"]["proxy_targets"]["passed"])
            self.assertTrue(payload["checks"]["context_peak_tokens"]["passed"])
            self.assertEqual(payload["proxy_metrics"]["raw_tool_payload_reduction_ratio"], 0.9)
            self.assertEqual(payload["proxy_metrics"]["main_context_ratio"], 0.4)
            self.assertEqual(payload["proxy_metrics"]["model_token_replay"]["candidate_peak_tokens"], 100000)

    def test_hand_authored_measurement_events_without_raw_telemetry_locators_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            workspace.mkdir()
            inputs = self._bound_inputs(root, workspace)
            trace = json.loads(inputs["measurement_trace"].read_text(encoding="utf-8"))
            trace["events"] = self._observed_trace_events()
            inputs["measurement_trace"].write_text(
                json.dumps(trace, sort_keys=True), encoding="utf-8"
            )
            self._refresh_binding(
                inputs["suite"], "measurement_trace", inputs["measurement_trace"]
            )

            result = subprocess.run(
                [
                    sys.executable,
                    str(SHADOW_CLI),
                    "--workspace",
                    str(workspace),
                    "--scenarios",
                    str(inputs["suite"]),
                    "--old-baseline",
                    str(inputs["old_baseline"]),
                    "--migration-report",
                    str(inputs["migration_report"]),
                    "--measurement-trace",
                    str(inputs["measurement_trace"]),
                    "--output",
                    str(root / "report.json"),
                ],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn("raw telemetry source_locator", result.stderr)

    def test_raw_telemetry_export_cannot_hide_unreferenced_events(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            workspace.mkdir()
            inputs = self._bound_inputs(root, workspace)
            self._bind_trace_events(inputs, self._observed_trace_events())
            inputs["telemetry_export"].write_text(
                inputs["telemetry_export"].read_text(encoding="utf-8")
                + '{"event_id":"HIDDEN","value":1}\n',
                encoding="utf-8",
            )
            trace = json.loads(inputs["measurement_trace"].read_text(encoding="utf-8"))
            new_source_hash = _sha256(inputs["telemetry_export"])
            for event in trace["events"]:
                event["source_locator"]["source_sha256"] = new_source_hash
            inputs["measurement_trace"].write_text(
                json.dumps(trace, sort_keys=True), encoding="utf-8"
            )
            self._refresh_binding(
                inputs["suite"], "measurement_trace", inputs["measurement_trace"]
            )

            result = subprocess.run(
                [
                    sys.executable,
                    str(SHADOW_CLI),
                    "--workspace",
                    str(workspace),
                    "--scenarios",
                    str(inputs["suite"]),
                    "--old-baseline",
                    str(inputs["old_baseline"]),
                    "--migration-report",
                    str(inputs["migration_report"]),
                    "--measurement-trace",
                    str(inputs["measurement_trace"]),
                    "--output",
                    str(root / "report.json"),
                ],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn("unreferenced JSONL lines", result.stderr)

    def test_old_semantics_are_read_from_a_hashed_frozen_workspace_locator(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            workspace.mkdir()
            self._write_scan_evidence(workspace)
            inputs = self._bound_inputs(root, workspace)

            old_source = inputs["old_workspace"] / "证据包/市场快照.md"
            old_source.parent.mkdir(parents=True, exist_ok=True)
            old_text = (
                "## EVI-20260801-001#1\n"
                "- **事实陈述**：市场状态已核验。\n"
                "- **状态**：已确认\n"
                "- **信息快照**：2026-08-01T09:00:00+08:00\n"
            )
            old_source.write_text(old_text, encoding="utf-8")
            fields = {
                "proposition": "市场状态已核验。",
                "information_cutoff": "2026-08-01T09:00:00+08:00",
                "status": "已确认",
                "relations": [],
            }
            baseline = json.loads(inputs["old_baseline"].read_text(encoding="utf-8"))
            baseline["workspace_sha256"] = _tree_sha256(inputs["old_workspace"])
            baseline["files"] = _tree_files(inputs["old_workspace"])
            baseline["semantic_units"] = [
                {
                    "scenario_id": "scan-market",
                    "unit_id": "EVI-20260801-001#1",
                    "source_locator": {
                        "path": "证据包/市场快照.md",
                        "start_line": 1,
                        "end_line": 4,
                        "source_sha256": _sha256(old_source),
                        "content_sha256": _sha256_text(old_text.rstrip("\n")),
                    },
                    "fields": fields,
                    "fields_sha256": _canonical_sha256(fields),
                }
            ]
            inputs["old_baseline"].write_text(json.dumps(baseline, ensure_ascii=False, sort_keys=True), encoding="utf-8")
            self._refresh_binding(inputs["suite"], "old_baseline", inputs["old_baseline"])
            migration = json.loads(inputs["migration_report"].read_text(encoding="utf-8"))
            migration["input_snapshot"] = {
                "sha256": _tree_sha256(inputs["old_workspace"]),
                "files": _tree_files(inputs["old_workspace"]),
            }
            inputs["migration_report"].write_text(json.dumps(migration, ensure_ascii=False, sort_keys=True), encoding="utf-8")
            self._refresh_binding(inputs["suite"], "migration_report", inputs["migration_report"])
            suite = json.loads(inputs["suite"].read_text(encoding="utf-8"))
            suite["scenarios"] = [
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
            ]
            inputs["suite"].write_text(json.dumps(suite, ensure_ascii=False, sort_keys=True), encoding="utf-8")
            self._bind_old_execution_artifact(
                inputs,
                scenario_id="scan-market",
                required_unit_ids=["EVI-20260801-001#1"],
                selected_unit_ids=["EVI-20260801-001#1"],
                workflow="scan",
                stage="scan",
                contract="scan-v1.json",
                selector={"objects": ["market:A股"]},
            )
            report = root / "report.json"

            result = subprocess.run(
                [
                    sys.executable,
                    str(SHADOW_CLI),
                    "--workspace",
                    str(workspace),
                    "--scenarios",
                    str(inputs["suite"]),
                    "--old-baseline",
                    str(inputs["old_baseline"]),
                    "--migration-report",
                    str(inputs["migration_report"]),
                    "--measurement-trace",
                    str(inputs["measurement_trace"]),
                    "--output",
                    str(report),
                ],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 1, result.stderr)
            payload = json.loads(report.read_text(encoding="utf-8"))
            self.assertTrue(payload["checks"]["old_baseline_provenance"]["passed"])
            self.assertTrue(
                payload["checks"]["semantic_equivalence"]["passed"],
                payload["scenarios"][0],
            )
            self.assertEqual(payload["scenarios"][0]["semantic_comparisons"][0]["old"], fields)

            self._bind_old_execution_artifact(
                inputs,
                scenario_id="scan-market",
                required_unit_ids=[
                    "EVI-20260801-001#1",
                    "EVI-20260801-001#2",
                ],
                selected_unit_ids=[
                    "EVI-20260801-001#1",
                    "EVI-20260801-001#2",
                ],
                workflow="scan",
                stage="scan",
                contract="scan-v1.json",
                selector={"objects": ["market:A股"]},
            )
            omitted = subprocess.run(
                [
                    sys.executable,
                    str(SHADOW_CLI),
                    "--workspace",
                    str(workspace),
                    "--scenarios",
                    str(inputs["suite"]),
                    "--old-baseline",
                    str(inputs["old_baseline"]),
                    "--migration-report",
                    str(inputs["migration_report"]),
                    "--measurement-trace",
                    str(inputs["measurement_trace"]),
                    "--output",
                    str(report),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(omitted.returncode, 2, omitted.stderr)
            self.assertIn("semantic_units do not match frozen selected_unit_ids", omitted.stderr)
            self._bind_old_execution_artifact(
                inputs,
                scenario_id="scan-market",
                required_unit_ids=["EVI-20260801-001#1"],
                selected_unit_ids=["EVI-20260801-001#1"],
                workflow="scan",
                stage="scan",
                contract="scan-v1.json",
                selector={"objects": ["market:A股"]},
            )

            old_source.write_text(
                old_text + "- **证据包 / 原子证据项**：EVI-20260731-099#1\n",
                encoding="utf-8",
            )
            baseline = json.loads(inputs["old_baseline"].read_text(encoding="utf-8"))
            baseline["workspace_sha256"] = _tree_sha256(inputs["old_workspace"])
            baseline["files"] = _tree_files(inputs["old_workspace"])
            baseline["semantic_units"][0]["source_locator"].update(
                {
                    "end_line": 5,
                    "source_sha256": _sha256(old_source),
                    "content_sha256": _sha256_text(
                        old_source.read_text(encoding="utf-8").rstrip("\n")
                    ),
                }
            )
            inputs["old_baseline"].write_text(
                json.dumps(baseline, ensure_ascii=False, sort_keys=True),
                encoding="utf-8",
            )
            self._refresh_binding(inputs["suite"], "old_baseline", inputs["old_baseline"])
            migration = json.loads(inputs["migration_report"].read_text(encoding="utf-8"))
            migration["input_snapshot"] = {
                "sha256": _tree_sha256(inputs["old_workspace"]),
                "files": _tree_files(inputs["old_workspace"]),
            }
            inputs["migration_report"].write_text(
                json.dumps(migration, ensure_ascii=False, sort_keys=True),
                encoding="utf-8",
            )
            self._refresh_binding(
                inputs["suite"], "migration_report", inputs["migration_report"]
            )

            tampered = subprocess.run(
                [
                    sys.executable,
                    str(SHADOW_CLI),
                    "--workspace",
                    str(workspace),
                    "--scenarios",
                    str(inputs["suite"]),
                    "--old-baseline",
                    str(inputs["old_baseline"]),
                    "--migration-report",
                    str(inputs["migration_report"]),
                    "--measurement-trace",
                    str(inputs["measurement_trace"]),
                    "--output",
                    str(report),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(tampered.returncode, 2, tampered.stderr)
            self.assertIn("relations do not exhaust the source excerpt", tampered.stderr)

    def test_frozen_judgment_uses_the_canonical_research_status_field(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            workspace.mkdir()
            inputs = self._bound_inputs(root, workspace)

            old_source = inputs["old_workspace"] / "判断日志/2026-08.md"
            old_source.parent.mkdir(parents=True, exist_ok=True)
            old_text = (
                "### J20260801-001 v1\n"
                "- **原子命题**：测试股份在窗口内保持相对强势。\n"
                "- **研究状态**：等待确认\n"
                "- **信息快照**：2026-08-01T10:00:00+08:00\n"
            )
            old_source.write_text(old_text, encoding="utf-8")
            fields = {
                "proposition": "测试股份在窗口内保持相对强势。",
                "information_cutoff": "2026-08-01T10:00:00+08:00",
                "status": "等待确认",
                "relations": [],
            }
            baseline = json.loads(inputs["old_baseline"].read_text(encoding="utf-8"))
            baseline["workspace_sha256"] = _tree_sha256(inputs["old_workspace"])
            baseline["files"] = _tree_files(inputs["old_workspace"])
            baseline["semantic_units"] = [
                {
                    "scenario_id": "review-judgment",
                    "unit_id": "J20260801-001 v1",
                    "source_locator": {
                        "path": "判断日志/2026-08.md",
                        "start_line": 1,
                        "end_line": 4,
                        "source_sha256": _sha256(old_source),
                        "content_sha256": _sha256_text(old_text.rstrip("\n")),
                    },
                    "fields": fields,
                    "fields_sha256": _canonical_sha256(fields),
                }
            ]
            inputs["old_baseline"].write_text(
                json.dumps(baseline, ensure_ascii=False, sort_keys=True),
                encoding="utf-8",
            )
            self._refresh_binding(inputs["suite"], "old_baseline", inputs["old_baseline"])
            migration = json.loads(inputs["migration_report"].read_text(encoding="utf-8"))
            migration["input_snapshot"] = {
                "sha256": _tree_sha256(inputs["old_workspace"]),
                "files": _tree_files(inputs["old_workspace"]),
            }
            inputs["migration_report"].write_text(
                json.dumps(migration, ensure_ascii=False, sort_keys=True),
                encoding="utf-8",
            )
            self._refresh_binding(
                inputs["suite"], "migration_report", inputs["migration_report"]
            )
            self._bind_old_execution_artifact(
                inputs,
                scenario_id="review-judgment",
                required_unit_ids=["J20260801-001 v1"],
                selected_unit_ids=["J20260801-001 v1"],
                workflow="review",
                stage="review",
                contract="review-v1.json",
                selector={"objects": ["个股:测试股份(600001)"]},
            )
            suite = json.loads(inputs["suite"].read_text(encoding="utf-8"))
            suite["scenarios"] = [
                {
                    "id": "review-judgment",
                    "case_type": "review",
                    "workflow": "review",
                    "stage": "review",
                    "contract": "review-v1.json",
                    "object_type": "stock",
                    "objects": ["个股:测试股份(600001)"],
                    "handoff": {
                        "judgment_ids": ["J20260801-001 v1"],
                        "evidence_ids": ["EVI-20260801-001#1"],
                    },
                    "information_cutoff": "2026-08-01T11:00:00+08:00",
                    "expectations": {"conditions": []},
                }
            ]
            inputs["suite"].write_text(
                json.dumps(suite, ensure_ascii=False, sort_keys=True),
                encoding="utf-8",
            )
            report = root / "report.json"

            result = subprocess.run(
                [
                    sys.executable,
                    str(SHADOW_CLI),
                    "--workspace",
                    str(workspace),
                    "--scenarios",
                    str(inputs["suite"]),
                    "--old-baseline",
                    str(inputs["old_baseline"]),
                    "--migration-report",
                    str(inputs["migration_report"]),
                    "--measurement-trace",
                    str(inputs["measurement_trace"]),
                    "--output",
                    str(report),
                ],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 1, result.stderr)
            payload = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual(
                payload["checks"]["old_baseline_provenance"]["semantic_unit_count"],
                1,
            )

            baseline = json.loads(inputs["old_baseline"].read_text(encoding="utf-8"))
            baseline["semantic_units"][0]["fields"]["status"] = "规避"
            baseline["semantic_units"][0]["fields_sha256"] = _canonical_sha256(
                baseline["semantic_units"][0]["fields"]
            )
            inputs["old_baseline"].write_text(
                json.dumps(baseline, ensure_ascii=False, sort_keys=True),
                encoding="utf-8",
            )
            self._refresh_binding(inputs["suite"], "old_baseline", inputs["old_baseline"])
            tampered_report = root / "tampered-report.json"
            tampered = subprocess.run(
                [
                    sys.executable,
                    str(SHADOW_CLI),
                    "--workspace",
                    str(workspace),
                    "--scenarios",
                    str(inputs["suite"]),
                    "--old-baseline",
                    str(inputs["old_baseline"]),
                    "--migration-report",
                    str(inputs["migration_report"]),
                    "--measurement-trace",
                    str(inputs["measurement_trace"]),
                    "--output",
                    str(tampered_report),
                ],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(tampered.returncode, 2, tampered.stderr)
            self.assertIn("status does not match source excerpt", tampered.stderr)
            self.assertFalse(tampered_report.exists())

    def test_one_canonical_unit_cannot_satisfy_both_conflict_and_denial(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            workspace.mkdir()
            self._write_analyze_risk_evidence(workspace)
            inputs = self._bound_inputs(root, workspace)
            suite = json.loads(inputs["suite"].read_text(encoding="utf-8"))
            suite["scenarios"] = [
                {
                    "id": "analyze-risk",
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
                        ]
                    },
                    "information_cutoff": "2026-08-01T10:00:00+08:00",
                    "expectations": {
                        "conditions": [
                            {"category": "conflict", "unit_id": "EVI-20260801-002#2"},
                            {"category": "denial", "unit_id": "EVI-20260801-002#2"},
                        ]
                    },
                }
            ]
            inputs["suite"].write_text(json.dumps(suite, ensure_ascii=False, sort_keys=True), encoding="utf-8")
            self._bind_old_evidence_semantic(
                inputs,
                workspace=workspace,
                relative_path="证据包/2026-08/EVI-20260801-002.md",
                scenario_id="analyze-risk",
                unit_id="EVI-20260801-002#1",
                proposition="对象事实已核验。",
                information_cutoff="2026-08-01T09:00:00+08:00",
                status="已确认",
                workflow="analyze",
                stage="analysis",
                contract="analyze-v1.json",
                selector={"objects": ["stock:测试股份"]},
            )
            report = root / "report.json"

            result = subprocess.run(
                [
                    sys.executable,
                    str(SHADOW_CLI),
                    "--workspace",
                    str(workspace),
                    "--scenarios",
                    str(inputs["suite"]),
                    "--old-baseline",
                    str(inputs["old_baseline"]),
                    "--migration-report",
                    str(inputs["migration_report"]),
                    "--measurement-trace",
                    str(inputs["measurement_trace"]),
                    "--output",
                    str(report),
                ],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 1, result.stderr)
            scenario = json.loads(report.read_text(encoding="utf-8"))["scenarios"][0]
            self.assertEqual(scenario["status"], "failed")
            self.assertIn("different unit IDs", scenario["execution_error"])

    def test_conflict_and_denial_use_distinct_canonical_statuses_and_counters(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            workspace.mkdir()
            self._write_analyze_risk_evidence(workspace)
            inputs = self._bound_inputs(root, workspace)
            suite = json.loads(inputs["suite"].read_text(encoding="utf-8"))
            suite["scenarios"] = [
                {
                    "id": "analyze-risk",
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
                        ]
                    },
                    "information_cutoff": "2026-08-01T10:00:00+08:00",
                    "expectations": {
                        "conditions": [
                            {"category": "conflict", "unit_id": "EVI-20260801-002#2"},
                            {"category": "denial", "unit_id": "EVI-20260801-002#3"},
                        ]
                    },
                }
            ]
            inputs["suite"].write_text(json.dumps(suite, ensure_ascii=False, sort_keys=True), encoding="utf-8")
            self._bind_old_evidence_semantic(
                inputs,
                workspace=workspace,
                relative_path="证据包/2026-08/EVI-20260801-002.md",
                scenario_id="analyze-risk",
                unit_id="EVI-20260801-002#1",
                proposition="对象事实已核验。",
                information_cutoff="2026-08-01T09:00:00+08:00",
                status="已确认",
                workflow="analyze",
                stage="analysis",
                contract="analyze-v1.json",
                selector={"objects": ["stock:测试股份"]},
            )
            report = root / "report.json"

            result = subprocess.run(
                [
                    sys.executable,
                    str(SHADOW_CLI),
                    "--workspace",
                    str(workspace),
                    "--scenarios",
                    str(inputs["suite"]),
                    "--old-baseline",
                    str(inputs["old_baseline"]),
                    "--migration-report",
                    str(inputs["migration_report"]),
                    "--measurement-trace",
                    str(inputs["measurement_trace"]),
                    "--output",
                    str(report),
                ],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 1, result.stderr)
            conditions = json.loads(report.read_text(encoding="utf-8"))["scenarios"][0]["conditions"]
            by_category = {item["category"]: item for item in conditions}
            self.assertEqual(by_category["conflict"]["status"], "冲突")
            self.assertEqual(by_category["conflict"]["exclusion_counter"], "conflict_count")
            self.assertEqual(by_category["conflict"]["exclusion_count"], 1)
            self.assertEqual(by_category["denial"]["status"], "已否证")
            self.assertEqual(by_category["denial"]["exclusion_counter"], "denial_count")
            self.assertEqual(by_category["denial"]["exclusion_count"], 1)
            self.assertTrue(all(item["passed"] for item in conditions))

    def test_rehashed_old_baseline_cannot_diverge_from_the_migration_input_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            workspace.mkdir()
            inputs = self._bound_inputs(root, workspace)
            (inputs["old_workspace"] / "README.md").write_text("# Rewritten after migration\n", encoding="utf-8")
            baseline = json.loads(inputs["old_baseline"].read_text(encoding="utf-8"))
            baseline["workspace_sha256"] = _tree_sha256(inputs["old_workspace"])
            baseline["files"] = _tree_files(inputs["old_workspace"])
            inputs["old_baseline"].write_text(json.dumps(baseline, sort_keys=True), encoding="utf-8")
            self._refresh_binding(inputs["suite"], "old_baseline", inputs["old_baseline"])
            report = root / "report.json"

            result = subprocess.run(
                [
                    sys.executable,
                    str(SHADOW_CLI),
                    "--workspace",
                    str(workspace),
                    "--scenarios",
                    str(inputs["suite"]),
                    "--old-baseline",
                    str(inputs["old_baseline"]),
                    "--migration-report",
                    str(inputs["migration_report"]),
                    "--measurement-trace",
                    str(inputs["measurement_trace"]),
                    "--output",
                    str(report),
                ],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn("migration input snapshot", result.stderr)
            self.assertFalse(report.exists())

    def test_real_migration_report_binds_into_shadow_replay(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old_workspace = root / "old-workspace"
            old_workspace.mkdir()
            self._write_scan_evidence(old_workspace)
            execution_fields = {
                "workflow": "scan",
                "stage": "scan",
                "contract": "scan-v1.json",
                "selector": {"objects": ["market:A股"]},
                "required_unit_ids": ["EVI-20260801-001#1"],
                "selected_unit_ids": ["EVI-20260801-001#1"],
            }
            execution_artifact = old_workspace / "运行记录/shadow-scan-market.json"
            execution_artifact.parent.mkdir(parents=True, exist_ok=True)
            execution_encoded = json.dumps(
                execution_fields,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            execution_artifact.write_text(execution_encoded + "\n", encoding="utf-8")
            workspace = root / "migrated-workspace"
            migration_run = subprocess.run(
                [
                    sys.executable,
                    str(REPO_ROOT / "scripts/migrate_workspace.py"),
                    "--input",
                    str(old_workspace),
                    "--output",
                    str(workspace),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(migration_run.returncode, 0, migration_run.stderr)
            migration_report = workspace / "迁移映射.json"
            migration = json.loads(migration_report.read_text(encoding="utf-8"))
            migration_id = migration["migration_id"]

            old_source = old_workspace / "证据包/2026-08/EVI-20260801-001.md"
            lines = old_source.read_text(encoding="utf-8").splitlines()
            start_line = lines.index("## EVI-20260801-001#1") + 1
            end_line = lines.index("## EVI-20260801-001#2") - 1
            excerpt = "\n".join(lines[start_line - 1 : end_line])
            fields = {
                "proposition": "市场状态已核验。",
                "information_cutoff": "2026-08-01T09:00:00+08:00",
                "status": "已确认",
                "relations": [],
            }
            baseline_path = root / "frozen-old-baseline.json"
            baseline_path.write_text(
                json.dumps(
                    {
                        "schema_version": "a-share-shadow-baseline-v1",
                        "session_id": TEST_SESSION_ID,
                        "migration_id": migration_id,
                        "captured_at": "2026-08-01T10:00:00+08:00",
                        "workspace_root": str(old_workspace),
                        "workspace_sha256": migration["input_snapshot"]["sha256"],
                        "files": migration["input_snapshot"]["files"],
                        "semantic_units": [
                            {
                                "scenario_id": "scan-market",
                                "unit_id": "EVI-20260801-001#1",
                                "source_locator": {
                                    "path": "证据包/2026-08/EVI-20260801-001.md",
                                    "start_line": start_line,
                                    "end_line": end_line,
                                    "source_sha256": _sha256(old_source),
                                    "content_sha256": _sha256_text(excerpt),
                                },
                                "fields": fields,
                                "fields_sha256": _canonical_sha256(fields),
                            }
                        ],
                        "execution_artifacts": [
                            {
                                "scenario_id": "scan-market",
                                "source_locator": {
                                    "path": "运行记录/shadow-scan-market.json",
                                    "start_line": 1,
                                    "end_line": 1,
                                    "source_sha256": _sha256(execution_artifact),
                                    "content_sha256": _sha256_text(execution_encoded),
                                },
                                "fields": execution_fields,
                                "fields_sha256": _canonical_sha256(execution_fields),
                            }
                        ],
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                encoding="utf-8",
            )
            telemetry_export = root / "telemetry-export.jsonl"
            telemetry_export.write_text("", encoding="utf-8")
            trace_path = root / "measurement-trace.json"
            trace_path.write_text(
                json.dumps(
                    {
                        "schema_version": "a-share-shadow-measurement-trace-v1",
                        "trace_id": "TRACE-REAL-MIGRATION-001",
                        "session_id": TEST_SESSION_ID,
                        "migration_id": migration_id,
                        "observed_at": "2026-08-01T12:00:00+08:00",
                        "events": [],
                    },
                    sort_keys=True,
                ),
                encoding="utf-8",
            )
            suite_path = root / "shadow-suite.json"
            suite_path.write_text(
                json.dumps(
                    {
                        "schema_version": "a-share-shadow-replay-v2",
                        "session_id": TEST_SESSION_ID,
                        "migration_id": migration_id,
                        "bindings": {
                            "old_baseline": {"path": str(baseline_path), "sha256": _sha256(baseline_path), "session_id": TEST_SESSION_ID},
                            "migration_report": {"path": str(migration_report), "sha256": _sha256(migration_report), "session_id": TEST_SESSION_ID},
                            "new_workspace": {"path": str(workspace), "sha256": _tree_sha256(workspace), "session_id": TEST_SESSION_ID},
                            "measurement_trace": {"path": str(trace_path), "sha256": _sha256(trace_path), "session_id": TEST_SESSION_ID},
                        },
                        "targets": {
                            "min_raw_tool_payload_reduction_ratio": 0.8,
                            "max_main_context_ratio": 0.52,
                            "max_main_context_peak_tokens": 100000,
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
                    sort_keys=True,
                ),
                encoding="utf-8",
            )
            self._bind_trace_events(
                {
                    "measurement_trace": trace_path,
                    "telemetry_export": telemetry_export,
                    "suite": suite_path,
                },
                self._observed_trace_events(),
            )
            report = root / "shadow-report.json"

            result = subprocess.run(
                [
                    sys.executable,
                    str(SHADOW_CLI),
                    "--workspace",
                    str(workspace),
                    "--scenarios",
                    str(suite_path),
                    "--old-baseline",
                    str(baseline_path),
                    "--migration-report",
                    str(migration_report),
                    "--measurement-trace",
                    str(trace_path),
                    "--output",
                    str(report),
                ],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 1, result.stderr)
            payload = json.loads(report.read_text(encoding="utf-8"))
            self.assertTrue(payload["checks"]["input_bindings"]["passed"])
            self.assertTrue(payload["checks"]["runtime_surface"]["passed"])
            self.assertTrue(payload["checks"]["old_baseline_provenance"]["passed"])
            self.assertTrue(
                payload["checks"]["semantic_equivalence"]["passed"],
                payload["scenarios"][0],
            )
            self.assertEqual(payload["scenarios"][0]["status"], "passed")

    def test_any_bound_artifact_or_workspace_byte_tamper_is_rejected(self) -> None:
        targets = {
            "old_baseline": "old_baseline sha256 mismatch",
            "migration_report": "migration_report sha256 mismatch",
            "measurement_trace": "measurement_trace sha256 mismatch",
            "new_workspace": "new_workspace sha256 mismatch",
        }
        for name, expected_error in targets.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                workspace = root / "workspace"
                workspace.mkdir()
                inputs = self._bound_inputs(root, workspace)
                if name == "new_workspace":
                    target = workspace / "AGENTS.md"
                else:
                    target = inputs[name]
                target.write_bytes(target.read_bytes() + b"\n")
                report = root / "report.json"

                result = subprocess.run(
                    [
                        sys.executable,
                        str(SHADOW_CLI),
                        "--workspace",
                        str(workspace),
                        "--scenarios",
                        str(inputs["suite"]),
                        "--old-baseline",
                        str(inputs["old_baseline"]),
                        "--migration-report",
                        str(inputs["migration_report"]),
                        "--measurement-trace",
                        str(inputs["measurement_trace"]),
                        "--output",
                        str(report),
                    ],
                    check=False,
                    capture_output=True,
                    text=True,
                )

                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn(expected_error, result.stderr)
                self.assertFalse(report.exists())

    def test_unobserved_token_event_cannot_pass_the_token_gate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            workspace.mkdir()
            inputs = self._bound_inputs(root, workspace)
            events = self._observed_trace_events()
            token_event = next(
                event
                for event in events
                if event["phase"] == "candidate" and event["metric"] == "main_context_peak_tokens"
            )
            token_event["observation_status"] = "estimated"
            self._bind_trace_events(inputs, events)
            report = root / "report.json"

            result = subprocess.run(
                [
                    sys.executable,
                    str(SHADOW_CLI),
                    "--workspace",
                    str(workspace),
                    "--scenarios",
                    str(inputs["suite"]),
                    "--old-baseline",
                    str(inputs["old_baseline"]),
                    "--migration-report",
                    str(inputs["migration_report"]),
                    "--measurement-trace",
                    str(inputs["measurement_trace"]),
                    "--output",
                    str(report),
                ],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 1, result.stderr)
            payload = json.loads(report.read_text(encoding="utf-8"))
            self.assertFalse(payload["checks"]["measurement_trace"]["passed"])
            self.assertFalse(payload["checks"]["context_peak_tokens"]["passed"])
            self.assertFalse(payload["proxy_metrics"]["model_token_replay"]["available"])
            self.assertIn(
                {"phase": "candidate", "metric": "main_context_peak_tokens"},
                payload["checks"]["measurement_trace"]["missing_observations"],
            )

    def test_v2_suite_cannot_reintroduce_self_reported_summary_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            workspace.mkdir()
            inputs = self._bound_inputs(root, workspace)
            suite = json.loads(inputs["suite"].read_text(encoding="utf-8"))
            suite["candidate_observation"] = {
                "raw_tool_payload_characters": 0,
                "main_context_peak_tokens": 1,
            }
            inputs["suite"].write_text(json.dumps(suite, sort_keys=True), encoding="utf-8")
            report = root / "report.json"

            result = subprocess.run(
                [
                    sys.executable,
                    str(SHADOW_CLI),
                    "--workspace",
                    str(workspace),
                    "--scenarios",
                    str(inputs["suite"]),
                    "--old-baseline",
                    str(inputs["old_baseline"]),
                    "--migration-report",
                    str(inputs["migration_report"]),
                    "--measurement-trace",
                    str(inputs["measurement_trace"]),
                    "--output",
                    str(report),
                ],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn("cannot self-report observations", result.stderr)
            self.assertFalse(report.exists())

    def test_suite_manifest_exposes_the_v2_shadow_schema(self) -> None:
        manifest = REPO_ROOT / ".agents/skills/a-share/shared/suite-manifest.yaml"
        values: dict[str, str] = {}
        for line in manifest.read_text(encoding="utf-8").splitlines():
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            if key in {"shadow_replay_cli", "shadow_replay_schema"}:
                values[key] = value.strip().strip('"\'')

        self.assertEqual(set(values), {"shadow_replay_cli", "shadow_replay_schema"})
        schema_path = (manifest.parent.parent / values["shadow_replay_schema"]).resolve()
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        self.assertEqual(schema["$id"], "a-share-shadow-replay-v2")
        self.assertEqual(
            schema["$defs"]["suite"]["properties"]["schema_version"]["const"],
            "a-share-shadow-replay-v2",
        )
        self.assertNotIn("const", schema["$defs"]["sessionId"])
        self.assertIn(
            "max_main_context_peak_tokens",
            schema["$defs"]["suite"]["properties"]["targets"]["required"],
        )

    def test_runtime_manifest_cannot_omit_a_managed_file_and_rehash(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            workspace.mkdir()
            inputs = self._bound_inputs(root, workspace)
            migration = json.loads(inputs["migration_report"].read_text(encoding="utf-8"))
            files = [
                item
                for item in migration["runtime_surface"]["files"]
                if item["path"] != "scripts/validate_release.py"
            ]
            migration["runtime_surface"]["files"] = files
            digest = hashlib.sha256()
            for item in sorted(files, key=lambda value: value["path"]):
                digest.update(item["path"].encode("utf-8"))
                digest.update(b"\0")
                digest.update(item["sha256"].encode("ascii"))
                digest.update(b"\n")
            migration["runtime_surface"]["sha256"] = digest.hexdigest()
            inputs["migration_report"].write_text(json.dumps(migration, sort_keys=True), encoding="utf-8")
            self._refresh_binding(inputs["suite"], "migration_report", inputs["migration_report"])
            report = root / "report.json"

            result = subprocess.run(
                [
                    sys.executable,
                    str(SHADOW_CLI),
                    "--workspace",
                    str(workspace),
                    "--scenarios",
                    str(inputs["suite"]),
                    "--old-baseline",
                    str(inputs["old_baseline"]),
                    "--migration-report",
                    str(inputs["migration_report"]),
                    "--measurement-trace",
                    str(inputs["measurement_trace"]),
                    "--output",
                    str(report),
                ],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn("runtime surface files manifest is incomplete", result.stderr)
            self.assertFalse(report.exists())


if __name__ == "__main__":
    unittest.main()
