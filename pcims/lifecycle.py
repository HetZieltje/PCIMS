"""Canonical lifecycle rules for inventory records.

Persistence constraints remain the final corruption barrier, while this module is the
single application-level vocabulary for deciding where an item is and whether a user
operation may move it somewhere else.
"""

from dataclasses import dataclass
from enum import StrEnum


class InventoryState(StrEnum):
    AVAILABLE = "available"
    PC_COMPONENT = "assembled PC"
    LAPTOP_BASE = "laptop"
    LAPTOP_COMPONENT = "installed in laptop"
    SOLD = "sold"


class LifecycleEvent(StrEnum):
    ASSEMBLE = "assemble"
    DISASSEMBLE = "disassemble"
    INSTALL_IN_LAPTOP = "install in laptop"
    REMOVE_FROM_LAPTOP = "remove from laptop"
    SELL_ITEM = "sell item"
    SELL_PC = "sell PC"
    SELL_LAPTOP = "sell laptop"
    UNDO_SALE = "undo sale"


@dataclass(frozen=True, slots=True)
class ItemPlacement:
    """The complete set of mutually exclusive item memberships."""

    pc_id: int | None = None
    laptop_id: int | None = None
    is_laptop: bool = False
    sale_id: int | None = None

    def __post_init__(self) -> None:
        homes = sum(
            (
                self.pc_id is not None,
                self.laptop_id is not None,
                self.is_laptop,
            )
        )
        if homes > 1:
            raise ValueError("An inventory item has more than one physical placement.")

    @property
    def home_state(self) -> InventoryState:
        if self.is_laptop:
            return InventoryState.LAPTOP_BASE
        if self.laptop_id is not None:
            return InventoryState.LAPTOP_COMPONENT
        if self.pc_id is not None:
            return InventoryState.PC_COMPONENT
        return InventoryState.AVAILABLE

    @property
    def state(self) -> InventoryState:
        return InventoryState.SOLD if self.sale_id is not None else self.home_state


_ALLOWED_TRANSITIONS: dict[
    LifecycleEvent, frozenset[tuple[InventoryState, InventoryState]]
] = {
    LifecycleEvent.ASSEMBLE: frozenset(
        {(InventoryState.AVAILABLE, InventoryState.PC_COMPONENT)}
    ),
    LifecycleEvent.DISASSEMBLE: frozenset(
        {(InventoryState.PC_COMPONENT, InventoryState.AVAILABLE)}
    ),
    LifecycleEvent.INSTALL_IN_LAPTOP: frozenset(
        {(InventoryState.AVAILABLE, InventoryState.LAPTOP_COMPONENT)}
    ),
    LifecycleEvent.REMOVE_FROM_LAPTOP: frozenset(
        {(InventoryState.LAPTOP_COMPONENT, InventoryState.AVAILABLE)}
    ),
    LifecycleEvent.SELL_ITEM: frozenset(
        {(InventoryState.AVAILABLE, InventoryState.SOLD)}
    ),
    LifecycleEvent.SELL_PC: frozenset(
        {(InventoryState.PC_COMPONENT, InventoryState.SOLD)}
    ),
    LifecycleEvent.SELL_LAPTOP: frozenset(
        {
            (InventoryState.LAPTOP_BASE, InventoryState.SOLD),
            (InventoryState.LAPTOP_COMPONENT, InventoryState.SOLD),
        }
    ),
    LifecycleEvent.UNDO_SALE: frozenset(
        {
            (InventoryState.SOLD, InventoryState.AVAILABLE),
            (InventoryState.SOLD, InventoryState.PC_COMPONENT),
            (InventoryState.SOLD, InventoryState.LAPTOP_BASE),
            (InventoryState.SOLD, InventoryState.LAPTOP_COMPONENT),
        }
    ),
}


def require_transition(
    event: LifecycleEvent,
    source: InventoryState,
    target: InventoryState,
    *,
    item_name: str = "Item",
) -> None:
    """Reject a lifecycle transition that the application does not define."""

    if (source, target) not in _ALLOWED_TRANSITIONS[event]:
        raise ValueError(
            f"{item_name!r} cannot {event.value}: it is currently {source.value}."
        )


def can_transition(
    event: LifecycleEvent, source: InventoryState, target: InventoryState
) -> bool:
    return (source, target) in _ALLOWED_TRANSITIONS[event]
