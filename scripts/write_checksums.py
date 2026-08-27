"""Write a deterministic SHA-256 manifest for flat release artifacts."""

import argparse
import hashlib
import os
from pathlib import Path

DEFAULT_MANIFEST = "SHA256SUMS"


def write_checksum_manifest(
    directory: Path,
    manifest_name: str = DEFAULT_MANIFEST,
) -> Path:
    """Hash every regular file in *directory* except the manifest itself."""

    if not directory.is_dir():
        raise ValueError(f"Release artifact directory does not exist: {directory}")
    if (
        Path(manifest_name).name != manifest_name
        or not manifest_name
        or "\n" in manifest_name
        or "\r" in manifest_name
    ):
        raise ValueError("Manifest name must be one non-empty file name.")

    manifest = directory / manifest_name
    temporary = manifest.with_name(f".{manifest.name}.tmp")
    artifacts: list[Path] = []
    for candidate in directory.iterdir():
        if candidate in {manifest, temporary}:
            continue
        if candidate.is_symlink() or not candidate.is_file():
            raise ValueError(
                f"Release artifact directory must contain only regular files: {candidate}"
            )
        if "\n" in candidate.name or "\r" in candidate.name:
            raise ValueError(
                f"Release artifact name contains a newline: {candidate.name!r}"
            )
        artifacts.append(candidate)
    if not artifacts:
        raise ValueError("Release artifact directory contains no files to hash.")

    lines = []
    for artifact in sorted(artifacts, key=lambda path: path.name):
        with artifact.open("rb") as source:
            digest = hashlib.file_digest(source, "sha256").hexdigest()
        lines.append(f"{digest}  {artifact.name}\n")

    try:
        temporary.write_text("".join(lines), encoding="utf-8", newline="\n")
        os.replace(temporary, manifest)
    finally:
        temporary.unlink(missing_ok=True)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--output", default=DEFAULT_MANIFEST)
    arguments = parser.parse_args()
    manifest = write_checksum_manifest(arguments.directory, arguments.output)
    print(manifest)


if __name__ == "__main__":
    main()
