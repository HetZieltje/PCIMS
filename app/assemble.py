import tkinter as tk
from tkinter import messagebox
from app import ui as ctk
from db.queries import assemble_inventory_pc, get_inventory_items, get_pc_names

class AssembleTab(ctk.CTkFrame):
    def __init__(self, master, app):
        super().__init__(master)
        self.app = app

        name = self.find_next_available_pc_name()

        # Entry field for the name of the assembled PC
        self.pc_name_label = ctk.CTkLabel(self, text="PC Name:")
        self.pc_name_entry = ctk.CTkEntry(self, width=300)
        self.pc_name_entry.insert(0, name)
        self.pc_name_label.grid(row=0, column=0, padx=10, pady=5, sticky=tk.W)
        self.pc_name_entry.grid(row=0, column=1, padx=10, pady=5, sticky=tk.W)

        # Create labels and dropdowns for each component type
        component_types = ("CPU", "Cooler", "Motherboard", "RAM", "GPU", "PSU", "SSD", "HDD", "Case", "Fan", "Extra")
        self.component_types = component_types

        # Create a dictionary to store the component type dropdowns
        self.component_entries = {}

        # Two flexible groups per row keep all controls visible at the default
        # window size. The previous eight-column grid was wider than the app.
        layout_positions = {
            component_type: (index // 2 + 1, (index % 2) * 2)
            for index, component_type in enumerate(component_types)
        }

        for component_type in component_types:
            row, col = layout_positions[component_type]
            label = ctk.CTkLabel(self, text=f"{component_type}:")
            listbox = tk.Listbox(self, selectmode=tk.MULTIPLE, exportselection=0, width=30, height=4, bg="#3e3e3e" if self.app.is_dark_mode else "white", fg="white" if self.app.is_dark_mode else "black")
            listbox.bind('<FocusIn>', lambda event, comp_type=component_type, listbox=listbox: self.update_listbox(comp_type, listbox))
            listbox.bind('<<ListboxSelect>>', lambda event, comp_type=component_type, listbox=listbox: self.handle_selection(comp_type, listbox))

            label.grid(row=row, column=col, padx=10, pady=5, sticky=tk.W)
            listbox.grid(row=row, column=col + 1, padx=10, pady=5, sticky=tk.EW)

            # Save the Listbox in the dictionary
            self.component_entries[component_type] = listbox

        # Create an "Assemble" button
        assemble_button = ctk.CTkButton(self, text="Assemble", command=self.assemble_pc)
        assemble_button.grid(row=7, column=0, columnspan=4, pady=10)

        self.grid_columnconfigure(1, weight=1)
        self.grid_columnconfigure(3, weight=1)

        for component_type in component_types:
            self.update_listbox(component_type, self.component_entries[component_type])

    def refresh(self):
        """Refresh all dropdowns when the tab is selected."""
        for component_type in self.component_types:
            self.update_listbox(component_type, self.component_entries[component_type])
        self.set_listbox_style()

    def set_listbox_style(self):
        for listbox in self.component_entries.values():
            listbox.configure(bg="#3e3e3e" if self.app.is_dark_mode else "white", fg="white" if self.app.is_dark_mode else "black")

    def get_inventory_items_by_type(self, component_type):
        # Retrieve all items of the specified component type from the main app
        items = get_inventory_items()

        # Use a list to store item names for the specified component type
        item_names = []

        # Iterate through items and add names to the list
        for item in items:
            # Check if the "Used In" column is "None"
            if item[2] == component_type and item[4] is None:
                item_names.append(item[1])

        return item_names  # Return the list of item names

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
        unique_items = set()
        for item in items:
            if item not in unique_items:
                listbox.insert(tk.END, item)
                unique_items.add(item)

    def handle_selection(self, component_type, listbox):
        selected_indices = listbox.curselection()
        if len(selected_indices) > 0:
            last_selected_index = selected_indices[-1]
            last_selected_item = listbox.get(last_selected_index)
            # Check if there are more items of the same type in the inventory
            items = self.get_inventory_items_by_type(component_type)
            if items.count(last_selected_item) > listbox.get(0, tk.END).count(last_selected_item):
                # Insert another item of the same type for selection directly underneath the selected one
                listbox.insert(last_selected_index + 1, last_selected_item)

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

        try:
            assemble_inventory_pc(pc_name, selected_components)
        except (ValueError, LookupError) as exc:
            messagebox.showerror("Unable to Assemble PC", str(exc))
            return

        # Refresh dropdowns and clear fields
        for component_type in self.component_types:
            self.update_listbox(component_type, self.component_entries[component_type])
        self.clear_entry_fields()
