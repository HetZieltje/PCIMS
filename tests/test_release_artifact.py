import tempfile
import unittest
import zipfile
from pathlib import Path

from scripts.release_artifact import copy_clean_source, verify_wheel_contents


class ReleaseArtifactTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_clean_source_copy_excludes_build_caches_but_keeps_package_assets(self):
        project = self.root / "project"
        package = project / "pcims"
        package.mkdir(parents=True)
        (package / "module.py").write_text("value = 1\n", encoding="utf-8")
        (package / "asset.txt").write_text("asset\n", encoding="utf-8")
        (project / "build" / "lib").mkdir(parents=True)
        (project / "build" / "lib" / "stale.py").write_text(
            "stale = True\n", encoding="utf-8"
        )
        (project / ".release-preview").mkdir()
        (project / ".release-preview" / "package.zip").write_bytes(b"temporary")
        destination = self.root / "clean"

        copy_clean_source(destination, project)

        self.assertTrue((destination / "pcims" / "asset.txt").is_file())
        self.assertFalse((destination / "build").exists())
        self.assertFalse((destination / ".release-preview").exists())

    def test_wheel_manifest_must_exactly_match_package_files(self):
        project = self.root / "project"
        package = project / "pcims"
        package.mkdir(parents=True)
        (package / "module.py").write_text("value = 1\n", encoding="utf-8")
        wheel = self.root / "pcims.whl"
        with zipfile.ZipFile(wheel, "w") as archive:
            archive.writestr("pcims/module.py", "value = 1\n")

        verify_wheel_contents(wheel, project)

        with zipfile.ZipFile(wheel, "a") as archive:
            archive.writestr("pcims/deleted.py", "stale = True\n")
        with self.assertRaisesRegex(RuntimeError, "unexpected"):
            verify_wheel_contents(wheel, project)


if __name__ == "__main__":
    unittest.main()
