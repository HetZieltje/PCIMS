"""Run the complete local release gate and stop at the first failure."""

import argparse
import os
import subprocess  # nosec B404
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from release_artifact import copy_clean_source, verify_wheel_contents, wheel_in

ROOT = Path(__file__).parents[1]


def run_python(
    *arguments: str,
    cwd: Path = ROOT,
    environment: dict[str, str] | None = None,
) -> None:
    # The executable and every argument are repository-owned, never user shell text.
    subprocess.run(  # nosec B603
        [sys.executable, *arguments],
        cwd=cwd,
        env=environment,
        check=True,
    )


def verify(output_directory: Path | None) -> None:
    run_python("-X", "dev", "-W", "error", "-m", "unittest", "discover", "-s", "tests")
    run_python("-m", "ruff", "check", "pcims", "tests", "scripts")
    run_python("-m", "mypy", "pcims", "--strict")
    run_python("-m", "bandit", "-q", "-r", "pcims", "scripts")
    run_python("-m", "compileall", "-q", "pcims", "scripts")
    if os.name == "nt":
        powershell = (
            Path(os.environ.get("SystemRoot", r"C:\Windows"))
            / "System32"
            / "WindowsPowerShell"
            / "v1.0"
            / "powershell.exe"
        )
        installer_environment = os.environ.copy()
        installer_environment["PYTHON"] = sys.executable
        subprocess.run(  # nosec B603
            [
                str(powershell),
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(ROOT / "tests" / "test-windows-installer.ps1"),
            ],
            cwd=ROOT,
            env=installer_environment,
            check=True,
        )
    else:
        subprocess.run(  # nosec B603
            ["/bin/sh", str(ROOT / "tests" / "test-linux-installer.sh")],
            cwd=ROOT,
            check=True,
        )

    with TemporaryDirectory() as temporary_directory:
        work = Path(temporary_directory)
        first = (output_directory or work / "dist").resolve()
        rebuilt = work / "dist-rebuilt"
        first_source = work / "source-first"
        rebuilt_source = work / "source-rebuilt"
        if first.exists() and tuple(first.glob("*.whl")):
            raise RuntimeError(f"Wheel output directory is not empty: {first}")
        first.mkdir(parents=True, exist_ok=True)
        rebuilt.mkdir()
        copy_clean_source(first_source)
        copy_clean_source(rebuilt_source)
        build_environment = os.environ.copy()
        build_environment["SOURCE_DATE_EPOCH"] = "315532800"
        for source, destination in (
            (first_source, first),
            (rebuilt_source, rebuilt),
        ):
            run_python(
                "-m",
                "build",
                "--wheel",
                "--no-isolation",
                "--outdir",
                str(destination),
                cwd=source,
                environment=build_environment,
            )
        run_python(
            str(ROOT / "scripts" / "verify-reproducible-wheel.py"),
            str(first),
            str(rebuilt),
        )
        verify_wheel_contents(wheel_in(first))
        outside_checkout = work / "outside-checkout"
        outside_checkout.mkdir()
        smoke_environment = os.environ.copy()
        smoke_environment["PYTHONPATH"] = str(wheel_in(first))
        run_python(
            str(ROOT / "scripts" / "smoke-installed.py"),
            cwd=outside_checkout,
            environment=smoke_environment,
        )

    print("complete release gate: OK")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-directory", type=Path)
    arguments = parser.parse_args()
    verify(arguments.output_directory)


if __name__ == "__main__":
    main()
