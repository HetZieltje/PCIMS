"""Shared, consistently validated dialogs used by multiple tabs."""

import tkinter as tk
from decimal import Decimal, InvalidOperation
from tkinter import messagebox, simpledialog

from app.calendar import DateEntry


def parse_money_input(value):
    """Parse a user-entered euro amount while preserving cent precision."""
    normalized = str(value).strip().replace(",", ".").replace(" ", "")
    if not normalized:
        raise ValueError("Please enter a selling price.")
    try:
        amount = Decimal(normalized)
    except InvalidOperation as exc:
        raise ValueError("Price must be numeric.") from exc
    if not amount.is_finite():
        raise ValueError("Price must be numeric.")
    if amount.as_tuple().exponent < -2:
        raise ValueError("Price must have up to 2 decimal places.")
    if amount < 0:
        raise ValueError("Selling price cannot be negative.")
    if amount >= Decimal("100000"):
        raise ValueError("Selling price must be less than 100,000.")
    return float(amount)


def ask_selling_price(parent, item_name):
    while True:
        entered = simpledialog.askstring(
            "Selling Price",
            f"Enter the total selling price for {item_name}:",
            parent=parent,
        )
        if entered is None:
            return None
        try:
            return parse_money_input(entered)
        except ValueError as exc:
            messagebox.showerror("Invalid Selling Price", str(exc), parent=parent)


def ask_sale_date(parent, item_name):
    result = {"date": None}
    popup = tk.Toplevel(parent)
    popup.title("Select Sale Date")
    popup.resizable(False, False)

    tk.Label(popup, text=f"Select sale date for {item_name}:").pack(pady=(14, 8), padx=20)
    entry = DateEntry(
        popup, width=12, background="darkblue", foreground="white",
        borderwidth=2, date_pattern="yyyy-mm-dd",
    )
    entry.pack(pady=8)

    button_frame = tk.Frame(popup)
    button_frame.pack(fill=tk.X, padx=16, pady=(8, 14))

    def close(selected):
        if selected:
            try:
                result["date"] = entry.get_date()
            except ValueError as exc:
                messagebox.showerror("Invalid Date", str(exc), parent=popup)
                return
        else:
            result["date"] = None
        popup.destroy()

    tk.Button(button_frame, text="Cancel", command=lambda: close(False)).pack(side=tk.RIGHT, padx=4)
    tk.Button(button_frame, text="Confirm", command=lambda: close(True)).pack(side=tk.RIGHT, padx=4)
    popup.protocol("WM_DELETE_WINDOW", lambda: close(False))
    popup.transient(parent.winfo_toplevel())
    popup.grab_set()
    popup.update_idletasks()
    x = popup.winfo_screenwidth() // 2 - popup.winfo_width() // 2
    y = popup.winfo_screenheight() // 2 - popup.winfo_height() // 2
    popup.geometry(f"+{x}+{y}")
    parent.wait_window(popup)
    return result["date"]
