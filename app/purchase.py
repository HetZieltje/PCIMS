import tkinter as tk
from tkinter import messagebox
from tkcalendar import DateEntry
import customtkinter as ctk
from customtkinter import *
from db.queries import add_item_to_inventory, add_expense

class PurchaseTab(ctk.CTkFrame):
    def __init__(self, master, app):
        super().__init__(master)

        # Reference to the main app
        self.app = app

        # List to store items in the current purchase
        self.current_purchase_items = []

        # Entry fields
        self.name_label = ctk.CTkLabel(self, text="Item Name:")
        self.name_entry = ctk.CTkEntry(self, width=300)
        self.name_label.grid(row=0, column=0, pady=5, padx=10, sticky=tk.W)
        self.name_entry.grid(row=0, column=1, pady=5, padx=10, sticky=tk.W)

        self.type_label = ctk.CTkLabel(self, text="Component Type:")
        self.type_var = tk.StringVar()
        self.type_dropdown = ctk.CTkComboBox(self, variable=self.type_var, values=[
            "", "CPU", "Cooler", "GPU", "Motherboard", "RAM", "SSD", "HDD", "Case", "PSU", "Fan", "Extra"
        ], state="readonly", width=200)
        self.type_label.grid(row=1, column=0, pady=5, padx=10, sticky=tk.W)
        self.type_dropdown.grid(row=1, column=1, pady=5, padx=10, sticky=tk.W)

        self.price_label = ctk.CTkLabel(self, text="Price (Euro):")
        self.price_entry = ctk.CTkEntry(self, validate="key", validatecommand=(self.register(self.validate_price), "%P"), width=150)
        self.price_label.grid(row=2, column=0, pady=5, padx=10, sticky=tk.W)
        self.price_entry.grid(row=2, column=1, pady=5, padx=10, sticky=tk.W)

        self.percent_label = ctk.CTkLabel(self, text="Percentage of Bundle Price:")
        self.percent_entry = ctk.CTkEntry(self, validate="key", validatecommand=(self.register(self.validate_percentage), "%P"), width=150)
        self.percent_label.grid(row=3, column=0, pady=5, padx=10, sticky=tk.W)
        self.percent_entry.grid(row=3, column=1, pady=5, padx=10, sticky=tk.W)

        self.date_label = ctk.CTkLabel(self, text="Purchase Date:")
        self.date_entry = DateEntry(self, width=15, background='darkblue', foreground='white', borderwidth=2)
        self.date_label.grid(row=4, column=0, pady=5, padx=10, sticky=tk.W)
        self.date_entry.grid(row=4, column=1, pady=5, padx=10, sticky=tk.W)

        self.add_item_button = ctk.CTkButton(self, text="Add Item", command=self.add_item)
        self.add_bundle_button = ctk.CTkButton(self, text="Add Bundle", command=self.add_bundle)
        self.add_item_button.grid(row=5, column=0, pady=5, padx=10, sticky=tk.W)
        self.add_bundle_button.grid(row=5, column=1, pady=5, padx=10, sticky=tk.W)

        # Listbox to display items in the current purchase
        self.current_purchase_listbox = tk.Listbox(self, selectmode=tk.MULTIPLE, exportselection=0, width=100, bg="#3e3e3e" if self.app.is_dark_mode else "white", fg="white" if self.app.is_dark_mode else "black")
        self.current_purchase_listbox.grid(row=6, column=0, columnspan=2, pady=10, padx=10, sticky=tk.W)

        # Delete Item button
        self.delete_item_button = ctk.CTkButton(self, text="Delete Item", command=self.delete_item)
        self.delete_item_button.grid(row=7, column=0, pady=5, padx=10, sticky=tk.W)

        # List to store items in the current purchase
        self.current_purchase_items = []
    
    def add_item(self):
        name = self.name_entry.get()
        component_type = self.type_var.get()

        # Check if name and component_type are not empty
        if not name or not component_type:
            # Show an error message or take any other necessary action
            messagebox.showerror("Error", "Please enter both item name and component type.")
            return

        if self.price_entry.get():
            price = float(self.price_entry.get().replace(',', '.').replace(' ', '.'))
            percentage = float(self.percent_entry.get().replace(',', '.').replace(' ', '.')) if self.percent_entry.get() else 100.0
            # Calculate the price after applying the percentage
            price_after_percentage = self.calc_price(price, percentage)
        else:
            messagebox.showerror("Error", "Please enter a price")
            return
        
        purchase_date = self.date_entry.get_date()
        
        # Add the item to the current purchases
        item = {"name": name, "component_type": component_type, "price": price_after_percentage, "purchase_date": purchase_date}
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
        # Validate form if no items are in the current purchase list
        if not self.current_purchase_items and not self.validate_form():
            messagebox.showerror("Error", "Please enter valid data.")
            return

        # Add the form entry if valid
        if self.validate_form():
            name = self.name_entry.get()
            type = self.type_var.get()
            price = float(self.price_entry.get().replace(',', '.').replace(' ', '.'))
            percentage = float(self.percent_entry.get().replace(',', '.').replace(' ', '.')) if self.percent_entry.get() else 100.0
            price_after_percentage = self.calc_price(price, percentage)
            purchase_date = self.date_entry.get_date()
            id = add_item_to_inventory(name, type, price_after_percentage)
            add_expense(id, name, type, price_after_percentage, purchase_date)

        # Add items from the current purchase list
        for item in self.current_purchase_items:            
            id = add_item_to_inventory(item['name'], item['component_type'], item['price'])
            add_expense(id, item['name'], item['component_type'], item['price'])

        # Clear the current purchase list and UI
        self.current_purchase_items = []
        self.current_purchase_listbox.delete(0, tk.END)
        self.name_entry.delete(0, tk.END)
        self.type_dropdown.set("")
        self.percent_entry.delete(0, tk.END)
        self.price_entry.configure(state="normal")
        self.name_entry.focus_set()
        self.price_entry.delete(0, tk.END)
        self.date_entry.set_date(self.date_entry._date.today())  # Reset to current date

    def validate_form(self):
        # Validate the form (name, price, and type must be filled)
        name = self.name_entry.get()
        item_type = self.type_var.get()
        price = self.price_entry.get()

        if not name or not item_type or not price:
            return False

        return True

    def validate_price(self, new_value):
        if not new_value:
            return True  # Allow empty input

        new_value = new_value.replace(",", ".").replace(" ", ".")

        try:
            # Attempt to convert the input to a float
            float_value = float(new_value)

            # Check if the value is non-negative and has at most 2 decimal places
            return 0 <= float_value < 10**5 and (len(new_value.split('.')[-1]) <= 2 if '.' in new_value else True)
        except ValueError:
            return False  # Disallow invalid input
        
    def validate_percentage(self, new_value):
        try:
            if not new_value:
                return True  # Allow empty input
            
            # Replace commas with periods
            new_value = new_value.replace(",", ".")

            # Replace commas with periods
            new_value = new_value.replace(" ", ".")

            # Attempt to convert the input to a float
            float_value = float(new_value)

            # Check if the value is within the valid range [0, 100]
            return 0 <= float_value <= 100
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
