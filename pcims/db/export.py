"""Portable, human-readable exports from one coherent database snapshot."""

import csv
import os
from collections.abc import Iterable
from pathlib import Path

from pcims.db.connection import Database
from pcims.db.reads import ReadQueries


def _publish_csv(
    path: Path, header: tuple[str, ...], rows: Iterable[tuple[object, ...]]
) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8-sig", newline="") as file:
            writer = csv.writer(file)
            writer.writerow(header)
            writer.writerows(rows)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def export_csv(
    directory: str | os.PathLike[str], *, database: Database
) -> tuple[Path, Path]:
    """Export purchases and sales, retaining stable IDs for reconciliation."""
    destination = Path(directory).expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)
    with database.transaction() as connection:
        queries = ReadQueries(connection)
        expenses = queries.list_expenses()
        sales = queries.list_sales()

    purchases_path = destination / "pcims-purchases.csv"
    sales_path = destination / "pcims-sales.csv"
    _publish_csv(
        purchases_path,
        (
            "expense_id",
            "name",
            "type",
            "price",
            "purchase_date",
            "status",
            "pc_id",
            "sale_id",
            "vendor",
            "serial_number",
            "storage_location",
            "condition",
            "warranty_until",
            "notes",
            "proof_count",
        ),
        (
            (
                item.id,
                item.name,
                item.item_type,
                f"{item.price_cents / 100:.2f}",
                item.purchase_date.isoformat(),
                "sold"
                if item.sale_id is not None
                else "assembled"
                if item.pc_id is not None
                else "available",
                item.pc_id or "",
                item.sale_id or "",
                item.details.vendor,
                item.details.serial_number,
                item.details.storage_location,
                item.details.condition or "",
                item.details.warranty_until.isoformat()
                if item.details.warranty_until
                else "",
                item.details.notes,
                len(item.proofs),
            )
            for item in expenses
        ),
    )
    _publish_csv(
        sales_path,
        (
            "sale_id",
            "sale_date",
            "kind",
            "name",
            "cost",
            "revenue",
            "profit",
            "expense_ids",
        ),
        (
            (
                sale.id,
                sale.sale_date.isoformat(),
                sale.kind,
                sale.name,
                f"{sale.cost_cents / 100:.2f}",
                f"{sale.selling_price_cents / 100:.2f}",
                f"{sale.profit_cents / 100:.2f}",
                ";".join(str(item.id) for item in sale.items),
            )
            for sale in sales
        ),
    )
    return purchases_path, sales_path
