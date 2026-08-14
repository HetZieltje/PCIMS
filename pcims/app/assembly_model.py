"""Typed hierarchical Qt model for components available to assemble."""

from dataclasses import dataclass

from PySide6.QtCore import QAbstractItemModel, QModelIndex, QPersistentModelIndex, Qt

from pcims.app.formatting import format_cents
from pcims.app.table_model import ID_ROLE
from pcims.db.models import Expense
from pcims.domain import ITEM_TYPES, ItemType

_ROOT_INDEX = QModelIndex()


@dataclass(frozen=True, slots=True)
class ComponentGroup:
    item_type: ItemType
    expenses: tuple[Expense, ...]


class AssemblyTreeModel(QAbstractItemModel):
    """Read-only groups with checkable, identity-bearing expense records."""

    HEADERS = ("Component", "Cost", "Purchased")

    def __init__(self) -> None:
        super().__init__()
        self._groups: tuple[ComponentGroup, ...] = ()
        self._group_row_by_expense_id: dict[int, int] = {}
        self._checked_ids: set[int] = set()

    @property
    def checked_ids(self) -> tuple[int, ...]:
        return tuple(
            expense.id
            for group in self._groups
            for expense in group.expenses
            if expense.id in self._checked_ids
        )

    def set_records(self, expenses: tuple[Expense, ...]) -> None:
        grouped: dict[ItemType, list[Expense]] = {
            item_type: [] for item_type in ITEM_TYPES
        }
        for expense in expenses:
            grouped[expense.item_type].append(expense)
        groups = tuple(
            ComponentGroup(item_type, tuple(grouped[item_type]))
            for item_type in ITEM_TYPES
            if grouped[item_type]
        )
        available_ids = {expense.id for group in groups for expense in group.expenses}
        self.beginResetModel()
        self._groups = groups
        self._checked_ids.intersection_update(available_ids)
        self._group_row_by_expense_id = {
            expense.id: group_row
            for group_row, group in enumerate(groups)
            for expense in group.expenses
        }
        self.endResetModel()

    def index(
        self,
        row: int,
        column: int,
        parent: QModelIndex | QPersistentModelIndex = _ROOT_INDEX,
    ) -> QModelIndex:
        if row < 0 or not 0 <= column < len(self.HEADERS):
            return QModelIndex()
        if not parent.isValid():
            if row >= len(self._groups):
                return QModelIndex()
            return self.createIndex(row, column, self._groups[row])
        parent_record = parent.internalPointer()
        if not isinstance(parent_record, ComponentGroup):
            return QModelIndex()
        if row >= len(parent_record.expenses):
            return QModelIndex()
        return self.createIndex(row, column, parent_record.expenses[row])

    def parent(  # type: ignore[override]
        self, index: QModelIndex | QPersistentModelIndex
    ) -> QModelIndex:
        if not index.isValid():
            return QModelIndex()
        record = index.internalPointer()
        if not isinstance(record, Expense):
            return QModelIndex()
        group_row = self._group_row_by_expense_id.get(record.id)
        if group_row is None:
            return QModelIndex()
        return self.createIndex(group_row, 0, self._groups[group_row])

    def rowCount(
        self, parent: QModelIndex | QPersistentModelIndex = _ROOT_INDEX
    ) -> int:
        if parent.column() > 0:
            return 0
        if not parent.isValid():
            return len(self._groups)
        record = parent.internalPointer()
        return len(record.expenses) if isinstance(record, ComponentGroup) else 0

    def columnCount(
        self, parent: QModelIndex | QPersistentModelIndex = _ROOT_INDEX
    ) -> int:
        return len(self.HEADERS)

    def data(
        self,
        index: QModelIndex | QPersistentModelIndex,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> object | None:
        if not index.isValid():
            return None
        record = index.internalPointer()
        if isinstance(record, ComponentGroup):
            if role == Qt.ItemDataRole.DisplayRole and index.column() == 0:
                return f"{record.item_type} ({len(record.expenses)})"
            return None
        if not isinstance(record, Expense):
            return None
        if role == Qt.ItemDataRole.DisplayRole:
            return (
                record.name,
                format_cents(record.price_cents),
                record.purchase_date.isoformat(),
            )[index.column()]
        if role == ID_ROLE:
            return record.id
        if role == Qt.ItemDataRole.CheckStateRole and index.column() == 0:
            return (
                Qt.CheckState.Checked
                if record.id in self._checked_ids
                else Qt.CheckState.Unchecked
            )
        return None

    def setData(
        self,
        index: QModelIndex | QPersistentModelIndex,
        value: object,
        role: int = Qt.ItemDataRole.EditRole,
    ) -> bool:
        record = index.internalPointer() if index.isValid() else None
        if (
            not isinstance(record, Expense)
            or index.column() != 0
            or role != Qt.ItemDataRole.CheckStateRole
        ):
            return False
        if value in (Qt.CheckState.Checked, Qt.CheckState.Checked.value):
            self._checked_ids.add(record.id)
        else:
            self._checked_ids.discard(record.id)
        self.dataChanged.emit(index, index, [Qt.ItemDataRole.CheckStateRole])
        return True

    def flags(self, index: QModelIndex | QPersistentModelIndex) -> Qt.ItemFlag:
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        if isinstance(index.internalPointer(), ComponentGroup):
            return Qt.ItemFlag.ItemIsEnabled
        flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if index.column() == 0:
            flags |= Qt.ItemFlag.ItemIsUserCheckable
        return flags

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> object | None:
        if (
            orientation == Qt.Orientation.Horizontal
            and role == Qt.ItemDataRole.DisplayRole
            and 0 <= section < len(self.HEADERS)
        ):
            return self.HEADERS[section]
        return None
