import unittest

from scripts.validate_release_tag import (
    release_tag_for_version,
    validate_release_tag,
)


class ReleaseVersionTests(unittest.TestCase):
    def test_stable_and_prerelease_versions_have_unambiguous_tags(self):
        self.assertEqual(release_tag_for_version("2.0.0"), "v2.0.0")
        self.assertEqual(release_tag_for_version("2.0.0a1"), "v2.0.0-alpha.1")
        self.assertEqual(release_tag_for_version("2.0.0b5"), "v2.0.0-beta.5")
        self.assertEqual(release_tag_for_version("2.0.0rc2"), "v2.0.0-rc.2")

    def test_development_and_noncanonical_versions_are_not_releasable(self):
        for version in (
            "2.0.0b5.dev0",
            "2.0",
            "2.0.0.post1",
            "2.0.0+local",
            "02.0.0",
            "2.00.0",
            "2.0.00",
            "2.0.0b05",
        ):
            with (
                self.subTest(version=version),
                self.assertRaisesRegex(ValueError, "not releasable"),
            ):
                release_tag_for_version(version)

    def test_release_tag_must_exactly_match_application_version(self):
        validate_release_tag("v2.0.0-beta.5", "2.0.0b5")
        with self.assertRaisesRegex(ValueError, "expected 'v2.0.0-beta.5'"):
            validate_release_tag("v2.0.0-beta.4", "2.0.0b5")


if __name__ == "__main__":
    unittest.main()
