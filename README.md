# PCIMS

PC Inventory Management Software is a cross-platform PySide6/Qt desktop
application for purchases, component inventory, assembled PCs, sales, profit,
and verified SQLite backups.

## Platforms

PCIMS uses Qt and supports Windows and Linux. Build and test distributable
packages on each target operating system.

## Install and run

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -e .
.venv\Scripts\python app.py
```

On Linux:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/python app.py
```

For a user-scoped Linux installation with an isolated environment and a desktop
menu entry, run this from the project directory:

```bash
sh scripts/install-linux.sh
```

This writes only beneath `$XDG_DATA_HOME` (or `~/.local/share`) and does not
require `sudo`. Rerunning it upgrades the installed application in place.

Application data follows platform conventions:

- Windows: `%LOCALAPPDATA%\PCIMS`
- Linux with `XDG_DATA_HOME`: `$XDG_DATA_HOME/pcims`
- Other Linux environments: `~/.local/share/pcims`

The application creates verified backups at startup and normal shutdown.
Manual backup and restore controls are available under Settings.

## Database policy

The Qt rewrite uses one normalized schema with integer cents and ID-based
relationships. Runtime migration and compatibility with pre-rewrite schemas
are intentionally not included. An incompatible database is rejected without
being modified; use a current-format backup or a new database path.

## Development checks

```powershell
.venv\Scripts\python -m pip install -e ".[dev]"
.venv\Scripts\python -X dev -W error -m unittest discover -s tests -v
.venv\Scripts\ruff check .
.venv\Scripts\mypy pcims --strict --no-error-summary
.venv\Scripts\bandit -q -r pcims
```

Tests configure temporary SQLite files and the Qt offscreen platform. They do
not open or delete the application database. CI runs the same checks on Windows
and Linux with Python 3.11 and 3.13, then builds the source and wheel packages.

## Build a desktop executable

Qt's deployment tool builds for the operating system on which it is run:

```powershell
.venv\Scripts\pyside6-deploy app.py --name PCIMS
```

Run the equivalent `.venv/bin/pyside6-deploy` command on Linux. Release builds
should be produced and smoke-tested independently on Windows and Linux; the CI
workflow runs the complete backend and offscreen Qt test suite on both.
