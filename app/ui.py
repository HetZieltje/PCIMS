"""CustomTkinter when available, with a small standard-Tk fallback."""

import tkinter as tk
from tkinter import ttk


try:  # Prefer the richer optional dependency.
    from customtkinter import (CTk, CTkButton, CTkComboBox, CTkEntry, CTkFrame,
                               CTkLabel, CTkSwitch, set_appearance_mode)
except ImportError:
    def _scaled_width(options):
        width = options.get("width")
        if isinstance(width, (int, float)):
            options["width"] = max(1, round(width / 10))


    class _ColorMixin:
        def _prepare_options(self, options):
            _scaled_width(options)
            color = options.pop("fg_color", None)
            if color and color != "transparent":
                options["background"] = color

        def configure(self, cnf=None, **options):
            self._prepare_options(options)
            return super().configure(cnf, **options)

        config = configure

        def cget(self, key):
            if key == "fg_color":
                key = "background"
            return super().cget(key)


    class CTk(_ColorMixin, tk.Tk):
        def __init__(self, *args, **options):
            self._prepare_options(options)
            super().__init__(*args, **options)


    class CTkFrame(_ColorMixin, tk.Frame):
        def __init__(self, master=None, **options):
            self._prepare_options(options)
            if options.get("background") == "transparent":
                options.pop("background")
            super().__init__(master, **options)


    class CTkLabel(_ColorMixin, tk.Label):
        def __init__(self, master=None, **options):
            self._prepare_options(options)
            super().__init__(master, **options)


    class CTkEntry(_ColorMixin, tk.Entry):
        def __init__(self, master=None, **options):
            self._prepare_options(options)
            super().__init__(master, **options)


    class CTkButton(_ColorMixin, tk.Button):
        def __init__(self, master=None, **options):
            self._prepare_options(options)
            super().__init__(master, **options)


    class CTkSwitch(_ColorMixin, tk.Checkbutton):
        def __init__(self, master=None, **options):
            self._prepare_options(options)
            super().__init__(master, **options)


    class CTkComboBox(ttk.Combobox):
        def __init__(self, master=None, **options):
            _scaled_width(options)
            if "variable" in options:
                options["textvariable"] = options.pop("variable")
            super().__init__(master, **options)


    def set_appearance_mode(_mode):
        """Standard Tk has no global appearance-mode switch."""
