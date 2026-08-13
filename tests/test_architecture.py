import unittest
from pathlib import Path


class ArchitectureTests(unittest.TestCase):
    def test_runtime_uses_one_collision_safe_package_namespace(self):
        root = Path(__file__).parents[1]
        self.assertTrue((root / "pcims" / "__init__.py").is_file())
        self.assertFalse((root / "app").exists())
        self.assertFalse((root / "db").exists())

    def test_runtime_has_no_tkinter_or_old_ui_compatibility_layer(self):
        app_directory = Path(__file__).parents[1] / "pcims" / "app"
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
        source = (root / "pcims" / "db" / "queries.py").read_text(encoding="utf-8")
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

    def test_schema_and_workflows_have_separate_modules(self):
        root = Path(__file__).parents[1] / "pcims" / "db"
        queries = (root / "queries.py").read_text(encoding="utf-8")
        schema = (root / "schema.py").read_text(encoding="utf-8")

        self.assertNotIn("CREATE TABLE", queries)
        self.assertNotIn("PRAGMA user_version", queries)
        self.assertIn("SCHEMA_DEFINITIONS", schema)
        self.assertIn("validate_current_data", schema)

    def test_qt_pages_depend_on_application_services_not_query_globals(self):
        pages = Path(__file__).parents[1] / "pcims" / "app" / "pages"
        for path in pages.glob("*.py"):
            with self.subTest(path=path.name):
                source = path.read_text(encoding="utf-8")
                self.assertNotIn("from pcims.db.queries import", source)
                self.assertNotIn("from pcims.db.backup import create_backup", source)

    def test_flat_application_grids_use_model_view_not_cell_widgets(self):
        pages = Path(__file__).parents[1] / "pcims" / "app" / "pages"
        for path in pages.glob("*.py"):
            with self.subTest(path=path.name):
                source = path.read_text(encoding="utf-8")
                self.assertNotIn("QTableWidget", source)
                self.assertNotIn("QTableWidgetItem", source)

    def test_linux_desktop_install_assets_are_present_and_user_scoped(self):
        root = Path(__file__).parents[1]
        installer = (root / "scripts" / "install-linux.sh").read_text(encoding="utf-8")
        desktop = (root / "packaging" / "linux" / "pcims.desktop").read_text(
            encoding="utf-8"
        )
        self.assertIn("XDG_DATA_HOME", installer)
        self.assertNotIn("sudo", installer)
        self.assertIn('Exec="@PCIMS_EXECUTABLE@"', desktop)
        self.assertIn("Terminal=false", desktop)


if __name__ == "__main__":
    unittest.main()
