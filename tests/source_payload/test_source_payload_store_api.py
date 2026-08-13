from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_ROOT = REPO_ROOT / ".agents/skills/a-share/shared"
if str(SHARED_ROOT) not in sys.path:
    sys.path.insert(0, str(SHARED_ROOT))

if "a_share_context" not in sys.modules:
    from tests import context as _context_test_package  # noqa: F401,E402

from a_share_context.source_payload import FileSourcePayloadStore  # type: ignore[import-not-found]  # noqa: E402


class SourcePayloadStoreApiTest(unittest.TestCase):
    def test_put_fails_closed_without_a_timezone_aware_acquisition_time(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = FileSourcePayloadStore(root)

            with self.assertRaisesRegex(ValueError, "acquired_at.*timezone"):
                store.put("payload\n", run_id="RUN-20260809-DIRECT-NO-TIME")

            self.assertEqual(list((root / ".source-payloads").rglob("*.payload")), [])

    def test_locate_fails_closed_when_reference_omits_acquisition_time(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = FileSourcePayloadStore(root)
            reference = store.put(
                "payload\n",
                run_id="RUN-20260809-DIRECT-LOCATE-NO-TIME",
                acquired_at="2026-08-09T09:00:00+08:00",
            )
            reference.pop("acquired_at")

            with self.assertRaisesRegex(ValueError, "acquired_at.*timezone"):
                store.locate(reference)

    def test_same_payload_acquired_twice_keeps_both_immutable_locators(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = FileSourcePayloadStore(root)
            first = store.put(
                "same payload\n",
                run_id="RUN-20260809-TWO-ACQUISITIONS",
                source_uri="https://example.invalid/source",
                acquired_at="2026-08-09T09:00:00+08:00",
            )
            second = store.put(
                "same payload\n",
                run_id="RUN-20260809-TWO-ACQUISITIONS",
                source_uri="https://example.invalid/source",
                acquired_at="2026-08-09T09:01:00+08:00",
            )

            self.assertNotEqual(first["payload_id"], second["payload_id"])
            self.assertNotEqual(first["path"], second["path"])
            self.assertEqual(store.locate(first)["acquired_at"], first["acquired_at"])
            self.assertEqual(store.locate(second)["acquired_at"], second["acquired_at"])

    def test_exact_duplicate_put_reuses_record_without_rewriting_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = FileSourcePayloadStore(root)
            arguments = {
                "run_id": "RUN-20260809-IDEMPOTENT",
                "payload_id": "PAY-IDEMPOTENT-001",
                "source_uri": "https://example.invalid/source",
                "acquired_at": "2026-08-09T09:00:00+08:00",
                "content_type": "text/plain",
            }
            first = store.put("same payload\n", **arguments)
            data_path = root / first["path"]
            meta_path = data_path.with_suffix(".json")
            old_timestamp = 1_500_000_000_000_000_000
            os.utime(data_path, ns=(old_timestamp, old_timestamp))
            os.utime(meta_path, ns=(old_timestamp, old_timestamp))

            second = store.put("same payload\n", **arguments)

            self.assertEqual(second, first)
            self.assertEqual(data_path.stat().st_mtime_ns, old_timestamp)
            self.assertEqual(meta_path.stat().st_mtime_ns, old_timestamp)

    def test_explicit_payload_id_cannot_overwrite_a_different_acquisition(self) -> None:
        changes = {
            "content": {"payload": "changed payload\n"},
            "time": {"acquired_at": "2026-08-09T09:01:00+08:00"},
            "source": {"source_uri": "https://example.invalid/different"},
        }
        for label, change in changes.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                store = FileSourcePayloadStore(root)
                arguments = {
                    "run_id": "RUN-20260809-EXPLICIT-ID",
                    "payload_id": "PAY-EXPLICIT-001",
                    "source_uri": "https://example.invalid/source",
                    "acquired_at": "2026-08-09T09:00:00+08:00",
                }
                first = store.put("original payload\n", **arguments)
                data_path = root / first["path"]
                meta_path = data_path.with_suffix(".json")
                original_data = data_path.read_bytes()
                original_metadata = meta_path.read_bytes()
                changed_payload = str(change.get("payload", "original payload\n"))
                changed_arguments = dict(arguments)
                changed_arguments.update(
                    {key: value for key, value in change.items() if key != "payload"}
                )

                with self.assertRaisesRegex(ValueError, "immutable source payload record"):
                    store.put(changed_payload, **changed_arguments)

                self.assertEqual(data_path.read_bytes(), original_data)
                self.assertEqual(meta_path.read_bytes(), original_metadata)


if __name__ == "__main__":
    unittest.main()
