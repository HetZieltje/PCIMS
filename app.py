from tkinter import ttk
import os
import customtkinter as ctk
from customtkinter import *

# Import tab modules
from app.inventory import InventoryTab
from app.assemble import AssembleTab
from app.purchase import PurchaseTab
from app.balance import BalanceTab

from db.connection import get_connection
from db.queries import initialize_database

class PCIMS(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("PC Inventory Management System")
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

# Initialize database only if it doesn't exist
def setup_database():
    db_path = os.path.join(os.path.dirname(__file__), 'db/pcims_db.db')

    # Check if the database file exists
    db_exists = os.path.exists(db_path)

    # Get a connection
    connection = get_connection()

    # If the database is new, initialize it
    if not db_exists:
        initialize_database()

    connection.close()

if __name__ == "__main__":
    setup_database()

    app = PCIMS()
    app.mainloop()
