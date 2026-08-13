from tkinter import messagebox, ttk
import os
import json
import sqlite3
from app import ui as ctk

# Import tab modules
from app.inventory import InventoryTab
from app.purchase import PurchaseTab
from app.assemble import AssembleTab
from app.balance import BalanceTab
from app.extras import ExtrasTab

from db.queries import initialize_database
from db.connection import get_data_dir
from db.backup import create_backup


CONFIG_PATH = get_data_dir() / "config.json"

class PCIMS(ctk.CTk):
    def __init__(self):
        # Load dark mode setting from config file
        self.load_dark_mode_setting()
        super().__init__()
        self.title("PCIMS")
        self.geometry("1024x768")

        # Set initial appearance mode
        self.is_dark_mode = self.config.get("is_dark_mode", True)

        # Notebook for managing tabs
        self.notebook = ttk.Notebook(self)

        # Add all tabs
        self.notebook.add(InventoryTab(self.notebook, self), text="Inventory")
        self.notebook.add(PurchaseTab(self.notebook, self), text="Enter Purchase")
        self.notebook.add(AssembleTab(self.notebook, self), text="Assemble PC")
        self.notebook.add(BalanceTab(self.notebook, self), text="Balance")
        self.notebook.add(ExtrasTab(self.notebook, self), text="Extras")

        self.notebook.pack(expand=1, fill="both")

        ctk.set_appearance_mode("dark")

        # Button frame
        self.button_frame = ctk.CTkFrame(self)
        self.button_frame.pack(side="top", fill="x", padx=10, pady=5)
        button_frame = self.button_frame

        # Add buttons to the button frame
        self.inventory_button = ctk.CTkButton(button_frame, text="Inventory", command=self.switch_to_inventory_tab)
        self.inventory_button.pack(side="left", padx=2, pady=3)

        self.add_purchase_button = ctk.CTkButton(button_frame, text="Add Purchase", command=self.switch_to_purchase_tab)
        self.add_purchase_button.pack(side="left", padx=2, pady=3)

        self.assemble_pc_button = ctk.CTkButton(button_frame, text="Assemble PC", command=self.switch_to_assemble_tab)
        self.assemble_pc_button.pack(side="left", padx=2, pady=3)

        self.extras_button = ctk.CTkButton(button_frame, text="Extras", command=self.switch_to_extras_tab)
        self.extras_button.pack(side="left", padx=2, pady=3)

        self.delete_button = ctk.CTkButton(button_frame, text="Delete", command=self.delete_item)
        self.delete_button.pack(side="left", padx=2, pady=3)

        self.sell_button = ctk.CTkButton(button_frame, text="Sell", command=self.sell_item)
        self.sell_button.pack(side="left", padx=2, pady=3)

        self.rename_button = ctk.CTkButton(button_frame, text="Rename", command=self.rename_item)
        self.rename_button.pack(side="left", padx=2, pady=3)

        self.balance_button = ctk.CTkButton(button_frame, text="Balance", command=self.switch_to_balance_tab)
        self.balance_button.pack(side="left", padx=2, pady=3)

        self.unsell_button = ctk.CTkButton(button_frame, text="Unsell", command=self.unsell_item)
        self.unsell_button.pack(side="left", padx=2, pady=3)

        # Add a small interactive icon to toggle dark mode
        self.dark_mode_icon = ctk.CTkButton(button_frame, text="       ☀️", command=self.toggle_dark_mode)
        self.dark_mode_icon.pack(side="right", padx=2, pady=3)

        self.apply_theme()

        # Bind tab selection event
        self.notebook.bind("<<NotebookTabChanged>>", self.refresh_tab)
        self.protocol("WM_DELETE_WINDOW", self.close_application)

    def refresh_tab(self, event=None):
        # Get the currently selected tab
        selected_tab = self.notebook.nametowidget(self.notebook.select())
        # Call the refresh method if it exists
        if hasattr(selected_tab, "refresh"):
            selected_tab.refresh()

        # Update button visibility based on the selected tab
        self.update_button_visibility()

    def refresh_all_tabs(self):
        """Reload every tab after an operation that replaces shared data."""
        for tab in self.notebook.winfo_children():
            refresh = getattr(tab, "refresh", None)
            if callable(refresh):
                refresh()
        self.update_button_visibility()

    def close_application(self):
        """Capture the latest session state before closing the window."""
        try:
            create_backup()
        except (OSError, ValueError, sqlite3.DatabaseError) as exc:
            should_close = messagebox.askyesno(
                "Backup Failed",
                f"PCIMS could not back up the latest changes:\n\n{exc}\n\nClose anyway?",
                parent=self,
            )
            if not should_close:
                return
        self.destroy()

    def update_button_visibility(self):
        selected_tab = self.notebook.index(self.notebook.select())

        # Hide all buttons initially
        self.inventory_button.pack_forget()
        self.add_purchase_button.pack_forget()
        self.assemble_pc_button.pack_forget()
        self.balance_button.pack_forget()
        self.extras_button.pack_forget()
        self.delete_button.pack_forget()
        self.sell_button.pack_forget()
        self.rename_button.pack_forget()
        self.unsell_button.pack_forget()

        # Show relevant buttons based on the selected tab
        if selected_tab == 0:  # Inventory Tab
            self.add_purchase_button.pack(side="left", padx=2, pady=3)
            self.assemble_pc_button.pack(side="left", padx=2, pady=3)
            self.balance_button.pack(side="left", padx=2, pady=3)
            self.extras_button.pack(side="left", padx=2, pady=3)
            self.delete_button.pack(side="left", padx=2, pady=3)
            self.sell_button.pack(side="left", padx=2, pady=3)
            self.rename_button.pack(side="left", padx=2, pady=3)
        elif selected_tab == 1:  # Purchase Tab
            self.inventory_button.pack(side="left", padx=2, pady=3)
            self.assemble_pc_button.pack(side="left", padx=2, pady=3)
            self.balance_button.pack(side="left", padx=2, pady=3)
            self.extras_button.pack(side="left", padx=2, pady=3)
        elif selected_tab == 2:  # Assemble Tab
            self.inventory_button.pack(side="left", padx=2, pady=3)
            self.add_purchase_button.pack(side="left", padx=2, pady=3)
            self.balance_button.pack(side="left", padx=2, pady=3)
            self.extras_button.pack(side="left", padx=2, pady=3)
        elif selected_tab == 3:  # Balance Tab
            self.inventory_button.pack(side="left", padx=2, pady=3)
            self.add_purchase_button.pack(side="left", padx=2, pady=3)
            self.assemble_pc_button.pack(side="left", padx=2, pady=3)
            self.extras_button.pack(side="left", padx=2, pady=3)
            self.delete_button.pack(side="left", padx=2, pady=3)
            self.unsell_button.pack(side="left", padx=2, pady=3)
            self.rename_button.pack(side="left", padx=2, pady=3)
        elif selected_tab == 4:  # Extras Tab
            self.inventory_button.pack(side="left", padx=2, pady=3)
            self.add_purchase_button.pack(side="left", padx=2, pady=3)
            self.assemble_pc_button.pack(side="left", padx=2, pady=3)
            self.balance_button.pack(side="left", padx=2, pady=3)
            self.sell_button.pack(side="left", padx=2, pady=3)
            self.rename_button.pack(side="left", padx=2, pady=3)

    def toggle_dark_mode(self):
        self.is_dark_mode = not self.is_dark_mode
        self.save_dark_mode_setting()
        self.apply_theme()
        self.refresh_tab()

    def save_dark_mode_setting(self):
        self.config["is_dark_mode"] = self.is_dark_mode
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = CONFIG_PATH.with_suffix(".tmp")
        with temporary_path.open("w", encoding="utf-8") as config_file:
            json.dump(self.config, config_file)
        os.replace(temporary_path, CONFIG_PATH)

    def load_dark_mode_setting(self):
        try:
            with CONFIG_PATH.open("r", encoding="utf-8") as config_file:
                self.config = json.load(config_file)
            if not isinstance(self.config, dict):
                self.config = {}
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            self.config = {}

    def apply_theme(self):
        if self.is_dark_mode:
            ctk.set_appearance_mode("dark")
            self.configure(fg_color="#2e2e2e")
            self.dark_mode_icon.configure(text="       ☀️")
            self.notebook.configure(style="Dark.TNotebook")
        else:
            ctk.set_appearance_mode("light")
            self.configure(fg_color="#f0f0f0")
            self.dark_mode_icon.configure(text="🌙")
            self.notebook.configure(style="Light.TNotebook")

        # Apply theme to all tabs
        for tab in self.notebook.winfo_children():
            tab.configure(fg_color=self.cget("fg_color"))

        # Apply custom style for the notebook
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Light.TNotebook", background="#f0f0f0", foreground="black")
        style.configure("Dark.TNotebook", background="#2e2e2e", foreground="white")

        # Update button frame background color
        self.button_frame.configure(fg_color=self.cget("fg_color"))

        # Refresh the current tab to apply the theme
        self.refresh_tab()

    def switch_to_inventory_tab(self):
        self.notebook.select(0)

    def switch_to_purchase_tab(self):
        self.notebook.select(1)

    def switch_to_assemble_tab(self):
        self.notebook.select(2)

    def switch_to_balance_tab(self):
        self.notebook.select(3)

    def switch_to_extras_tab(self):
        self.notebook.select(4)

    def delete_item(self):
        # Get the selected item from the left or right Treeview
        selected_tab = self.notebook.nametowidget(self.notebook.select())
        if hasattr(selected_tab, "delete_item"):
            selected_tab.delete_item()

    def sell_item(self):
        # Get the selected item from the left or right Treeview
        selected_tab = self.notebook.nametowidget(self.notebook.select())
        if hasattr(selected_tab, "sell_item"):
            selected_tab.sell_item()

    def rename_item(self):
        selected_tab = self.notebook.nametowidget(self.notebook.select())
        if hasattr(selected_tab, "rename_item"):
            selected_tab.rename_item()

    def unsell_item(self):
        # Get the selected item from the right Treeview in the Balance tab
        selected_tab = self.notebook.nametowidget(self.notebook.select())
        if hasattr(selected_tab, "unsell_item"):
            selected_tab.unsell_item()

def setup_database():
    """Apply idempotent schema setup and migrations on every launch."""
    initialize_database()
    try:
        create_backup()
    except (OSError, ValueError, sqlite3.DatabaseError) as exc:
        return f"PCIMS started, but its automatic backup failed: {exc}"
    return None

if __name__ == "__main__":
    startup_warning = setup_database()

    app = PCIMS()
    if startup_warning:
        app.after(0, lambda: messagebox.showwarning("Backup Warning", startup_warning))
    app.mainloop()
