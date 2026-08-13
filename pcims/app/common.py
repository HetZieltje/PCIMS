"""Shared Qt helpers."""

import sqlite3
from collections.abc import Sequence

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QMessageBox,
    QTableWidget,
    QTableWidgetItem,
    QWidget,
)

ID_ROLE = Qt.ItemDataRole.UserRole
SORT_ROLE = Qt.ItemDataRole.UserRole + 1
DATA_OPERATION_ERRORS = (OSError, ValueError, LookupError, sqlite3.DatabaseError)


class SortableTableItem(QTableWidgetItem):
    """Table item that sorts by a typed value instead of rendered text."""

    def __lt__(self, other: QTableWidgetItem) -> bool:
        left = self.data(SORT_ROLE)
        right = other.data(SORT_ROLE)
        if left is not None and right is not None:
            return bool(left < right)
        return super().__lt__(other)


def configure_table(
    table: QTableWidget, headers: Sequence[str], stretch_column: int = 1
) -> None:
    table.setColumnCount(len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.setAlternatingRowColors(True)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setSortingEnabled(True)
    table.verticalHeader().setVisible(False)
    header = table.horizontalHeader()
    header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    if 0 <= stretch_column < len(headers):
        header.setSectionResizeMode(stretch_column, QHeaderView.ResizeMode.Stretch)


def table_item(
    text: object,
    record_id: int | None = None,
    sort_value: object | None = None,
    alignment: Qt.AlignmentFlag | None = None,
) -> SortableTableItem:
    item = SortableTableItem(str(text))
    if record_id is not None:
        item.setData(ID_ROLE, int(record_id))
    if sort_value is not None:
        item.setData(SORT_ROLE, sort_value)
    if alignment is not None:
        item.setTextAlignment(alignment)
    return item


def selected_ids(table: QTableWidget) -> list[int]:
    rows = sorted({index.row() for index in table.selectionModel().selectedRows()})
    record_ids: list[int] = []
    for row in rows:
        item = table.item(row, 0)
        if item is None:
            continue
        record_id = item.data(ID_ROLE)
        if record_id is not None:
            record_ids.append(int(record_id))
    return record_ids


def show_error(parent: QWidget | None, title: str, error: BaseException) -> None:
    QMessageBox.critical(parent, title, str(error))


def ask_confirmation(parent: QWidget | None, title: str, text: str) -> bool:
    return (
        QMessageBox.question(
            parent,
            title,
            text,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        == QMessageBox.StandardButton.Yes
    )
