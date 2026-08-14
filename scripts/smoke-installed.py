"""Smoke the installed wheel from outside the source checkout."""

from importlib.metadata import distribution
from pathlib import Path
from tempfile import TemporaryDirectory

from pcims.db.connection import Database
from pcims.domain import NewExpense, SaleTerms
from pcims.services import ApplicationServices


def main() -> None:
    entry_points = distribution("pcims").entry_points
    gui_entry = next(entry for entry in entry_points if entry.name == "pcims")
    if gui_entry.value != "pcims.app.application:main":
        raise RuntimeError(f"Unexpected GUI entry point: {gui_entry.value}")

    with TemporaryDirectory() as temporary_directory:
        services = ApplicationServices(
            Database.at(Path(temporary_directory) / "installed-wheel.db")
        )
        services.initialize()
        expense_id = services.add_expenses(
            [NewExpense.create("Artifact GPU", "GPU", "100.00", "2026-08-14")]
        )[0]
        services.sell_items(
            [expense_id], SaleTerms.create("125.00", "2026-08-14")
        )
        if services.financial_summary().profit_cents != 2_500:
            raise RuntimeError("Installed wheel produced an invalid financial result.")

    print("installed wheel smoke: OK")


if __name__ == "__main__":
    main()
