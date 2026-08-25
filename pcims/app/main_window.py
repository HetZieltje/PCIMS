"""Main Qt window and application-wide presentation state."""

from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QMainWindow,
    QMessageBox,
    QTabWidget,
)

from pcims.app.appearance import apply_application_theme
from pcims.app.common import show_error
from pcims.app.pages.activity import ActivityPage
from pcims.app.pages.assemble import AssemblePage
from pcims.app.pages.inventory import InventoryPage
from pcims.app.pages.purchases import PurchasesPage
from pcims.app.pages.sales import SalesPage
from pcims.app.pages.settings import SettingsPage
from pcims.app.refresh import RefreshCoordinator, bind_refresh
from pcims.app.tasks import TaskManager
from pcims.app.window_state import WindowStateStore
from pcims.contracts import BackupResult
from pcims.drafts import PurchaseDraftStore
from pcims.services import ApplicationServices


class MainWindow(QMainWindow):
    def __init__(self, services: ApplicationServices) -> None:
        super().__init__()
        self.services = services
        self.setWindowTitle("PCIMS — PC Inventory Management")
        self.resize(1240, 800)
        self.setMinimumSize(900, 600)
        self.window_state = WindowStateStore()
        self._closing_after_backup = False
        self._close_requested = False
        self._close_backup_running = False
        self._data_generation = 0
        self._backed_up_generation: int | None = None
        self.tasks = TaskManager(self)
        self.tasks.became_idle.connect(self._continue_close)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.setCentralWidget(self.tabs)
        self.inventory_page = InventoryPage(self.services, tasks=self.tasks)
        self.purchases_page = PurchasesPage(
            self.services,
            tasks=self.tasks,
            draft_store=PurchaseDraftStore(self.services.database_path),
        )
        self.assemble_page = AssemblePage(self.services, tasks=self.tasks)
        self.sales_page = SalesPage(self.services, tasks=self.tasks)
        self.activity_page = ActivityPage(self.services, tasks=self.tasks)
        self.settings_page = SettingsPage(
            self.services,
            theme=self.window_state.theme,
            has_pending_changes=lambda: self.purchases_page.has_staged_items,
            tasks=self.tasks,
        )
        self.data_pages = (
            self.inventory_page,
            self.purchases_page,
            self.assemble_page,
            self.sales_page,
            self.activity_page,
        )
        self.pages = (
            *self.data_pages,
            self.settings_page,
        )
        bindings = (
            bind_refresh(
                self.inventory_page,
                lambda: self.inventory_page.load_snapshot(),
                lambda snapshot: self.inventory_page.apply_snapshot(snapshot),
            ),
            bind_refresh(
                self.purchases_page,
                lambda: self.purchases_page.load_snapshot(),
                lambda snapshot: self.purchases_page.apply_snapshot(snapshot),
            ),
            bind_refresh(
                self.assemble_page,
                lambda: self.assemble_page.load_snapshot(),
                lambda snapshot: self.assemble_page.apply_snapshot(snapshot),
            ),
            bind_refresh(
                self.sales_page,
                lambda: self.sales_page.load_snapshot(),
                lambda snapshot: self.sales_page.apply_snapshot(snapshot),
            ),
            bind_refresh(
                self.activity_page,
                lambda: self.activity_page.load_snapshot(),
                lambda snapshot: self.activity_page.apply_snapshot(snapshot),
            ),
        )
        self.refreshes = RefreshCoordinator(self.tasks, bindings, self)
        self.refreshes.refreshed.connect(
            lambda: self.statusBar().showMessage("Data refreshed", 2500)
        )
        self.refreshes.failed.connect(
            lambda _page, error: show_error(self, "Unable to refresh data", error)
        )
        for page, title in zip(
            self.pages,
            (
                "Inventory",
                "Purchases",
                "Assemble",
                "Sales and History",
                "Activity",
                "Settings",
            ),
        ):
            self.tabs.addTab(page, title)
        for page in self.data_pages:
            page.data_changed.connect(self._on_data_changed)
        self.settings_page.theme_changed.connect(self.apply_theme)
        self.settings_page.database_restored.connect(self._after_database_restore)
        self.tabs.currentChanged.connect(self.refresh_current)
        self.apply_theme(self.window_state.theme)
        self._restore_window_state()
        self.statusBar().showMessage("Ready")
        self.refresh_current()

    def create_startup_backup(self) -> None:
        self.statusBar().showMessage("Creating startup backup…")
        generation = self._data_generation
        self._startup_backup_task = self.tasks.run(
            self.services.create_backup,
            lambda backup: self._startup_backup_finished(generation, backup),
            self._startup_backup_failed,
            owner=self,
        )

    def _startup_backup_finished(self, generation: int, backup: BackupResult) -> None:
        self._backed_up_generation = generation if backup.durable else None
        self.statusBar().showMessage("Startup backup complete", 2500)
        if backup.has_warnings:
            QMessageBox.warning(
                self,
                "Backup warning",
                f"The startup backup was created at {backup.path}, with warnings:"
                f"\n\n{backup.warning_text}",
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
        if page is not None:
            self.refreshes.start_if_dirty(page)

    @property
    def refresh_running(self) -> bool:
        return self.refreshes.active

    def _cancel_pending_refreshes(self) -> None:
        self.refreshes.pause()

    def refresh_all(self) -> None:
        self.refreshes.refresh_all()
        self.statusBar().showMessage("Refreshing data…")

    def _on_data_changed(self) -> None:
        self._data_generation += 1
        self.refreshes.invalidate_all(self.tabs.currentWidget())
        self.statusBar().showMessage("Data updated", 2500)

    def _after_database_restore(self) -> None:
        self.purchases_page.discard_staged()
        self._on_data_changed()
        self.statusBar().showMessage("Backup restored", 5000)

    def _restore_window_state(self) -> None:
        self.window_state.restore(
            self,
            self.tabs,
            (
                ("inventory", self.inventory_page.splitter),
                ("sales", self.sales_page.splitter),
                ("sales_details", self.sales_page.detail_splitter),
            ),
        )

    def _save_window_state(self) -> None:
        self.window_state.save(
            self,
            self.tabs,
            (
                ("inventory", self.inventory_page.splitter),
                ("sales", self.sales_page.splitter),
                ("sales_details", self.sales_page.detail_splitter),
            ),
        )

    def apply_theme(self, theme: str) -> None:
        self.window_state.theme = apply_application_theme(theme)

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._closing_after_backup:
            self._save_window_state()
            event.accept()
            return
        if self._close_requested:
            event.ignore()
            return
        if self.purchases_page.has_staged_items:
            answer = QMessageBox.question(
                self,
                "Saved purchase draft",
                "The Purchases tab contains items that have not been recorded. "
                "They are saved and will be restored next time. Close PCIMS?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        event.ignore()
        self._cancel_pending_refreshes()
        self._close_requested = True
        self.setEnabled(False)
        self._continue_close()

    def _continue_close(self) -> None:
        if (
            not self._close_requested
            or self._close_backup_running
            or self._closing_after_backup
        ):
            return
        if self.tasks.active:
            self.statusBar().showMessage("Waiting for background work to finish…")
            return
        if self._backed_up_generation == self._data_generation:
            self._closing_after_backup = True
            self._close_requested = False
            self.setEnabled(True)
            self.close()
            return
        self._close_backup_running = True
        self.statusBar().showMessage("Backing up before closing…")
        self._close_backup_task = self.tasks.run(
            self.services.create_backup,
            self._close_backup_finished,
            self._close_backup_failed,
            owner=self,
        )

    def _close_backup_finished(self, backup: BackupResult) -> None:
        self._close_backup_running = False
        if backup.has_warnings:
            QMessageBox.warning(
                self,
                "Backup warning",
                f"The latest changes were backed up to:\n{backup.path}\n\n"
                f"Backup warnings:\n{backup.warning_text}",
            )
        self._closing_after_backup = True
        self._close_requested = False
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
            self._close_requested = False
            self.setEnabled(True)
            self.close()
            return
        self._close_requested = False
        self.refreshes.resume()
        self.setEnabled(True)
        self.refresh_current()
        self.statusBar().showMessage("Close cancelled because backup failed", 5000)
