"""Main Qt window and application-wide presentation state."""

import sqlite3

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QColor, QCloseEvent, QPalette
from PySide6.QtWidgets import QApplication, QMainWindow, QMessageBox, QTabWidget

from app.pages.assemble import AssemblePage
from app.pages.inventory import InventoryPage
from app.pages.purchases import PurchasesPage
from app.pages.sales import SalesPage
from app.pages.settings import SettingsPage
from db.backup import create_backup


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("PCIMS — PC Inventory Management")
        self.resize(1240, 800)
        self.setMinimumSize(900, 600)
        self.settings = QSettings("PCIMS", "PCIMS")

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.setCentralWidget(self.tabs)
        self.inventory_page = InventoryPage()
        self.purchases_page = PurchasesPage()
        self.assemble_page = AssemblePage()
        self.sales_page = SalesPage()
        self.settings_page = SettingsPage(self.settings.value("theme", "system"))
        self.pages = (
            self.inventory_page,
            self.purchases_page,
            self.assemble_page,
            self.sales_page,
            self.settings_page,
        )
        for page, title in zip(
            self.pages,
            ("Inventory", "Purchases", "Assemble", "Sales and History", "Settings"),
        ):
            self.tabs.addTab(page, title)
            page.data_changed.connect(self.refresh_all)
        self.settings_page.theme_changed.connect(self.apply_theme)
        self.tabs.currentChanged.connect(self.refresh_current)
        self.apply_theme(self.settings.value("theme", "system"))
        self.statusBar().showMessage("Ready")

    def refresh_current(self, index=None):
        index = self.tabs.currentIndex() if index is None else index
        page = self.tabs.widget(index)
        refresh = getattr(page, "refresh", None)
        if callable(refresh):
            refresh()

    def refresh_all(self):
        for page in self.pages:
            refresh = getattr(page, "refresh", None)
            if callable(refresh):
                refresh()
        self.statusBar().showMessage("Data refreshed", 2500)

    def apply_theme(self, theme):
        theme = theme if theme in {"system", "light", "dark"} else "system"
        application = QApplication.instance()
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
                QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor(125, 125, 125)
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
            palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor(130, 130, 130))
            application.setPalette(palette)
        if theme != "system":
            application.setStyleSheet(
                "QGroupBox { font-weight: 600; } QPushButton { padding: 5px 10px; }"
            )
        self.settings.setValue("theme", theme)

    def closeEvent(self, event: QCloseEvent):
        try:
            create_backup()
        except (OSError, ValueError, sqlite3.DatabaseError) as error:
            answer = QMessageBox.question(
                self,
                "Backup failed",
                f"The latest changes could not be backed up:\n\n{error}\n\nClose anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        event.accept()
