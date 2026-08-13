import tkinter as tk
import tkinter.messagebox
import tkinter.simpledialog
import tkinter.ttk
import json
from app import ui as ctk
from app.widgets import create_scrollable_treeview
from db.queries import (delete_expenses, get_expenses, get_inventory_value,
                        get_sales, get_sold_pc_parts, rename_parts, undo_sale)

class BalanceTab(ctk.CTkFrame):
    def __init__(self, master, app):
        super().__init__(master)

        # Reference to the main app
        self.app = app

        # Create a PanedWindow to hold two separate Treeviews (left and right)
        balance_panedwindow = tk.PanedWindow(self, orient=tk.HORIZONTAL, sashwidth=5, sashrelief=tk.RAISED)
        balance_panedwindow.pack(expand=1, fill="both")

        # Left Treeview to display expenses
        left_frame, self.left_tree = create_scrollable_treeview(balance_panedwindow, columns=("Name", "Type", "Price", "Purchase Date"), show="headings", selectmode="browse", style="Custom.Treeview")
        self.left_tree.heading("Name", text="Name")
        self.left_tree.heading("Type", text="Type")
        self.left_tree.heading("Price", text="Price")
        self.left_tree.heading("Purchase Date", text="Purchase Date")

        # Right Treeview to display sold PCs
        right_frame, self.right_tree = create_scrollable_treeview(balance_panedwindow, columns=("Name", "Total Cost", "Selling Price", "Profit", "Sale Date"), show="headings", selectmode="browse", style="Custom.Treeview")
        self.right_tree.heading("Name", text="Name")
        self.right_tree.heading("Total Cost", text="Total Cost")
        self.right_tree.heading("Selling Price", text="Selling Price")
        self.right_tree.heading("Profit", text="Profit")
        self.right_tree.heading("Sale Date", text="Sale Date")

        # Add the Treeviews to the PanedWindow
        balance_panedwindow.add(left_frame)
        balance_panedwindow.add(right_frame)

        # Set the initial width of the left Treeview
        balance_panedwindow.paneconfig(left_frame, minsize=350)

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
        style = tk.ttk.Style()
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
        if self.app.is_dark_mode:
            self.left_tree.configure(style="Dark.Treeview")
            self.right_tree.configure(style="Dark.Treeview")
        else:
            self.left_tree.configure(style="Light.Treeview")
            self.right_tree.configure(style="Light.Treeview")

    def refresh(self):
        # Clear the existing items in both Treeviews
        for item in self.left_tree.get_children():
            self.left_tree.delete(item)
        for item in self.right_tree.get_children():
            self.right_tree.delete(item)

        # Get the list of expenses and sold PCs from the app
        expenses = get_expenses()
        sold_items = get_sales()

        # Insert each expense into the Treeview on the left
        total_expenses = 0.0
        for expense in expenses:
            expense_info = (
                expense['name'],
                expense['type'],
                f"€{expense['price']:.2f}",  # Format price with 2 decimals
                expense['purchase_date']
            )
            self.left_tree.insert("", tk.END, values=expense_info, tags=(json.dumps([expense['id']]),))
            total_expenses += expense['price']

        total_income = 0.0
        for sold_item in sold_items:
            sold_item_info = (
                sold_item['name'], f"€{sold_item['cost']:.2f}",
                f"€{sold_item['selling_price']:.2f}", f"€{sold_item['profit']:.2f}",
                sold_item['sale_date']
            )
            self.right_tree.insert(
                "", tk.END, values=sold_item_info,
                tags=(json.dumps([sold_item['id']]),)
            )
            total_income += sold_item['selling_price']

        # Realized profit is the sum of recorded sale profits. Unsold inventory
        # is reflected separately in total assets.
        total_profit = sum(item['profit'] for item in sold_items)

        # Calculate inventory value
        inventory_value = get_inventory_value()

        # Net assets: cash received plus remaining stock, less purchase costs.
        total_assets = total_income + inventory_value - total_expenses
        self.total_income_label.configure(text=f"Total Income: €{total_income:.2f}")
        self.total_expenses_label.configure(text=f"Total Expenses: €{total_expenses:.2f}")
        self.total_profit_label.configure(text=f"Total Profit: €{total_profit:.2f}")
        self.inventory_value_label.configure(text=f"Inventory Value: €{inventory_value:.2f}")
        self.total_assets_label.configure(text=f"Net Assets: €{total_assets:.2f}")

        self.set_treeview_style()

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
            income_id = json.loads(self.right_tree.item(selected_item, 'tags')[0])[0]
            item_values = self.right_tree.item(selected_item, 'values')
            item_name = item_values[0]
            parts_info = get_sold_pc_parts(income_id)
            if parts_info:
                parts_details = "\n".join([f"{part['name']} ({part['type']}): €{part['price']} (Purchased on {part['purchase_date']})" for part in parts_info])
                tk.messagebox.showinfo("Sold PC Parts Information", parts_details)
            else:
                tk.messagebox.showinfo("Sold Item Information", f"No parts information available for the selected item: {item_name}.")

    def unsell_item(self):
        selected_item = self.right_tree.selection()
        if selected_item:
            income_id = json.loads(self.right_tree.item(selected_item, 'tags')[0])[0]
            item_name = self.right_tree.item(selected_item, 'values')[0]
            confirm = tk.messagebox.askyesno("Confirm Unsell", "Do you want to unsell the selected item?")
            if confirm:
                try:
                    undo_sale(income_id, item_name)
                except (ValueError, LookupError) as exc:
                    tk.messagebox.showerror("Unable to Undo Sale", str(exc))
                    return
                self.refresh()

    def delete_item(self):
        selected_item = self.left_tree.selection()
        if not selected_item:
            tk.messagebox.showerror("Error", "Please select an item to delete.")
            return

        item_values = self.left_tree.item(selected_item, 'values')
        item_name = item_values[0]

        # Confirm deletion
        confirm = tk.messagebox.askyesno("Confirm Deletion", f"Do you want to delete {item_name} from expenses?")
        if confirm:
            # Get the item ID from the tags
            item_ids = json.loads(self.left_tree.item(selected_item, 'tags')[0])
            try:
                delete_expenses(item_ids)
            except (ValueError, LookupError) as exc:
                tk.messagebox.showerror("Unable to Delete Expense", str(exc))
                return

            # Refresh the Treeview
            self.refresh()

    def rename_item(self):
        selected_item = self.left_tree.selection()

        if selected_item:
            item_ids = json.loads(self.left_tree.item(selected_item, 'tags')[0])
            item_values = self.left_tree.item(selected_item, 'values')
            old_name = item_values[0]

            new_name = tk.simpledialog.askstring("Rename item", f"Enter new name for {old_name}:", initialvalue=old_name)
            if new_name:
                try:
                    rename_parts(item_ids, new_name)
                except (ValueError, LookupError) as exc:
                    tk.messagebox.showerror("Unable to Rename Expense", str(exc))
                    return
                self.refresh()
        else:
            tk.messagebox.showerror("Error", "Please select an item to rename.")
