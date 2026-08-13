import unittest
from datetime import date, datetime

from app.calendar import parse_date_input


class DateInputTests(unittest.TestCase):
    def test_accepts_iso_text_and_date_objects(self):
        expected = date(2026, 8, 12)
        self.assertEqual(parse_date_input("2026-08-12"), expected)
        self.assertEqual(parse_date_input(expected), expected)
        self.assertEqual(parse_date_input(datetime(2026, 8, 12, 12, 30)), expected)

    def test_rejects_ambiguous_or_impossible_dates(self):
        for value in ("12-08-2026", "2026-02-30", ""):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "YYYY-MM-DD"):
                parse_date_input(value)


if __name__ == "__main__":
    unittest.main()
