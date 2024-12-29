import tkinter as tk
from tkinter import messagebox, simpledialog, StringVar, Listbox, ttk
from datetime import datetime
from tkcalendar import DateEntry
import customtkinter as ctk
from customtkinter import *
from db.queries import add_expense, get_inventory_items, delete_item_from_inventory, add_income, get_purchase_date, get_expenses

class AutocompleteEntry(ctk.CTkEntry):
    def __init__(self, master, suggestions, *args, **kwargs):
        self.var = StringVar()
        kwargs['textvariable'] = self.var
        super().__init__(master, *args, **kwargs)
        self.suggestions = suggestions
        self.var.trace_add("write", self.changed)
        self.bind("<Right>", self.selection)
        self.bind("<Up>", self.move_up)
        self.bind("<Down>", self.move_down)
        self.listbox_up = False
        self.listbox = None

    def changed(self, *args):
        if self.var.get() == "":
            if self.listbox_up:
                self.listbox.destroy()
                self.listbox_up = False
        else:
            words = self.comparison()
            if words:
                if not self.listbox_up:
                    self.listbox = Listbox(self.master, width=min(self.winfo_width(), 50), bg="#3e3e3e" if self.master.app.is_dark_mode else "white", fg="white" if self.master.app.is_dark_mode else "black")
                    self.listbox.bind("<Button-1>", self.selection)
                    self.listbox.bind("<Right>", self.selection)
                    self.listbox.place(x=self.winfo_x(), y=self.winfo_y() + self.winfo_height())
                    self.listbox_up = True

                self.listbox.delete(0, tk.END)
                for w in words:
                    self.listbox.insert(tk.END, w)
            else:
                if self.listbox_up:
                    self.listbox.destroy()
                    self.listbox_up = False

    def selection(self, event):
        if self.listbox_up:
            self.var.set(self.listbox.get(tk.ACTIVE))
            self.listbox.destroy()
            self.listbox_up = False
            self.icursor(tk.END)

    def move_up(self, event):
        if self.listbox_up:
            if self.listbox.curselection() == ():
                index = '0'
            else:
                index = self.listbox.curselection()[0]
            if index != '0':
                self.listbox.selection_clear(first=index)
                index = str(int(index) - 1)
                self.listbox.selection_set(first=index)
                self.listbox.activate(index)

    def move_down(self, event):
        if self.listbox_up:
            if self.listbox.curselection() == ():
                index = '0'
            else:
                index = self.listbox.curselection()[0]
            if index != tk.END:
                self.listbox.selection_clear(first=index)
                index = str(int(index) + 1)
                self.listbox.selection_set(first=index)
                self.listbox.activate(index)

    def comparison(self):
        pattern = self.var.get().lower()
        return [w for w in self.suggestions if w.lower().startswith(pattern)]

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
        self.name_label = ctk.CTkLabel(self, text="Extra Name:")
        self.name_entry = AutocompleteEntry(self, self.existing_extras, width=300)
        self.name_label.grid(row=0, column=0, pady=5, padx=10, sticky=tk.W)
        self.name_entry.grid(row=0, column=1, pady=5, padx=10, sticky=tk.W)

        self.price_label = ctk.CTkLabel(self, text="Price (Euro):")
        self.price_entry = ctk.CTkEntry(self, validate="key", validatecommand=(self.register(self.validate_price), "%P"), width=150)
        self.price_label.grid(row=1, column=0, pady=5, padx=10, sticky=tk.W)
        self.price_entry.grid(row=1, column=1, pady=5, padx=10, sticky=tk.W)

        self.quantity_label = ctk.CTkLabel(self, text="Quantity:")
        self.quantity_entry = ctk.CTkEntry(self, validate="key", validatecommand=(self.register(self.validate_quantity), "%P"), width=50)
        self.quantity_entry.insert(0, "1")  # Default quantity to 1
        self.quantity_label.grid(row=2, column=0, pady=5, padx=10, sticky=tk.W)
        self.quantity_entry.grid(row=2, column=1, pady=5, padx=(10, 0), sticky=tk.W)

        self.quantity_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.quantity_frame.grid(row=2, column=1, pady=5, padx=(60, 10), sticky=tk.W)
        self.quantity_minus_button = ctk.CTkButton(self.quantity_frame, text="-", width=20, command=self.decrease_quantity)
        self.quantity_minus_button.pack(side=tk.LEFT)
        self.quantity_plus_button = ctk.CTkButton(self.quantity_frame, text="+", width=20, command=self.increase_quantity)
        self.quantity_plus_button.pack(side=tk.LEFT)

        self.price_switch_var = tk.StringVar(value="total")
        
        self.price_switch_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.price_switch_frame.grid(row=3, column=0, columnspan=2, pady=5, padx=10, sticky=tk.W)

        self.total_price_label = ctk.CTkLabel(self.price_switch_frame, text="Total Price")
        self.total_price_label.pack(side=tk.LEFT, padx=(0, 10))

        self.price_switch = ctk.CTkSwitch(self.price_switch_frame, text="Price per Item", variable=self.price_switch_var, onvalue="per_item", offvalue="total", command=self.toggle_price_mode)
        self.price_switch.pack(side=tk.RIGHT)

        self.date_label = ctk.CTkLabel(self, text="Purchase Date:")
        self.date_entry = DateEntry(self, width=15, background='darkblue', foreground='white', borderwidth=2, date_pattern='yyyy-mm-dd')
        self.date_label.grid(row=4, column=0, pady=5, padx=10, sticky=tk.W)
        self.date_entry.grid(row=4, column=1, pady=5, padx=10, sticky=tk.W)

        self.add_extra_button = ctk.CTkButton(self, text="Add New Extra", command=self.add_extra)
        self.add_extra_button.grid(row=5, column=0, pady=5, padx=10, sticky=tk.W)

        # Treeview to display extras
        self.extras_tree = ttk.Treeview(self, columns=("Name", "Price", "Quantity", "Used In"), show="headings", selectmode="browse", style="Custom.Treeview")
        self.extras_tree.heading("Name", text="Name")
        self.extras_tree.heading("Price", text="Price")
        self.extras_tree.heading("Quantity", text="Quantity")
        self.extras_tree.heading("Used In", text="Used In")
        self.extras_tree.grid(row=0, column=2, rowspan=6, pady=10, padx=10, sticky=tk.NS)

        # Load extras upon opening the tab
        self.refresh()

    def get_all_extras(self):
        expenses = get_expenses()
        return sorted(set(expense['name'] for expense in expenses if expense['type'] == 'Extra'))

    def add_extra(self):
        name = self.name_entry.get()
        if not name:
            messagebox.showerror("Error", "Please enter the extra name.")
            return

        if self.price_entry.get():
            price = float(self.price_entry.get().replace(',', '.').replace(' ', '.'))
        else:
            messagebox.showerror("Error", "Please enter a price.")
            return

        if self.quantity_entry.get():
            quantity = int(self.quantity_entry.get())
        else:
            messagebox.showerror("Error", "Please enter a quantity.")
            return

        if self.price_switch_var.get() == "total":
            price_per_item = price / quantity
        else:
            price_per_item = price

        purchase_date = self.date_entry.get_date()
        for _ in range(quantity):
            add_expense(name, "Extra", price_per_item, purchase_date)
        self.refresh()

        # Reset the entry fields
        self.name_entry.delete(0, tk.END)
        self.price_entry.delete(0, tk.END)
        self.quantity_entry.delete(0, tk.END)
        self.quantity_entry.insert(0, "1")  # Reset quantity to 1
        self.date_entry.set_date(datetime.today())

    def delete_extra(self):
        selected_item = self.extras_tree.selection()
        if not selected_item:
            messagebox.showerror("Error", "Please select an extra to delete.")
            return

        item_id = int(self.extras_tree.item(selected_item, 'tags')[0])
        item_values = self.extras_tree.item(selected_item, 'values')
        used_in_pc = item_values[3]

        if used_in_pc != 'None':
            # If the extra is used in a PC, raise an error message
            messagebox.showerror("Error", f"The {item_values[0]} is currently used in {used_in_pc} and cannot be deleted.")
            return

        # Ask for confirmation before deleting the item
        confirm = messagebox.askyesno("Confirm Deletion", "Do you want to remove the item from inventory?")
        if confirm:
            delete_item_from_inventory(item_id)
            self.refresh()

    def sell_extra(self):
        selected_item = self.extras_tree.selection()
        if not selected_item:
            messagebox.showerror("Error", "Please select an extra to sell.")
            return

        item_id = int(self.extras_tree.item(selected_item, 'tags')[0])
        item_values = self.extras_tree.item(selected_item, 'values')
        item_name = item_values[0]
        used_in_pc = item_values[3]

        if used_in_pc != 'None':
            # If the extra is used in a PC, raise an error message
            messagebox.showerror("Error", f"The {item_name} is currently used in {used_in_pc} and cannot be sold.")
            return

        # Prompt the user for the selling price
        selling_price = self.get_selling_price(item_name)

        if selling_price is not None:
            # Prompt the user for the sale date
            sale_date = self.get_sale_date(item_name)
            if sale_date is None:
                return

            # Check if the sale date is before the purchase date
            purchase_date_str = get_purchase_date(item_id)
            if not purchase_date_str:
                messagebox.showerror("Error", f"Purchase date not found for item ID {item_id}.")
                return

            purchase_date = datetime.strptime(purchase_date_str, "%Y-%m-%d").date()
            if sale_date < purchase_date:
                messagebox.showerror("Error", f"The sale date cannot be before the purchase date ({purchase_date}).")
                return

            # Ask for confirmation before selling the item
            confirm = messagebox.askyesno("Confirm Sell", f"Do you want to sell the {item_name} for €{selling_price:.2f}?")
            if confirm:
                delete_item_from_inventory(item_id)
                total_cost = float(item_values[1][1:])
                profit = round(selling_price - total_cost, 2)
                add_income(item_name, total_cost, selling_price, profit, sale_date)
                self.refresh()

    def get_selling_price(self, item_name):
        while True:
            try:
                selling_price_str = simpledialog.askstring("Selling Price", f"Enter the selling price for {item_name}:")
                if selling_price_str is None:
                    return None

                normalized_price_str = selling_price_str.replace(',', '.').replace(' ', '')
                if not normalized_price_str.replace('.', '').isdigit() or normalized_price_str.count('.') > 1:
                    raise ValueError("Price must be numeric.")

                selling_price = float(normalized_price_str)
                if '.' in normalized_price_str and len(normalized_price_str.split('.')[-1]) > 2:
                    raise ValueError("Price must have up to 2 decimal places.")
                if selling_price < 0:
                    raise ValueError("Selling price must be positive.")
                if selling_price >= 10**5:
                    raise ValueError("Selling price must be less than 100,000.")

                return selling_price

            except ValueError as e:
                messagebox.showerror("Invalid Selling Price", str(e))

    def get_sale_date(self, item_name):
        sale_date_popup = tk.Toplevel(self)
        sale_date_popup.title("Select Sale Date")

        sale_date_popup.update_idletasks()
        width = 300
        height = 200
        x = (sale_date_popup.winfo_screenwidth() // 2) - (width // 2)
        y = (sale_date_popup.winfo_screenheight() // 2) - (height // 2)
        sale_date_popup.geometry(f'{width}x{height}+{x}+{y}')

        tk.Label(sale_date_popup, text=f"Select sale date for {item_name}:").pack(pady=10)
        sale_date_entry = DateEntry(sale_date_popup, width=12, background='darkblue', foreground='white', borderwidth=2, date_pattern='yyyy-mm-dd')
        sale_date_entry.pack(pady=10)

        def on_confirm():
            self.sale_date = sale_date_entry.get_date()
            sale_date_popup.destroy()

        def on_cancel():
            self.sale_date = None
            sale_date_popup.destroy()

        tk.Button(sale_date_popup, text="Confirm", command=on_confirm).pack(side=tk.LEFT, padx=10, pady=10)
        tk.Button(sale_date_popup, text="Cancel", command=on_cancel).pack(side=tk.RIGHT, padx=10, pady=10)

        sale_date_popup.transient(self)
        sale_date_popup.grab_set()
        self.wait_window(sale_date_popup)

        return self.sale_date

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
            avg_price = round(data["total_price"] / data["quantity"], 2)
            extra_info = (name, f"€{avg_price}", data["quantity"], used_in)
            self.extras_tree.insert("", tk.END, values=extra_info, tags=(data["ids"][0],))

        self.existing_extras = self.get_all_extras()
        self.name_entry.suggestions = self.existing_extras
        self.set_treeview_style()

    def set_treeview_style(self):
        style = ttk.Style()
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

    def delete_item(self):
        self.delete_extra()

    def sell_item(self):
        self.sell_extra()
