"""DateEntry adapter with a dependency-free ISO-date fallback."""

from datetime import date, datetime
from tkinter import ttk


try:
    from tkcalendar import DateEntry
except ImportError:
    class DateEntry(ttk.Entry):
        def __init__(self, master=None, date_pattern="yyyy-mm-dd", **options):
            del date_pattern
            for unsupported in ("background", "foreground", "borderwidth"):
                options.pop(unsupported, None)
            super().__init__(master, **options)
            self.set_date(date.today())

        def get_date(self):
            return parse_date_input(self.get())

        def set_date(self, value):
            if isinstance(value, datetime):
                value = value.date()
            if not isinstance(value, date):
                value = parse_date_input(value)
            self.delete(0, "end")
            self.insert(0, value.isoformat())


def parse_date_input(value):
    """Normalize a date-like value or raise a user-facing validation error."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise ValueError("Date must use the YYYY-MM-DD format.") from exc
