"""Reject release tags that do not exactly represent the runtime version."""

import argparse
import re

from pcims.version import application_version

_RELEASE_VERSION = re.compile(
    r"^(?P<base>(?:0|[1-9][0-9]*)\."
    r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*))"
    r"(?:(?P<stage>a|b|rc)(?P<number>0|[1-9][0-9]*))?$"
)
_TAG_STAGE = {"a": "alpha", "b": "beta", "rc": "rc"}


def release_tag_for_version(version: str) -> str:
    """Return the repository's tag spelling for one releasable PEP 440 version."""
    match = _RELEASE_VERSION.fullmatch(version)
    if match is None:
        raise ValueError(
            f"Application version {version!r} is not releasable; "
            "remove development, post-release, or local suffixes first."
        )
    tag = f"v{match['base']}"
    stage = match["stage"]
    if stage is not None:
        tag += f"-{_TAG_STAGE[stage]}.{match['number']}"
    return tag


def validate_release_tag(tag: str, version: str) -> None:
    expected = release_tag_for_version(version)
    if tag != expected:
        raise ValueError(
            f"Release tag {tag!r} does not match application version {version!r}; "
            f"expected {expected!r}."
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("tag")
    arguments = parser.parse_args()
    version = application_version()
    validate_release_tag(arguments.tag, version)
    print(version)


if __name__ == "__main__":
    main()
