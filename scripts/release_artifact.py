"""Build and install PCIMS from a clean source copy with manifest verification."""

import os
import shutil
import subprocess  # nosec B404
import sys
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).parents[1]


def copy_clean_source(destination: Path, project_root: Path = ROOT) -> None:
    ignored = shutil.ignore_patterns(
        ".git",
        ".venv",
        "build",
        "dist",
        "dist-*",
        "*.egg-info",
        "__pycache__",
        "*.pyc",
    )
    shutil.copytree(project_root, destination, ignore=ignored)


def wheel_in(directory: Path) -> Path:
    wheels = tuple(directory.glob("*.whl"))
    if len(wheels) != 1:
        raise RuntimeError(
            f"Expected exactly one wheel in {directory}, found {len(wheels)}."
        )
    return wheels[0]


def verify_wheel_contents(wheel: Path, project_root: Path = ROOT) -> None:
    expected = {
        path.relative_to(project_root).as_posix()
        for path in (project_root / "pcims").rglob("*")
        if path.is_file()
        and "__pycache__" not in path.parts
        and path.suffix != ".pyc"
    }
    with zipfile.ZipFile(wheel) as archive:
        packaged = {
            name
            for name in archive.namelist()
            if name.startswith("pcims/") and not name.endswith("/")
        }
    if packaged != expected:
        missing = sorted(expected - packaged)
        unexpected = sorted(packaged - expected)
        raise RuntimeError(
            f"Wheel package files differ (missing={missing}, unexpected={unexpected})."
        )


def install_verified_wheel() -> None:
    with TemporaryDirectory() as temporary_directory:
        work = Path(temporary_directory)
        clean_source = work / "source"
        wheel_directory = work / "wheel"
        copy_clean_source(clean_source)
        wheel_directory.mkdir()
        environment = os.environ.copy()
        environment["SOURCE_DATE_EPOCH"] = "315532800"
        subprocess.run(  # nosec B603
            [
                sys.executable,
                "-m",
                "pip",
                "wheel",
                "--no-build-isolation",
                "--no-deps",
                "--wheel-dir",
                str(wheel_directory),
                str(clean_source),
            ],
            env=environment,
            check=True,
        )
        wheel = wheel_in(wheel_directory)
        verify_wheel_contents(wheel)
        subprocess.run(  # nosec B603
            [sys.executable, "-m", "pip", "install", "--no-deps", str(wheel)],
            check=True,
        )


if __name__ == "__main__":
    install_verified_wheel()
