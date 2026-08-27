"""Portable, human-readable exports from one coherent database snapshot."""

import csv
import os
from collections.abc import Iterable
from pathlib import Path

from pcims.db.connection import Database
from pcims.db.reads import ReadQueries
from pcims.models import Expense


def _plain_cents(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    absolute = abs(cents)
    return f"{sign}{absolute // 100}.{absolute % 100:02d}"


def _inventory_status(item: Expense) -> str:
    if item.sale_id is not None:
        return "sold"
    if item.is_laptop:
        return "laptop"
    if item.laptop_id is not None:
        return "installed in laptop"
    if item.pc_id is not None:
        return "assembled"
    return "available"


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
            "item_id",
            "name",
            "type",
            "price",
            "purchase_date",
            "status",
            "pc_id",
            "laptop_id",
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
                item.display_type,
                _plain_cents(item.purchase_cost_cents),
                item.purchase_date.isoformat(),
                _inventory_status(item),
                item.pc_id or "",
                (item.id if item.is_laptop else item.laptop_id) or "",
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
            "item_ids",
        ),
        (
            (
                sale.id,
                sale.sale_date.isoformat(),
                sale.kind,
                sale.name,
                _plain_cents(sale.cost_cents),
                _plain_cents(sale.selling_price_cents),
                _plain_cents(sale.profit_cents),
                ";".join(str(item.id) for item in sale.items),
            )
            for sale in sales
        ),
    )
    return purchases_path, sales_path
