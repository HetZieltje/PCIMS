import unittest

from pcims.app.formatting import (
    allocate_cents,
    format_cents,
    parse_money_cents,
)


class FormattingTests(unittest.TestCase):
    def test_money_input_is_exact_and_locale_friendly(self):
        self.assertEqual(parse_money_cents(" € 12,34 "), 1234)
        self.assertEqual(parse_money_cents("1.01"), 101)
        self.assertEqual(parse_money_cents("€1,234.56"), 123456)
        self.assertEqual(parse_money_cents("1.234,56"), 123456)
        self.assertEqual(parse_money_cents("1 234,56"), 123456)

    def test_invalid_money_is_rejected(self):
        for value in (
            "",
            "NaN",
            "Infinity",
            "-1",
            "1.2345",
            "12,34.56",
            "1000000000",
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_money_cents(value)

    def test_quantity_allocation_preserves_every_cent(self):
        self.assertEqual(allocate_cents(1000, 3), [334, 333, 333])
        self.assertEqual(sum(allocate_cents(1000, 3)), 1000)

    def test_format_cents(self):
        self.assertEqual(format_cents(123456), "€1,234.56")
        self.assertEqual(format_cents(-50), "-€0.50")
        self.assertEqual(format_cents(9_007_199_254_740_993), "€90,071,992,547,409.93")

    def test_every_supported_display_value_round_trips_exactly(self):
        for cents in (0, 1, 99, 100, 123456, 99_999_999_999):
            with self.subTest(cents=cents):
                self.assertEqual(parse_money_cents(format_cents(cents)), cents)


if __name__ == "__main__":
    unittest.main()
