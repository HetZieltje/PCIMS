import unittest
from pathlib import Path


class ArchitectureTests(unittest.TestCase):
    def test_runtime_has_no_tkinter_or_old_ui_compatibility_layer(self):
        app_directory = Path(__file__).parents[1] / "app"
        source_lines = "\n".join(
            path.read_text(encoding="utf-8") for path in app_directory.rglob("*.py")
        ).splitlines()
        imports = "\n".join(
            line.strip()
            for line in source_lines
            if line.lstrip().startswith(("import ", "from "))
        ).casefold()
        for old_dependency in ("tkinter", "customtkinter", "tkcalendar"):
            self.assertNotIn(old_dependency, imports)
        for old_module in ("ui.py", "calendar.py", "widgets.py"):
            self.assertFalse((app_directory / old_module).exists())

    def test_backend_has_no_legacy_schema_terms_or_compatibility_apis(self):
        root = Path(__file__).parents[1]
        source = (root / "db" / "queries.py").read_text(encoding="utf-8")
        for legacy_term in (
            "used_in",
            "in_inventory",
            "assembled_pc_parts",
            "sold_pcs",
            "price real",
            "add_income",
            "add_sold_pc",
        ):
            self.assertNotIn(legacy_term, source.casefold())


if __name__ == "__main__":
    unittest.main()
