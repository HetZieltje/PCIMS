"""Reusable Tkinter widgets shared by PCIMS tabs."""

import tkinter as tk
from tkinter import ttk

from app import ui as ctk


def create_scrollable_treeview(parent, **tree_options):
    """Return a Treeview in a frame with working horizontal/vertical bars."""
    container = tk.Frame(parent, highlightthickness=0, borderwidth=0)
    tree = ttk.Treeview(container, **tree_options)
    vertical = ttk.Scrollbar(container, orient=tk.VERTICAL, command=tree.yview)
    horizontal = ttk.Scrollbar(container, orient=tk.HORIZONTAL, command=tree.xview)
    tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)

    tree.grid(row=0, column=0, sticky=tk.NSEW)
    vertical.grid(row=0, column=1, sticky=tk.NS)
    horizontal.grid(row=1, column=0, sticky=tk.EW)
    container.grid_rowconfigure(0, weight=1)
    container.grid_columnconfigure(0, weight=1)
    return container, tree


class AutocompleteEntry(ctk.CTkEntry):
    """Entry with keyboard- and mouse-accessible substring suggestions."""

    def __init__(self, master, suggestions, app=None, *args, **kwargs):
        self.var = tk.StringVar()
        kwargs["textvariable"] = self.var
        super().__init__(master, *args, **kwargs)
        self.app = app or getattr(master, "app", None)
        self.suggestions = []
        self.set_suggestions(suggestions)
        self.listbox = None

        self.var.trace_add("write", self.changed)
        self.bind("<Right>", self.selection)
        self.bind("<Return>", self.selection)
        self.bind("<Tab>", self.selection)
        self.bind("<Up>", lambda event: self.move_selection(-1))
        self.bind("<Down>", lambda event: self.move_selection(1))
        self.bind("<Escape>", self.hide_listbox)
        self.bind("<FocusOut>", self.defer_focus_check)
        self.master.bind("<Button-1>", self.hide_listbox_on_background_click, add="+")

    @property
    def listbox_up(self):
        return self.listbox is not None and self.listbox.winfo_exists()

    def set_suggestions(self, suggestions):
        self.suggestions = sorted({str(value) for value in suggestions}, key=str.casefold)

    def comparison(self):
        pattern = self.var.get().casefold()
        return [word for word in self.suggestions if pattern in word.casefold()]

    def changed(self, *_):
        words = self.comparison() if self.var.get() else []
        if not words:
            self.hide_listbox()
            return
        if not self.listbox_up:
            dark = bool(self.app and self.app.is_dark_mode)
            self.listbox = tk.Listbox(
                self.master,
                height=min(len(words), 10),
                width=50,
                bg="#3e3e3e" if dark else "white",
                fg="white" if dark else "black",
                exportselection=False,
            )
            self.listbox.bind("<Button-1>", self.selection)
            self.listbox.bind("<Motion>", self.on_motion)
            self.listbox.bind("<Return>", self.selection)
            self.listbox.bind("<Escape>", self.hide_listbox)
            self.listbox.place(x=self.winfo_x(), y=self.winfo_y() + self.winfo_height())
        self.listbox.configure(height=min(len(words), 10))
        self.listbox.delete(0, tk.END)
        for word in words:
            self.listbox.insert(tk.END, word)
        self.listbox.selection_set(0)
        self.listbox.activate(0)

    def selection(self, event=None):
        if not self.listbox_up:
            return None
        if event is not None and getattr(event, "widget", None) is self.listbox and hasattr(event, "y"):
            index = self.listbox.nearest(event.y)
        else:
            selected = self.listbox.curselection()
            index = selected[0] if selected else 0
        self.var.set(self.listbox.get(index))
        self.icursor(tk.END)
        self.hide_listbox()
        return "break"

    def move_selection(self, delta):
        if not self.listbox_up:
            return None
        selected = self.listbox.curselection()
        current = selected[0] if selected else 0
        target = max(0, min(self.listbox.size() - 1, current + delta))
        self.listbox.selection_clear(0, tk.END)
        self.listbox.selection_set(target)
        self.listbox.activate(target)
        self.listbox.see(target)
        return "break"

    def on_motion(self, event):
        if self.listbox_up:
            index = self.listbox.nearest(event.y)
            self.listbox.selection_clear(0, tk.END)
            self.listbox.selection_set(index)

    def defer_focus_check(self, _event=None):
        self.after_idle(self.hide_if_focus_left)

    def hide_if_focus_left(self):
        focus = self.focus_get()
        if focus is not self and focus is not self.listbox:
            self.hide_listbox()

    def hide_listbox_on_background_click(self, event):
        if self.listbox_up and event.widget not in (self, self.listbox):
            self.hide_listbox()

    def hide_listbox(self, _event=None):
        if self.listbox_up:
            self.listbox.destroy()
        self.listbox = None
