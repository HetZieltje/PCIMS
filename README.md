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
require `sudo`. Each run builds and smoke-tests a fresh environment using the
hash-locked runtime dependencies, then replaces the prior installation only
after verification succeeds.

Application data follows platform conventions:

- Windows: `%LOCALAPPDATA%\PCIMS`
- Linux with `XDG_DATA_HOME`: `$XDG_DATA_HOME/pcims`
- Other Linux environments: `~/.local/share/pcims`

`PCIMS_DATA_DIR` and `PCIMS_DB_PATH` are supported for controlled deployments
and tests. Both must be absolute paths so a desktop launch cannot select a
different database merely because its working directory changed.

The application creates verified backups at startup and normal shutdown.
Manual backup and restore controls are available under Settings.

## Database policy

The Qt rewrite uses one normalized schema with integer cents and ID-based
relationships. Runtime migration and compatibility with pre-rewrite schemas
are intentionally not included. An incompatible database is rejected without
being modified; use a current-format backup or a new database path.

## Development checks

```powershell
.venv\Scripts\python -m pip install --require-hashes -r requirements-dev.lock
.venv\Scripts\python -m pip install --no-deps -e .
.venv\Scripts\python -X dev -W error -m unittest discover -s tests -v
.venv\Scripts\ruff check .
.venv\Scripts\mypy pcims --strict --no-error-summary
.venv\Scripts\bandit -q -r pcims scripts
```

Regenerate the runtime, development, and build lock files deliberately whenever
dependency ranges are changed; never hand-edit hashes independently of the
resolved package versions.

Tests configure temporary SQLite files and the Qt offscreen platform. They do
not open or delete the application database. CI runs the same checks on Windows
and Linux with Python 3.11, 3.13, and 3.14, validates the installed dependency
graph, then builds the wheel reproducibly, installs it, and smoke-tests its
backend and Qt frontend from outside the source checkout. The Linux job also
performs one real, user-scoped desktop installation from the locked dependencies
and validates its generated desktop entry.

## Release artifacts

The reproducible wheel is the authoritative cross-platform release artifact.
On Linux, `scripts/install-linux.sh` turns the checked-out release into a
transactional, user-scoped desktop installation. Both paths are exercised in
CI.

Ad-hoc `pyside6-deploy` output is not treated as a release artifact because its
Nuitka toolchain, platform libraries, signing, and installed-program smoke test
are not yet locked into this repository. A native executable should only be
published after that platform-specific pipeline is reproducible and verified.
