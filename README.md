# PCIMS

PC Inventory Management Software is a cross-platform PySide6/Qt desktop
application for purchases, component inventory, assembled PCs, sales, profit,
return on investment (ROI), and verified SQLite backups.

The PySide6 rewrite is currently available as a `2.0.0b3` prerelease. Beta
builds use the current normalized database format and deliberately do not open
databases created by the former Tkinter application.

## Platforms

PCIMS uses Qt and supports Windows, macOS, and Linux. Native preview packages
are built and smoke-tested on each target operating system.

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

For a user-scoped Windows installation with an isolated environment and a Start
Menu shortcut, run this from the project directory:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\install-windows.ps1
```

The Windows installer confines application replacement to
`%LOCALAPPDATA%\PCIMS\application`, serializes concurrent upgrades, smoke-tests
the staged build, and restores the prior installation if publication fails.
It builds from a clean source copy and rejects any wheel whose package manifest
differs from the current source tree.

For a user-scoped Linux installation with an isolated environment and a desktop
menu entry, run this from the project directory:

```bash
sh scripts/install-linux.sh
```

This writes only beneath `$XDG_DATA_HOME` (or `~/.local/share`) and does not
require `sudo`. Each run builds and smoke-tests a fresh environment using the
hash-locked runtime dependencies, then replaces the prior installation only
after the same clean-wheel manifest verification succeeds.

Application data follows platform conventions:

- Windows: `%LOCALAPPDATA%\PCIMS`
- macOS: `~/Library/Application Support/PCIMS`
- Linux with `XDG_DATA_HOME`: `$XDG_DATA_HOME/pcims`
- Other Linux environments: `~/.local/share/pcims`

`PCIMS_DATA_DIR` and `PCIMS_DB_PATH` are supported for controlled deployments
and tests. Both must be absolute paths so a desktop launch cannot select a
different database merely because its working directory changed.

The application creates verified backups at startup and normal shutdown.
Manual backup and restore controls are available under Settings. Proofs of
purchase are stored inside the database, so verified backups and restores
include them automatically.

## Database policy

The pre-release Qt rewrite starts from one clean baseline schema with integer
cents, ID-based relationships, and no compatibility layer for Tkinter-era
layouts. Databases from the clean Qt baseline are upgraded through ordered,
transactional migrations after a verified pre-upgrade backup. Modified, unknown,
and legacy schemas are rejected without being changed.

Components can be corrected in place, including after sale or while assigned to
a PC. Editing an active PC atomically updates its name and complete ordered parts
list, and each component has its own ID, so multiple parts of the same category
remain distinct. Selling a PC marks that same PC record sold instead of deleting
it. Undoing the sale restores the same PC identity and membership. Standalone
sales can likewise be deleted with Undo, returning all of their items to stock.
An existing sale's price and date can be corrected in place without changing its
identity or sold-item membership. The sale date must remain on or after every
included item's purchase date.
The financial summary and each sale report ROI on cost as profit divided by the
purchase cost of the sold items. A zero-cost sale shows `N/A` instead of an
undefined or infinite percentage.

Each individual item can have up to 20 proofs of purchase in PDF, PNG, JPEG, or
WebP format, with a 20 MiB limit per file. Proofs can be selected while staging
a purchase or managed later from Inventory and purchase history, including for
sold items. Identical proof content is stored once regardless of its attachment
filename and linked to every applicable item. Total stored proof content is
capped at 512 MiB so receipts cannot silently expand every backup without limit.

Automatic backup retention is configurable from 1 to 30 copies. An unchanged
database reuses its newest verified backup instead of storing a duplicate. The
Settings page reports database, proof, and automatic-backup storage usage.
Window geometry, splitters, table widths, and table sorting are restored between
sessions. The former Activity feed has been removed; unexpected application
errors remain available in the bounded diagnostic log.

## Development checks

```powershell
.venv\Scripts\python -m pip install --require-hashes -r requirements-dev.lock
.venv\Scripts\python -m pip install --no-deps -e .
.venv\Scripts\python scripts\verify.py
```

Regenerate the runtime, development, build, and packaging lock files
deliberately whenever dependency ranges are changed; never hand-edit hashes
independently of the resolved package versions.

Tests configure temporary SQLite files and the Qt offscreen platform. They do
not open or delete the application database. `scripts/verify.py` stops at the
first failed test, quality check, build, or installed-wheel smoke test. CI runs
that same gate on Windows and Linux with Python 3.11, 3.13, and 3.14, then
installs the resulting wheel as a package. The Linux job also performs one real,
user-scoped desktop installation from the locked dependencies and validates its
generated desktop entry.

## Release artifacts

Preview releases contain native, smoke-tested PyInstaller packages built from
the hash-locked packaging environment:

- `PCIMS-2.0.0b3-Windows-x64.zip`
- `PCIMS-2.0.0b3-macOS-arm64.zip`
- `PCIMS-2.0.0b3-Linux-x86_64.tar.gz`

These beta packages are not code-signed, so Windows SmartScreen or macOS
Gatekeeper may show a warning. The reproducible wheel remains the authoritative
Python package. The repository also retains transactional, user-scoped Windows
and Linux installation scripts for source checkouts; both are exercised with
real locked environments and installed Qt smoke tests in CI.
