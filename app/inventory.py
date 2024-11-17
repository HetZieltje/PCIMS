import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
import customtkinter as ctk
from customtkinter import *
from db.queries import *

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
        self.refresh()

    def refresh(self):
        # Clear the existing items in both Treeviews
        for item in self.left_tree.get_children():
            self.left_tree.delete(item)
        for item in self.right_tree.get_children():
            self.right_tree.delete(item)

        # Get the list of inventory items and assembled PCs from the app
        inventory_items = get_inventory_items()
        assembled_pcs = get_assembled_pcs()

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
                    delete_item_from_inventory(item_id)
                    delete_expense(item_id)

                    # Refresh the inventory Treeview
                    self.refresh()
                    self.app.balance_tab.refresh_balance_tab()

        elif selected_item_right:
            # Retrieve the PC name from the right Treeview
            pc_name = self.right_tree.item(selected_item_right, 'values')[0]

            # Ask for confirmation before deleting the assembled PC
            confirm = messagebox.askyesno("Confirm Deletion", "Do you want to delete the assembled PC?")
            if confirm:
                # Delete the assembled PC from the database
                delete_assembled_pc(pc_name)

                update_used_in_for_deleted_pc(pc_name)

                # Refresh the inventory Treeview
                self.refresh()
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
                delete_item_from_inventory(item_id)
                total_cost = float(item_values[2][1:])
                profit = round(selling_price - total_cost, 2)
                self.app.add_income(item_id, item_name, total_cost, selling_price, profit)

                # Refresh the inventory Treeview and balance tab
                self.refresh()
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

                total_cost = float(pc_info[1][1:])
                profit = round(selling_price - total_cost, 2)

                # Add the income entry
                self.app.add_income(pc_name, total_cost, selling_price, profit)

                # Delete components used in the PC from the inventory
                self.app.delete_components_used_in_pc(pc_name)

                # Delete the assembled PC from the database
                delete_assembled_pc(pc_name)

                # Refresh the inventory Treeview and balance tab
                self.refresh()
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
