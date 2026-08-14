"""Fail unless two independently built wheel files are byte-identical."""

import hashlib
import sys
from pathlib import Path


def wheel_in(directory: Path) -> Path:
    wheels = tuple(directory.glob("*.whl"))
    if len(wheels) != 1:
        raise RuntimeError(
            f"Expected exactly one wheel in {directory}, found {len(wheels)}."
        )
    return wheels[0]


def digest(path: Path) -> str:
    checksum = hashlib.sha256()
    with path.open("rb") as artifact:
        for block in iter(lambda: artifact.read(1024 * 1024), b""):
            checksum.update(block)
    return checksum.hexdigest()


def main(arguments: list[str]) -> int:
    if len(arguments) != 2:
        raise SystemExit("usage: verify-reproducible-wheel.py FIRST_DIR SECOND_DIR")
    first = wheel_in(Path(arguments[0]))
    second = wheel_in(Path(arguments[1]))
    first_digest = digest(first)
    second_digest = digest(second)
    if first_digest != second_digest:
        raise RuntimeError(
            f"Wheel builds differ: {first.name}={first_digest}, "
            f"{second.name}={second_digest}"
        )
    print(f"reproducible wheel sha256: {first_digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
