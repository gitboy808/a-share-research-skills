from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_ROOT = REPO_ROOT / ".agents/skills/a-share/shared"
SOURCE_PAYLOAD_CLI = SHARED_ROOT / "scripts/source_payload_store.py"
if str(SHARED_ROOT) not in sys.path:
    sys.path.insert(0, str(SHARED_ROOT))

from context import assemble, hydrate  # type: ignore[import-not-found]  # noqa: E402


def _write_evidence(root: Path, evidence_id: str, source_locator: dict[str, object]) -> None:
    evidence_root = root / "证据包/2026-08"
    evidence_root.mkdir(parents=True)
    (evidence_root / f"{evidence_id}.md").write_text(
        """---
schema_version: "a-share-workspace-v3"
artifact_type: "evidence_package"
id: "EVIDENCE_ID"
status: "complete"
information_cutoff: "2026-08-09T08:30:00+08:00"
---

# CLI 来源载荷闭环

### EVIDENCE_ID#001｜CLI 原始来源

- **事实陈述**：这条事实必须回到 CLI 外置的原始载荷核验。
- **状态**：已确认
- **来源组 ID**：SRCGRP-CLI-001
- **来源定位**：SOURCE_LOCATOR
- **关联对象 / 档案字段**：市场:A股 / market_state
""".replace("EVIDENCE_ID", evidence_id).replace(
            "SOURCE_LOCATOR",
            json.dumps(source_locator, ensure_ascii=False, separators=(",", ":")),
        ),
        encoding="utf-8",
    )


def _research_run(root: Path, run_id: str) -> dict[str, object]:
    return {
        "workspace_root": str(root),
        "schema_version": "a-share-workspace-v3",
        "run_id": run_id,
        "workflow": "investigate",
        "stage": "research",
        "objects": ["市场:A股"],
        "information_cutoff": "2026-08-09T09:00:00+08:00",
        "task_contract": "investigate-market-v1",
    }


class SourcePayloadCutoffTest(unittest.TestCase):
    def test_cli_acquired_at_reaches_assemble_future_information_guard(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_path = root / "source.txt"
            input_path.write_text("future source line\n", encoding="utf-8")
            put_result = subprocess.run(
                [
                    sys.executable,
                    str(SOURCE_PAYLOAD_CLI),
                    "put",
                    "--root",
                    str(root),
                    "--run-id",
                    "RUN-20260809-CLI-FUTURE",
                    "--input-file",
                    str(input_path),
                    "--acquired-at",
                    "2026-08-09T10:00:00+08:00",
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(put_result.returncode, 0, put_result.stderr)
            locator = json.loads(put_result.stdout)
            self.assertEqual(locator["acquired_at"], "2026-08-09T10:00:00+08:00")
            locator.update({"line_start": 1, "line_end": 1})
            evidence_id = "EVI-20260809-CLI-FUTURE"
            _write_evidence(root, evidence_id, locator)

            assembled = assemble(
                _research_run(root, "RUN-20260809-CLI-FUTURE")
            )

            self.assertEqual(assembled["coverage"]["required_covered"], 0)
            self.assertEqual(assembled["gaps"][0]["reason"], "future_information")
            self.assertEqual(assembled["stable_references"], [])

    def test_cli_line_range_survives_evidence_projection_and_hydrate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_path = root / "source.txt"
            input_path.write_text(
                "unselected line\nverified source line\ntrailing line\n",
                encoding="utf-8",
            )
            put_result = subprocess.run(
                [
                    sys.executable,
                    str(SOURCE_PAYLOAD_CLI),
                    "put",
                    "--root",
                    str(root),
                    "--run-id",
                    "RUN-20260809-CLI-HYDRATE",
                    "--input-file",
                    str(input_path),
                    "--acquired-at",
                    "2026-08-09T08:45:00+08:00",
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(put_result.returncode, 0, put_result.stderr)
            locator = json.loads(put_result.stdout)
            locator.update({"line_start": 2, "line_end": 2})
            evidence_id = "EVI-20260809-CLI-HYDRATE"
            _write_evidence(root, evidence_id, locator)

            assembled = assemble(
                _research_run(root, "RUN-20260809-CLI-HYDRATE")
            )

            self.assertEqual(assembled["coverage"]["required_covered"], 1)
            projected_locator = assembled["stable_references"][0]["source_locator"]
            self.assertEqual(projected_locator["start_line"], 2)
            self.assertEqual(projected_locator["end_line"], 2)
            self.assertEqual(projected_locator["acquired_at"], "2026-08-09T08:45:00+08:00")
            hydrated = hydrate(assembled)
            self.assertEqual(hydrated["missing_references"], [])
            self.assertEqual(hydrated["units"][0]["verification_text"], "verified source line")

    def test_payload_hydrate_fails_when_authoritative_markdown_is_unreadable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_path = root / "source.txt"
            input_path.write_text("verified source line\n", encoding="utf-8")
            put_result = subprocess.run(
                [
                    sys.executable,
                    str(SOURCE_PAYLOAD_CLI),
                    "put",
                    "--root",
                    str(root),
                    "--run-id",
                    "RUN-20260809-CLI-PARSE",
                    "--input-file",
                    str(input_path),
                    "--acquired-at",
                    "2026-08-09T08:45:00+08:00",
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(put_result.returncode, 0, put_result.stderr)
            locator = json.loads(put_result.stdout)
            locator.update({"line_start": 1, "line_end": 1})
            evidence_id = "EVI-20260809-CLI-PARSE"
            _write_evidence(root, evidence_id, locator)
            assembled = assemble(
                _research_run(root, "RUN-20260809-CLI-PARSE")
            )
            self.assertEqual(len(assembled["stable_references"]), 1)

            broken = root / "证据包/2026-08/EVI-20260809-BROKEN.md"
            broken.symlink_to("missing-authoritative-target.md")
            hydrated = hydrate(assembled)

            self.assertEqual(hydrated["units"], [])
            self.assertEqual(hydrated["quality"]["verification_failures"], 1)
            self.assertIn(
                "unreadable authoritative document",
                hydrated["missing_references"][0]["reason"],
            )


if __name__ == "__main__":
    unittest.main()
