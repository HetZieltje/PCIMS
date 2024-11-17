from tkinter import ttk
import os
import customtkinter as ctk
from customtkinter import *

# Import tab modules
from app.inventory import InventoryTab
from app.assemble import AssembleTab
from app.purchase import PurchaseTab
from app.balance import BalanceTab

from db.queries import initialize_database

class PCIMS(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("PCIMS")
        self.geometry("800x600")

        # Notebook for managing tabs
        self.notebook = ttk.Notebook(self)

        # Add all tabs
        self.notebook.add(InventoryTab(self.notebook, self), text="Inventory")
        self.notebook.add(AssembleTab(self.notebook, self), text="Assemble")
        self.notebook.add(PurchaseTab(self.notebook, self), text="Purchase")
        self.notebook.add(BalanceTab(self.notebook, self), text="Balance")

        self.notebook.pack(expand=1, fill="both")

        ctk.set_appearance_mode("light")

        # Bind tab selection event
        self.notebook.bind("<<NotebookTabChanged>>", self.on_tab_change)

    def on_tab_change(self, event):
        # Get the currently selected tab
        selected_tab = self.notebook.nametowidget(self.notebook.select())
        # Call the refresh method if it exists
        if hasattr(selected_tab, "refresh"):
            selected_tab.refresh()

    def switch_to_tab(self, tab_name):
        """Switch to a specific tab by its name."""
        tab_map = {
            "Inventory": 0,
            "Assemble": 1,
            "Purchase": 2,
            "Balance": 3,
        }
        if tab_name in tab_map:
            self.notebook.select(tab_map[tab_name])
        else:
            raise ValueError(f"Tab '{tab_name}' does not exist.")

    def on_closing(self):
        """Handle the window close event."""
        db_path = os.path.join(os.path.dirname(__file__), 'db/pcims_db.db')

        # Check if the database file exists
        
        if os.path.exists(db_path):
            os.remove(db_path)

        self.destroy()  # Close the application
    
# Initialize database only if it doesn't exist
def setup_database():
    db_path = os.path.join(os.path.dirname(__file__), 'db/pcims_db.db')

    # Check if the database file exists
    if not os.path.exists(db_path):
        # Get a connection and initialize the database
        initialize_database()

if __name__ == "__main__":
    setup_database()

    app = PCIMS()
    # app.protocol("WM_DELETE_WINDOW", app.on_closing)
    app.mainloop()
