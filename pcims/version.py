"""Runtime version derived from installed distribution metadata."""

from importlib.metadata import PackageNotFoundError, version


def application_version() -> str:
    try:
        return version("pcims")
    except PackageNotFoundError:
        return "development"
