"""Smoke the installed wheel from outside the source checkout."""

import os
import time
from datetime import date
from importlib.metadata import distribution
from pathlib import Path
from tempfile import TemporaryDirectory

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pcims.app.application import create_application
from pcims.app.main_window import MainWindow
from pcims.db.connection import Database
from pcims.domain import NewExpense, SaleTerms
from pcims.services import ApplicationServices
from pcims.version import application_version


def main() -> None:
    entry_points = distribution("pcims").entry_points
    gui_entry = next(entry for entry in entry_points if entry.name == "pcims")
    if gui_entry.value != "pcims.app.application:main":
        raise RuntimeError(f"Unexpected GUI entry point: {gui_entry.value}")
    if application_version() != distribution("pcims").version:
        raise RuntimeError("Runtime and installed distribution versions differ.")
    application = create_application([])

    with TemporaryDirectory() as temporary_directory:
        services = ApplicationServices(
            Database.at(Path(temporary_directory) / "installed-wheel.db")
        )
        services.initialize()
        expense_id = services.add_expenses(
            [NewExpense.create("Artifact GPU", "GPU", "100.00", "2026-08-14")]
        )[0]
        sale_id = services.sell_items(
            [expense_id], SaleTerms.create("125.00", "2026-08-14")
        )
        services.update_sale(sale_id, SaleTerms.create("120.00", "2026-08-15"))
        if services.financial_summary().profit_cents != 2_000:
            raise RuntimeError("Installed wheel produced an invalid financial result.")
        dashboard = services.balance_snapshot(None, date(2026, 8, 15))
        if dashboard.summary.profit_cents != 2_000 or not dashboard.points:
            raise RuntimeError("Installed wheel produced an invalid balance dashboard.")
        window = MainWindow(services)
        window.balance_page.period.setCurrentIndex(
            window.balance_page.period.findData("all")
        )
        window.tabs.setCurrentWidget(window.balance_page)
        deadline = time.monotonic() + 5
        while window.tasks.active and time.monotonic() < deadline:
            application.processEvents()
            time.sleep(0.005)
        if (
            window.tasks.active
            or window.tabs.count() != 7
            or window.balance_page.table_model.rowCount() == 0
        ):
            raise RuntimeError("Installed Qt frontend did not initialize completely.")
        window.deleteLater()
        application.processEvents()

    print("installed wheel backend and Qt smoke: OK")


if __name__ == "__main__":
    main()
