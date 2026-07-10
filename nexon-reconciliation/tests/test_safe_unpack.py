from __future__ import annotations

import sys
import tempfile
import unittest
import zipfile
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from recon_core.safe_unpack import extract_zip  # noqa: E402


class SafeExtractTests(unittest.TestCase):
    def test_extracts_safe_member(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "safe.zip"
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("invoice/detail.csv", "service_id,amount\nsvc-1,10\n")

            output = root / "out"
            inventory = extract_zip(archive, output)

            self.assertEqual([], inventory["blocked"])
            self.assertTrue((output / "invoice" / "detail.csv").is_file())
            self.assertEqual(1, len(inventory["members"]))

    def test_blocks_unsafe_members(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "unsafe.zip"
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("../escape.txt", "bad")
                zf.writestr("/absolute.txt", "bad")
                zf.writestr("C:/drive.txt", "bad")

            output = root / "out"
            inventory = extract_zip(archive, output)

            self.assertEqual(3, len(inventory["blocked"]))
            self.assertFalse((root / "escape.txt").exists())

    def test_blocks_duplicate_normalized_member(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "duplicate.zip"
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("same.txt", "first")
                zf.writestr("same.txt", "second")

            inventory = extract_zip(archive, root / "out")

            self.assertEqual(0, len(inventory["members"]))
            self.assertEqual(1, len(inventory["blocked"]))
            self.assertFalse((root / "out" / "same.txt").exists())

    def test_blocks_case_insensitive_duplicate_normalized_member(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "duplicate-case.zip"
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("Same.txt", "first")
                zf.writestr("same.txt", "second")

            inventory = extract_zip(archive, root / "out")

            self.assertEqual(0, len(inventory["members"]))
            self.assertEqual(1, len(inventory["blocked"]))

    def test_blocks_corrupt_archive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "corrupt.zip"
            archive.write_bytes(b"not a zip")

            inventory = extract_zip(archive, root / "out")

            self.assertEqual(0, len(inventory["members"]))
            self.assertEqual(1, len(inventory["blocked"]))

    def test_blocks_archive_above_member_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "too-many.zip"
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("one.txt", "1")
                zf.writestr("two.txt", "2")

            inventory = extract_zip(archive, root / "out", max_members=1)

            self.assertEqual(0, len(inventory["members"]))
            self.assertTrue(inventory["blocked"])

    def test_blocks_member_above_size_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "too-large.zip"
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("large.txt", "12345")

            inventory = extract_zip(archive, root / "out", max_single_file_bytes=4)

            self.assertEqual(0, len(inventory["members"]))
            self.assertTrue(inventory["blocked"])


if __name__ == "__main__":
    unittest.main()
