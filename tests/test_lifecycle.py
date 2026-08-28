import unittest

from pcims.lifecycle import (
    InventoryState,
    ItemPlacement,
    LifecycleEvent,
    can_transition,
    require_transition,
)


class InventoryLifecycleTests(unittest.TestCase):
    def test_each_valid_physical_placement_has_one_state(self):
        cases = (
            (ItemPlacement(), InventoryState.AVAILABLE),
            (ItemPlacement(pc_id=1), InventoryState.PC_COMPONENT),
            (ItemPlacement(laptop_id=1), InventoryState.LAPTOP_COMPONENT),
            (ItemPlacement(is_laptop=True), InventoryState.LAPTOP_BASE),
            (ItemPlacement(sale_id=1), InventoryState.SOLD),
            (ItemPlacement(pc_id=1, sale_id=1), InventoryState.SOLD),
            (ItemPlacement(laptop_id=1, sale_id=1), InventoryState.SOLD),
            (ItemPlacement(is_laptop=True, sale_id=1), InventoryState.SOLD),
        )
        for placement, expected in cases:
            with self.subTest(placement=placement):
                self.assertEqual(placement.state, expected)

    def test_conflicting_physical_placements_are_rejected(self):
        for placement in (
            {"pc_id": 1, "laptop_id": 2},
            {"pc_id": 1, "is_laptop": True},
            {"laptop_id": 1, "is_laptop": True},
        ):
            with (
                self.subTest(placement=placement),
                self.assertRaisesRegex(ValueError, "more than one"),
            ):
                ItemPlacement(**placement)

    def test_transition_matrix_allows_only_defined_workflows(self):
        states = tuple(InventoryState)
        expected = {
            LifecycleEvent.ASSEMBLE: {
                (InventoryState.AVAILABLE, InventoryState.PC_COMPONENT)
            },
            LifecycleEvent.DISASSEMBLE: {
                (InventoryState.PC_COMPONENT, InventoryState.AVAILABLE)
            },
            LifecycleEvent.INSTALL_IN_LAPTOP: {
                (InventoryState.AVAILABLE, InventoryState.LAPTOP_COMPONENT)
            },
            LifecycleEvent.REMOVE_FROM_LAPTOP: {
                (InventoryState.LAPTOP_COMPONENT, InventoryState.AVAILABLE)
            },
            LifecycleEvent.SELL_ITEM: {
                (InventoryState.AVAILABLE, InventoryState.SOLD),
            },
            LifecycleEvent.SELL_PC: {
                (InventoryState.PC_COMPONENT, InventoryState.SOLD),
            },
            LifecycleEvent.SELL_LAPTOP: {
                (InventoryState.LAPTOP_BASE, InventoryState.SOLD),
                (InventoryState.LAPTOP_COMPONENT, InventoryState.SOLD),
            },
            LifecycleEvent.UNDO_SALE: {
                (InventoryState.SOLD, InventoryState.AVAILABLE),
                (InventoryState.SOLD, InventoryState.PC_COMPONENT),
                (InventoryState.SOLD, InventoryState.LAPTOP_BASE),
                (InventoryState.SOLD, InventoryState.LAPTOP_COMPONENT),
            },
        }
        for event in LifecycleEvent:
            for source in states:
                for target in states:
                    with self.subTest(event=event, source=source, target=target):
                        self.assertEqual(
                            can_transition(event, source, target),
                            (source, target) in expected[event],
                        )

    def test_invalid_transition_has_a_user_facing_reason(self):
        with self.assertRaisesRegex(ValueError, "currently sold"):
            require_transition(
                LifecycleEvent.ASSEMBLE,
                InventoryState.SOLD,
                InventoryState.PC_COMPONENT,
                item_name="GPU",
            )


if __name__ == "__main__":
    unittest.main()
