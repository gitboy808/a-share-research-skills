from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_ROOT = REPO_ROOT / ".agents/skills/a-share/shared"
CONTEXT_CLI = SHARED_ROOT / "scripts/context_workspace.py"
if str(SHARED_ROOT) not in sys.path:
    sys.path.insert(0, str(SHARED_ROOT))

from context import assemble, hydrate  # type: ignore[import-not-found]  # noqa: E402


class CanonicalContextInterfaceTest(unittest.TestCase):
    def test_assemble_exposes_one_canonical_reference_collection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = assemble(
                {
                    "workspace_root": directory,
                    "information_cutoff": "2026-08-09T09:00:00+08:00",
                },
                {
                    "contract_id": "test.canonical-output",
                    "version": "1.0.0",
                    "required_evidence": [],
                },
            )

        self.assertIn("stable_references", result)
        self.assertNotIn("workspace", result)
        self.assertNotIn("workset", result)

    def test_manifest_requires_canonical_keys(self) -> None:
        aliases = {
            "root": "workspace_root",
            "snapshot_cutoff": "information_cutoff",
            "contract": "task_contract",
            "task_contract_ref": "task_contract",
            "persist_workset": "persist_workset_manifest",
            "persistent_write": "persist_workset_manifest",
        }
        with tempfile.TemporaryDirectory() as directory:
            for alias, canonical in aliases.items():
                with self.subTest(alias=alias):
                    manifest: dict[str, object] = {
                        "workspace_root": directory,
                        "information_cutoff": "2026-08-09T09:00:00+08:00",
                    }
                    if alias == "root":
                        manifest.pop("workspace_root")
                        manifest[alias] = directory
                    elif alias == "snapshot_cutoff":
                        manifest.pop("information_cutoff")
                        manifest[alias] = "2026-08-09T09:00:00+08:00"
                    else:
                        manifest[alias] = False
                    with self.assertRaisesRegex(ValueError, canonical):
                        assemble(
                            manifest,
                            {
                                "contract_id": "test.no-live-aliases",
                                "version": "1.0.0",
                                "required_evidence": [],
                            },
                        )

    def test_hydrate_accepts_only_stable_references_envelope(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            for alias in ("references", "workspace"):
                with self.subTest(alias=alias):
                    with self.assertRaisesRegex(ValueError, "stable_references"):
                        hydrate({"workspace_root": directory, alias: []})

    def test_cli_accepts_only_named_input_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run = root / "run.json"
            run.write_text(
                json.dumps(
                    {
                        "workspace_root": directory,
                        "information_cutoff": "2026-08-09T09:00:00+08:00",
                    }
                ),
                encoding="utf-8",
            )
            references = root / "references.json"
            references.write_text(
                json.dumps({"workspace_root": directory, "stable_references": []}),
                encoding="utf-8",
            )
            commands = (
                ("assemble", str(run)),
                ("hydrate", str(references)),
                ("hydrate", "--stable-references", str(references)),
            )
            for arguments in commands:
                with self.subTest(arguments=arguments):
                    result = subprocess.run(
                        [sys.executable, str(CONTEXT_CLI), *arguments],
                        check=False,
                        capture_output=True,
                        text=True,
                    )
                    self.assertEqual(result.returncode, 2)


if __name__ == "__main__":
    unittest.main()
