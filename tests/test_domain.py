import unittest
from dataclasses import FrozenInstanceError
from datetime import UTC, date, datetime

from pcims.domain import NewExpense, SaleTerms


class DomainValueTests(unittest.TestCase):
    def test_new_expense_normalizes_external_values_once(self):
        expense = NewExpense.create("  CPU  ", "cpu", "1,01", "2026-08-14")

        self.assertEqual(
            expense,
            NewExpense("CPU", "CPU", 101, date(2026, 8, 14)),
        )
        with self.assertRaises(FrozenInstanceError):
            expense.name = "Changed"  # type: ignore[misc]

    def test_invalid_domain_values_cannot_be_constructed(self):
        invalid_factories = (
            lambda: NewExpense.create("", "CPU", 1),
            lambda: NewExpense.create("CPU", "Unknown", 1),
            lambda: NewExpense.create("CPU\nrenamed", "CPU", 1),
            lambda: NewExpense.create("x" * 201, "CPU", 1),
            lambda: NewExpense("CPU", "CPU", True, date(2026, 8, 14)),
            lambda: SaleTerms(-1, date(2026, 8, 14)),
        )
        for factory in invalid_factories:
            with self.subTest(factory=factory), self.assertRaises(ValueError):
                factory()
        for factory in (
            lambda: NewExpense(
                "CPU", "CPU", 100, datetime(2026, 8, 14, 12, tzinfo=UTC)
            ),
            lambda: SaleTerms(100, datetime(2026, 8, 14, 12, tzinfo=UTC)),
        ):
            with self.subTest(factory=factory), self.assertRaises(TypeError):
                factory()

    def test_sale_terms_hold_exact_cents_and_date(self):
        terms = SaleTerms.create("1.234,56", "2026-08-14")

        self.assertEqual(terms.selling_price_cents, 123_456)
        self.assertEqual(terms.sale_date, date(2026, 8, 14))


if __name__ == "__main__":
    unittest.main()
