"""Main Qt window and application-wide presentation state."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import cast

from PySide6.QtCore import QByteArray, QSettings, Qt, QTimer
from PySide6.QtGui import QCloseEvent, QColor, QPalette
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QMessageBox,
    QTabWidget,
    QWidget,
)

from pcims.app.common import show_error
from pcims.app.pages.assemble import AssemblePage
from pcims.app.pages.inventory import InventoryPage
from pcims.app.pages.purchases import PurchasesPage
from pcims.app.pages.sales import SalesPage
from pcims.app.pages.settings import SettingsPage
from pcims.app.tasks import BackgroundTask, run_in_background
from pcims.db.backup import BackupResult
from pcims.services import ApplicationServices, default_services


@dataclass(slots=True)
class RefreshState:
    requested_generation: int = 0
    running_generation: int | None = None
    task: BackgroundTask[object] | None = None
    pending: bool = False


class MainWindow(QMainWindow):
    def __init__(self, services: ApplicationServices | None = None) -> None:
        super().__init__()
        self.services = services or default_services()
        self.setWindowTitle("PCIMS — PC Inventory Management")
        self.resize(1240, 800)
        self.setMinimumSize(900, 600)
        self.settings = QSettings("PCIMS", "PCIMS")
        self._closing_after_backup = False
        self._close_backup_running = False

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.setCentralWidget(self.tabs)
        self.inventory_page = InventoryPage(self.services)
        self.purchases_page = PurchasesPage(self.services)
        self.assemble_page = AssemblePage(self.services)
        self.sales_page = SalesPage(self.services)
        self.settings_page = SettingsPage(
            self.services,
            str(self.settings.value("theme", "system")),
            has_pending_changes=lambda: self.purchases_page.has_staged_items,
        )
        self.pages = (
            self.inventory_page,
            self.purchases_page,
            self.assemble_page,
            self.sales_page,
            self.settings_page,
        )
        self._dirty_pages: set[QWidget] = set(self.pages[:-1])
        self._refresh_states: dict[QWidget, RefreshState] = {
            page: RefreshState() for page in self.pages[:-1]
        }
        for page, title in zip(
            self.pages,
            ("Inventory", "Purchases", "Assemble", "Sales and History", "Settings"),
        ):
            self.tabs.addTab(page, title)
        for page in self.pages[:-1]:
            page.data_changed.connect(self._on_data_changed)
        self.settings_page.theme_changed.connect(self.apply_theme)
        self.settings_page.database_restored.connect(self._after_database_restore)
        self.tabs.currentChanged.connect(self.refresh_current)
        self.apply_theme(str(self.settings.value("theme", "system")))
        self._restore_window_state()
        self.statusBar().showMessage("Ready")
        self.refresh_current()

    def create_startup_backup(self) -> None:
        self.statusBar().showMessage("Creating startup backup…")
        self._startup_backup_task = run_in_background(
            self.services.create_backup,
            self._startup_backup_finished,
            self._startup_backup_failed,
        )

    def _startup_backup_finished(self, backup: BackupResult) -> None:
        self.statusBar().showMessage("Startup backup complete", 2500)
        if backup.has_cleanup_warnings:
            QMessageBox.warning(
                self,
                "Backup cleanup warning",
                f"The startup backup was created at {backup.path}, but old backup "
                f"cleanup failed:\n\n{backup.cleanup_warning}",
            )

    def _startup_backup_failed(self, error: Exception) -> None:
        self.statusBar().showMessage("Startup backup failed", 5000)
        QMessageBox.warning(
            self,
            "Backup warning",
            f"PCIMS started, but its startup backup could not be created:\n\n{error}",
        )

    def refresh_current(self, index: int | None = None) -> None:
        index = self.tabs.currentIndex() if index is None else index
        page = self.tabs.widget(index)
        if page not in self._dirty_pages:
            return
        self._start_refresh(page)

    def _start_refresh(self, page: QWidget) -> None:
        state = self._refresh_states.get(page)
        if state is None:
            self._dirty_pages.discard(page)
            return
        state.requested_generation += 1
        if state.task is not None:
            state.pending = True
            return
        self._launch_refresh(page, state)

    def _launch_refresh(self, page: QWidget, state: RefreshState) -> None:
        load_snapshot = getattr(page, "load_snapshot", None)
        apply_snapshot = getattr(page, "apply_snapshot", None)
        if not callable(load_snapshot) or not callable(apply_snapshot):
            self._dirty_pages.discard(page)
            return
        loader = cast(Callable[[], object], load_snapshot)
        applier = cast(Callable[[object], None], apply_snapshot)
        generation = state.requested_generation
        state.running_generation = generation
        state.pending = False
        state.task = run_in_background(
            loader,
            lambda snapshot: self._refresh_succeeded(
                page, generation, applier, snapshot
            ),
            lambda error: self._refresh_failed(page, generation, error),
        )

    def _refresh_succeeded(
        self,
        page: QWidget,
        generation: int,
        apply_snapshot: Callable[[object], None],
        snapshot: object,
    ) -> None:
        state = self._finish_refresh(page, generation)
        if state is None:
            return
        if state.requested_generation == generation:
            apply_snapshot(snapshot)
            self._dirty_pages.discard(page)
            self.statusBar().showMessage("Data refreshed", 2500)
        self._launch_pending_refresh(page, state)

    def _refresh_failed(
        self, page: QWidget, generation: int, error: Exception
    ) -> None:
        state = self._finish_refresh(page, generation)
        if state is None:
            return
        if state.requested_generation == generation:
            self._dirty_pages.add(page)
            show_error(self, "Unable to refresh data", error)
        self._launch_pending_refresh(page, state)

    def _finish_refresh(
        self, page: QWidget, generation: int
    ) -> RefreshState | None:
        state = self._refresh_states.get(page)
        if state is None or state.running_generation != generation:
            return None
        state.task = None
        state.running_generation = None
        return state

    def _launch_pending_refresh(self, page: QWidget, state: RefreshState) -> None:
        if state.pending:
            self._launch_refresh(page, state)

    @property
    def refresh_running(self) -> bool:
        return any(state.task is not None for state in self._refresh_states.values())

    def _data_work_running(self) -> bool:
        return self.refresh_running or any(
            bool(getattr(page, "command_running", False)) for page in self.pages[:-1]
        )

    def _cancel_pending_refreshes(self) -> None:
        for state in self._refresh_states.values():
            state.requested_generation += 1
            state.pending = False

    def refresh_all(self) -> None:
        self._dirty_pages.update(self.pages[:-1])
        for page in self.pages[:-1]:
            self._start_refresh(page)
        self.statusBar().showMessage("Refreshing dataâ€¦")

    def _on_data_changed(self) -> None:
        for state in self._refresh_states.values():
            state.requested_generation += 1
        self._dirty_pages.update(self.pages[:-1])
        self.refresh_current()
        self.statusBar().showMessage("Data updated", 2500)

    def _after_database_restore(self) -> None:
        self.purchases_page.discard_staged()
        self._on_data_changed()
        self.statusBar().showMessage("Backup restored", 5000)

    def _restore_window_state(self) -> None:
        geometry = self.settings.value("window/geometry")
        if isinstance(geometry, QByteArray):
            self.restoreGeometry(geometry)
        try:
            tab_index = int(str(self.settings.value("window/active_tab", 0)))
        except (TypeError, ValueError):
            tab_index = 0
        if 0 <= tab_index < self.tabs.count():
            self.tabs.setCurrentIndex(tab_index)
        for key, splitter in (
            ("inventory", self.inventory_page.splitter),
            ("sales", self.sales_page.splitter),
            ("sales_details", self.sales_page.detail_splitter),
        ):
            state = self.settings.value(f"window/splitters/{key}")
            if isinstance(state, QByteArray):
                splitter.restoreState(state)

    def _save_window_state(self) -> None:
        self.settings.setValue("window/geometry", self.saveGeometry())
        self.settings.setValue("window/active_tab", self.tabs.currentIndex())
        for key, splitter in (
            ("inventory", self.inventory_page.splitter),
            ("sales", self.sales_page.splitter),
            ("sales_details", self.sales_page.detail_splitter),
        ):
            self.settings.setValue(f"window/splitters/{key}", splitter.saveState())
        self.settings.sync()

    def apply_theme(self, theme: str) -> None:
        theme = theme if theme in {"system", "light", "dark"} else "system"
        existing = QApplication.instance()
        if existing is None:
            raise RuntimeError("A QApplication must exist before applying a theme.")
        application = cast(QApplication, existing)
        application.setPalette(application.style().standardPalette())
        application.setStyleSheet("")
        if theme == "light":
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
        elif theme == "dark":
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
        if theme != "system":
            application.setStyleSheet(
                "QGroupBox { font-weight: 600; } QPushButton { padding: 5px 10px; }"
            )
        self.settings.setValue("theme", theme)

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._closing_after_backup:
            self._save_window_state()
            event.accept()
            return
        if self._close_backup_running:
            event.ignore()
            return
        if self.purchases_page.has_staged_items:
            answer = QMessageBox.question(
                self,
                "Unrecorded purchase",
                "The Purchases tab contains items that have not been recorded. "
                "Close and discard them?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        event.ignore()
        self._cancel_pending_refreshes()
        self._close_backup_running = True
        self.setEnabled(False)
        self.statusBar().showMessage("Backing up before closing…")
        self._close_backup_task = run_in_background(
            self.services.create_backup,
            self._close_backup_finished,
            self._close_backup_failed,
        )

    def _close_backup_finished(self, backup: BackupResult) -> None:
        if self._data_work_running():
            QTimer.singleShot(10, lambda: self._close_backup_finished(backup))
            return
        self._close_backup_running = False
        if backup.has_cleanup_warnings:
            QMessageBox.warning(
                self,
                "Backup cleanup warning",
                f"The latest changes were backed up to:\n{backup.path}\n\n"
                f"Some old backups could not be removed:\n{backup.cleanup_warning}",
            )
        self._closing_after_backup = True
        self.setEnabled(True)
        self.close()

    def _close_backup_failed(self, error: Exception) -> None:
        self._close_backup_running = False
        answer = QMessageBox.question(
            self,
            "Backup failed",
            f"The latest changes could not be backed up:\n\n{error}\n\nClose anyway?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._closing_after_backup = True
            self.setEnabled(True)
            self.close()
            return
        self.setEnabled(True)
        self.statusBar().showMessage("Close cancelled because backup failed", 5000)
