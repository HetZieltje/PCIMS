"""Single source of truth for the application and distribution version."""

__version__ = "2.0.0b4"


def application_version() -> str:
    return __version__
