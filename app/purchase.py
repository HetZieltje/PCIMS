import tkinter as tk
from tkinter import messagebox
import customtkinter as ctk
from customtkinter import *
from db.queries import *

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
                add_item_to_inventory(name, item_type, price_after_percentage)
        
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
                add_item_to_inventory(name, item_type, price_after_percentage)

            # Iterate through each purchase and add the item to the inventory
            for item in self.current_purchase_items:
                name = item['name']
                item_type = item['component_type']
                price = item['price']
                add_item_to_inventory(name, item_type, price)

        """# Refresh the inventory Treeview
        self.app.refresh_inventory_treeview()
        self.app.refresh_balance_tab()"""

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

        """# Update the dropdowns in AssembleTab after adding the bundle
        for component_type in self.app.assemble_tab.component_types:
            self.app.assemble_tab.update_dropdown(component_type, self.app.assemble_tab.component_entries[component_type])"""

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
