import sys
import unittest
from types import ModuleType


if "customtkinter" not in sys.modules:
    customtkinter = ModuleType("customtkinter")
    customtkinter.CTkEntry = object
    sys.modules["customtkinter"] = customtkinter

from app.widgets import AutocompleteEntry


class FakeVariable:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value


class FakeListbox:
    def __init__(self, size, selected):
        self._size = size
        self.selected = selected

    def winfo_exists(self):
        return True

    def curselection(self):
        return (self.selected,)

    def size(self):
        return self._size

    def selection_clear(self, *_):
        pass

    def selection_set(self, target):
        self.selected = target

    def activate(self, target):
        self.selected = target

    def see(self, target):
        self.selected = target


class AutocompleteLogicTests(unittest.TestCase):
    def make_entry(self, suggestions, query=""):
        entry = object.__new__(AutocompleteEntry)
        entry.var = FakeVariable(query)
        entry.set_suggestions(suggestions)
        entry.listbox = None
        return entry

    def test_suggestions_are_deduplicated_sorted_and_case_insensitive(self):
        entry = self.make_entry(["GPU", "cpu", "GPU", "Cooler"], "PU")

        self.assertEqual(entry.suggestions, ["Cooler", "cpu", "GPU"])
        self.assertEqual(entry.comparison(), ["cpu", "GPU"])

    def test_keyboard_navigation_is_clamped_to_valid_rows(self):
        entry = self.make_entry([])
        entry.listbox = FakeListbox(size=3, selected=2)

        self.assertEqual(entry.move_selection(1), "break")
        self.assertEqual(entry.listbox.selected, 2)
        entry.listbox.selected = 0
        self.assertEqual(entry.move_selection(-1), "break")
        self.assertEqual(entry.listbox.selected, 0)


if __name__ == "__main__":
    unittest.main()
