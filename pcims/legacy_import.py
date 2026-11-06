"""Explicit one-time importer for the final Tkinter PCIMS database.

This module is deliberately not part of application startup.  It reads the
legacy database through a read-only SQLite connection and publishes a newly
created current-schema database only after every row has been validated.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import tempfile
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from pcims.db.connection import Database
from pcims.db.schema import (
    SCHEMA_VERSION,
    initialize_database,
    validate_current_data,
    validate_proof_files,
    validate_schema,
)
from pcims.domain import normalized_date, normalized_item_type, normalized_text
from pcims.money import MAX_MONEY_CENTS, parse_money_cents

_REQUIRED_COLUMNS: dict[str, frozenset[str]] = {
    "expenses": frozenset(
        {
            "id",
            "name",
            "type",
            "price",
            "price_cents",
            "purchase_date",
            "in_inventory",
            "used_in",
        }
    ),
    "assembled_pcs": frozenset({"id", "name"}),
    "assembled_pc_parts": frozenset(
        {"pc_id", "expense_id", "component_type", "position"}
    ),
    "income": frozenset(
        {
            "id",
            "old_id",
            "name",
            "cost",
            "selling_price",
            "profit",
            "cost_cents",
            "selling_price_cents",
            "profit_cents",
            "sale_date",
            "is_pc",
        }
    ),
    "sold_pcs": frozenset(
        {
            "id",
            "name",
            "cpu",
            "cooler",
            "gpu",
            "motherboard",
            "ram",
            "ssd",
            "hdd",
            "pc_case",
            "psu",
            "fan",
            "extra",
        }
    ),
    "sold_pc_parts": frozenset({"sale_id", "expense_id", "component_type", "position"}),
}

_LEGACY_PC_COMPONENT_COLUMNS: tuple[tuple[str, str], ...] = (
    ("CPU", "cpu"),
    ("Cooler", "cooler"),
    ("GPU", "gpu"),
    ("Motherboard", "motherboard"),
    ("RAM", "ram"),
    ("SSD", "ssd"),
    ("HDD", "hdd"),
    ("Case", "pc_case"),
    ("PSU", "psu"),
    ("Fan", "fan"),
    ("Extra", "extra"),
)


class LegacyImportError(RuntimeError):
    """The legacy database cannot be represented safely in the current model."""


@dataclass(frozen=True, slots=True)
class LegacyImportSummary:
    """Counts from one successfully published import."""

    items: int
    active_pcs: int
    sold_pcs: int
    sales: int


@dataclass(frozen=True, slots=True)
class _LegacyItem:
    identifier: int
    name: str
    item_type: str
    price_cents: int
    purchase_date: str
    in_inventory: bool
    used_in: str | None


@dataclass(frozen=True, slots=True)
class _LegacyPC:
    identifier: int
    name: str
    item_ids: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class _LegacySale:
    identifier: int
    name: str
    selling_price_cents: int
    sale_date: str
    is_pc: bool
    item_ids: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class _LegacySoldPC:
    name: str
    fallback_rows: tuple[tuple[int, int, str], ...]


@dataclass(frozen=True, slots=True)
class _LegacyData:
    items: tuple[_LegacyItem, ...]
    active_pcs: tuple[_LegacyPC, ...]
    sales: tuple[_LegacySale, ...]


def _fail(message: str) -> LegacyImportError:
    return LegacyImportError(f"Legacy database cannot be imported: {message}")


def _positive_integer(value: object, label: str) -> int:
    if type(value) is not int or value <= 0:
        raise _fail(f"{label} must be a positive integer.")
    return value


def _position(value: object, label: str) -> int:
    if type(value) is not int or value < 0:
        raise _fail(f"{label} must be a non-negative integer.")
    return value


def _boolean(value: object, label: str) -> bool:
    if value not in (0, 1) or type(value) is not int:
        raise _fail(f"{label} must be 0 or 1.")
    return bool(value)


def _text(value: object, label: str) -> str:
    try:
        return normalized_text(value, label)
    except (TypeError, ValueError) as error:
        raise _fail(f"{label} is invalid: {error}") from error


def _item_type(value: object, label: str) -> str:
    try:
        return normalized_item_type(value)
    except (TypeError, ValueError) as error:
        raise _fail(f"{label} is invalid: {error}") from error


def _iso_date(value: object, label: str) -> str:
    # normalized_date(None) intentionally means "today" for new UI input, but
    # an importer must never invent missing historical data.
    if value is None:
        raise _fail(f"{label} is missing.")
    try:
        parsed = normalized_date(value)
    except (TypeError, ValueError) as error:
        raise _fail(f"{label} is invalid: {error}") from error
    return parsed.isoformat()


def _cents(
    row: sqlite3.Row,
    cents_column: str,
    real_column: str,
    label: str,
) -> int:
    stored = row[cents_column]
    if stored is not None:
        if type(stored) is not int or not 0 <= stored <= MAX_MONEY_CENTS:
            raise _fail(f"{label} cents are invalid.")
        return stored
    try:
        return parse_money_cents(row[real_column], label)
    except (TypeError, ValueError) as error:
        raise _fail(f"{label} is invalid: {error}") from error


def _signed_cents(
    row: sqlite3.Row,
    cents_column: str,
    real_column: str,
    label: str,
) -> int:
    stored = row[cents_column]
    if stored is not None:
        if type(stored) is not int or not -MAX_MONEY_CENTS <= stored <= MAX_MONEY_CENTS:
            raise _fail(f"{label} cents are invalid.")
        return stored
    value = row[real_column]
    text = str(value).strip()
    negative = text.startswith("-")
    if negative:
        text = text[1:]
    try:
        cents = parse_money_cents(text, label)
    except (TypeError, ValueError) as error:
        raise _fail(f"{label} is invalid: {error}") from error
    return -cents if negative else cents


def _require_legacy_schema(connection: sqlite3.Connection) -> None:
    version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    if version != 2:
        raise _fail(
            "only the final Tkinter schema (user_version 2) is supported; "
            f"this file reports version {version}."
        )
    tables = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    missing_tables = sorted(_REQUIRED_COLUMNS.keys() - tables)
    if missing_tables:
        raise _fail(f"required tables are missing: {', '.join(missing_tables)}.")
    for table, required in _REQUIRED_COLUMNS.items():
        actual = {
            str(row[1]) for row in connection.execute(f'PRAGMA table_info("{table}")')
        }
        missing = sorted(required - actual)
        if missing:
            raise _fail(
                f"table {table} is missing required columns: {', '.join(missing)}."
            )


def _read_items(connection: sqlite3.Connection) -> tuple[_LegacyItem, ...]:
    items: list[_LegacyItem] = []
    seen: set[int] = set()
    for row in connection.execute("SELECT * FROM expenses ORDER BY id"):
        identifier = _positive_integer(row["id"], "Expense ID")
        if identifier in seen:
            raise _fail(f"expense ID {identifier} is duplicated.")
        seen.add(identifier)
        used_in_value = row["used_in"]
        used_in = (
            None
            if used_in_value is None
            else _text(used_in_value, f"Expense {identifier} PC assignment")
        )
        items.append(
            _LegacyItem(
                identifier=identifier,
                name=_text(row["name"], f"Expense {identifier} name"),
                item_type=_item_type(row["type"], f"Expense {identifier} type"),
                price_cents=_cents(
                    row, "price_cents", "price", f"Expense {identifier} price"
                ),
                purchase_date=_iso_date(
                    row["purchase_date"], f"Expense {identifier} purchase date"
                ),
                in_inventory=_boolean(
                    row["in_inventory"], f"Expense {identifier} inventory flag"
                ),
                used_in=used_in,
            )
        )
    return tuple(items)


def _membership_rows(
    connection: sqlite3.Connection,
    *,
    sold: bool,
) -> dict[int, tuple[tuple[int, int, str], ...]]:
    table = "sold_pc_parts" if sold else "assembled_pc_parts"
    query = (
        """SELECT sale_id,expense_id,component_type,position
           FROM sold_pc_parts ORDER BY sale_id,position,expense_id"""
        if sold
        else """SELECT pc_id,expense_id,component_type,position
                 FROM assembled_pc_parts ORDER BY pc_id,position,expense_id"""
    )
    memberships: dict[int, list[tuple[int, int, str]]] = {}
    for row in connection.execute(query):
        owner_id = _positive_integer(row[0], f"{table} owner ID")
        item_id = _positive_integer(row[1], f"{table} expense ID")
        component_type = _item_type(row[2], f"{table} component type")
        position = _position(row[3], f"{table} position")
        memberships.setdefault(owner_id, []).append((position, item_id, component_type))
    return {owner: tuple(rows) for owner, rows in memberships.items()}


def _validated_item_ids(
    *,
    owner_label: str,
    owner_name: str,
    rows: tuple[tuple[int, int, str], ...],
    items_by_id: dict[int, _LegacyItem],
    expected_inventory: bool,
) -> tuple[int, ...]:
    if not rows:
        raise _fail(f"{owner_label} has no recorded components.")
    positions = [position for position, _item_id, _kind in rows]
    if len(positions) != len(set(positions)):
        raise _fail(f"{owner_label} has duplicate component positions.")
    item_ids: list[int] = []
    for _position_value, item_id, component_type in rows:
        item = items_by_id.get(item_id)
        if item is None:
            raise _fail(f"{owner_label} references missing expense {item_id}.")
        if item.item_type != component_type:
            raise _fail(
                f"{owner_label} records expense {item_id} as {component_type}, "
                f"but the expense type is {item.item_type}."
            )
        if item.in_inventory is not expected_inventory:
            state = "in" if expected_inventory else "out of"
            raise _fail(
                f"expense {item_id} in {owner_label} must be {state} inventory."
            )
        if item.used_in != owner_name:
            raise _fail(
                f"expense {item_id} in {owner_label} does not point back to "
                f"PC {owner_name!r}."
            )
        item_ids.append(item_id)
    if len(item_ids) != len(set(item_ids)):
        raise _fail(f"{owner_label} contains a component more than once.")
    return tuple(item_ids)


def _read_active_pcs(
    connection: sqlite3.Connection,
    items_by_id: dict[int, _LegacyItem],
) -> tuple[_LegacyPC, ...]:
    memberships = _membership_rows(connection, sold=False)
    pcs: list[_LegacyPC] = []
    known_pc_ids: set[int] = set()
    for row in connection.execute("SELECT id,name FROM assembled_pcs ORDER BY id"):
        identifier = _positive_integer(row["id"], "Assembled PC ID")
        name = _text(row["name"], f"Assembled PC {identifier} name")
        known_pc_ids.add(identifier)
        item_ids = _validated_item_ids(
            owner_label=f"assembled PC {identifier}",
            owner_name=name,
            rows=memberships.get(identifier, ()),
            items_by_id=items_by_id,
            expected_inventory=True,
        )
        pcs.append(_LegacyPC(identifier, name, item_ids))
    orphan_owners = sorted(memberships.keys() - known_pc_ids)
    if orphan_owners:
        raise _fail(
            "assembled_pc_parts contains unknown PC IDs: "
            + ", ".join(map(str, orphan_owners))
            + "."
        )
    return tuple(pcs)


def _read_sales(
    connection: sqlite3.Connection,
    items_by_id: dict[int, _LegacyItem],
) -> tuple[_LegacySale, ...]:
    sold_memberships = _membership_rows(connection, sold=True)
    sold_pcs: dict[int, _LegacySoldPC] = {}
    for row in connection.execute("SELECT * FROM sold_pcs ORDER BY id"):
        identifier = _positive_integer(row["id"], "Sold PC sale ID")
        if identifier in sold_pcs:
            raise _fail(f"sold PC sale ID {identifier} is duplicated.")
        name = _text(row["name"], f"Sold PC sale {identifier} name")
        fallback_rows: list[tuple[int, int, str]] = []
        for component_type, column in _LEGACY_PC_COMPONENT_COLUMNS:
            value = row[column]
            if value is None:
                continue
            item_id = _positive_integer(
                value, f"Sold PC sale {identifier} {component_type} expense ID"
            )
            fallback_rows.append((len(fallback_rows), item_id, component_type))
        sold_pcs[identifier] = _LegacySoldPC(
            name=name,
            fallback_rows=tuple(fallback_rows),
        )

    sales: list[_LegacySale] = []
    known_sale_ids: set[int] = set()
    for row in connection.execute("SELECT * FROM income ORDER BY id"):
        identifier = _positive_integer(row["id"], "Sale ID")
        known_sale_ids.add(identifier)
        name = _text(row["name"], f"Sale {identifier} name")
        is_pc = _boolean(row["is_pc"], f"Sale {identifier} PC flag")
        sale_date = _iso_date(row["sale_date"], f"Sale {identifier} date")
        selling_price_cents = _cents(
            row,
            "selling_price_cents",
            "selling_price",
            f"Sale {identifier} selling price",
        )
        recorded_cost = _cents(
            row, "cost_cents", "cost", f"Sale {identifier} recorded cost"
        )
        recorded_profit = _signed_cents(
            row, "profit_cents", "profit", f"Sale {identifier} recorded profit"
        )

        if is_pc:
            if row["old_id"] is not None:
                raise _fail(f"PC sale {identifier} unexpectedly has an old item ID.")
            sold_pc = sold_pcs.get(identifier)
            if sold_pc is None:
                raise _fail(f"PC sale {identifier} has no sold_pcs row.")
            if sold_pc.name != name:
                raise _fail(
                    f"PC sale {identifier} has conflicting names {name!r} and "
                    f"{sold_pc.name!r}."
                )
            normalized_rows = sold_memberships.get(identifier, ())
            if normalized_rows:
                item_ids = _validated_item_ids(
                    owner_label=f"sold PC sale {identifier}",
                    owner_name=name,
                    rows=normalized_rows,
                    items_by_id=items_by_id,
                    expected_inventory=False,
                )
                if sold_pc.fallback_rows:
                    fallback_ids = _validated_item_ids(
                        owner_label=f"sold PC sale {identifier} legacy slots",
                        owner_name=name,
                        rows=sold_pc.fallback_rows,
                        items_by_id=items_by_id,
                        expected_inventory=False,
                    )
                    unrecorded = sorted(set(fallback_ids) - set(item_ids))
                    if unrecorded:
                        raise _fail(
                            f"sold PC sale {identifier} legacy slots reference expenses "
                            "missing from sold_pc_parts: "
                            + ", ".join(map(str, unrecorded))
                            + "."
                        )
            else:
                if not sold_pc.fallback_rows:
                    raise _fail(
                        f"sold PC sale {identifier} has no recorded components."
                    )
                fallback_ids = _validated_item_ids(
                    owner_label=f"sold PC sale {identifier} legacy slots",
                    owner_name=name,
                    rows=sold_pc.fallback_rows,
                    items_by_id=items_by_id,
                    expected_inventory=False,
                )
                component_order = {
                    component_type: position
                    for position, (component_type, _column) in enumerate(
                        _LEGACY_PC_COMPONENT_COLUMNS
                    )
                }
                # Early Tkinter sales retained only the first ID of each type in
                # sold_pcs. Every component still retained its exact PC name in
                # expenses.used_in, so combine that complete assignment with the
                # typed legacy IDs as integrity anchors.
                reconstructed = sorted(
                    (
                        item
                        for item in items_by_id.values()
                        if not item.in_inventory and item.used_in == name
                    ),
                    key=lambda item: (component_order[item.item_type], item.identifier),
                )
                reconstructed_rows = tuple(
                    (position, item.identifier, item.item_type)
                    for position, item in enumerate(reconstructed)
                )
                item_ids = _validated_item_ids(
                    owner_label=f"sold PC sale {identifier}",
                    owner_name=name,
                    rows=reconstructed_rows,
                    items_by_id=items_by_id,
                    expected_inventory=False,
                )
                missing_anchors = sorted(set(fallback_ids) - set(item_ids))
                if missing_anchors:
                    raise _fail(
                        f"sold PC sale {identifier} cannot recover legacy expenses: "
                        + ", ".join(map(str, missing_anchors))
                        + "."
                    )
        else:
            if identifier in sold_pcs or identifier in sold_memberships:
                raise _fail(f"item sale {identifier} also has sold-PC records.")
            item_id = _positive_integer(row["old_id"], f"Sale {identifier} old item ID")
            item = items_by_id.get(item_id)
            if item is None:
                raise _fail(f"sale {identifier} references missing expense {item_id}.")
            if item.in_inventory or item.used_in is not None:
                raise _fail(
                    f"standalone sold expense {item_id} has an invalid inventory assignment."
                )
            item_ids = (item_id,)

        actual_cost = sum(items_by_id[item_id].price_cents for item_id in item_ids)
        if actual_cost != recorded_cost:
            raise _fail(
                f"sale {identifier} records cost {recorded_cost}, but its items "
                f"total {actual_cost} cents."
            )
        if selling_price_cents - actual_cost != recorded_profit:
            raise _fail(f"sale {identifier} has an inconsistent recorded profit.")
        latest_purchase = max(
            items_by_id[item_id].purchase_date for item_id in item_ids
        )
        if sale_date < latest_purchase:
            raise _fail(
                f"sale {identifier} predates one or more of its purchased items."
            )
        sales.append(
            _LegacySale(
                identifier,
                name,
                selling_price_cents,
                sale_date,
                is_pc,
                item_ids,
            )
        )

    orphan_sold_pcs = sorted(sold_pcs.keys() - known_sale_ids)
    orphan_memberships = sorted(sold_memberships.keys() - known_sale_ids)
    if orphan_sold_pcs or orphan_memberships:
        identifiers = sorted(set(orphan_sold_pcs) | set(orphan_memberships))
        raise _fail(
            "sold-PC records reference unknown sale IDs: "
            + ", ".join(map(str, identifiers))
            + "."
        )
    return tuple(sales)


def _validate_lifecycle(data: _LegacyData) -> None:
    active_memberships = {item_id for pc in data.active_pcs for item_id in pc.item_ids}
    sold_memberships = {item_id for sale in data.sales for item_id in sale.item_ids}
    duplicate = sorted(active_memberships & sold_memberships)
    if duplicate:
        raise _fail(
            "expenses are both assembled and sold: "
            + ", ".join(map(str, duplicate))
            + "."
        )
    sold_occurrences = [item_id for sale in data.sales for item_id in sale.item_ids]
    if len(sold_occurrences) != len(set(sold_occurrences)):
        raise _fail("one or more expenses belong to multiple sales.")

    for item in data.items:
        if item.identifier in active_memberships or item.identifier in sold_memberships:
            continue
        if not item.in_inventory:
            raise _fail(
                f"expense {item.identifier} is hidden from inventory without a sale; "
                "the current model has no equivalent hidden state. Delete or restore "
                "it in the original app before importing."
            )
        if item.used_in is not None:
            raise _fail(
                f"expense {item.identifier} points to missing PC {item.used_in!r}."
            )

    pc_names: dict[str, str] = {}
    for name in [pc.name for pc in data.active_pcs] + [
        sale.name for sale in data.sales if sale.is_pc
    ]:
        folded = name.casefold()
        existing = pc_names.get(folded)
        if existing is not None:
            raise _fail(
                f"PC names {existing!r} and {name!r} collide in the current "
                "case-insensitive name model. Rename one in the original app."
            )
        pc_names[folded] = name


def _read_legacy(source: Path) -> _LegacyData:
    uri = f"{source.as_uri()}?mode=ro"
    try:
        with closing(sqlite3.connect(uri, uri=True, timeout=10)) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only=ON")
            connection.execute("PRAGMA busy_timeout=10000")
            connection.execute("BEGIN")
            check = connection.execute("PRAGMA quick_check").fetchone()[0]
            if check != "ok":
                raise _fail(f"SQLite quick check failed: {check}")
            violation = connection.execute("PRAGMA foreign_key_check").fetchone()
            if violation is not None:
                raise _fail(
                    f"foreign-key violation in {violation[0]} row {violation[1]}."
                )
            _require_legacy_schema(connection)
            items = _read_items(connection)
            items_by_id = {item.identifier: item for item in items}
            active_pcs = _read_active_pcs(connection, items_by_id)
            sales = _read_sales(connection, items_by_id)
            data = _LegacyData(items, active_pcs, sales)
            _validate_lifecycle(data)
            return data
    except LegacyImportError:
        raise
    except sqlite3.Error as error:
        raise _fail(f"SQLite could not read the source safely: {error}") from error


def _write_current(data: _LegacyData, staging: Path) -> None:
    database = Database.at(staging)
    initialize_database(database)
    with database.transaction(write=True) as connection:
        connection.executemany(
            """INSERT INTO inventory_items
               (id,name,item_type,price_cents,purchase_date)
               VALUES (?,?,?,?,?)""",
            (
                (
                    item.identifier,
                    item.name,
                    item.item_type,
                    item.price_cents,
                    item.purchase_date,
                )
                for item in data.items
            ),
        )
        connection.executemany(
            """INSERT INTO item_costs (item_id,cash_paid_cents,origin)
               VALUES (?,?,'purchase')""",
            ((item.identifier, item.price_cents) for item in data.items),
        )
        for pc in data.active_pcs:
            connection.execute(
                "INSERT INTO pcs (id,name,status) VALUES (?,?,'active')",
                (pc.identifier, pc.name),
            )
            connection.executemany(
                "INSERT INTO pc_parts (pc_id,item_id,position) VALUES (?,?,?)",
                (
                    (pc.identifier, item_id, position)
                    for position, item_id in enumerate(pc.item_ids)
                ),
            )

        next_pc_id = max((pc.identifier for pc in data.active_pcs), default=0) + 1
        for sale in data.sales:
            if sale.is_pc:
                pc_id = next_pc_id
                next_pc_id += 1
                connection.execute(
                    "INSERT INTO pcs (id,name,status) VALUES (?,?,'active')",
                    (pc_id, sale.name),
                )
                connection.executemany(
                    "INSERT INTO pc_parts (pc_id,item_id,position) VALUES (?,?,?)",
                    (
                        (pc_id, item_id, position)
                        for position, item_id in enumerate(sale.item_ids)
                    ),
                )
                connection.execute(
                    """INSERT INTO sales
                       (id,name,kind,pc_id,selling_price_cents,sale_date)
                       VALUES (?,?,'pc',?,?,?)""",
                    (
                        sale.identifier,
                        sale.name,
                        pc_id,
                        sale.selling_price_cents,
                        sale.sale_date,
                    ),
                )
            else:
                connection.execute(
                    """INSERT INTO sales
                       (id,name,kind,pc_id,selling_price_cents,sale_date)
                       VALUES (?,?,'item',NULL,?,?)""",
                    (
                        sale.identifier,
                        sale.name,
                        sale.selling_price_cents,
                        sale.sale_date,
                    ),
                )
            connection.executemany(
                "INSERT INTO sale_items (sale_id,item_id,position) VALUES (?,?,?)",
                (
                    (sale.identifier, item_id, position)
                    for position, item_id in enumerate(sale.item_ids)
                ),
            )
        validate_schema(connection)
        validate_current_data(connection)
        validate_proof_files(connection)
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise _fail(f"new database integrity check failed: {integrity}")
        violation = connection.execute("PRAGMA foreign_key_check").fetchone()
        if violation is not None:
            raise _fail(
                f"new database has a foreign-key violation in {violation[0]} "
                f"row {violation[1]}."
            )
    with closing(database.connect()) as connection:
        busy, _remaining, _checkpointed = connection.execute(
            "PRAGMA wal_checkpoint(TRUNCATE)"
        ).fetchone()
        if busy:
            raise _fail("new database could not be checkpointed for publication.")
        journal_mode = str(
            connection.execute("PRAGMA journal_mode=DELETE").fetchone()[0]
        )
        if journal_mode.casefold() != "delete":
            raise _fail("new database could not be made self-contained.")


def _cleanup_staging(staging: Path) -> None:
    for candidate in (
        staging,
        Path(f"{staging}-wal"),
        Path(f"{staging}-shm"),
    ):
        try:
            candidate.unlink(missing_ok=True)
        except OSError:
            pass


def import_legacy_database(
    source_path: str | os.PathLike[str],
    target_path: str | os.PathLike[str],
) -> LegacyImportSummary:
    """Import one final-schema Tkinter database into a brand-new current DB.

    The source is opened read-only.  The target must not exist and is published
    with an exclusive hard link only after a staged database passes current
    schema, semantic, foreign-key, and SQLite integrity validation.
    """

    source = Path(source_path).expanduser().resolve()
    target = Path(target_path).expanduser().resolve()
    if source == target:
        raise LegacyImportError("Source and target database paths must differ.")
    if not source.is_file():
        raise LegacyImportError(f"Legacy source database does not exist: {source}")
    if target.exists():
        raise LegacyImportError(
            f"Target already exists and will not be overwritten: {target}"
        )
    if not target.parent.is_dir():
        raise LegacyImportError(f"Target directory does not exist: {target.parent}")

    data = _read_legacy(source)
    descriptor, staging_name = tempfile.mkstemp(
        prefix=f".{target.name}.import-", suffix=".tmp", dir=target.parent
    )
    staging = Path(staging_name).resolve()
    try:
        os.close(descriptor)
        # Keep the securely-created private file in place. SQLite can initialize
        # an empty file directly, avoiding a delete-and-recreate race.
        _write_current(data, staging)
        if target.exists():
            raise LegacyImportError(
                f"Target appeared during import and will not be overwritten: {target}"
            )
        try:
            os.link(staging, target)
        except FileExistsError as error:
            raise LegacyImportError(
                f"Target appeared during import and will not be overwritten: {target}"
            ) from error
        except OSError as error:
            raise LegacyImportError(
                "The validated database could not be published atomically on this "
                f"filesystem: {error}"
            ) from error
        if os.name != "nt":
            target.chmod(0o600)
    except BaseException:
        if target.exists() and staging.exists():
            try:
                if os.path.samefile(staging, target):
                    target.unlink()
            except OSError:
                pass
        raise
    finally:
        _cleanup_staging(staging)

    return LegacyImportSummary(
        items=len(data.items),
        active_pcs=len(data.active_pcs),
        sold_pcs=sum(sale.is_pc for sale in data.sales),
        sales=len(data.sales),
    )


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Import the final Tkinter PCIMS database into a new current-schema "
            "database. The source is never modified and the target must not exist."
        )
    )
    parser.add_argument("source", type=Path, help="legacy pcims_db.db path")
    parser.add_argument("target", type=Path, help="new current database path")
    parsed = parser.parse_args(arguments)
    try:
        summary = import_legacy_database(parsed.source, parsed.target)
    except LegacyImportError as error:
        parser.exit(2, f"error: {error}\n")
    print(
        f"Imported {summary.items} items, {summary.active_pcs} active PCs, "
        f"{summary.sold_pcs} sold PCs, and {summary.sales} sales into "
        f"{parsed.target.resolve()} (schema {SCHEMA_VERSION})."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
