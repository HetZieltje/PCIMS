"""Shared Qt dialog helpers."""

from PySide6.QtWidgets import QMessageBox, QWidget


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
