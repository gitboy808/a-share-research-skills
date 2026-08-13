from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
RELEASE_VALIDATOR = PROJECT_ROOT / "scripts/validate_release.py"
DEPLOYMENT_VALIDATOR = PROJECT_ROOT / "scripts/validate_deployment.py"


class SecurityValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.workspace = Path(self.temporary_directory.name) / "workspace"
        shutil.copytree(
            PROJECT_ROOT,
            self.workspace,
            ignore=shutil.ignore_patterns(".git", ".context", ".source-payloads", "__pycache__"),
        )

    def initialize_public_repository(self) -> None:
        subprocess.run(["git", "init", "-q"], cwd=self.workspace, check=True)
        subprocess.run(["git", "add", "."], cwd=self.workspace, check=True)

    def test_public_release_blocks_a_realistic_openai_token_without_echoing_it(self) -> None:
        token = "sk-" + "Ab9_" * 10
        fixture = self.workspace / "docs/credential-fixture.txt"
        fixture.write_text(f"OPENAI_API_KEY={token}\n", encoding="utf-8")
        self.initialize_public_repository()
        subprocess.run(["git", "add", "-f", str(fixture.relative_to(self.workspace))], cwd=self.workspace, check=True)

        result = subprocess.run(
            [sys.executable, str(RELEASE_VALIDATOR), "--root", str(self.workspace)],
            cwd=PROJECT_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 1)
        self.assertIn("docs/credential-fixture.txt", result.stdout)
        self.assertIn("sk-…", result.stdout)
        self.assertNotIn(token, result.stdout)

    def test_public_release_scans_large_tracked_files_without_echoing_the_token(self) -> None:
        token = "sk-" + "Lr8_" * 10
        fixture = self.workspace / "docs/large-credential-fixture.txt"
        fixture.write_text(
            ("research context\n" * 80_000) + f"OPENAI_API_KEY={token}\n",
            encoding="utf-8",
        )
        self.assertGreater(fixture.stat().st_size, 1_000_000)
        self.initialize_public_repository()

        result = subprocess.run(
            [sys.executable, str(RELEASE_VALIDATOR), "--root", str(self.workspace)],
            cwd=PROJECT_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 1)
        self.assertIn("docs/large-credential-fixture.txt", result.stdout)
        self.assertIn("sk-…", result.stdout)
        self.assertNotIn(token, result.stdout)

    def test_public_release_fails_closed_for_an_undecodable_tracked_file(self) -> None:
        fixture = self.workspace / "docs/undecodable-fixture.bin"
        fixture.write_bytes(b"\xff\xfe\x00\x81")
        self.initialize_public_repository()

        result = subprocess.run(
            [sys.executable, str(RELEASE_VALIDATOR), "--root", str(self.workspace)],
            cwd=PROJECT_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 1)
        self.assertIn("security scan unavailable", result.stdout)
        self.assertIn("docs/undecodable-fixture.bin", result.stdout)

    def test_public_release_does_not_treat_an_sk_hynix_article_slug_as_a_token(self) -> None:
        article_slug = "sk-" + "hynix-memory-stocks-rise-on-demand"
        fixture = self.workspace / "docs/research-source-fixture.md"
        fixture.write_text(
            f"[primary article](https://247wallst.com/investing/2026/08/09/{article_slug}/)\n",
            encoding="utf-8",
        )
        self.initialize_public_repository()

        result = subprocess.run(
            [sys.executable, str(RELEASE_VALIDATOR), "--root", str(self.workspace)],
            cwd=PROJECT_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("mode: public_release_v3", result.stdout)
        self.assertNotIn("OpenAI-style API key", result.stdout)

    def test_public_release_requires_the_runtime_contract_and_validation_closure(self) -> None:
        self.initialize_public_repository()
        required_surface = [
            "scripts/security_scan.py",
            "scripts/validate_deployment.py",
            ".agents/skills/a-share/shared/context/README.md",
            ".agents/skills/a-share/shared/context/eligibility.py",
            ".agents/skills/a-share/shared/scripts/source_payload_store.py",
            ".agents/skills/a-share/shared/contracts/investigate-event-v1.json",
            ".agents/skills/a-share/shared/contracts/investigate-industry-v1.json",
            ".agents/skills/a-share/shared/contracts/investigate-theme-v1.json",
            "docs/adr/0029-以资格模式隔离当前分析与历史审计.md",
        ]
        subprocess.run(
            ["git", "rm", "--cached", "-q", "--", *required_surface],
            cwd=self.workspace,
            check=True,
        )

        result = subprocess.run(
            [sys.executable, str(RELEASE_VALIDATOR), "--root", str(self.workspace)],
            cwd=PROJECT_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 1)
        for relative in required_surface:
            self.assertIn(f"missing required tracked file: {relative}", result.stdout)

    def test_public_release_has_no_profile_switch(self) -> None:
        self.initialize_public_repository()
        result = subprocess.run(
            [
                sys.executable,
                str(RELEASE_VALIDATOR),
                "--root",
                str(self.workspace),
                "--profile",
                "full",
            ],
            cwd=PROJECT_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("unrecognized arguments: --profile full", result.stderr)

    def test_public_release_derives_the_current_suite_surface_instead_of_a_handwritten_subset(self) -> None:
        self.initialize_public_repository()
        required_surface = [
            ".agents/skills/a-share/shared/contracts/scan-v1.json",
            ".agents/skills/a-share/a-share-scan/SKILL.md",
            ".agents/skills/a-share/shared/context/projection.py",
            "模板/证据包模板.md",
        ]
        subprocess.run(
            ["git", "rm", "--cached", "-q", "--", *required_surface],
            cwd=self.workspace,
            check=True,
        )

        result = subprocess.run(
            [sys.executable, str(RELEASE_VALIDATOR), "--root", str(self.workspace)],
            cwd=PROJECT_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 1)
        for relative in required_surface:
            self.assertIn(f"missing required tracked file: {relative}", result.stdout)

    def test_public_release_requires_every_formal_contract_role_even_if_a_file_is_deleted(self) -> None:
        self.initialize_public_repository()
        subprocess.run(
            [
                "git",
                "rm",
                "-f",
                "-q",
                "--",
                ".agents/skills/a-share/shared/contracts/scan-v1.json",
            ],
            cwd=self.workspace,
            check=True,
        )

        result = subprocess.run(
            [sys.executable, str(RELEASE_VALIDATOR), "--root", str(self.workspace)],
            cwd=PROJECT_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 1)
        self.assertIn("missing registered contract role: scan/scan", result.stdout)

    def test_private_deployment_blocks_a_realistic_token_without_echoing_it(self) -> None:
        token = "sk-" + "Cd7_" * 10
        fixture = self.workspace / "报告/private-security-fixture.txt"
        fixture.write_text(f"credential={token}\n", encoding="utf-8")

        result = subprocess.run(
            [sys.executable, str(DEPLOYMENT_VALIDATOR), "--root", str(self.workspace)],
            cwd=PROJECT_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 1)
        self.assertIn("报告/private-security-fixture.txt", result.stdout)
        self.assertIn("sk-…", result.stdout)
        self.assertNotIn(token, result.stdout)

    def test_private_deployment_accepts_research_data_and_an_sk_hynix_source_url(self) -> None:
        article_slug = "sk-" + "hynix-memory-stocks-rise-on-demand"
        fixture = self.workspace / "报告/private-research-source.txt"
        fixture.write_text(
            f"source=https://247wallst.com/investing/2026/08/09/{article_slug}/\n",
            encoding="utf-8",
        )

        result = subprocess.run(
            [sys.executable, str(DEPLOYMENT_VALIDATOR), "--root", str(self.workspace)],
            cwd=PROJECT_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("mode: private_deployment", result.stdout)
        self.assertNotIn("private runtime path is tracked", result.stdout)
        self.assertNotIn("OpenAI-style API key", result.stdout)

    def test_public_release_rejects_a_tracked_private_research_state_file(self) -> None:
        self.initialize_public_repository()
        subprocess.run(["git", "add", "-f", "当前判断.md"], cwd=self.workspace, check=True)

        result = subprocess.run(
            [sys.executable, str(RELEASE_VALIDATOR), "--root", str(self.workspace)],
            cwd=PROJECT_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 1)
        self.assertIn("private runtime path is tracked: 当前判断.md", result.stdout)

    def test_public_release_still_blocks_a_lowercase_secret_outside_a_url(self) -> None:
        token = "sk-" + "a" * 32
        fixture = self.workspace / "docs/lowercase-credential-fixture.txt"
        fixture.write_text(f"credential={token}\n", encoding="utf-8")
        self.initialize_public_repository()

        result = subprocess.run(
            [sys.executable, str(RELEASE_VALIDATOR), "--root", str(self.workspace)],
            cwd=PROJECT_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 1)
        self.assertIn("docs/lowercase-credential-fixture.txt", result.stdout)
        self.assertNotIn(token, result.stdout)


if __name__ == "__main__":
    unittest.main()
