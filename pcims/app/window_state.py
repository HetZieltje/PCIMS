"""Persistent Qt window preferences independent from application lifecycle."""

from collections.abc import Iterable

from PySide6.QtCore import QByteArray, QSettings
from PySide6.QtWidgets import QMainWindow, QSplitter, QTableView, QTabWidget

from pcims.app.appearance import ThemeName, normalize_theme


class WindowStateStore:
    def __init__(self, settings: QSettings | None = None) -> None:
        self._settings = settings or QSettings("PCIMS", "PCIMS")

    @property
    def theme(self) -> ThemeName:
        return normalize_theme(self._settings.value("theme", "system"))

    @theme.setter
    def theme(self, value: ThemeName) -> None:
        self._settings.setValue("theme", value)

    @property
    def backup_retention(self) -> int:
        try:
            value = int(str(self._settings.value("backups/retention", 14)))
        except (TypeError, ValueError):
            return 14
        return value if 1 <= value <= 30 else 14

    @backup_retention.setter
    def backup_retention(self, value: int) -> None:
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not 1 <= value <= 30
        ):
            raise ValueError("Backup retention must be between 1 and 30.")
        self._settings.setValue("backups/retention", value)

    def restore(
        self,
        window: QMainWindow,
        tabs: QTabWidget,
        splitters: Iterable[tuple[str, QSplitter]],
        tables: Iterable[tuple[str, QTableView]] = (),
    ) -> None:
        geometry = self._settings.value("window/geometry")
        if isinstance(geometry, QByteArray):
            window.restoreGeometry(geometry)
        try:
            tab_index = int(str(self._settings.value("window/active_tab", 0)))
        except (TypeError, ValueError):
            tab_index = 0
        if 0 <= tab_index < tabs.count():
            tabs.setCurrentIndex(tab_index)
        for key, splitter in splitters:
            state = self._settings.value(f"window/splitters/{key}")
            if isinstance(state, QByteArray):
                splitter.restoreState(state)
        for key, table in tables:
            state = self._settings.value(f"window/tables/{key}")
            try:
                column_count = int(
                    str(self._settings.value(f"window/tables/{key}_columns", -1))
                )
            except (TypeError, ValueError):
                column_count = -1
            if (
                column_count == table.model().columnCount()
                and isinstance(state, QByteArray)
                and table.horizontalHeader().restoreState(state)
            ):
                section = table.horizontalHeader().sortIndicatorSection()
                if 0 <= section < table.model().columnCount():
                    table.sortByColumn(
                        section, table.horizontalHeader().sortIndicatorOrder()
                    )

    def save(
        self,
        window: QMainWindow,
        tabs: QTabWidget,
        splitters: Iterable[tuple[str, QSplitter]],
        tables: Iterable[tuple[str, QTableView]] = (),
    ) -> None:
        self._settings.setValue("window/geometry", window.saveGeometry())
        self._settings.setValue("window/active_tab", tabs.currentIndex())
        for key, splitter in splitters:
            self._settings.setValue(f"window/splitters/{key}", splitter.saveState())
        for key, table in tables:
            self._settings.setValue(
                f"window/tables/{key}", table.horizontalHeader().saveState()
            )
            self._settings.setValue(
                f"window/tables/{key}_columns", table.model().columnCount()
            )
        self._settings.sync()
