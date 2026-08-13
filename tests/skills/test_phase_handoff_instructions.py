from __future__ import annotations

import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SKILL_ROOT = REPO_ROOT / ".agents/skills/a-share"
SPECIALISTS = (
    "a-share-scan",
    "a-share-investigate",
    "a-share-analyze",
    "a-share-review",
    "a-share-meta-review",
)


def read_skill(name: str) -> str:
    return (SKILL_ROOT / name / "SKILL.md").read_text(encoding="utf-8")


class PhaseHandoffInstructionsTest(unittest.TestCase):
    def test_router_reuses_only_run_id_across_workflow_phases(self) -> None:
        router = read_skill("a-share-research")

        self.assertIn("Reuse only the RUN ID across workflow phases", router)
        self.assertIn("fresh phase manifest", router)
        self.assertNotIn("Pass the same run ID, task contract version and manifest between workflows", router)

    def test_every_specialist_requires_a_formal_phase_envelope_even_when_called_directly(self) -> None:
        missing = []
        for name in SPECIALISTS:
            content = read_skill(name)
            required_fragments = (
                "shared/contracts/README.md#阶段运行协议",
                "fresh phase manifest",
                "timezone-aware `information_cutoff`",
                "task contract",
            )
            for fragment in required_fragments:
                if fragment not in content:
                    missing.append(f"{name}: {fragment}")

        self.assertEqual(missing, [])

    def test_every_specialist_closes_research_before_starting_presentation(self) -> None:
        missing = []
        for name in SPECIALISTS:
            content = read_skill(name)
            for fragment in (
                "End the research context",
                "new presentation context",
                "canonical artifact IDs and stable references",
            ):
                if fragment not in content:
                    missing.append(f"{name}: {fragment}")

        self.assertEqual(missing, [])

    def test_analysis_and_review_name_their_formal_handoff_ids(self) -> None:
        analysis = read_skill("a-share-analyze")
        review = read_skill("a-share-review")

        self.assertIn("`handoff.evidence_ids`", analysis)
        self.assertIn("`handoff.evidence_ids`", review)
        self.assertIn("`handoff.judgment_ids`", review)

    def test_each_judgment_version_is_a_self_contained_immutable_snapshot(self) -> None:
        analysis = read_skill("a-share-analyze")
        template = (REPO_ROOT / "模板/判断条目模板.md").read_text(
            encoding="utf-8"
        )

        for content in (analysis, template):
            self.assertIn("self-contained immutable snapshot", content)
            self.assertIn("repeat every schema-required field", content)
            self.assertIn("信息快照", content)
        self.assertNotIn("版本更新只追加上一版本、新信息快照", template)

    def test_report_writing_specialists_allocate_a_presentation_report_id(self) -> None:
        missing = []
        for name in (
            "a-share-scan",
            "a-share-investigate",
            "a-share-analyze",
            "a-share-review",
        ):
            content = read_skill(name)
            if "next_id.py RPT --root <workspace> --date YYYYMMDD" not in content:
                missing.append(name)

        self.assertEqual(missing, [])


if __name__ == "__main__":
    unittest.main()
