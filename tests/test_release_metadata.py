import hashlib
import tempfile
import unittest
from pathlib import Path

from scripts.write_checksums import write_checksum_manifest


class ReleaseMetadataTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_checksum_manifest_is_complete_sorted_and_reproducible(self):
        second = self.root / "PCIMS-Windows.zip"
        first = self.root / "PCIMS-Linux.tar.gz"
        second.write_bytes(b"windows")
        first.write_bytes(b"linux")
        (self.root / ".SHA256SUMS.tmp").write_text("interrupted", encoding="utf-8")

        manifest = write_checksum_manifest(self.root)
        expected = "".join(
            f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n"
            for path in (first, second)
        )
        self.assertEqual(manifest.read_text(encoding="utf-8"), expected)

        write_checksum_manifest(self.root)
        self.assertEqual(manifest.read_text(encoding="utf-8"), expected)
        self.assertFalse((self.root / ".SHA256SUMS.tmp").exists())

    def test_checksum_manifest_rejects_empty_or_nested_artifact_sets(self):
        with self.assertRaisesRegex(ValueError, "no files"):
            write_checksum_manifest(self.root)

        (self.root / "nested").mkdir()
        with self.assertRaisesRegex(ValueError, "regular files"):
            write_checksum_manifest(self.root)

    def test_checksum_manifest_requires_a_plain_output_name(self):
        (self.root / "artifact.zip").write_bytes(b"artifact")
        with self.assertRaisesRegex(ValueError, "one non-empty file name"):
            write_checksum_manifest(self.root, "metadata/SHA256SUMS")


if __name__ == "__main__":
    unittest.main()
