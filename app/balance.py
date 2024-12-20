import tkinter as tk
from tkinter import ttk, messagebox
import customtkinter as ctk
from customtkinter import *
from db.queries import get_expenses, get_inventory_items, get_sales, add_expense, get_inventory_value, get_sold_pc_parts

class BalanceTab(ctk.CTkFrame):
    def __init__(self, master, app):
        super().__init__(master)

        # Reference to the main app
        self.app = app

        # Create a PanedWindow to hold two separate Treeviews (left and right)
        balance_panedwindow = tk.PanedWindow(self, orient=tk.HORIZONTAL, sashwidth=5, sashrelief=tk.RAISED)
        balance_panedwindow.pack(expand=1, fill="both")

        # Left Treeview to display expenses
        self.left_tree = ttk.Treeview(balance_panedwindow, columns=("Name", "Type", "Price", "Purchase Date"), show="headings", selectmode="browse", style="Custom.Treeview")
        self.left_tree.heading("Name", text="Name")
        self.left_tree.heading("Type", text="Type")
        self.left_tree.heading("Price", text="Price")
        self.left_tree.heading("Purchase Date", text="Purchase Date")
        self.left_tree.pack(side=tk.LEFT, expand=1, fill="both")

        # Right Treeview to display sold PCs
        self.right_tree = ttk.Treeview(balance_panedwindow, columns=("Name", "Total Cost", "Selling Price", "Profit", "Sale Date"), show="headings", selectmode="browse", style="Custom.Treeview")
        self.right_tree.heading("Name", text="Name")
        self.right_tree.heading("Total Cost", text="Total Cost")
        self.right_tree.heading("Selling Price", text="Selling Price")
        self.right_tree.heading("Profit", text="Profit")
        self.right_tree.heading("Sale Date", text="Sale Date")
        self.right_tree.pack(side=tk.RIGHT, expand=1, fill="both")

        # Add the Treeviews to the PanedWindow
        balance_panedwindow.add(self.left_tree)
        balance_panedwindow.add(self.right_tree)

        # Set the initial width of the left Treeview
        balance_panedwindow.paneconfig(self.left_tree, minsize=400)

        # Left Treeview columns configuration
        self.left_tree.column("Name", minwidth=100, width=200)
        self.left_tree.column("Type", minwidth=100, width=150)
        self.left_tree.column("Price", minwidth=100, width=150)
        self.left_tree.column("Purchase Date", minwidth=100, width=150)

        # Right Treeview columns configuration
        self.right_tree.column("Name", minwidth=100, width=200)
        self.right_tree.column("Total Cost", minwidth=100, width=150)
        self.right_tree.column("Selling Price", minwidth=100, width=150)
        self.right_tree.column("Profit", minwidth=100, width=150)
        self.right_tree.column("Sale Date", minwidth=100, width=150)

        # Frame for labels
        label_frame = ctk.CTkFrame(self)
        label_frame.pack(side=tk.BOTTOM, fill="x", padx=10, pady=5)

        # Labels for sum of expenses, total income, and profit
        self.total_income_label = ctk.CTkLabel(label_frame)
        self.total_income_label.grid(row=0, column=0, padx=10, pady=5, sticky="ew")

        self.total_expenses_label = ctk.CTkLabel(label_frame)
        self.total_expenses_label.grid(row=0, column=1, padx=10, pady=5, sticky="ew")
        
        self.total_profit_label = ctk.CTkLabel(label_frame)
        self.total_profit_label.grid(row=0, column=2, padx=10, pady=5, sticky="ew")

        self.inventory_value_label = ctk.CTkLabel(label_frame)
        self.inventory_value_label.grid(row=0, column=3, padx=10, pady=5, sticky="ew")

        self.total_assets_label = ctk.CTkLabel(label_frame)
        self.total_assets_label.grid(row=0, column=4, padx=10, pady=5, sticky="ew")

        # Configure grid columns to expand equally
        label_frame.grid_columnconfigure(0, weight=1)
        label_frame.grid_columnconfigure(1, weight=1)
        label_frame.grid_columnconfigure(2, weight=1)
        label_frame.grid_columnconfigure(3, weight=1)
        label_frame.grid_columnconfigure(4, weight=1)

        # Load expenses and sold PCs upon opening the tab
        self.refresh()

        # Bind the left mouse button click to the treeview widgets
        self.left_tree.bind("<Button-1>", self.toggle_selection_left)
        self.right_tree.bind("<Button-1>", self.toggle_selection_right)

        # Bind the left mouse button click to the treeview headers for sorting
        for col in self.left_tree["columns"]:
            self.left_tree.heading(col, text=col, command=lambda _col=col: self.sort_column(self.left_tree, _col, False))
        for col in self.right_tree["columns"]:
            self.right_tree.heading(col, text=col, command=lambda _col=col: self.sort_column(self.right_tree, _col, False))

        # Bind the double-click event to the right Treeview
        self.right_tree.bind("<Double-1>", self.show_sold_pc_parts)

        # Apply custom style for light and dark themes
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Light.Treeview", background="white", foreground="black", fieldbackground="white", highlightthickness=0, bd=0)
        style.configure("Light.Treeview.Heading", background="lightgray", foreground="black")
        style.map("Light.Treeview", background=[("selected", "lightgray")])

        style.configure("Dark.Treeview", background="#3e3e3e", foreground="white", fieldbackground="#3e3e3e", highlightthickness=0, bd=0)
        style.configure("Dark.Treeview.Heading", background="#4e4e4e", foreground="white")
        style.map("Dark.Treeview", background=[("selected", "#5e5e5e")])

        # Set initial style based on the current theme
        self.set_treeview_style()

    def set_treeview_style(self):
        style = ttk.Style()
        if self.app.is_dark_mode:
            self.left_tree.configure(style="Dark.Treeview")
            self.right_tree.configure(style="Dark.Treeview")
        else:
            self.left_tree.configure(style="Light.Treeview")
            self.right_tree.configure(style="Light.Treeview")

    def refresh(self):
        # Check if the expenses tab is entirely empty
        if not get_expenses() and get_inventory_items():
            # Populate it from inventory if needed
            self.populate_expenses_from_inventory()

        # Clear the existing items in the Treeviews and update the labels
        self.left_tree.delete(*self.left_tree.get_children())
        self.right_tree.delete(*self.right_tree.get_children())

        # Get the list of expenses and sold PCs from the app
        expenses = get_expenses()
        sold_items = get_sales()

        # Insert each expense into the Treeview for expenses
        total_expenses = 0.0
        for expense in expenses:
            expense_info = (
                expense['name'],
                expense['type'],
                f"€{expense['price']}",  # Format as Euros
                expense['purchase_date']
            )
            self.left_tree.insert("", tk.END, values=expense_info)
            total_expenses += expense['price']

        # Insert each sold PC into the Treeview for sold PCs
        total_income = 0.0
        for sold_item in sold_items:
            sold_item_info = (
                sold_item['name'],
                f"€{sold_item['cost']}",
                f"€{sold_item['selling_price']}",
                f"€{sold_item['profit']}",
                sold_item['sale_date']
            )
            self.right_tree.insert("", tk.END, values=sold_item_info)
            total_income += sold_item['selling_price']

        # Calculate total profit and update the labels with the calculated totals
        total_profit = total_income - total_expenses

        # Calculate inventory value
        inventory_value = get_inventory_value()

        # Calculate profit + inventory value
        total_assets = total_profit + inventory_value
        self.total_income_label.configure(text=f"Total Income: €{total_income:.2f}")
        self.total_expenses_label.configure(text=f"Total Expenses: €{total_expenses:.2f}")
        self.total_profit_label.configure(text=f"Total Profit: €{total_profit:.2f}")
        self.inventory_value_label.configure(text=f"Inventory Value: €{inventory_value:.2f}")
        self.total_assets_label.configure(text=f"Profit + Inventory Value: €{total_assets:.2f}")

        self.set_treeview_style()

    def populate_expenses_from_inventory(self):
        # Get the list of inventory items from the main app
        inventory_items = get_inventory_items()

        # Iterate through inventory items and add them as expenses to the database
        for item in inventory_items:
            item_id = item[0]
            name = item[1]
            component_type = item[2]
            price = item[3]
            add_expense(item_id, name, component_type, price)

    def toggle_selection_left(self, event):
        clicked_item = self.left_tree.identify('item', event.x, event.y)
        if clicked_item:
            # Deselect any item in the right treeview
            for item in self.right_tree.selection():
                self.right_tree.selection_remove(item)
            if clicked_item in self.left_tree.selection():
                self.left_tree.selection_remove(clicked_item)
            else:
                self.left_tree.selection_set(clicked_item)

    def toggle_selection_right(self, event):
        clicked_item = self.right_tree.identify('item', event.x, event.y)
        if clicked_item:
            # Deselect any item in the left treeview
            for item in self.left_tree.selection():
                self.left_tree.selection_remove(item)
            if clicked_item in self.right_tree.selection():
                self.right_tree.selection_remove(clicked_item)
            else:
                self.right_tree.selection_set(clicked_item)

    def sort_column(self, treeview, col, reverse):
        # Get the list of items in the specified column
        items = [(self.convert_to_number(treeview.set(k, col)), k) for k in treeview.get_children('')]

        # Sort the items based on the column values
        items.sort(key=lambda x: (isinstance(x[0], str), x[0].lower() if isinstance(x[0], str) else x[0]), reverse=reverse)

        # Rearrange the items in the Treeview
        for index, (val, k) in enumerate(items):
            treeview.move(k, '', index)

        # Reverse the sorting order for the next click
        treeview.heading(col, command=lambda: self.sort_column(treeview, col, not reverse))

    def convert_to_number(self, value):
        # Check if the value starts with "€" and try to convert it to a float
        if value.startswith("€"):
            try:
                return float(value[1:])
            except ValueError:
                return value
            
        return value

    def show_sold_pc_parts(self, event):
        selected_item = self.right_tree.selection()
        if selected_item:
            income_id = self.right_tree.item(selected_item, 'values')[0]
            parts_info = get_sold_pc_parts(income_id)
            if parts_info:
                parts_details = "\n".join([f"{part['name']} ({part['type']}): €{part['price']} (Purchased on {part['purchase_date']})" for part in parts_info])
                messagebox.showinfo("Sold PC Parts Information", parts_details)
            else:
                messagebox.showinfo("Sold PC Parts Information", "No parts information found for the selected PC.")
