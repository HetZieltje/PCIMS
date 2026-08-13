"""Shared Qt helpers."""

import sqlite3

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QMessageBox,
    QTableWidget,
    QTableWidgetItem,
)

ID_ROLE = Qt.ItemDataRole.UserRole
SORT_ROLE = Qt.ItemDataRole.UserRole + 1
DATA_OPERATION_ERRORS = (OSError, ValueError, LookupError, sqlite3.DatabaseError)


class SortableTableItem(QTableWidgetItem):
    """Table item that sorts by a typed value instead of rendered text."""

    def __lt__(self, other):
        left = self.data(SORT_ROLE)
        right = other.data(SORT_ROLE)
        if left is not None and right is not None:
            return left < right
        return super().__lt__(other)


def configure_table(table: QTableWidget, headers, stretch_column=1):
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


def table_item(text, record_id=None, sort_value=None, alignment=None):
    item = SortableTableItem(str(text))
    if record_id is not None:
        item.setData(ID_ROLE, int(record_id))
    if sort_value is not None:
        item.setData(SORT_ROLE, sort_value)
    if alignment is not None:
        item.setTextAlignment(alignment)
    return item


def selected_ids(table: QTableWidget):
    rows = sorted({index.row() for index in table.selectionModel().selectedRows()})
    return [table.item(row, 0).data(ID_ROLE) for row in rows]


def show_error(parent, title, error):
    QMessageBox.critical(parent, title, str(error))


def ask_confirmation(parent, title, text):
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
