from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
NEXT_ID = REPO_ROOT / ".agents/skills/a-share/shared/scripts/next_id.py"


class NextIdTests(unittest.TestCase):
    def test_report_ids_are_allocated_across_existing_presentation_reports(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "CONTEXT.md").write_text("# Context\n", encoding="utf-8")
            (root / "研究规则.md").write_text("# Rules\n", encoding="utf-8")
            report_root = root / "报告/2026-08"
            report_root.mkdir(parents=True)
            (report_root / "RPT-20260809-001.md").write_text(
                'id: "RPT-20260809-001"\n', encoding="utf-8"
            )
            (report_root / "RPT-20260809-003.md").write_text(
                'id: "RPT-20260809-003"\n', encoding="utf-8"
            )

            result = subprocess.run(
                [
                    sys.executable,
                    str(NEXT_ID),
                    "RPT",
                    "--root",
                    str(root),
                    "--date",
                    "20260809",
                ],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "RPT-20260809-004")


if __name__ == "__main__":
    unittest.main()
