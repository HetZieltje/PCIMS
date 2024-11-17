import tkinter as tk
from tkinter import ttk, messagebox
import customtkinter as ctk
from customtkinter import *
from db.queries import *

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

        for component_type in component_types:
            self.update_dropdown(component_type, self.component_entries[component_type])

    def refresh(self):
        """Refresh all dropdowns when the tab is selected."""
        for component_type in self.component_types:
            self.update_dropdown(component_type, self.component_entries[component_type])

    def get_inventory_items_by_type(self, component_type):
        # Retrieve all items of the specified component type from the main app
        items = get_inventory_items()

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
        existing_pc_names = get_pc_names()

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

    def map_components_to_db_schema(self, selected_components):
        """
        Map selected components to the database schema.
        Fills empty slots with "" for storage, fans, and extras.
        
        Args:
            selected_components (dict): Dictionary of component types and selected values.
                Example: {"CPU": "Intel i7", "HDD": "Seagate 1TB", "Fan": "CoolerMaster Fan"}
        
        Returns:
            dict: A dictionary with all database columns and their mapped values.
        """
        # Predefined database schema structure
        db_columns = {
            "cpu1": "", "cpu2": "", "cooler1": "", "cooler2": "",
            "motherboard": "", "ram": "", "ssd1": "", "ssd2": "", "hdd1": "",
            "hdd2": "", "gpu1": "", "gpu2": "", "pc_case": "", "psu": "",
            "fan1": "", "fan2": "", "fan3": "", "extra1": "", "extra2": "", "extra3": ""
        }
        
        # Direct mapping for single-use component types
        direct_mappings = {
            "Motherboard": "motherboard", "RAM": "ram",
            "Case": "pc_case", "PSU": "psu"
        }

        # Handle storage, fans, and extras (multi-use types)
        multi_mappings = {
            "CPU": "cpu", "GPU": "gpu", "Cooler": "cooler",
            "SSD": "ssd", "HDD": "hdd",
            "Fan": "fan", "Extra": "extra"
        }

        # Map single-use components
        for component_type, db_column in direct_mappings.items():
            db_columns[db_column] = selected_components.get(component_type, "") or ""

        # Handle multi-use components dynamically
        for component_type, prefix in multi_mappings.items():
            values = [value for key, value in selected_components.items() if key == component_type and value]
            for i, value in enumerate(values[:2] if prefix in ["cpu", "cooler", "gpu"] else values[:3]):
                db_columns[f"{prefix}{i + 1}"] = value or ""

        return db_columns

    def assemble_pc(self):
        pc_name = self.pc_name_entry.get()
        selected_components = {component_type: self.component_entries[component_type].get() for component_type in self.component_types}

        # Ensure at least one component is selected
        if not any(selected_components.values()):
            messagebox.showerror("Error", "At least one component must be selected.")
            return

        # Check if the PC name is already in use
        if pc_name in get_pc_names():
            messagebox.showerror("Error", "PC name already in use. Please choose a different name.")
            return

        # Map selected components to the database schema
        db_columns = self.map_components_to_db_schema(selected_components)

        # Update the `used_in` field for all selected components
        for type, name in selected_components.items():
            if name:  # Only update if a component is selected
                    update_used_in_component(pc_name, name, type)

        # Calculate the total price
        price = round(get_total_pc_price(pc_name), 2)

        # Insert into the database
        assemble_pc(pc_name, price, db_columns)

        # Refresh dropdowns and clear fields
        for component_type in self.component_types:
            self.update_dropdown(component_type, self.component_entries[component_type])
        self.clear_entry_fields()
