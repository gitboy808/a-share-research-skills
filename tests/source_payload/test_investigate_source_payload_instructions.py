from __future__ import annotations

import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
INVESTIGATE_SKILL = REPO_ROOT / ".agents/skills/a-share/a-share-investigate/SKILL.md"


class InvestigateSourcePayloadInstructionsTest(unittest.TestCase):
    def test_investigation_externalizes_then_hydrates_before_evidence_write(self) -> None:
        content = INVESTIGATE_SKILL.read_text(encoding="utf-8")

        for instruction in (
            "source_payload_store.py put",
            "source_payload_store.py excerpt",
            "context_workspace.py hydrate",
            "--acquired-at <ISO-8601-with-timezone>",
            "source-payload:<payload_id>",
            "source_payload_candidate",
            "authority=source_payload_store",
            "status=unverified",
            "verification_only",
            "cannot enter a workset manifest",
            "before writing an atomic evidence item",
            "workset manifest contains the locator but never payload text",
            "Release removes payload text and open handles from the active context; it never deletes store files",
            "No automatic cleanup is implemented",
            "decodable page-text or structured-extraction payload",
        ):
            self.assertIn(instruction, content)


if __name__ == "__main__":
    unittest.main()
