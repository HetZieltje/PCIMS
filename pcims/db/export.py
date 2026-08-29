"""Portable, human-readable exports from one coherent database snapshot."""

import csv
import logging
import os
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from pcims.db.connection import Database
from pcims.db.reads import ReadQueries
from pcims.models import Expense


@dataclass(slots=True)
class _PublicationState:
    final: Path
    rollback: Path
    moved: bool = False
    published: bool = False


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


def _write_csv(
    path: Path, header: tuple[str, ...], rows: Iterable[tuple[object, ...]]
) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(header)
        writer.writerows(rows)
        file.flush()
        os.fsync(file.fileno())


def _remove_temporary(path: Path, primary_error: BaseException | None) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        return
    except OSError as cleanup_error:
        if primary_error is None:
            raise
        primary_error.add_note(
            f"Unable to clean up temporary export {path}: {cleanup_error}"
        )


def _sync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _publish_csv_pair(staged: tuple[tuple[Path, Path], ...]) -> None:
    """Replace both exports, restoring the previous pair if publication fails."""
    states: list[_PublicationState] = []
    primary_error: BaseException | None = None
    try:
        for final, temporary in staged:
            rollback = final.with_name(f".{final.name}.{uuid.uuid4().hex}.rollback")
            state = _PublicationState(final, rollback)
            states.append(state)
            if final.exists():
                os.replace(final, rollback)
                state.moved = True
            os.replace(temporary, final)
            state.published = True
        _sync_directory(staged[0][0].parent)
    except BaseException as error:
        primary_error = error
        for state in reversed(states):
            try:
                if state.moved:
                    os.replace(state.rollback, state.final)
                elif state.published:
                    state.final.unlink()
            except OSError as rollback_error:
                error.add_note(
                    f"Unable to restore the previous export at {state.final}: "
                    f"{rollback_error}. Recovery data may remain at {state.rollback}."
                )
        raise
    finally:
        for _final, temporary in staged:
            _remove_temporary(temporary, primary_error)

    logger = logging.getLogger("pcims.db.export")
    for state in states:
        try:
            state.rollback.unlink()
        except FileNotFoundError:
            pass
        except OSError as error:
            logger.warning(
                "Unable to remove prior export snapshot %s: %s", state.rollback, error
            )


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
    token = uuid.uuid4().hex
    purchases_temporary = destination / f".{purchases_path.name}.{token}.tmp"
    sales_temporary = destination / f".{sales_path.name}.{token}.tmp"
    primary_error: BaseException | None = None
    try:
        _write_csv(
            purchases_temporary,
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
        _write_csv(
            sales_temporary,
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
        _publish_csv_pair(
            (
                (purchases_path, purchases_temporary),
                (sales_path, sales_temporary),
            )
        )
    except BaseException as error:
        primary_error = error
        raise
    finally:
        _remove_temporary(purchases_temporary, primary_error)
        _remove_temporary(sales_temporary, primary_error)
    return purchases_path, sales_path
