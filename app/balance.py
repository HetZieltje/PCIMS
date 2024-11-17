import tkinter as tk
from tkinter import ttk
import customtkinter as ctk
from customtkinter import *
from db.queries import *

class BalanceTab(ctk.CTkFrame):
    def __init__(self, master, app):
        super().__init__(master)

        # Reference to the main app
        self.app = app

        # Left Treeview
        self.left_tree = ttk.Treeview(self, columns=("Name", "Component Type", "Price"), show="headings", selectmode="browse")
        self.left_tree.heading("Name", text="Name")
        self.left_tree.heading("Component Type", text="Component Type")
        self.left_tree.heading("Price", text="Price")
        self.left_tree.pack(side=tk.LEFT, padx=10, pady=10, fill=tk.BOTH, expand=1)

        # Right Treeview
        self.right_tree = ttk.Treeview(self, columns=("Name", "Total Cost", "Selling Price", "Profit"), show="headings", selectmode="browse")
        self.right_tree.heading("Name", text="Name")
        self.right_tree.heading("Total Cost", text="Total Cost")
        self.right_tree.heading("Selling Price", text="Selling Price")
        self.right_tree.heading("Profit", text="Profit")
        self.right_tree.pack(side=tk.RIGHT, padx=10, pady=10, fill=tk.BOTH, expand=1)

        # Left Treeview columns configuration
        self.left_tree.column("Name", minwidth=45, width=150)
        self.left_tree.column("Component Type", minwidth=100, width=100)
        self.left_tree.column("Price", minwidth=50, width=50)

        # Right Treeview columns configuration
        self.right_tree.column("Name", minwidth=45, width=45)
        self.right_tree.column("Total Cost", minwidth=70, width=70)
        self.right_tree.column("Selling Price", minwidth=80, width=80)
        self.right_tree.column("Profit", minwidth=50, width=50)

        # Labels for sum of expenses, total income, and profit
        self.total_income_label = ctk.CTkLabel(self)
        self.total_income_label.pack(side=tk.TOP, padx=10, pady=(630, 5))

        self.total_expenses_label = ctk.CTkLabel(self)
        self.total_expenses_label.pack(side=tk.TOP, padx=10, pady=5)
        
        self.total_profit_label = ctk.CTkLabel(self)
        self.total_profit_label.pack(side=tk.TOP, padx=10, pady=5)
        
        # Load expenses and sold PCs upon opening the tab
        self.refresh()

        # Bind the left mouse button click to the treeview widgets
        self.left_tree.bind("<Button-1>", self.toggle_selection_left)
        self.right_tree.bind("<Button-1>", self.toggle_selection_right)

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
        sold_items = get_sold_items()

        # Insert each expense into the Treeview for expenses
        total_expenses = 0.0
        for expense in expenses:
            expense_info = (
                expense['name'],
                expense['type'],
                f"€{expense['price']}"  # Format as Euros
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
                f"€{sold_item['profit']}"
            )
            self.right_tree.insert("", tk.END, values=sold_item_info)
            total_income += sold_item['selling_price']

        # Calculate total profit and update the labels with the calculated totals
        total_profit = total_income - total_expenses
        self.total_income_label.configure(text=f"Total Income: €{total_income:.2f}")
        self.total_expenses_label.configure(text=f"Total Expenses: €{total_expenses:.2f}")
        self.total_profit_label.configure(text=f"Total Profit: €{total_profit:.2f}")

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
