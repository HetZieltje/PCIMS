import tkinter as tk
from tkinter import ttk, messagebox
import customtkinter as ctk
from customtkinter import *
from db.queries import get_inventory_items, get_pc_names, update_used_in_component, get_total_pc_price, assemble_pc

class AssembleTab(ctk.CTkFrame):
    def __init__(self, master, app):
        super().__init__(master)
        self.app = app

        name = self.find_next_available_pc_name()

        # Entry field for the name of the assembled PC
        self.pc_name_label = ctk.CTkLabel(self, text="PC Name:")
        self.pc_name_entry = ctk.CTkEntry(self, width=200)
        self.pc_name_entry.insert(0, name)
        self.pc_name_label.grid(row=0, column=0, padx=10, pady=5, sticky=tk.W)
        self.pc_name_entry.grid(row=0, column=1, pady=5, sticky=tk.W)

        # Create labels and dropdowns for each component type
        component_types = ("CPU", "Cooler", "GPU", "Motherboard", "RAM", "SSD", "HDD", "Case", "PSU", "Fan", "Extra")
        self.component_types = component_types

        # Create a dictionary to store the component type dropdowns
        self.component_entries = {}

        for idx, component_type in enumerate(component_types):
            label = ctk.CTkLabel(self, text=f"{component_type}:")
            label.grid(row=idx // 2 + 1, column=(idx % 2) * 2, padx=10, pady=5, sticky=tk.W)

            # Create a Listbox to allow multiple selections
            listbox = tk.Listbox(self, selectmode=tk.MULTIPLE, exportselection=0, width=30)
            listbox.bind('<FocusIn>', lambda event, comp_type=component_type, listbox=listbox: self.update_listbox(comp_type, listbox))
            listbox.grid(row=idx // 2 + 1, column=(idx % 2) * 2 + 1, pady=5, sticky=tk.W)

            # Save the Listbox in the dictionary
            self.component_entries[component_type] = listbox

        # Create an "Assemble" button
        assemble_button = ctk.CTkButton(self, text="Assemble", command=self.assemble_pc)
        assemble_button.grid(row=(len(component_types) // 2) + 2, column=0, columnspan=4, pady=10)

        for component_type in component_types:
            self.update_listbox(component_type, self.component_entries[component_type])

    def refresh(self):
        """Refresh all dropdowns when the tab is selected."""
        for component_type in self.component_types:
            self.update_listbox(component_type, self.component_entries[component_type])

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

    def update_listbox(self, component_type, listbox):
        items = self.get_inventory_items_by_type(component_type)
        listbox.delete(0, tk.END)
        for item in items:
            listbox.insert(tk.END, item)

    def clear_entry_fields(self):
        # Use the find_next_available_pc_name method to set the next available PC name
        next_pc_name = self.find_next_available_pc_name()
        self.pc_name_entry.delete(0, tk.END)
        self.pc_name_entry.insert(0, next_pc_name)

        for component_type in self.component_types:
            self.component_entries[component_type].selection_clear(0, tk.END)  # Clear the selection

        # Set the focus to the PC name entry field
        self.pc_name_entry.focus_set()

    def assemble_pc(self):
        pc_name = self.pc_name_entry.get()
        selected_components = {component_type: ';'.join([self.component_entries[component_type].get(idx) for idx in self.component_entries[component_type].curselection()]) for component_type in self.component_types}

        # Ensure at least one component is selected
        if not any(selected_components.values()):
            messagebox.showerror("Error", "At least one component must be selected.")
            return

        # Check if the PC name is already in use
        if pc_name in get_pc_names():
            messagebox.showerror("Error", "PC name already in use. Please choose a different name.")
            return

        # Update the `used_in` field for all selected components
        for type, names in selected_components.items():
            if names:  # Only update if components are selected
                update_used_in_component(pc_name, names, type)

        # Calculate the total price
        price = round(get_total_pc_price(pc_name), 2)

        # Insert into the database
        assemble_pc(pc_name, price, selected_components)

        # Refresh dropdowns and clear fields
        for component_type in self.component_types:
            self.update_listbox(component_type, self.component_entries[component_type])
        self.clear_entry_fields()
