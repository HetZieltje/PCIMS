"""Compatibility-free executable wrapper for the Qt application."""

from pcims.app.application import main

if __name__ == "__main__":
    raise SystemExit(main())
