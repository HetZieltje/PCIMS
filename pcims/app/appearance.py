"""Application-wide Qt palette policy."""

from typing import Literal, cast

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

ThemeName = Literal["system", "light", "dark"]


def normalize_theme(theme: object) -> ThemeName:
    value = str(theme)
    return cast(ThemeName, value if value in {"system", "light", "dark"} else "system")


def apply_application_theme(theme: object) -> ThemeName:
    """Apply one normalized palette and return the effective theme name."""
    normalized = normalize_theme(theme)
    existing = QApplication.instance()
    if existing is None:
        raise RuntimeError("A QApplication must exist before applying a theme.")
    application = cast(QApplication, existing)
    application.setPalette(application.style().standardPalette())
    application.setStyleSheet(_application_style_sheet(normalized))
    if normalized == "light":
        palette = QPalette()
        palette.setColor(QPalette.ColorRole.Window, QColor(245, 245, 245))
        palette.setColor(QPalette.ColorRole.WindowText, QColor(25, 25, 25))
        palette.setColor(QPalette.ColorRole.Base, Qt.GlobalColor.white)
        palette.setColor(QPalette.ColorRole.AlternateBase, QColor(240, 240, 240))
        palette.setColor(QPalette.ColorRole.ToolTipBase, Qt.GlobalColor.white)
        palette.setColor(QPalette.ColorRole.ToolTipText, QColor(25, 25, 25))
        palette.setColor(QPalette.ColorRole.Text, QColor(25, 25, 25))
        palette.setColor(QPalette.ColorRole.Button, QColor(238, 238, 238))
        palette.setColor(QPalette.ColorRole.ButtonText, QColor(25, 25, 25))
        palette.setColor(QPalette.ColorRole.Highlight, QColor(0, 120, 212))
        palette.setColor(QPalette.ColorRole.HighlightedText, Qt.GlobalColor.white)
        palette.setColor(
            QPalette.ColorGroup.Disabled,
            QPalette.ColorRole.Text,
            QColor(125, 125, 125),
        )
        application.setPalette(palette)
    elif normalized == "dark":
        palette = QPalette()
        palette.setColor(QPalette.ColorRole.Window, QColor(37, 37, 38))
        palette.setColor(QPalette.ColorRole.WindowText, Qt.GlobalColor.white)
        palette.setColor(QPalette.ColorRole.Base, QColor(30, 30, 30))
        palette.setColor(QPalette.ColorRole.AlternateBase, QColor(45, 45, 48))
        palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(45, 45, 48))
        palette.setColor(QPalette.ColorRole.ToolTipText, Qt.GlobalColor.white)
        palette.setColor(QPalette.ColorRole.Text, Qt.GlobalColor.white)
        palette.setColor(QPalette.ColorRole.Button, QColor(45, 45, 48))
        palette.setColor(QPalette.ColorRole.ButtonText, Qt.GlobalColor.white)
        palette.setColor(QPalette.ColorRole.BrightText, Qt.GlobalColor.red)
        palette.setColor(QPalette.ColorRole.Highlight, QColor(0, 120, 212))
        palette.setColor(QPalette.ColorRole.HighlightedText, Qt.GlobalColor.white)
        palette.setColor(
            QPalette.ColorGroup.Disabled,
            QPalette.ColorRole.Text,
            QColor(130, 130, 130),
        )
        application.setPalette(palette)
    application.setStyleSheet(_application_style_sheet(normalized))
    return normalized


def _application_style_sheet(theme: ThemeName) -> str:
    danger = "#ff6b5f" if theme == "dark" else "#c42b1c"
    base = (
        "QGroupBox { font-weight: 600; } QPushButton { padding: 5px 10px; } "
        if theme != "system"
        else ""
    )
    return (
        base
        + f'QPushButton[destructive="true"] {{ color: {danger}; font-weight: 600; }} '
        f'QPushButton[destructive="true"]:hover {{ border-color: {danger}; }} '
        'QLabel[heading="true"] { font-size: 14px; font-weight: 600; }'
    )
