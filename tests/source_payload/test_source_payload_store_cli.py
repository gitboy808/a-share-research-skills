from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE_PAYLOAD_CLI = REPO_ROOT / ".agents/skills/a-share/shared/scripts/source_payload_store.py"
ACQUIRED_AT = "2026-08-09T09:00:00+08:00"


def payload_id(content: bytes) -> str:
    digest = hashlib.sha256()
    digest.update(content)
    digest.update(b"\0\0")
    digest.update(ACQUIRED_AT.encode("utf-8"))
    return f"PAY-{digest.hexdigest()[:24]}"


class SourcePayloadStoreCliTest(unittest.TestCase):
    def run_cli(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SOURCE_PAYLOAD_CLI), *arguments],
            check=False,
            capture_output=True,
            text=True,
        )

    def put(
        self,
        root: Path,
        content: str = "payload\n",
        *,
        run_id: str = "RUN-20260809-TEST",
        acquired_at: str = ACQUIRED_AT,
        extra: tuple[str, ...] = (),
    ) -> tuple[subprocess.CompletedProcess[str], Path]:
        source = root / "source.txt"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(content, encoding="utf-8")
        result = self.run_cli(
            "put",
            "--root",
            str(root),
            "--run-id",
            run_id,
            "--input-file",
            str(source),
            "--acquired-at",
            acquired_at,
            *extra,
        )
        return result, source

    def reference_file(self, root: Path, value: object) -> Path:
        path = root / "reference.json"
        path.write_text(
            value if isinstance(value, str) else json.dumps(value), encoding="utf-8"
        )
        return path

    def locate(self, root: Path, reference: Path) -> subprocess.CompletedProcess[str]:
        return self.run_cli(
            "locate", "--root", str(root), "--reference", str(reference)
        )

    def assert_closed(
        self, result: subprocess.CompletedProcess[str], message: str
    ) -> None:
        self.assertEqual(result.returncode, 2)
        self.assertIn(message, result.stderr)
        self.assertEqual(result.stdout, "")

    def test_put_requires_a_timezone_aware_acquisition_time(self) -> None:
        cases = ((None, "acquired-at"), ("2026-08-09T09:00:00", "timezone"))
        for acquired_at, message in cases:
            with self.subTest(acquired_at=acquired_at), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                source = root / "source.txt"
                source.write_text("payload\n", encoding="utf-8")
                arguments = [
                    "put",
                    "--root",
                    str(root),
                    "--run-id",
                    "RUN-20260809-TIME",
                    "--input-file",
                    str(source),
                ]
                if acquired_at:
                    arguments.extend(("--acquired-at", acquired_at))
                self.assert_closed(self.run_cli(*arguments), message)

    def test_put_returns_only_a_compact_locator_without_payload_text(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result, _ = self.put(
                root,
                "top secret raw payload\nsecond line\n",
                extra=(
                    "--source-uri",
                    "https://example.invalid/source",
                    "--content-type",
                    "text/plain",
                ),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            locator = json.loads(result.stdout)
            self.assertEqual(
                set(locator),
                {"kind", "payload_id", "path", "sha256", "byte_length", "acquired_at"},
            )
            self.assertEqual(locator["sha256"], "790998dbe1d0c08b7657e41e02b5ba969cb75fa39aa5268a0ec3904596961945")
            self.assertEqual(locator["byte_length"], 35)
            self.assertNotIn("top secret raw payload", result.stdout)
            self.assertEqual(len(result.stdout.splitlines()), 1)
            self.assertNotIn(": ", result.stdout)
            metadata = json.loads((root / locator["path"]).with_suffix(".json").read_text(encoding="utf-8"))
            self.assertEqual(metadata["source_uri"], "https://example.invalid/source")
            self.assertEqual(metadata["content_type"], "text/plain")
            self.assertEqual(metadata["acquired_at"], ACQUIRED_AT)

    def test_locate_returns_a_verified_locator_without_payload_text(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stored, _ = self.put(root, "first line\nverification line\nlast line\n")
            self.assertEqual(stored.returncode, 0, stored.stderr)
            located = self.locate(root, self.reference_file(root, stored.stdout))
            self.assertEqual(located.returncode, 0, located.stderr)
            self.assertEqual(json.loads(located.stdout), json.loads(stored.stdout))
            self.assertNotIn("verification line", located.stdout)

    def test_locate_rejects_invalid_or_changed_reference_identity(self) -> None:
        cases = (
            ("naive_time", {"acquired_at": "2026-08-09T09:00:00"}, "timezone"),
            ("changed_time", {"acquired_at": "2026-08-09T08:00:00+08:00"}, "acquired_at differs from stored metadata"),
            ("escaped_path", {"path": "../outside.payload"}, "escapes source payload store"),
            ("path_payload_id", {"payload_id": "..", "path": ".source-payloads/RUN/../.payload"}, "invalid source payload reference"),
        )
        for label, changes, message in cases:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                stored, _ = self.put(root)
                self.assertEqual(stored.returncode, 0, stored.stderr)
                reference = json.loads(stored.stdout)
                reference.update(changes)
                self.assert_closed(self.locate(root, self.reference_file(root, reference)), message)

    def test_locate_rejects_payload_tampering_even_if_reference_is_rehashed(self) -> None:
        for rewrite_reference in (False, True):
            with self.subTest(rewrite_reference=rewrite_reference), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                stored, _ = self.put(root, "original payload\n")
                reference = json.loads(stored.stdout)
                changed = b"changed payload\n"
                (root / reference["path"]).write_bytes(changed)
                if rewrite_reference:
                    reference.update(
                        sha256=hashlib.sha256(changed).hexdigest(),
                        byte_length=len(changed),
                    )
                result = self.locate(root, self.reference_file(root, reference))
                self.assert_closed(
                    result,
                    "differs from stored metadata" if rewrite_reference else "content hash changed",
                )

    def test_locate_without_path_rejects_a_candidate_symlink_outside_store(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            sandbox = Path(directory)
            root = sandbox / "workspace"
            run_directory = root / ".source-payloads/RUN-20260809-LOCATE-LINK"
            run_directory.mkdir(parents=True)
            content = b"outside payload\n"
            identifier = f"PAY-{hashlib.sha256(content).hexdigest()[:24]}"
            outside = sandbox / f"{identifier}.payload"
            outside.write_bytes(content)
            (run_directory / f"{identifier}.payload").symlink_to(outside)
            reference = {
                "kind": "source_payload",
                "payload_id": identifier,
                "sha256": hashlib.sha256(content).hexdigest(),
                "byte_length": len(content),
                "acquired_at": ACQUIRED_AT,
            }
            self.assert_closed(
                self.locate(root, self.reference_file(root, reference)),
                "escapes source payload store",
            )

    def test_put_rejects_path_like_run_id(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result, _ = self.put(Path(directory), run_id="..")
            self.assert_closed(result, "invalid run_id")

    def test_put_rejects_store_and_run_directory_symlink_escapes(self) -> None:
        cases = (("store", "escapes workspace root"), ("run", "escapes source payload store"))
        for kind, message in cases:
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                sandbox = Path(directory)
                root, outside = sandbox / "workspace", sandbox / "outside"
                root.mkdir()
                outside.mkdir()
                if kind == "store":
                    (root / ".source-payloads").symlink_to(outside, target_is_directory=True)
                    run_id = "RUN-20260809-STORE-ESCAPE"
                else:
                    store = root / ".source-payloads"
                    store.mkdir()
                    run_id = "RUN-20260809-RUN-ESCAPE"
                    (store / run_id).symlink_to(outside, target_is_directory=True)
                result, _ = self.put(root, run_id=run_id)
                self.assert_closed(result, message)
                self.assertEqual(list(outside.iterdir()), [])

    def test_put_does_not_follow_existing_payload_or_metadata_symlink(self) -> None:
        for suffix, message in ((".payload", "payload file symlink"), (".json", "metadata file symlink")):
            with self.subTest(suffix=suffix), tempfile.TemporaryDirectory() as directory:
                sandbox = Path(directory)
                root = sandbox / "workspace"
                run_id = "RUN-20260809-FILE-LINK"
                run_directory = root / f".source-payloads/{run_id}"
                run_directory.mkdir(parents=True)
                content = "candidate payload\n"
                identifier = payload_id(content.encode())
                outside = sandbox / f"outside{suffix}"
                original = "must remain unchanged\n"
                outside.write_text(original, encoding="utf-8")
                (run_directory / f"{identifier}{suffix}").symlink_to(outside)
                result, _ = self.put(root, content, run_id=run_id)
                self.assert_closed(result, message)
                self.assertEqual(outside.read_text(encoding="utf-8"), original)
                if suffix == ".json":
                    self.assertFalse((run_directory / f"{identifier}.payload").exists())

    def test_excerpt_returns_only_the_explicit_line_range(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stored, _ = self.put(root, "first line\nverification line\nlast line\n")
            reference = self.reference_file(root, stored.stdout)
            result = self.run_cli(
                "excerpt",
                "--root",
                str(root),
                "--reference",
                str(reference),
                "--start-line",
                "2",
                "--end-line",
                "2",
                "--max-chars",
                "100",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            excerpt = json.loads(result.stdout)
            self.assertEqual(
                {key: excerpt[key] for key in ("excerpt", "start_line", "end_line", "character_length")},
                {"excerpt": "verification line", "start_line": 2, "end_line": 2, "character_length": 17},
            )
            self.assertNotIn("first line", result.stdout)
            self.assertNotIn("last line", result.stdout)

    def test_excerpt_rejects_incomplete_range_and_over_limit_output(self) -> None:
        cases = ((["--start-line", "1"], "--end-line"), (["--start-line", "1", "--end-line", "1", "--max-chars", "10"], "exceeds max_chars"))
        for arguments, message in cases:
            with self.subTest(message=message), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                stored, _ = self.put(root, "verification line\n")
                reference = self.reference_file(root, stored.stdout)
                result = self.run_cli(
                    "excerpt",
                    "--root",
                    str(root),
                    "--reference",
                    str(reference),
                    *arguments,
                )
                self.assert_closed(result, message)


if __name__ == "__main__":
    unittest.main()
