import unittest

from app.formatting import (
    allocate_cents,
    allocate_weighted_cents,
    format_cents,
    parse_money_cents,
)


class FormattingTests(unittest.TestCase):
    def test_money_input_is_exact_and_locale_friendly(self):
        self.assertEqual(parse_money_cents(" € 12,34 "), 1234)
        self.assertEqual(parse_money_cents("1.01"), 101)

    def test_invalid_money_is_rejected(self):
        for value in ("", "NaN", "Infinity", "-1", "1.234"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_money_cents(value)

    def test_quantity_allocation_preserves_every_cent(self):
        self.assertEqual(allocate_cents(1000, 3), [334, 333, 333])
        self.assertEqual(sum(allocate_cents(1000, 3)), 1000)

    def test_weighted_allocation_preserves_every_cent(self):
        self.assertEqual(allocate_weighted_cents(1000, [33.33, 33.33, 33.34]), [333, 333, 334])

    def test_format_cents(self):
        self.assertEqual(format_cents(123456), "€1,234.56")
        self.assertEqual(format_cents(-50), "€-0.50")


if __name__ == "__main__":
    unittest.main()
