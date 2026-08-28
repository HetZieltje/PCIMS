import os
import random
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from pcims.db.connection import Database
from pcims.db.schema import validate_current_data
from pcims.domain import NewExpense, SaleTerms
from pcims.drafts import DraftPurchase, PurchaseDraftStore
from pcims.proofs import NewProof
from pcims.services import ApplicationServices

TEST_DATE = date(2026, 1, 1)


class ResilienceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.database = Database.at(self.root / "resilience.db")
        self.services = ApplicationServices(self.database)
        self.services.initialize()

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def assert_invariants(self) -> None:
        with self.database.transaction() as connection:
            validate_current_data(connection)

    def test_generated_pc_and_sale_sequences_preserve_all_invariants(self):
        generator = random.Random(0xC1A5)
        item_types = ("CPU", "GPU", "RAM", "SSD", "PSU")
        for cycle in range(20):
            purchases = tuple(
                NewExpense.create(
                    f"Generated {cycle}-{position}",
                    generator.choice(item_types),
                    generator.randint(1, 2_000),
                    TEST_DATE,
                )
                for position in range(3)
            )
            first, second, spare = self.services.add_expenses(purchases)
            self.assert_invariants()
            pc_id = self.services.assemble_pc(f"Generated PC {cycle}", (first, second))
            self.assert_invariants()
            self.services.update_pc(
                pc_id, f"Generated PC {cycle} revised", (first, spare)
            )
            self.assert_invariants()
            sale_id = self.services.sell_pc(
                pc_id, SaleTerms.create(generator.randint(1, 4_000), TEST_DATE)
            )
            self.assert_invariants()
            self.services.undo_sale(sale_id)
            self.services.disassemble_pc(pc_id)
            self.assert_invariants()
            item_sale = self.services.sell_items(
                (first, second, spare),
                SaleTerms.create(generator.randint(1, 4_000), TEST_DATE),
            )
            self.assert_invariants()
            self.services.undo_sale(item_sale)
            self.assert_invariants()

    def test_interrupted_database_write_rolls_back_without_partial_record(self):
        with (
            self.assertRaisesRegex(RuntimeError, "simulated interruption"),
            self.database.transaction(write=True) as connection,
        ):
            connection.execute(
                """INSERT INTO inventory_items
                   (name,item_type,price_cents,purchase_date)
                   VALUES ('Interrupted','GPU',100,'2026-01-01')"""
            )
            raise RuntimeError("simulated interruption")
        with self.database.transaction() as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM inventory_items WHERE name='Interrupted'"
            ).fetchone()[0]
        self.assertEqual(count, 0)
        self.assert_invariants()

    def test_interrupted_draft_publish_preserves_previous_complete_draft(self):
        store = PurchaseDraftStore(self.database.path, self.root / "draft-root")
        original = DraftPurchase(
            1,
            NewExpense.create("Original draft", "RAM", 25, TEST_DATE),
            (NewProof("receipt.pdf", "application/pdf", b"%PDF-1.4\n%%EOF"),),
        )
        replacement = DraftPurchase(
            2, NewExpense.create("Replacement draft", "SSD", 40, TEST_DATE)
        )
        store.save((original,))
        with (
            patch("pcims.drafts.os.replace", side_effect=OSError("power lost")),
            self.assertRaisesRegex(OSError, "power lost"),
        ):
            store.save((replacement,))
        self.assertEqual(store.load(), (original,))
        self.assertFalse(store.path.with_suffix(".tmp").exists())

    def test_full_diagnostics_validate_proofs_and_report_startup_storage(self):
        self.services.add_expenses(
            (NewExpense.create("Proof item", "GPU", 10, TEST_DATE),),
            ((NewProof("proof.pdf", "application/pdf", b"%PDF-1.4\n%%EOF"),),),
        )
        snapshot = self.services.diagnostics_snapshot(thorough=True)
        self.assertEqual(
            {check.status for check in snapshot.checks}.difference(
                {"Passed", "Warning"}
            ),
            set(),
        )
        self.assertIn("Proof contents", {check.name for check in snapshot.checks})
        self.assertEqual(snapshot.storage.proof_count, 1)

    @unittest.skipIf(os.name == "nt", "directory fsync is POSIX-specific")
    def test_draft_directory_permissions_are_private(self):
        store = PurchaseDraftStore(self.database.path, self.root / "private-draft")
        store.save(
            (DraftPurchase(1, NewExpense.create("Private", "RAM", 1, TEST_DATE)),)
        )
        self.assertEqual(store.path.parent.stat().st_mode & 0o777, 0o700)
        self.assertEqual(store.path.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
