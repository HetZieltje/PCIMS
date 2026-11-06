import hashlib
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from pcims.db.backup import validate_database
from pcims.db.connection import Database
from pcims.db.schema import SCHEMA_VERSION, validate_current_data, validate_schema
from pcims.legacy_import import LegacyImportError, import_legacy_database

LEGACY_SCHEMA = """
CREATE TABLE expenses (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    type TEXT NOT NULL,
    price REAL NOT NULL,
    price_cents INTEGER,
    purchase_date TEXT NOT NULL,
    in_inventory INTEGER NOT NULL,
    used_in TEXT
);
CREATE TABLE assembled_pcs (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE);
CREATE TABLE assembled_pc_parts (
    pc_id INTEGER NOT NULL REFERENCES assembled_pcs(id),
    expense_id INTEGER NOT NULL UNIQUE REFERENCES expenses(id),
    component_type TEXT NOT NULL,
    position INTEGER NOT NULL,
    PRIMARY KEY (pc_id,expense_id)
);
CREATE TABLE income (
    id INTEGER PRIMARY KEY,
    old_id INTEGER,
    name TEXT NOT NULL,
    cost REAL NOT NULL,
    selling_price REAL NOT NULL,
    profit REAL NOT NULL,
    cost_cents INTEGER,
    selling_price_cents INTEGER,
    profit_cents INTEGER,
    sale_date TEXT NOT NULL,
    is_pc INTEGER NOT NULL
);
CREATE TABLE sold_pcs (
    id INTEGER PRIMARY KEY REFERENCES income(id),
    name TEXT NOT NULL,
    cpu INTEGER REFERENCES expenses(id),
    cooler INTEGER REFERENCES expenses(id),
    gpu INTEGER REFERENCES expenses(id),
    motherboard INTEGER REFERENCES expenses(id),
    ram INTEGER REFERENCES expenses(id),
    ssd INTEGER REFERENCES expenses(id),
    hdd INTEGER REFERENCES expenses(id),
    pc_case INTEGER REFERENCES expenses(id),
    psu INTEGER REFERENCES expenses(id),
    fan INTEGER REFERENCES expenses(id),
    extra INTEGER REFERENCES expenses(id)
);
CREATE TABLE sold_pc_parts (
    sale_id INTEGER NOT NULL REFERENCES income(id),
    expense_id INTEGER NOT NULL REFERENCES expenses(id),
    component_type TEXT NOT NULL,
    position INTEGER NOT NULL,
    PRIMARY KEY (sale_id,expense_id)
);
PRAGMA user_version=2;
"""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _create_representative_legacy(path: Path) -> None:
    with closing(sqlite3.connect(path)) as database:
        database.executescript(LEGACY_SCHEMA)
        database.executemany(
            """INSERT INTO expenses
               (id,name,type,price,price_cents,purchase_date,in_inventory,used_in)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                (1, "Available cooler", "Cooler", 25.0, 2500, "2026-01-01", 1, None),
                (2, "CPU", "CPU", 100.0, 10000, "2026-01-02", 1, "Active PC"),
                (3, "RAM A", "RAM", 20.0, 2000, "2026-01-02", 1, "Active PC"),
                (4, "Sold GPU", "GPU", 50.0, 5000, "2026-01-03", 0, None),
                (5, "RAM B", "RAM", 15.0, 1500, "2026-01-04", 0, "Sold PC"),
                (6, "RAM C", "RAM", 15.0, 1500, "2026-01-04", 0, "Sold PC"),
            ),
        )
        database.execute("INSERT INTO assembled_pcs VALUES (5,'Active PC')")
        database.executemany(
            "INSERT INTO assembled_pc_parts VALUES (5,?,?,?)",
            ((2, "CPU", 0), (3, "RAM", 1)),
        )
        database.executemany(
            """INSERT INTO income
               (id,old_id,name,cost,selling_price,profit,cost_cents,
                selling_price_cents,profit_cents,sale_date,is_pc)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                (1, 4, "Sold GPU", 50.0, 70.0, 20.0, 5000, 7000, 2000, "2026-02-01", 0),
                (
                    2,
                    None,
                    "Sold PC",
                    30.0,
                    45.0,
                    15.0,
                    3000,
                    4500,
                    1500,
                    "2026-02-02",
                    1,
                ),
            ),
        )
        database.execute("INSERT INTO sold_pcs (id,name,ram) VALUES (2,'Sold PC',5)")
        database.executemany(
            "INSERT INTO sold_pc_parts VALUES (2,?,?,?)",
            ((5, "RAM", 0), (6, "RAM", 1)),
        )
        database.commit()


class LegacyImportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.source = self.root / "pcims_db.db"
        self.target = self.root / "pcims.db"

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_imports_items_multiple_pc_parts_and_sales_without_touching_source(
        self,
    ) -> None:
        _create_representative_legacy(self.source)
        source_hash = _sha256(self.source)

        summary = import_legacy_database(self.source, self.target)

        self.assertEqual(_sha256(self.source), source_hash)
        self.assertEqual(summary.items, 6)
        self.assertEqual(summary.active_pcs, 1)
        self.assertEqual(summary.sold_pcs, 1)
        self.assertEqual(summary.sales, 2)
        validate_database(self.target)
        self.assertFalse(Path(f"{self.target}-wal").exists())
        self.assertFalse(Path(f"{self.target}-shm").exists())
        with Database.at(self.target).transaction() as database:
            self.assertEqual(
                database.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION
            )
            validate_schema(database)
            validate_current_data(database)
            self.assertEqual(
                [
                    tuple(row)
                    for row in database.execute(
                        "SELECT id FROM inventory_items ORDER BY id"
                    )
                ],
                [(1,), (2,), (3,), (4,), (5,), (6,)],
            )
            self.assertEqual(
                [
                    tuple(row)
                    for row in database.execute(
                        """SELECT s.id,si.item_id FROM sales s
                           JOIN sale_items si ON si.sale_id=s.id
                           ORDER BY s.id,si.position"""
                    )
                ],
                [(1, 4), (2, 5), (2, 6)],
            )
            self.assertEqual(
                [
                    tuple(row)
                    for row in database.execute(
                        """SELECT p.name,pp.item_id FROM pcs p
                           JOIN pc_parts pp ON pp.pc_id=p.id
                           ORDER BY p.id,pp.position"""
                    )
                ],
                [("Active PC", 2), ("Active PC", 3), ("Sold PC", 5), ("Sold PC", 6)],
            )

    def test_rejects_unrepresentable_hidden_item_without_publishing_target(
        self,
    ) -> None:
        _create_representative_legacy(self.source)
        with closing(sqlite3.connect(self.source)) as database:
            database.execute("UPDATE expenses SET in_inventory=0 WHERE id=1")
            database.commit()

        with self.assertRaisesRegex(LegacyImportError, "hidden from inventory"):
            import_legacy_database(self.source, self.target)

        self.assertFalse(self.target.exists())
        self.assertEqual(tuple(self.root.glob(".pcims.db.import-*.tmp*")), ())

    def test_imports_historical_sold_pc_from_legacy_slots_and_assignments(self) -> None:
        _create_representative_legacy(self.source)
        with closing(sqlite3.connect(self.source)) as database:
            database.execute("DELETE FROM sold_pc_parts WHERE sale_id=2")
            database.commit()

        summary = import_legacy_database(self.source, self.target)

        self.assertEqual(summary.sold_pcs, 1)
        with Database.at(self.target).transaction() as database:
            self.assertEqual(
                [
                    tuple(row)
                    for row in database.execute(
                        """SELECT si.item_id FROM sale_items si
                           WHERE si.sale_id=2 ORDER BY si.position"""
                    )
                ],
                [(5,), (6,)],
            )
            self.assertEqual(
                [
                    tuple(row)
                    for row in database.execute(
                        """SELECT pp.item_id FROM pc_parts pp
                           JOIN sales s ON s.pc_id=pp.pc_id
                           WHERE s.id=2 ORDER BY pp.position"""
                    )
                ],
                [(5,), (6,)],
            )

    def test_rejects_legacy_slot_that_conflicts_with_normalized_membership(
        self,
    ) -> None:
        _create_representative_legacy(self.source)
        with closing(sqlite3.connect(self.source)) as database:
            database.execute("UPDATE sold_pcs SET ram=4 WHERE id=2")
            database.commit()

        with self.assertRaisesRegex(
            LegacyImportError, "records expense 4 as RAM, but the expense type is GPU"
        ):
            import_legacy_database(self.source, self.target)

        self.assertFalse(self.target.exists())

    def test_rejects_inconsistent_sale_atomically(self) -> None:
        _create_representative_legacy(self.source)
        with closing(sqlite3.connect(self.source)) as database:
            database.execute("UPDATE income SET cost_cents=1 WHERE id=2")
            database.commit()

        with self.assertRaisesRegex(LegacyImportError, "items total 3000 cents"):
            import_legacy_database(self.source, self.target)

        self.assertFalse(self.target.exists())

    def test_never_overwrites_an_existing_target(self) -> None:
        _create_representative_legacy(self.source)
        self.target.write_bytes(b"keep me")

        with self.assertRaisesRegex(LegacyImportError, "will not be overwritten"):
            import_legacy_database(self.source, self.target)

        self.assertEqual(self.target.read_bytes(), b"keep me")

    def test_rejects_a_schema_other_than_the_final_tkinter_baseline(self) -> None:
        _create_representative_legacy(self.source)
        with closing(sqlite3.connect(self.source)) as database:
            database.execute("PRAGMA user_version=1")

        with self.assertRaisesRegex(LegacyImportError, "user_version 2"):
            import_legacy_database(self.source, self.target)

        self.assertFalse(self.target.exists())


if __name__ == "__main__":
    unittest.main()
