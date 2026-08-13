"""Typed Qt model/view helpers for flat record tables."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Generic, TypeAlias, TypeVar

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QPersistentModelIndex, Qt
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QTableView

T = TypeVar("T")
SortKey: TypeAlias = int | str
ID_ROLE = Qt.ItemDataRole.UserRole
_ROOT_INDEX = QModelIndex()


@dataclass(frozen=True, slots=True)
class Column(Generic[T]):
    """Presentation and sorting rules for one domain-record field."""

    title: str
    display: Callable[[T], str]
    sort_key: Callable[[T], SortKey]


class RecordTableModel(QAbstractTableModel, Generic[T]):
    """A read-only table model that retains typed records instead of widget cells."""

    def __init__(
        self,
        columns: Sequence[Column[T]],
        record_id: Callable[[T], int],
    ) -> None:
        super().__init__()
        self._columns = tuple(columns)
        self._record_id = record_id
        self._records: list[T] = []
        self._sort_column: int | None = None
        self._sort_order = Qt.SortOrder.AscendingOrder

    @property
    def records(self) -> tuple[T, ...]:
        return tuple(self._records)

    def set_records(self, records: Sequence[T]) -> None:
        self.beginResetModel()
        self._records = list(records)
        if self._sort_column is not None:
            self._sort_records()
        self.endResetModel()

    def record_at(self, row: int) -> T | None:
        if 0 <= row < len(self._records):
            return self._records[row]
        return None

    def rowCount(
        self, parent: QModelIndex | QPersistentModelIndex = _ROOT_INDEX
    ) -> int:
        return 0 if parent.isValid() else len(self._records)

    def columnCount(
        self, parent: QModelIndex | QPersistentModelIndex = _ROOT_INDEX
    ) -> int:
        return 0 if parent.isValid() else len(self._columns)

    def data(
        self,
        index: QModelIndex | QPersistentModelIndex,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> object | None:
        if not index.isValid() or not 0 <= index.row() < len(self._records):
            return None
        if not 0 <= index.column() < len(self._columns):
            return None
        record = self._records[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return self._columns[index.column()].display(record)
        if role == ID_ROLE:
            return self._record_id(record)
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        return None

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> object | None:
        if (
            role == Qt.ItemDataRole.DisplayRole
            and orientation == Qt.Orientation.Horizontal
            and 0 <= section < len(self._columns)
        ):
            return self._columns[section].title
        return None

    def sort(
        self,
        column: int,
        order: Qt.SortOrder = Qt.SortOrder.AscendingOrder,
    ) -> None:
        if not 0 <= column < len(self._columns):
            return
        self._sort_column = column
        self._sort_order = order
        old_indexes = self.persistentIndexList()
        old_locations = [
            (self._record_id(self._records[index.row()]), index.column())
            for index in old_indexes
        ]
        self.layoutAboutToBeChanged.emit()
        self._sort_records()
        row_by_id = {
            self._record_id(record): row for row, record in enumerate(self._records)
        }
        self.changePersistentIndexList(
            old_indexes,
            [self.index(row_by_id[record_id], column) for record_id, column in old_locations],
        )
        self.layoutChanged.emit()

    def _sort_records(self) -> None:
        if self._sort_column is None:
            return
        self._records.sort(
            key=self._columns[self._sort_column].sort_key,
            reverse=self._sort_order == Qt.SortOrder.DescendingOrder,
        )


def configure_table_view(
    table: QTableView, model: QAbstractTableModel, stretch_column: int = 1
) -> None:
    """Apply the application's standard read-only table behavior."""

    table.setModel(model)
    table.setAlternatingRowColors(True)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.verticalHeader().setVisible(False)
    header = table.horizontalHeader()
    header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    if 0 <= stretch_column < model.columnCount():
        header.setSectionResizeMode(stretch_column, QHeaderView.ResizeMode.Stretch)
    table.setSortingEnabled(True)
    table.sortByColumn(0, Qt.SortOrder.AscendingOrder)


def selected_ids(table: QTableView) -> list[int]:
    """Return stable record IDs for selected rows, independent of visual sorting."""

    return [
        int(index.data(ID_ROLE))
        for index in sorted(table.selectionModel().selectedRows(), key=lambda item: item.row())
        if index.data(ID_ROLE) is not None
    ]
