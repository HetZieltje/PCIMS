import os
import tkinter as tk
import uuid
import sqlite3
from tkinter import ttk, messagebox, simpledialog
import customtkinter as ctk
from customtkinter import *

class PCIMS(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("PCIMS")

        # Initialize SQLite database connection
        database_path = os.path.join(os.path.dirname(__file__), 'pcims_db.db')
        self.conn = sqlite3.connect(database_path)
        self.create_tables()  # Create tables if they don't exist

        # Create notebook (tabbed interface)
        self.notebook = ttk.Notebook(self)

        # Create tabs
        self.purchase_tab = PurchaseTab(self.notebook, self)
        self.assemble_tab = AssembleTab(self.notebook, self)
        self.inventory_tab = InventoryTab(self.notebook, self)
        self.balance_tab = BalanceTab(self.notebook, self)

        # Add tabs to notebook
        self.notebook.add(self.inventory_tab, text="Inventory")
        self.notebook.add(self.purchase_tab, text="Enter Purchase")
        self.notebook.add(self.assemble_tab, text="Assemble PC")
        self.notebook.add(self.balance_tab, text="Balance")

        # Pack notebook
        self.notebook.pack(expand=1, fill="both")

        ctk.set_appearance_mode("light")

    def get_sold_items(self):
        # Get the list of sold PCs from the database
        with self.conn:
            cursor = self.conn.cursor()
            cursor.execute("SELECT * FROM income")
            return [{"name": row[1], "cost": row[2], "selling_price": row[3], "profit": row[4]} for row in cursor.fetchall()]

    def switch_to_purchase_tab(self):
        # Switch to the Enter Purchases tab
        self.notebook.select(self.purchase_tab)

    def switch_to_assemble_tab(self):
        # Switch to the Assemble PC tab
        self.notebook.select(self.assemble_tab)
    
    def create_tables(self):
        # Create tables for inventory, assembled PCs, and purchases if they don't exist
        with self.conn:
            cursor = self.conn.cursor()

            # Table for inventory
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS inventory (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    type TEXT NOT NULL,
                    price REAL NOT NULL,
                    used_in TEXT
                )
            ''')

            # Table for assembled PCs
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS assembled_pcs (
                    name TEXT PRIMARY KEY,
                    price REAL NOT NULL,
                    cpu TEXT,
                    cooler TEXT,
                    motherboard TEXT,
                    ram TEXT,
                    ssd TEXT,
                    hdd TEXT,
                    gpu TEXT,
                    pc_case TEXT,
                    psu TEXT,
                    fan TEXT,
                    extra TEXT
                )
            ''')

            # Table for expenses
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS expenses (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    type TEXT NOT NULL,
                    price REAL NOT NULL
                )
            ''')

            # Table for sold PCs
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS income (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    cost REAL NOT NULL,
                    selling_price REAL NOT NULL,
                    profit REAL NOT NULL
                )
            ''')
    
    def add_item_to_inventory(self, name, item_type, price, used_in=None):
        with self.conn:
            cursor = self.conn.cursor()
            item_id = str(uuid.uuid4())
            cursor.execute(
                "INSERT INTO inventory (id, name, type, price, used_in) VALUES (?, ?, ?, ?, ?)",
                (item_id, name, item_type, price, used_in)
            )

            # Add the same item to expenses as well
            self.add_expense(item_id, name, item_type, price)

    def get_inventory_items(self):
        with self.conn:
            cursor = self.conn.cursor()
            cursor.execute("SELECT id, name, type, price, used_in FROM inventory")
            return cursor.fetchall()

    def delete_item_from_inventory(self, item_id):
        with self.conn:
            cursor = self.conn.cursor()
            cursor.execute("DELETE FROM inventory WHERE id=?", (item_id,))

        # Update the dropdowns in AssembleTab after deleting the item
        for component_type in self.assemble_tab.component_types:
            self.assemble_tab.update_dropdown(component_type, self.assemble_tab.component_entries[component_type])

    def delete_item_from_expenses(self, item_id):
        with self.conn:
            cursor = self.conn.cursor()
            cursor.execute("DELETE FROM expenses WHERE id=?", (item_id,))
        
        # Update the dropdowns in AssembleTab after deleting the item
        for component_type in self.assemble_tab.component_types:
            self.assemble_tab.update_dropdown(component_type, self.assemble_tab.component_entries[component_type])

    def assemble_pc(self, pc_name, price, components):
        with self.conn:
            cursor = self.conn.cursor()
            cursor.execute(
                "INSERT INTO assembled_pcs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (pc_name,) + (price,) + tuple(components.values())
            )

    def get_component_id_by_name(self, component_name):
        with self.conn:
            cursor = self.conn.cursor()
            cursor.execute("SELECT id FROM inventory WHERE name = ? AND used_in IS NULL LIMIT 1", (component_name,))
            result = cursor.fetchone()
            return result[0] if result else None
    
    def get_assembled_pcs(self):
        with self.conn:
            cursor = self.conn.cursor()
            cursor.execute("SELECT * FROM assembled_pcs")
            return cursor.fetchall()

    def clear_database(self):
        with self.conn:
            cursor = self.conn.cursor()

            # Clear the inventory table
            cursor.execute("DELETE FROM inventory")

            # Clear the assembled_pcs table
            cursor.execute("DELETE FROM assembled_pcs")

    def get_all_inventory_names(self):
        with self.conn:
            cursor = self.conn.cursor()
            cursor.execute("SELECT name FROM inventory")
            return [row[0] for row in cursor.fetchall()]
        
    def get_all_assembled_pc_names(self):
        pc_names = set()

        with self.conn:
            # Fetch PC names from assembled_pcs
            cursor = self.conn.cursor()
            cursor.execute("SELECT name FROM assembled_pcs WHERE name LIKE 'PC %'")
            pc_names.update(row[0] for row in cursor.fetchall())
            
            # Fetch PC names from balance
            cursor.execute("SELECT name FROM income WHERE name LIKE 'PC %'")
            pc_names.update(row[0] for row in cursor.fetchall())

        return pc_names

    def delete_assembled_pc(self, pc_name):
        with self.conn:
            cursor = self.conn.cursor()
            cursor.execute("DELETE FROM assembled_pcs WHERE name=?", (pc_name,))
            
    def on_closing(self):
        # Clear the database before closing the application
        self.clear_database()

        # Close the database connection when the application is closed
        self.conn.close()
        self.destroy()

    def update_used_in_for_component(self, pc_name, name, item_type):
        with self.conn:
            cursor = self.conn.cursor()
            # Find the first instance where used_in is not set
            cursor.execute("""
                UPDATE inventory 
                SET used_in = ? 
                WHERE name = ? AND type = ? AND used_in IS NULL 
                    AND id = (
                        SELECT id 
                        FROM inventory 
                        WHERE name = ? AND type = ? AND used_in IS NULL 
                        ORDER BY id 
                        LIMIT 1
                    );
            """, (pc_name, name, item_type, name, item_type))

        # Update the dropdowns in AssembleTab after deleting the item
        for component_type in self.assemble_tab.component_types:
            self.assemble_tab.update_dropdown(component_type, self.assemble_tab.component_entries[component_type])

    def update_used_in_for_deleted_pc(self, deleted_pc_name):
        with self.conn:
            cursor = self.conn.cursor()
            cursor.execute("""
                UPDATE inventory 
                SET used_in = NULL 
                WHERE used_in = ?;
            """, (deleted_pc_name,))

        # Update the dropdowns in AssembleTab after deleting the item
        for component_type in self.assemble_tab.component_types:
            self.assemble_tab.update_dropdown(component_type, self.assemble_tab.component_entries[component_type])

    def get_expenses(self):
        # Get the list of expenses from the database
        with self.conn:
            cursor = self.conn.cursor()
            cursor.execute("SELECT * FROM expenses")
            return [{"name": row[1], "component_type": row[2], "price": row[3]} for row in cursor.fetchall()]

    def add_expense(self, item_id, name, item_type, price):
        with self.conn:
            cursor = self.conn.cursor()
            cursor.execute(
                "INSERT INTO expenses (id, name, type, price) VALUES (?, ?, ?, ?)",
                (item_id, name, item_type, price)
            )

    def add_income(self, item_id, name, total_cost, selling_price, profit):
        with self.conn:
            cursor = self.conn.cursor()
            cursor.execute(
                "INSERT INTO income VALUES (?, ?, ?, ?, ?)",
                (item_id, name, total_cost, selling_price, profit)
            )

    def delete_components_used_in_pc(self, pc_name):
        with self.conn:
            cursor = self.conn.cursor()
            # Delete components from the inventory where used_in is the provided PC name
            cursor.execute("DELETE FROM inventory WHERE used_in=?", (pc_name,))
    
    def get_total_pc_price(self, pc_name):
        with self.conn:
            cursor = self.conn.cursor()
            cursor.execute("SELECT SUM(price) FROM inventory WHERE used_in=?", (pc_name,))
            total_price = cursor.fetchone()[0]
            return total_price if total_price is not None else 0

class InventoryTab(ctk.CTkFrame):
    def __init__(self, master, app):
        super().__init__(master)

        # Reference to the main app
        self.app = app

        self.current_purchase_items = []

        # Create a PanedWindow to hold two separate Treeviews (left and right)
        inventory_panedwindow = tk.PanedWindow(self, orient=tk.HORIZONTAL, sashwidth=5, sashrelief=tk.RAISED)
        inventory_panedwindow.pack(expand=1, fill="both")

        # Left Treeview to display the inventory (similar to the current inventory_tab)
        self.left_tree = ttk.Treeview(inventory_panedwindow, columns=("Name", "Type", "Price", "Used In"), show="headings", selectmode="browse")
        self.left_tree.heading("Name", text="Name")
        self.left_tree.heading("Type", text="Type")
        self.left_tree.heading("Price", text="Price")
        self.left_tree.heading("Used In", text="Used In")
        self.left_tree.pack(side=tk.LEFT, expand=1, fill="both")

        # Right Treeview to display the assembled PCs (similar to the current assemble_tab)
        self.right_tree = ttk.Treeview(inventory_panedwindow, columns=("Name", "Price", "CPU", "Cooler", "Motherboard", "RAM", "SSD", "HDD", "GPU", "Case", "PSU", "Fan", "Extra"), show="headings", selectmode="browse")
        self.right_tree.heading("Name", text="Name")
        self.right_tree.heading("Price", text="Price")

        component_types = ["CPU", "Cooler", "Motherboard", "RAM", "SSD", "HDD", "GPU", "Case", "PSU", "Fan", "Extra"]
        for component_type in component_types:
            self.right_tree.heading(component_type, text=component_type)

        self.component_types = component_types
        self.right_tree.pack(side=tk.RIGHT, expand=1, fill="both")

        # Add the Treeviews to the PanedWindow
        inventory_panedwindow.add(self.left_tree)
        inventory_panedwindow.add(self.right_tree)

        # Set the initial width of the left Treeview
        inventory_panedwindow.paneconfig(self.left_tree, minsize=150)

        # Left Treeview columns configuration
        self.left_tree.column("Name", minwidth=45, width=150)
        self.left_tree.column("Type", minwidth=80, width=80)
        self.left_tree.column("Price", minwidth=50, width=50)
        self.left_tree.column("Used In", minwidth=50, width=50)

        # Right Treeview columns configuration
        self.right_tree.column("Name", minwidth=45, width=45)
        self.right_tree.column("Price", minwidth=50, width=50)
        self.right_tree.column("CPU", minwidth=35, width=35)
        self.right_tree.column("Cooler", minwidth=45, width=45)
        self.right_tree.column("Motherboard", minwidth=80, width=80)
        self.right_tree.column("RAM", minwidth=35, width=35)
        self.right_tree.column("SSD", minwidth=35, width=35)
        self.right_tree.column("HDD", minwidth=35, width=35)
        self.right_tree.column("GPU", minwidth=35, width=35)
        self.right_tree.column("Case", minwidth=35, width=35)
        self.right_tree.column("PSU", minwidth=35, width=35)
        self.right_tree.column("Fan", minwidth=30, width=30)
        self.right_tree.column("Extra", minwidth=40, width=40)

        # Delete button (for the left and right Treeviews)
        self.delete_button = ctk.CTkButton(self, text="Delete", command=self.delete_item)
        self.delete_button.pack(side="left", padx=2, pady=3)

        # Add Purchase button
        self.add_purchase_button = ctk.CTkButton(self, text="Add Purchase", command=self.switch_to_purchase_tab)
        self.add_purchase_button.pack(side="left", padx=2, pady=3)

        # Assemble PC button
        self.assemble_pc_button = ctk.CTkButton(self, text="Assemble PC", command=self.switch_to_assemble_tab)
        self.assemble_pc_button.pack(side="left", padx=2, pady=3)

        # Sell button
        self.sell_button = ctk.CTkButton(self, text="Sell", command=self.sell_item)
        self.sell_button.pack(side="left", padx=2, pady=3)

        # Bind the left mouse button click to the treeview widgets
        self.left_tree.bind("<Button-1>", self.toggle_selection_left)
        self.right_tree.bind("<Button-1>", self.toggle_selection_right)

        # Load inventory upon opening the application
        self.refresh_inventory_treeview()

    def refresh_inventory_treeview(self):
        # Clear the existing items in both Treeviews
        for item in self.left_tree.get_children():
            self.left_tree.delete(item)
        for item in self.right_tree.get_children():
            self.right_tree.delete(item)

        # Get the list of inventory items and assembled PCs from the app
        inventory_items = self.app.get_inventory_items()
        assembled_pcs = self.app.get_assembled_pcs()

        # Insert each inventory item into the Treeview on the left
        for item in inventory_items:
            component_id = item[0]
            # Ensure that component_id is in used_in_dict before accessing it
            item_info = item[1], item[2], f"€{item[3]}", item[4]
            self.left_tree.insert("", tk.END, values=item_info, tags=(component_id,))

        # Insert each assembled PC into the Treeview on the right
        for pc in assembled_pcs:
            pc_info = pc[0], f"€{pc[1]}", pc[2], pc[3], pc[4], pc[5], pc[6], pc[7], pc[8], pc[9], pc[10], pc[11], pc[12]
            self.right_tree.insert("", tk.END, values=pc_info)

    def switch_to_purchase_tab(self):
        # Call the switch_to_purchase_tab function in the main app
        self.app.switch_to_purchase_tab()

    def switch_to_assemble_tab(self):
        # Call the switch_to_assemble_tab function in the main app
        self.app.switch_to_assemble_tab()
    
    def delete_item(self):
        # Get the selected item from the left or right Treeview
        selected_item_left = self.left_tree.selection()
        selected_item_right = self.right_tree.selection()

        if selected_item_left:
            # Retrieve the item ID from the tags
            item_id = self.left_tree.item(selected_item_left, 'tags')[0]

            # Get the values displayed in the Treeview for the selected item
            item_values = self.left_tree.item(selected_item_left, 'values')

            # Check if the 'Used In' column is not empty for the selected item
            used_in_pc = item_values[3]

            if used_in_pc != 'None':
                # Display an error message
                messagebox.showerror("Error", f"The item is currently in use in '{used_in_pc}' and cannot be deleted.")
            else:
                # Ask for confirmation before deleting the assembled PC
                confirm = messagebox.askyesno("Confirm Deletion", "Do you want to delete the component")
                if confirm:

                    # Delete the item from the database
                    self.app.delete_item_from_inventory(item_id)
                    self.app.delete_item_from_expenses(item_id)

                    # Refresh the inventory Treeview
                    self.refresh_inventory_treeview()
                    self.app.balance_tab.refresh_balance_tab()

        elif selected_item_right:
            # Retrieve the PC name from the right Treeview
            pc_name = self.right_tree.item(selected_item_right, 'values')[0]

            # Ask for confirmation before deleting the assembled PC
            confirm = messagebox.askyesno("Confirm Deletion", "Do you want to delete the assembled PC?")
            if confirm:
                # Delete the assembled PC from the database
                self.app.delete_assembled_pc(pc_name)

                self.app.update_used_in_for_deleted_pc(pc_name)

                # Refresh the inventory Treeview
                self.refresh_inventory_treeview()
                self.app.balance_tab.refresh_balance_tab()
    
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

    def sell_item(self):
        # Get the selected items from both left and right Treeviews
        selected_item_left = self.left_tree.selection()
        selected_item_right = self.right_tree.selection()

        if selected_item_left:
            # Sell single item
            self.sell_single_item(selected_item_left)

        elif selected_item_right:
            # Sell assembled PC
            self.sell_assembled_pc(selected_item_right)

    def sell_single_item(self, selected_item_left):
        # Retrieve the item ID and values from the selected item
        item_id = self.left_tree.item(selected_item_left, 'tags')[0]
        item_values = self.left_tree.item(selected_item_left, 'values')
        item_name = item_values[0]
        used_in_pc = item_values[3]

        if used_in_pc != 'None':
            # If the item is used in a PC, raise an error message
            messagebox.showerror("Error", f"The {item_name} is currently used in {used_in_pc} and cannot be sold.")
            return

        # Prompt the user for the selling price
        selling_price = self.get_selling_price(item_name)

        if selling_price is not None:
            # Ask for confirmation before selling the standalone item
            confirm = messagebox.askyesno("Confirm Sell", f"Do you want to sell the {item_name} for €{selling_price:.2f}?")
            if confirm:
                # Remove the item from inventory and add to income table
                self.app.delete_item_from_inventory(item_id)
                total_cost = float(item_values[2][1:])
                profit = round(selling_price - total_cost, 2)
                self.app.add_income(item_id, item_name, total_cost, selling_price, profit)

                # Refresh the inventory Treeview and balance tab
                self.refresh_inventory_treeview()
                self.app.balance_tab.refresh_balance_tab()

    def sell_assembled_pc(self, selected_item_right):
        # Retrieve the PC name from the right Treeview
        pc_info = self.right_tree.item(selected_item_right, 'values')
        pc_name = pc_info[0]

        # Prompt the user for the selling price using the existing method
        selling_price = self.get_selling_price(pc_name)

        if selling_price is not None:
            # Ask for confirmation before selling the assembled PC
            confirm = messagebox.askyesno("Confirm Sell", f"Do you want to sell {pc_name} for €{selling_price:.2f}?")
            if confirm:

                # Add the assembled PC to the income table
                item_id = str(uuid.uuid4())
                total_cost = float(pc_info[1][1:])
                profit = round(selling_price - total_cost, 2)

                # Add the income entry
                self.app.add_income(item_id, pc_name, total_cost, selling_price, profit)

                # Delete components used in the PC from the inventory
                self.app.delete_components_used_in_pc(pc_name)

                # Delete the assembled PC from the database
                self.app.delete_assembled_pc(pc_name)

                # Refresh the inventory Treeview and balance tab
                self.refresh_inventory_treeview()
                self.app.balance_tab.refresh_balance_tab()

    def get_selling_price(self, item_name):
        while True:
            try:
                # Prompt the user for the selling price
                selling_price_str = simpledialog.askstring("Selling Price", f"Enter the selling price for {item_name}:")
                if selling_price_str is None:
                    # If the user cancels the input, return None
                    return None

                # Convert the input to a float
                selling_price = float(selling_price_str)

                # Ensure the selling price is non-negative
                if selling_price < 0:
                    messagebox.showerror("Invalid Selling Price", "Selling price must be positive.")
                else:
                    # Return the valid selling price
                    return selling_price

            except ValueError:
                # Handle invalid input (non-numeric)
                messagebox.showerror("Invalid Selling Price", "Please enter a valid numeric value for the selling price.")

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
        self.refresh_balance_tab()

        # Bind the left mouse button click to the treeview widgets
        self.left_tree.bind("<Button-1>", self.toggle_selection_left)
        self.right_tree.bind("<Button-1>", self.toggle_selection_right)

    def refresh_balance_tab(self):
        # Check if the expenses tab is entirely empty
        if not self.app.get_expenses() and self.app.get_inventory_items():
            # Populate it from inventory if needed
            self.populate_expenses_from_inventory()

        # Clear the existing items in the Treeviews and update the labels
        self.left_tree.delete(*self.left_tree.get_children())
        self.right_tree.delete(*self.right_tree.get_children())

        # Get the list of expenses and sold PCs from the app
        expenses = self.app.get_expenses()
        sold_items = self.app.get_sold_items()

        # Insert each expense into the Treeview for expenses
        total_expenses = 0.0
        for expense in expenses:
            expense_info = (
                expense['name'],
                expense['component_type'],
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
        inventory_items = self.app.get_inventory_items()

        # Iterate through inventory items and add them as expenses to the database
        for item in inventory_items:
            item_id = item[0]
            name = item[1]
            component_type = item[2]
            price = item[3]
            self.app.add_expense(item_id, name, component_type, price)

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

class AssembleTab(ctk.CTkFrame):
    def __init__(self, master, app):
        super().__init__(master)
        self.app = app

        name = self.find_next_available_pc_name()

        # Entry field for the name of the assembled PC
        self.pc_name_label = ctk.CTkLabel(self, text="PC Name:")
        self.pc_name_entry = ctk.CTkEntry(self, width=325)
        self.pc_name_entry.insert(0, name)
        self.pc_name_label.grid(row=0, column=0, padx=10, pady=5, sticky=tk.W)
        self.pc_name_entry.grid(row=0, column=1, pady=5, sticky=tk.W)

        # Create labels and dropdowns for each component type
        component_types = ("CPU", "Cooler", "Motherboard", "RAM", "SSD", "HDD", "GPU", "Case", "PSU", "Fan", "Extra")
        self.component_types = component_types

        # Create a dictionary to store the component type dropdowns
        self.component_entries = {}

        for idx, component_type in enumerate(component_types):
            label = ctk.CTkLabel(self, text=f"{component_type}:")
            label.grid(row=idx + 1, column=0, padx=10, pady=5, sticky=tk.W)

            # Create a StringVar to store the selected value
            var = tk.StringVar(self)
            dropdown = ttk.Combobox(self, textvariable=var, state="readonly", width=50)
            dropdown.bind('<FocusIn>', lambda event, comp_type=component_type, dropdown=dropdown: self.update_dropdown(comp_type, dropdown))
            dropdown.grid(row=idx + 1, column=1, pady=5, sticky=tk.W)

            # Save the Combobox in the dictionary
            self.component_entries[component_type] = dropdown

            # Save the StringVar in the dictionary
            self.component_entries[f"{component_type}_var"] = var

        # Create an "Assemble" button
        assemble_button = ctk.CTkButton(self, text="Assemble", command=self.assemble_pc)
        assemble_button.grid(row=len(component_types) + 1, column=0, columnspan=2, pady=10)

        # Initialize the UUID dictionary
        self.uuid_dict = {}

        for component_type in component_types:
            self.update_dropdown(component_type, self.component_entries[component_type])

    def get_inventory_items_by_type(self, component_type):
        # Retrieve all items of the specified component type from the main app
        items = self.app.get_inventory_items()

        # Use a set to store unique item names for the specified component type
        unique_items = set()

        # Iterate through items and add unique names to the set
        for item in items:
            # Check if the "Used In" column is "None"
            if item[2] == component_type and item[4] is None:
                unique_items.add(item[1])

        return sorted(list(unique_items))  # Convert the set to a sorted list for consistent ordering
    
    def find_next_available_pc_name(self):
        # Get the list of existing PC names
        existing_pc_names = self.app.get_all_assembled_pc_names()

        # Iterate through numbers to find the first available PC name
        for i in range(1, 9999):  # You can adjust the range as needed
            pc_name = f"PC {i}"
            if pc_name not in existing_pc_names:
                return pc_name

        # Return a default name if no available name is found
        return "PC"

    def update_dropdown(self, component_type, dropdown):
        items = self.get_inventory_items_by_type(component_type)
        names = [""] + items
        # Set the updated values to the dropdown
        dropdown["values"] = names

    def clear_entry_fields(self):
        # Use the find_next_available_pc_name method to set the next available PC name
        next_pc_name = self.find_next_available_pc_name()
        self.pc_name_entry.delete(0, tk.END)
        self.pc_name_entry.insert(0, next_pc_name)

        for component_type in self.component_types:
            self.component_entries[component_type].set("")  # Clear the selection

        # Set the focus to the PC name entry field
        self.pc_name_entry.focus_set()

    def assemble_pc(self):
        pc_name = self.pc_name_entry.get()
        components = {component_type: self.component_entries[component_type].get() for component_type in self.component_types}

        # Check if at least one component is selected
        if not any(components.values()):
            messagebox.showerror("Error", "At least one component must be selected.")
            return

        # Check if the PC name is not already in use
        if pc_name in self.app.get_all_inventory_names():
            messagebox.showerror("Error", "PC name already in use. Please choose a different name.")
            return

        # Update the used_in field for each component in the assembled PC in the Inventory database
        for component_type, component_name in components.items():
            self.app.update_used_in_for_component(pc_name, component_name, component_type)

        price = round(self.app.get_total_pc_price(pc_name), 2)

        # Assemble PC in the main app
        self.app.assemble_pc(pc_name, price, components)

        # Refresh the Treeview or take any other necessary action to update the UI
        self.app.inventory_tab.refresh_inventory_treeview()

        # Update the dropdowns in AssembleTab after assembling the PC
        for component_type in self.component_types:
            self.update_dropdown(component_type, self.component_entries[component_type])

        # Clear the entry fields
        self.clear_entry_fields()

class PurchaseTab(ctk.CTkFrame):
    def __init__(self, master, app):
        super().__init__(master)

        # Reference to the main app
        self.app = app

        # List to store items in the current purchase
        self.current_purchase_items = []

        # Entry fields
        self.name_label = ctk.CTkLabel(self, text="Item Name:")
        self.name_entry = ctk.CTkEntry(self, width=310)
        self.name_label.grid(row=1, column=0, pady=5, padx=10, sticky=tk.W)
        self.name_entry.grid(row=1, column=1, pady=5, padx=10, sticky=tk.W)

        self.type_label = ctk.CTkLabel(self, text="Component Type:")
        self.type_var = tk.StringVar()
        self.type_dropdown = ctk.CTkComboBox(self, variable=self.type_var, values=[
            "", "CPU", "Cooler", "Motherboard", "RAM", "SSD", "HDD", "GPU", "Case", "PSU", "Fan", "Extra"
        ], state="readonly", width=110)  # Set state to readonly and height to the number of items
        self.type_label.grid(row=2, column=0, pady=5, padx=10, sticky=tk.W)
        self.type_dropdown.grid(row=2, column=1, pady=5, padx=10, sticky=tk.W)

        self.price_label = ctk.CTkLabel(self, text="Price (Euro):")
        self.price_entry = ctk.CTkEntry(self, validate="key", validatecommand=(self.register(self.validate_price), "%P"), width=60)  # Set width for 5 numbers + decimal
        self.price_label.grid(row=3, column=0, pady=5, padx=10, sticky=tk.W)
        self.price_entry.grid(row=3, column=1, pady=5, padx=10, sticky=tk.W)

        self.percent_label = ctk.CTkLabel(self, text="Percentage of Bundle Price:")
        self.percent_entry = ctk.CTkEntry(self, validate="key", validatecommand=(self.register(self.validate_percentage), "%P"), width=60)
        self.percent_label.grid(row=4, column=0, pady=5, padx=10, sticky=tk.W)
        self.percent_entry.grid(row=4, column=1, pady=5, padx=10, sticky=tk.W)

        self.add_item_button = ctk.CTkButton(self, text="Add Item", command=self.add_item)
        self.add_bundle_button = ctk.CTkButton(self, text="Add Bundle", command=self.add_bundle)
        self.add_item_button.grid(row=5, column=0, pady=5, padx=10, sticky=tk.W)
        self.add_bundle_button.grid(row=5, column=1, pady=10, padx=10, sticky=tk.W)

        # Listbox to display items in the current purchase
        self.current_purchase_listbox = tk.Listbox(self, selectmode=tk.MULTIPLE, exportselection=0, width=80)
        self.current_purchase_listbox.grid(row=6, column=0, columnspan=2, pady=10, padx=10, sticky=tk.W)

        # Delete Item button
        self.delete_item_button = ctk.CTkButton(self, text="Delete Item", command=self.delete_item)
        self.delete_item_button.grid(row=7, column=0, pady=5, padx=10, sticky=tk.W)

        # List to store items in the current purchase
        self.current_purchase_items = []
    
    def validate_percentage(self, new_value):
        try:
            if not new_value:
                return True  # Allow empty input
            # Attempt to convert the input to a float
            float_value = float(new_value)
            # Check if the value is within the valid range [0, 100]
            return 0 <= float_value <= 100
        except ValueError:
            return False  # Disallow non-numeric input
    
    def add_item(self):
        name = self.name_entry.get()
        component_type = self.type_var.get()

        # Check if name and component_type are not empty
        if not name or not component_type:
            # Show an error message or take any other necessary action
            messagebox.showerror("Error", "Please enter both item name and component type.")
            return

        if self.price_entry.get():
            price = float(self.price_entry.get())
            percentage = float(self.percent_entry.get()) if self.percent_entry.get() else 100.0
            # Calculate the price after applying the percentage
            price_after_percentage = self.calc_price(price, percentage)
        else:
            messagebox.showerror("Error", "Please enter a price")
            return
        
        # Add the item to the current purchases
        item = {"name": name, "component_type": component_type, "price": price_after_percentage}
        self.current_purchase_items.append(item)

        # Refresh the Listbox or take any other necessary action to update the UI
        self.refresh_purchase_listbox()

        # Clear the entry fields
        self.name_entry.delete(0, tk.END)
        self.type_dropdown.set("")  # Clear the selection
        self.percent_entry.delete(0, tk.END)

        # Disable the price entry field
        self.price_entry.configure(state="disabled")

        # Set the focus to the listbox
        self.current_purchase_listbox.focus_set()

    def calc_price(self, price, percentage):
        # Calculate the price after applying the percentage
        calculated_price = price * (percentage / 100)
        # Round off the calculated price to two decimal places
        rounded_price = round(calculated_price, 2)
        return rounded_price
    
    def add_bundle(self):
        if not self.current_purchase_items:
            # Check if the current form has valid data
            if self.validate_form():
                # Add the contents of the form directly into the inventory
                name = self.name_entry.get()
                item_type = self.type_var.get()
                price = float(self.price_entry.get())
                percentage = float(self.percent_entry.get()) if self.percent_entry.get() else 100.0  # Default to 100.0 if empty

                 # Calculate the price after applying the percentage
                price_after_percentage = self.calc_price(price, percentage)

                # Add the item to the inventory
                self.app.add_item_to_inventory(name, item_type, price_after_percentage)
        
            else:
                # Show an error message or take any other necessary action
                messagebox.showerror("Error", "Please enter valid data.")
                return

        # Check if the current purchase list has data
        if self.current_purchase_items:
            # Check if the current form has valid data
            if self.validate_form():
                # Add the contents of the form directly into the inventory
                name = self.name_entry.get()
                item_type = self.type_var.get()
                price = float(self.price_entry.get())
                percentage = float(self.percent_entry.get()) if self.percent_entry.get() else 100.0  # Default to 100.0 if empty

                # Calculate the price after applying the percentage
                price_after_percentage = self.calc_price(price, percentage)

                # Add the item to the inventory
                self.app.add_item_to_inventory(name, item_type, price_after_percentage)

            # Iterate through each purchase and add the item to the inventory
            for item in self.current_purchase_items:
                name = item['name']
                item_type = item['component_type']
                price = item['price']
                self.app.add_item_to_inventory(name, item_type, price)

        # Refresh the inventory Treeview
        self.app.inventory_tab.refresh_inventory_treeview()
        self.app.balance_tab.refresh_balance_tab()

        # Clear the current purchase list
        self.current_purchase_items = []

        # Clear the Listbox
        self.current_purchase_listbox.delete(0, tk.END)

        # Clear the entry fields
        self.name_entry.delete(0, tk.END)
        self.type_dropdown.set("")  # Clear the selection
        self.percent_entry.delete(0, tk.END)

        # Enable the price entry field
        self.price_entry.configure(state="normal")

        # Set the focus to the name entry field
        self.name_entry.focus_set()

        # Clear the price entry field
        self.price_entry.delete(0, tk.END)

        # Update the dropdowns in AssembleTab after adding the bundle
        for component_type in self.app.assemble_tab.component_types:
            self.app.assemble_tab.update_dropdown(component_type, self.app.assemble_tab.component_entries[component_type])

    def validate_form(self):
        # Validate the form (name, price, and type must be filled)
        name = self.name_entry.get()
        item_type = self.type_var.get()
        price = self.price_entry.get()

        if not name or not item_type or not price:
            return False

        return True

    def validate_price(self, new_value):
        try:
            if not new_value:
                return True  # Allow empty input

            # Attempt to convert the input to a float
            float_value = float(new_value)

            # Check if the value is non-negative and has at most 2 decimal places
            return 0 <= float_value < 10**9 and (len(new_value.split('.')[-1]) <= 2 if '.' in new_value else True)

        except ValueError:
            return False  # Disallow non-numeric input

    def delete_item(self):
        selected_item_index = self.current_purchase_listbox.curselection()

        if selected_item_index and selected_item_index[0] < len(self.current_purchase_items):
            # Remove the item from the current_purchase_items list
            del self.current_purchase_items[selected_item_index[0]]

            # Refresh the Listbox
            self.refresh_purchase_listbox()

            # If the list becomes empty, clear the price entry and enable it for editing
            if not self.current_purchase_items:
                self.price_entry.configure(state="normal")
                self.price_entry.delete(0, tk.END)

    def refresh_purchase_listbox(self):
        # Clear the existing items in the Listbox
        self.current_purchase_listbox.delete(0, tk.END)

        # Insert each purchase into the Listbox
        for item in self.current_purchase_items:
            item_info = f"{item['name']} - {item['component_type']} - {item['price']}"
            self.current_purchase_listbox.insert(tk.END, item_info)

if __name__ == "__main__":
    app = PCIMS()
    app.geometry("515x530")
    # app.protocol("WM_DELETE_WINDOW", app.on_closing)  # Bind closing event, for deleting database upon close, debugging line
    app.mainloop()
