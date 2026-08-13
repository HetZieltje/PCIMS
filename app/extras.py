import tkinter as tk
import tkinter.messagebox
import tkinter.simpledialog
import tkinter.ttk
import tkinter.filedialog
import sqlite3
from datetime import datetime
from app.calendar import DateEntry
from app import ui as ctk
from app.money import allocate_total
from db.queries import (add_expenses, get_expenses, get_inventory_items,
                        get_purchase_date, rename_parts, sell_inventory_items)
from db.backup import create_backup, restore_backup
from db.connection import get_database_path
from app.dialogs import ask_sale_date, ask_selling_price
from app.widgets import AutocompleteEntry, create_scrollable_treeview
import json

class ExtrasTab(ctk.CTkFrame):
    def __init__(self, master, app):
        super().__init__(master)

        # Reference to the main app
        self.app = app

        # Initialize sale_date attribute
        self.sale_date = None

        # Get all unique extra names from expenses
        self.existing_extras = self.get_all_extras()

        # Entry fields
        self.form_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.form_frame.grid(row=0, column=0, rowspan=6, pady=10, padx=10, sticky=tk.NW)

        self.name_label = ctk.CTkLabel(self.form_frame, text="Name:")
        self.name_entry = AutocompleteEntry(self.form_frame, self.existing_extras, self.app, width=300)
        self.name_label.grid(row=0, column=0, pady=5, padx=10, sticky=tk.W)
        self.name_entry.grid(row=0, column=1, pady=5, padx=10, sticky=tk.W)

        self.price_label = ctk.CTkLabel(self.form_frame, text="Total Price (Euro):")
        self.price_entry = ctk.CTkEntry(self.form_frame, validate="key", validatecommand=(self.register(self.validate_price), "%P"), width=150)
        self.price_label.grid(row=1, column=0, pady=5, padx=10, sticky=tk.W)
        self.price_entry.grid(row=1, column=1, pady=5, padx=10, sticky=tk.W)

        self.quantity_label = ctk.CTkLabel(self.form_frame, text="Quantity:")
        self.quantity_entry = ctk.CTkEntry(self.form_frame, validate="key", validatecommand=(self.register(self.validate_quantity), "%P"), width=50)
        self.quantity_entry.insert(0, "1")  # Default quantity to 1
        self.quantity_label.grid(row=2, column=0, pady=5, padx=10, sticky=tk.W)
        self.quantity_entry.grid(row=2, column=1, pady=5, padx=(10, 0), sticky=tk.W)

        self.quantity_frame = ctk.CTkFrame(self.form_frame, fg_color="transparent")
        self.quantity_frame.grid(row=2, column=1, pady=5, padx=(80, 10), sticky=tk.W)
        self.quantity_minus_button = ctk.CTkButton(self.quantity_frame, text="-", width=20, command=self.decrease_quantity)
        self.quantity_minus_button.pack(side=tk.LEFT, padx=(0, 5))
        self.quantity_plus_button = ctk.CTkButton(self.quantity_frame, text="+", width=20, command=self.increase_quantity)
        self.quantity_plus_button.pack(side=tk.LEFT)

        self.price_switch_var = tk.StringVar(value="total")

        self.price_switch_frame = ctk.CTkFrame(self.form_frame, fg_color="transparent")
        self.price_switch_frame.grid(row=3, column=0, columnspan=2, pady=5, padx=10, sticky=tk.W)

        self.total_price_label = ctk.CTkLabel(self.price_switch_frame, text="Total Price")
        self.total_price_label.pack(side=tk.LEFT, padx=(0, 10))

        self.price_switch = ctk.CTkSwitch(self.price_switch_frame, text="Price per Item", variable=self.price_switch_var, onvalue="per_item", offvalue="total", command=self.toggle_price_mode)
        self.price_switch.pack(side=tk.RIGHT)

        self.date_label = ctk.CTkLabel(self.form_frame, text="Purchase Date:")
        self.date_entry = DateEntry(self.form_frame, width=15, background='darkblue', foreground='white', borderwidth=2, date_pattern='yyyy-mm-dd')
        self.date_label.grid(row=4, column=0, pady=5, padx=10, sticky=tk.W)
        self.date_entry.grid(row=4, column=1, pady=5, padx=10, sticky=tk.W)

        self.add_extra_button = ctk.CTkButton(self.form_frame, text="Add New Extra", command=self.add_extra)
        self.add_extra_button.grid(row=5, column=0, pady=5, padx=10, sticky=tk.W)

        self.backup_button = ctk.CTkButton(self.form_frame, text="Backup Database", command=self.backup_database)
        self.backup_button.grid(row=6, column=0, pady=5, padx=10, sticky=tk.W)

        self.restore_button = ctk.CTkButton(self.form_frame, text="Restore Backup", command=self.restore_database)
        self.restore_button.grid(row=6, column=1, pady=5, padx=10, sticky=tk.W)

        # Treeview to display extras
        self.extras_tree_frame, self.extras_tree = create_scrollable_treeview(
            self, columns=("Name", "Price", "Quantity", "Used In"),
            show="headings", selectmode="browse", style="Custom.Treeview",
        )
        self.extras_tree.heading("Name", text="Name")
        self.extras_tree.heading("Price", text="Price")
        self.extras_tree.heading("Quantity", text="Quantity")
        self.extras_tree.heading("Used In", text="Used In")
        self.extras_tree_frame.grid(row=0, column=1, rowspan=7, pady=10, padx=10, sticky=tk.NSEW)

        # Configure the grid to make the Treeview fill the right side
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        # Load extras upon opening the tab
        self.refresh()

    def get_all_extras(self):
        expenses = get_expenses()
        return sorted(set(expense['name'] for expense in expenses if expense['type'] == 'Extra'))

    def add_extra(self):
        name = self.name_entry.get()
        if not name:
            tk.messagebox.showerror("Error", "Please enter the extra name.")
            return

        if self.price_entry.get():
            price = round(float(self.price_entry.get().replace(',', '.').replace(' ', '.')), 2)
        else:
            tk.messagebox.showerror("Error", "Please enter a price.")
            return

        if self.quantity_entry.get():
            quantity = int(self.quantity_entry.get())
        else:
            tk.messagebox.showerror("Error", "Please enter a quantity.")
            return

        if self.price_switch_var.get() == "total":
            item_prices = allocate_total(price, [1] * quantity)
        else:
            item_prices = [price] * quantity

        try:
            purchase_date = self.date_entry.get_date()
        except ValueError as exc:
            tk.messagebox.showerror("Invalid Purchase Date", str(exc), parent=self)
            return
        try:
            add_expenses([
                {"name": name, "component_type": "Extra", "price": item_price,
                 "purchase_date": purchase_date}
                for item_price in item_prices
            ])
        except ValueError as exc:
            tk.messagebox.showerror("Unable to Add Extras", str(exc))
            return
        self.refresh()

        # Reset the entry fields
        self.name_entry.delete(0, tk.END)
        self.price_entry.delete(0, tk.END)
        self.quantity_entry.delete(0, tk.END)
        self.quantity_entry.insert(0, "1")  # Reset quantity to 1
        self.date_entry.set_date(datetime.today())

    def sell_extra(self):
        selected_item = self.extras_tree.selection()
        if not selected_item:
            tk.messagebox.showerror("Error", "Please select an extra to sell.")
            return

        item_ids = json.loads(self.extras_tree.item(selected_item, 'tags')[0])
        item_values = self.extras_tree.item(selected_item, 'values')
        item_name = item_values[0]
        used_in_pc = item_values[3]

        if used_in_pc != 'None':
            # If the extra is used in a PC, raise an error message
            tk.messagebox.showerror("Error", f"The {item_name} is currently used in {used_in_pc} and cannot be sold.")
            return

        # Ask how many items to sell if there are more than one
        quantity_to_sell = 1
        if int(item_values[2]) > 1:
            quantity_to_sell = tk.simpledialog.askinteger("Quantity", f"Enter the quantity of {item_name} to sell (1-{item_values[2]}):", minvalue=1, maxvalue=int(item_values[2]))
            if quantity_to_sell is None:
                return

        # Prompt the user for the total selling price
        total_selling_price = self.get_selling_price(item_name)
        if total_selling_price is None:
            return

        # Prompt the user for the sale date
        sale_date = self.get_sale_date(item_name)
        if sale_date is None:
            return

        # Check if the sale date is before the purchase date for any item
        for item_id in item_ids[:quantity_to_sell]:
            purchase_date_str = get_purchase_date(item_id)
            if not purchase_date_str:
                tk.messagebox.showerror("Error", f"Purchase date not found for item ID {item_id}.")
                return

            purchase_date = datetime.strptime(purchase_date_str, "%Y-%m-%d").date()
            if sale_date < purchase_date:
                tk.messagebox.showerror("Error", f"The sale date cannot be before the purchase date ({purchase_date}).")
                return

        # Ask for confirmation before selling the item
        confirm = tk.messagebox.askyesno("Confirm Sell", f"Do you want to sell {quantity_to_sell} of {item_name} for €{total_selling_price:.2f}?")
        if confirm:
            try:
                sell_inventory_items(item_ids[:quantity_to_sell], total_selling_price, sale_date)
            except (ValueError, LookupError) as exc:
                tk.messagebox.showerror("Unable to Sell Extras", str(exc))
                return

            # Refresh the inventory Treeview and balance tab
            self.refresh()

    def get_selling_price(self, item_name):
        return ask_selling_price(self, item_name)

    def get_sale_date(self, item_name):
        return ask_sale_date(self, item_name)

    def backup_database(self):
        try:
            backup_path = create_backup()
        except (OSError, ValueError, sqlite3.DatabaseError) as exc:
            tk.messagebox.showerror("Backup Failed", str(exc), parent=self)
            return
        tk.messagebox.showinfo("Backup Complete", f"Backup saved to:\n{backup_path}", parent=self)

    def restore_database(self):
        backup_directory = get_database_path().parent / "backups"
        selected = tk.filedialog.askopenfilename(
            parent=self,
            title="Select a PCIMS backup",
            initialdir=str(backup_directory),
            filetypes=(("SQLite databases", "*.db"), ("All files", "*.*")),
        )
        if not selected:
            return
        confirmed = tk.messagebox.askyesno(
            "Restore Database",
            "Replace the current database with this backup?\n\n"
            "A safety backup of the current data will be created first.",
            parent=self,
        )
        if not confirmed:
            return
        try:
            safety_backup = restore_backup(selected)
        except (OSError, ValueError, sqlite3.DatabaseError) as exc:
            tk.messagebox.showerror("Restore Failed", str(exc), parent=self)
            return
        self.app.refresh_all_tabs()
        tk.messagebox.showinfo(
            "Restore Complete",
            f"The backup was restored.\n\nPrevious data was saved to:\n{safety_backup}",
            parent=self,
        )

    def validate_price(self, new_value):
        if not new_value:
            return True

        new_value = new_value.replace(",", ".").replace(" ", ".")

        try:
            float_value = float(new_value)
            return 0 <= float_value < 10**5 and (len(new_value.split('.')[-1]) <= 2 if '.' in new_value else True)
        except ValueError:
            return False

    def validate_quantity(self, new_value):
        if not new_value:
            return True

        try:
            int_value = int(new_value)
            return int_value > 0
        except ValueError:
            return False

    def toggle_price_mode(self):
        if self.price_switch_var.get() == "per_item":
            self.price_label.configure(text="Price per Item (Euro):")
        else:
            self.price_label.configure(text="Total Price (Euro):")

    def increase_quantity(self):
        current_quantity = int(self.quantity_entry.get() or 0)
        self.quantity_entry.delete(0, tk.END)
        self.quantity_entry.insert(0, str(current_quantity + 1))

    def decrease_quantity(self):
        current_quantity = int(self.quantity_entry.get() or 0)
        if current_quantity > 1:
            self.quantity_entry.delete(0, tk.END)
            self.quantity_entry.insert(0, str(current_quantity - 1))

    def refresh(self):
        self.extras_tree.delete(*self.extras_tree.get_children())
        extras = get_inventory_items()

        combined_extras = {}
        for extra in extras:
            if extra[2] == "Extra":
                key = (extra[1], extra[4])
                if key not in combined_extras:
                    combined_extras[key] = {"total_price": 0, "quantity": 0, "ids": []}
                combined_extras[key]["total_price"] += extra[3]
                combined_extras[key]["quantity"] += 1
                combined_extras[key]["ids"].append(extra[0])

        for (name, used_in), data in combined_extras.items():
            avg_price = round(data["total_price"] / data["quantity"], 2)  # Round to 2 decimals
            extra_info = (name, f"€{avg_price:.2f}", data["quantity"], used_in)  # Format price with 2 decimals
            self.extras_tree.insert("", tk.END, values=extra_info, tags=(json.dumps(data["ids"]),))

        self.existing_extras = self.get_all_extras()
        self.name_entry.set_suggestions(self.existing_extras)
        self.set_treeview_style()

    def set_treeview_style(self):
        style = tk.ttk.Style()
        style.theme_use("clam")
        if self.app.is_dark_mode:
            style.configure("Dark.Treeview", background="#3e3e3e", foreground="white", fieldbackground="#3e3e3e", highlightthickness=0, bd=0)
            style.configure("Dark.Treeview.Heading", background="#4e4e4e", foreground="white")
            style.map("Dark.Treeview", background=[("selected", "#5e5e5e")])
            self.extras_tree.configure(style="Dark.Treeview")
        else:
            style.configure("Light.Treeview", background="white", foreground="black", fieldbackground="white", highlightthickness=0, bd=0)
            style.configure("Light.Treeview.Heading", background="lightgray", foreground="black")
            style.map("Light.Treeview", background=[("selected", "lightgray")])
            self.extras_tree.configure(style="Light.Treeview")

    def sell_item(self):
        self.sell_extra()

    def rename_extra(self):
        selected_item = self.extras_tree.selection()

        if selected_item:
            item_ids = json.loads(self.extras_tree.item(selected_item, 'tags')[0])
            item_values = self.extras_tree.item(selected_item, 'values')
            old_name = item_values[0]

            new_name = tk.simpledialog.askstring("Rename Extra", f"Enter new name for {old_name}:", initialvalue=old_name)
            if new_name:
                try:
                    rename_parts(item_ids, new_name)
                except (ValueError, LookupError) as exc:
                    tk.messagebox.showerror("Unable to Rename Extra", str(exc))
                    return
                self.refresh()
        else:
            tk.messagebox.showerror("Error", "Please select an item to rename.")

    def rename_item(self):
        self.rename_extra()
