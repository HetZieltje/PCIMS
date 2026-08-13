"""Compatibility-free executable wrapper for the Qt application."""

from app.application import main


if __name__ == "__main__":
    raise SystemExit(main())
