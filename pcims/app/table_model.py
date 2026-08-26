"""Typed Qt model/view helpers for flat record tables."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Generic, TypeAlias, TypeVar

from PySide6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QPersistentModelIndex,
    QPoint,
    Qt,
)
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QMenu, QTableView

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


@dataclass(frozen=True, slots=True)
class ContextAction:
    """One lazily enabled action in a table's row context menu."""

    text: str
    callback: Callable[[], None]
    enabled: Callable[[], bool]
    separator_before: bool = False


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
        prepared = list(records)
        identifiers = [self._record_id(record) for record in prepared]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("Table record IDs must be unique.")
        self.beginResetModel()
        self._records = prepared
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
            [
                self.index(row_by_id[record_id], column)
                for record_id, column in old_locations
            ],
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


def configure_context_menu(
    table: QTableView,
    actions: Sequence[ContextAction],
) -> None:
    """Attach a standard selection-aware context menu to a record table."""

    configured_actions = tuple(actions)
    table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)

    def show_menu(position: QPoint) -> None:
        select_context_row(table, position)
        menu = build_context_menu(table, configured_actions)
        try:
            menu.exec(table.viewport().mapToGlobal(position))
        finally:
            menu.deleteLater()

    table.customContextMenuRequested.connect(show_menu)


def select_context_row(table: QTableView, position: QPoint) -> None:
    """Apply native-feeling row selection before opening a context menu."""

    index = table.indexAt(position)
    if index.isValid() and not table.selectionModel().isRowSelected(
        index.row(), index.parent()
    ):
        table.clearSelection()
        table.selectRow(index.row())
        table.setCurrentIndex(index)
    elif not index.isValid() and position.x() >= 0 and position.y() >= 0:
        table.clearSelection()


def build_context_menu(
    table: QTableView,
    actions: Sequence[ContextAction],
) -> QMenu:
    """Build a context menu using the selection state at opening time."""

    menu = QMenu(table)
    for configured in actions:
        if configured.separator_before:
            menu.addSeparator()
        action = menu.addAction(configured.text)
        action.setEnabled(configured.enabled())
        action.triggered.connect(configured.callback)
    return menu


def selected_ids(table: QTableView) -> list[int]:
    """Return stable record IDs for selected rows, independent of visual sorting."""

    return [
        int(index.data(ID_ROLE))
        for index in sorted(
            table.selectionModel().selectedRows(), key=lambda item: item.row()
        )
        if index.data(ID_ROLE) is not None
    ]
