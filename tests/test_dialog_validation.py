import sys
import types
import unittest


# The parser is GUI-independent; stub the optional calendar dependency so this
# test remains runnable in minimal CI environments.
tkcalendar = types.ModuleType("tkcalendar")
tkcalendar.DateEntry = object
sys.modules.setdefault("tkcalendar", tkcalendar)

from app.dialogs import parse_money_input


class MoneyInputTests(unittest.TestCase):
    def test_accepts_decimal_comma_and_whitespace(self):
        self.assertEqual(parse_money_input(" 12,34 "), 12.34)

    def test_rejects_fractional_cents(self):
        with self.assertRaisesRegex(ValueError, "up to 2 decimal"):
            parse_money_input("1.234")

    def test_rejects_non_finite_and_out_of_range_values(self):
        for value in ("NaN", "Infinity", "-0.01", "100000"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_money_input(value)


if __name__ == "__main__":
    unittest.main()
