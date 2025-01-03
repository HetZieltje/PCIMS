import tkinter as tk
from datetime import datetime
import json
from tkcalendar import DateEntry
import customtkinter as ctk
from db.queries import get_inventory_items, get_assembled_pcs, delete_item_from_inventory, delete_expense, delete_assembled_pc, add_income, delete_components_used_in_pc, get_purchase_date, add_sold_pc, get_item_cost, update_used_in_for_deleted_pc, rename_part, rename_pc

class InventoryTab(ctk.CTkFrame):
    def __init__(self, master, app):
        super().__init__(master)

        # Reference to the main app
        self.app = app

        self.current_purchase_items = []
        self.sale_date = None

        # Create a PanedWindow to hold two separate Treeviews (left and right)
        inventory_panedwindow = tk.PanedWindow(self, orient=tk.HORIZONTAL, sashwidth=5, sashrelief=tk.RAISED)
        inventory_panedwindow.pack(expand=1, fill="both")

        # Left Treeview to display the inventory (similar to the current inventory_tab)
        self.left_tree = tk.ttk.Treeview(inventory_panedwindow, columns=("Name", "Type", "Price", "Quantity", "Used In"), show="headings", selectmode="browse", style="Custom.Treeview")
        self.left_tree.heading("Name", text="Name")
        self.left_tree.heading("Type", text="Type")
        self.left_tree.heading("Price", text="Price")
        self.left_tree.heading("Quantity", text="Quantity")
        self.left_tree.heading("Used In", text="Used In")
        self.left_tree.pack(side=tk.LEFT, expand=1, fill="both")

        # Right Treeview to display the assembled PCs (similar to the current assemble_tab)
        self.right_tree = tk.ttk.Treeview(inventory_panedwindow, columns=("Name", "Price", "CPU", "Cooler", "GPU", "Motherboard", "RAM", "SSD", "HDD", "Case", "PSU", "Fan", "Extra"), show="headings", selectmode="browse", style="Custom.Treeview")
        self.right_tree.heading("Name", text="Name")
        self.right_tree.heading("Price", text="Price")

        component_types = ["CPU", "Cooler", "Motherboard", "GPU", "RAM", "SSD", "HDD", "Case", "PSU", "Fan", "Extra"]
        for component_type in component_types:
            self.right_tree.heading(component_type, text=component_type)

        self.component_types = component_types
        self.right_tree.pack(side=tk.RIGHT, expand=1, fill="both")

        # Add the Treeviews to the PanedWindow
        inventory_panedwindow.add(self.left_tree)
        inventory_panedwindow.add(self.right_tree)

        # Set the initial width of the left Treeview
        inventory_panedwindow.paneconfig(self.left_tree, minsize=400)

        # Left Treeview columns configuration
        self.left_tree.column("Name", minwidth=150, width=200)
        self.left_tree.column("Type", minwidth=100, width=150)
        self.left_tree.column("Price", minwidth=50, width=100)
        self.left_tree.column("Quantity", minwidth=50, width=100)
        self.left_tree.column("Used In", minwidth=50, width=100)

        # Right Treeview columns configuration
        self.right_tree.column("Name", minwidth=45, width=100)
        self.right_tree.column("Price", minwidth=50, width=100)
        self.right_tree.column("CPU", minwidth=35, width=70)
        self.right_tree.column("Cooler", minwidth=45, width=90)
        self.right_tree.column("GPU", minwidth=35, width=70)
        self.right_tree.column("Motherboard", minwidth=80, width=160)
        self.right_tree.column("RAM", minwidth=35, width=70)
        self.right_tree.column("SSD", minwidth=35, width=70)
        self.right_tree.column("HDD", minwidth=35, width=70)
        self.right_tree.column("Case", minwidth=35, width=70)
        self.right_tree.column("PSU", minwidth=35, width=70)
        self.right_tree.column("Fan", minwidth=30, width=60)
        self.right_tree.column("Extra", minwidth=40, width=80)

        # Bind the left mouse button click to the treeview widgets
        self.left_tree.bind("<Button-1>", self.toggle_selection_left)
        self.right_tree.bind("<Button-1>", self.toggle_selection_right)

        # Bind the left mouse button click to the treeview headers for sorting
        for col in self.left_tree["columns"]:
            self.left_tree.heading(col, text=col, command=lambda _col=col: self.sort_column(self.left_tree, _col, False))
        for col in self.right_tree["columns"]:
            self.right_tree.heading(col, text=col, command=lambda _col=col: self.sort_column(self.right_tree, _col, False))

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

        # Load inventory upon opening the application
        self.refresh()

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

        # Get the list of inventory items and assembled PCs from the app
        inventory_items = get_inventory_items()
        assembled_pcs = get_assembled_pcs()

        # Combine identical items and calculate average price
        combined_items = {}
        for item in inventory_items:
            if item[2] != "Extra":
                key = (item[1], item[2], item[4])  # (Name, Type, Used In)
                if key not in combined_items:
                    combined_items[key] = [[item[0]], item[3], 1]  # [[IDs], Total Price, Quantity]
                else:
                    combined_items[key][0].append(item[0])
                    combined_items[key][1] += item[3]
                    combined_items[key][2] += 1

        # Insert each combined inventory item into the Treeview on the left
        for key, value in combined_items.items():
            name, type_, used_in = key
            ids, total_price, quantity = value
            avg_price = total_price / quantity
            item_info = name, type_, f"€{avg_price:.2f}", quantity, used_in
            self.left_tree.insert("", tk.END, values=item_info, tags=(json.dumps(ids),))

        # Insert each assembled PC into the Treeview on the right
        for pc in assembled_pcs:
            pc_info = pc[1], f"€{pc[2]}", *[self.format_component(pc[i]) for i in range(3, 14)]
            self.right_tree.insert("", tk.END, values=pc_info)

        self.set_treeview_style()

    def format_component(self, component):
        if not component:
            return ""
        items = component.split(';')
        item_counts = {}
        for item in items:
            if item in item_counts:
                item_counts[item] += 1
            else:
                item_counts[item] = 1
        formatted_items = [f"{count}x {item}" if count > 1 else item for item, count in item_counts.items()]
        return ";".join(formatted_items)

    def switch_to_purchase_tab(self):
        # Call the switch_to_purchase_tab function in the main app
        self.app.switch_to_purchase_tab()

    def switch_to_assemble_tab(self):
        # Call the switch_to_assemble_tab function in the main app
        self.app.switch_to_assemble_tab()
    
    def switch_to_balance_tab(self):
        # Call the switch_to_balance_tab function in the main app
        self.app.switch_to_balance_tab()
    
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
        # Retrieve the item IDs and values from the selected item
        item_ids = json.loads(self.left_tree.item(selected_item_left, 'tags')[0])
        item_values = self.left_tree.item(selected_item_left, 'values')
        item_name = item_values[0]
        used_in_pc = item_values[4]  # Updated index for 'Used In'

        if used_in_pc != 'None':
            # If the item is used in a PC, raise an error message
            tk.messagebox.showerror("Error", f"The {item_name} is currently used in {used_in_pc} and cannot be sold.")
            return

        # Ask how many items to sell if there are more than one
        quantity_to_sell = 1
        if len(item_ids) > 1:
            quantity_to_sell = tk.simpledialog.askinteger("Quantity", f"Enter the quantity of {item_name} to sell (1-{len(item_ids)}):", minvalue=1, maxvalue=len(item_ids))
            if quantity_to_sell is None:
                return

        # Prompt the user for the total selling price
        total_selling_price = self.get_selling_price(item_name)
        if total_selling_price is None:
            return

        # Calculate the selling price per item
        selling_price_per_item = total_selling_price / quantity_to_sell

        # Prompt the user for the sale date
        sale_date = self.get_sale_date(item_name)
        if sale_date is None:
            return

        # Check if the sale date is before the purchase date for any item
        for item_id in item_ids[:quantity_to_sell]:
            purchase_date_str = get_purchase_date(item_id)
            if purchase_date_str is None:
                continue
            purchase_date = datetime.strptime(purchase_date_str, "%Y-%m-%d").date()
            if sale_date < purchase_date:
                tk.messagebox.showerror("Error", f"The sale date cannot be before the purchase date ({purchase_date}).")
                return

        # Ask for confirmation before selling the standalone item
        confirm = tk.messagebox.askyesno("Confirm Sell", f"Do you want to sell {quantity_to_sell} of {item_name} for €{total_selling_price:.2f}?")
        if confirm:
            for _ in range(quantity_to_sell):
                # Remove one item from inventory and add to income table
                item_id = item_ids.pop(0)
                delete_item_from_inventory(item_id)

                # Query the database for the cost price of the item
                total_cost = float(get_item_cost(item_id))
                profit = round(selling_price_per_item - total_cost, 2)
                add_income(item_id, item_name, total_cost, selling_price_per_item, profit, sale_date, False)

            # Update the Treeview tag with the remaining IDs
            if item_ids:
                self.left_tree.item(selected_item_left, tags=(json.dumps(item_ids),))
            else:
                self.left_tree.delete(selected_item_left)

            # Refresh the inventory Treeview and balance tab
            self.refresh()

    def sell_assembled_pc(self, selected_item_right):
        # Retrieve the PC name from the right Treeview
        pc_info = self.right_tree.item(selected_item_right, 'values')
        pc_name = pc_info[0]

        # Prompt the user for the selling price using the existing method
        selling_price = self.get_selling_price(pc_name)

        if selling_price is not None:
            # Prompt the user for the sale date
            sale_date = self.get_sale_date(pc_name)
            if sale_date is None:
                return

            # Check if the sale date is before the purchase date of any component
            inventory_items = get_inventory_items()
            for item in inventory_items:
                if item[4] == pc_name:
                    purchase_date = datetime.strptime(get_purchase_date(item[0]), "%Y-%m-%d").date()
                    if sale_date < purchase_date:
                        tk.messagebox.showerror("Error", f"The sale date cannot be before the purchase date ({purchase_date}) of any component.")
                        return

            # Ask for confirmation before selling the assembled PC
            confirm = tk.messagebox.askyesno("Confirm Sell", f"Do you want to sell {pc_name} for €{selling_price:.2f}?")
            if confirm:
                total_cost = float(pc_info[1][1:])
                profit = round(selling_price - total_cost, 2)

                # Add the income entry and retrieve the id
                income_id = add_income(None, pc_name, total_cost, selling_price, profit, sale_date, True)

                # Delete components used in the PC from the inventory
                component_ids = {component: None for component in ["cpu", "cooler", "gpu", "motherboard", "ram", "ssd", "hdd", "case", "psu", "fan", "extra"]}
                for item in get_inventory_items():
                    if item[4] == pc_name:
                        component_type = item[2].lower()
                        if component_type in component_ids and component_ids[component_type] is None:
                            component_ids[component_type] = item[0]

                delete_components_used_in_pc(pc_name)

                # Add the sold PC to the sold_pcs table
                add_sold_pc(income_id, pc_name, list(component_ids.values()))

                # Delete the assembled PC from the database
                delete_assembled_pc(pc_name)

                # Refresh the inventory Treeview and balance tab
                self.refresh()

    def get_sale_date(self, item_name):
        sale_date_popup = tk.Toplevel(self)
        sale_date_popup.title("Select Sale Date")

        # Center the popup window
        sale_date_popup.update_idletasks()
        width = 300  # Set a fixed width for the popup
        height = 200  # Set a fixed height for the popup
        x = (sale_date_popup.winfo_screenwidth() // 2) - (width // 2)
        y = (sale_date_popup.winfo_screenheight() // 2) - (height // 2)
        sale_date_popup.geometry(f'{width}x{height}+{x}+{y}')

        tk.Label(sale_date_popup, text=f"Select sale date for {item_name}:").pack(pady=10)
        sale_date_entry = DateEntry(sale_date_popup, width=12, background='darkblue', foreground='white', borderwidth=2)
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

    def get_selling_price(self, item_name):
        while True:
            try:
                # Prompt the user for the total selling price
                selling_price_str = tk.simpledialog.askstring("Selling Price", f"Enter the total selling price for {item_name}:")
                if selling_price_str is None:
                    # If the user cancels the input, return None
                    return None

                # Normalize the input (replace commas with dots for decimals)
                normalized_price_str = selling_price_str.replace(',', '.').replace(' ', '')

                # Check if input is numeric
                if not normalized_price_str.replace('.', '').isdigit() or normalized_price_str.count('.') > 1:
                    raise ValueError("Price must be numeric.")

                # Convert to float
                selling_price = float(normalized_price_str)

                # Validate decimal places (if any) and range
                if '.' in normalized_price_str and len(normalized_price_str.split('.')[-1]) > 2:
                    raise ValueError("Price must have up to 2 decimal places.")
                if selling_price < 0:
                    raise ValueError("Selling price must be positive.")
                if selling_price >= 10**5:
                    raise ValueError("Selling price must be less than 100,000.")

                # Return the valid selling price
                return selling_price

            except ValueError as e:
                # Handle invalid input
                tk.messagebox.showerror("Invalid Selling Price", str(e))

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

    def delete_item(self):
        # Get the selected item from the left or right Treeview
        selected_item_left = self.left_tree.selection()
        selected_item_right = self.right_tree.selection()

        if selected_item_left:
            # Retrieve the item IDs and values from the selected item
            item_ids = json.loads(self.left_tree.item(selected_item_left, 'tags')[0])
            item_values = self.left_tree.item(selected_item_left, 'values')
            item_name = item_values[0]
            used_in_pc = item_values[4]  # Updated index for 'Used In'

            if used_in_pc != 'None':
                # If the item is used in a PC, raise an error message
                tk.messagebox.showerror("Error", f"The {item_name} is currently used in {used_in_pc} and cannot be deleted.")
                return

            # Ask for confirmation before deleting the item
            confirm = tk.messagebox.askyesno("Confirm Deletion", f"Do you want to delete {item_name} from expenses?")
            if confirm:
                for item_id in item_ids:
                    delete_expense(item_id)

                # Remove the item from the Treeview
                self.left_tree.delete(selected_item_left)

                # Refresh the inventory Treeview
                self.refresh()

        elif selected_item_right:
            # Retrieve the PC name from the right Treeview
            pc_name = self.right_tree.item(selected_item_right, 'values')[0]

            # Ask for confirmation before deleting the assembled PC
            confirm = tk.messagebox.askyesno("Confirm Deletion", "Do you want to delete the assembled PC?")
            if confirm:
                # Clear the used_in field for all components used in the PC
                update_used_in_for_deleted_pc(pc_name)

                # Delete the assembled PC from the database
                delete_assembled_pc(pc_name)

                # Refresh the inventory Treeview
                self.refresh()

        else:
            tk.messagebox.showerror("Error", "Please select an item to delete.")

    def rename_item(self):
        selected_item_left = self.left_tree.selection()
        selected_item_right = self.right_tree.selection()

        if selected_item_left:
            item_ids = json.loads(self.left_tree.item(selected_item_left, 'tags')[0])
            item_values = self.left_tree.item(selected_item_left, 'values')
            old_name = item_values[0]

            new_name = tk.simpledialog.askstring("Rename Part", f"Enter new name for {old_name}:", initialvalue=old_name)
            if new_name:
                for item_id in item_ids:
                    rename_part(item_id, old_name, new_name)
                self.refresh()

        elif selected_item_right:
            item_values = self.right_tree.item(selected_item_right, 'values')
            old_name = item_values[0]

            new_name = tk.simpledialog.askstring("Rename PC", f"Enter new name for {old_name}:", initialvalue=old_name)
            if new_name:
                rename_pc(old_name, new_name)
                self.refresh()

        else:
            tk.messagebox.showerror("Error", "Please select an item to rename.")