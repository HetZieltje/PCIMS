import unittest
from decimal import Decimal

from app.money import allocate_total


class MoneyAllocationTests(unittest.TestCase):
    def test_equal_split_preserves_every_cent(self):
        amounts = allocate_total("10.00", [1, 1, 1])
        self.assertEqual(amounts, [Decimal("3.34"), Decimal("3.33"), Decimal("3.33")])
        self.assertEqual(sum(amounts), Decimal("10.00"))

    def test_weighted_bundle_preserves_total(self):
        amounts = allocate_total("10.00", [Decimal("33.33"), Decimal("33.33"), Decimal("33.34")])
        self.assertEqual(sum(amounts), Decimal("10.00"))
        self.assertEqual(amounts, [Decimal("3.33"), Decimal("3.33"), Decimal("3.34")])

    def test_invalid_weights_are_rejected(self):
        for weights in ([], [0, 0], [1, -1]):
            with self.subTest(weights=weights), self.assertRaises(ValueError):
                allocate_total("10", weights)


if __name__ == "__main__":
    unittest.main()
