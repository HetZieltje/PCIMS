"""Persistent Qt window preferences independent from application lifecycle."""

from collections.abc import Iterable

from PySide6.QtCore import QByteArray, QSettings
from PySide6.QtWidgets import QMainWindow, QSplitter, QTabWidget

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

    def restore(
        self,
        window: QMainWindow,
        tabs: QTabWidget,
        splitters: Iterable[tuple[str, QSplitter]],
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

    def save(
        self,
        window: QMainWindow,
        tabs: QTabWidget,
        splitters: Iterable[tuple[str, QSplitter]],
    ) -> None:
        self._settings.setValue("window/geometry", window.saveGeometry())
        self._settings.setValue("window/active_tab", tabs.currentIndex())
        for key, splitter in splitters:
            self._settings.setValue(f"window/splitters/{key}", splitter.saveState())
        self._settings.sync()
