# PCIMS

PC Inventory Management Software is a local Tkinter application for tracking component purchases, inventory, assembled PCs, sales, and realized profit.

## Run

```powershell
python -m pip install -r requirements.txt
python app.py
```

`customtkinter` and `tkcalendar` provide the preferred appearance and calendar
picker. If they cannot be installed, PCIMS falls back to standard Tk controls
and an ISO-date field so the application remains usable offline.

Application data is stored in `%LOCALAPPDATA%\PCIMS` on Windows (or `~/.pcims` when `LOCALAPPDATA` is unavailable). An older `db/pcims_db.db` is copied to the user-data directory on first launch. Verified backups are created at startup and on a normal close; manual backup and restore controls are available on the Extras tab.

## Test

```powershell
python -m unittest discover -s tests -v
```

Tests always configure a temporary SQLite file. They never open or delete the application database.

For an explicitly isolated database outside the test suite, set `PCIMS_DB_PATH` before starting Python.
