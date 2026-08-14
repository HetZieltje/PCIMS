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
        database_package = root / "pcims" / "db"
        source = "\n".join(
            path.read_text(encoding="utf-8")
            for path in database_package.glob("*.py")
        )
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

    def test_schema_reads_records_and_commands_have_separate_modules(self):
        root = Path(__file__).parents[1] / "pcims" / "db"
        reads = (root / "reads.py").read_text(encoding="utf-8")
        commands = (root / "commands.py").read_text(encoding="utf-8")
        records = (root / "records.py").read_text(encoding="utf-8")
        schema = (root / "schema.py").read_text(encoding="utf-8")

        self.assertFalse((root / "queries.py").exists())
        self.assertIn("class ReadQueries", reads)
        self.assertNotIn("INSERT INTO", reads)
        self.assertIn("def sell_pc", commands)
        self.assertNotIn("class ReadQueries", commands)
        self.assertIn("def expense_from_row", records)
        self.assertNotIn("CREATE TABLE", reads + commands + records)
        self.assertNotIn("PRAGMA user_version", reads + commands + records)
        self.assertIn("SCHEMA_DEFINITIONS", schema)
        self.assertIn("validate_current_data", schema)

    def test_database_operations_have_no_implicit_process_global_fallback(self):
        database_package = Path(__file__).parents[1] / "pcims" / "db"
        source = "\n".join(
            path.read_text(encoding="utf-8") for path in database_package.glob("*.py")
        )
        self.assertNotIn("configure_database", source)
        self.assertNotIn("get_database()", source)
        self.assertNotIn("database: Database | None", source)

    def test_qt_pages_depend_on_application_services_not_query_globals(self):
        pages = Path(__file__).parents[1] / "pcims" / "app" / "pages"
        for path in pages.glob("*.py"):
            with self.subTest(path=path.name):
                source = path.read_text(encoding="utf-8")
                self.assertNotIn("from pcims.db.commands", source)
                self.assertNotIn("from pcims.db.reads", source)
                self.assertNotIn("from pcims.db.backup import create_backup", source)

    def test_service_commands_are_typed_and_normalized_outside_sql_workflows(self):
        root = Path(__file__).parents[1] / "pcims"
        services = (root / "services.py").read_text(encoding="utf-8")
        commands = (root / "db" / "commands.py").read_text(encoding="utf-8")
        self.assertNotIn("PurchaseInput", services)
        self.assertNotIn("Iterable[object]", services)
        self.assertNotIn("selling_price: object", services)
        self.assertNotIn("def _money_cents", commands)
        self.assertNotIn("def _iso_date", commands)

    def test_database_coordination_cannot_be_bypassed_by_low_level_workflows(self):
        root = Path(__file__).parents[1] / "pcims"
        connection = (root / "db" / "connection.py").read_text(encoding="utf-8")
        backup = (root / "db" / "backup.py").read_text(encoding="utf-8")
        services = (root / "services.py").read_text(encoding="utf-8")
        self.assertIn("with self.gate.shared():", connection)
        self.assertIn("with database.gate.exclusive():", backup)
        self.assertNotIn("database.gate", services)

    def test_flat_application_grids_use_model_view_not_cell_widgets(self):
        pages = Path(__file__).parents[1] / "pcims" / "app" / "pages"
        for path in pages.glob("*.py"):
            with self.subTest(path=path.name):
                source = path.read_text(encoding="utf-8")
                self.assertNotIn("QTableWidget", source)
                self.assertNotIn("QTableWidgetItem", source)

    def test_background_work_has_one_owned_lifecycle_and_explicit_refresh_contracts(self):
        app = Path(__file__).parents[1] / "pcims" / "app"
        source = "\n".join(
            path.read_text(encoding="utf-8") for path in app.rglob("*.py")
        )
        main_window = (app / "main_window.py").read_text(encoding="utf-8")
        self.assertNotIn("run_in_background", source)
        self.assertIn("class TaskManager", source)
        self.assertIn("bind_refresh(", main_window)
        self.assertNotIn('getattr(page, "load_snapshot"', main_window)
        self.assertNotIn('getattr(page, "command_running"', main_window)

    def test_linux_desktop_install_assets_are_present_and_user_scoped(self):
        root = Path(__file__).parents[1]
        installer = (root / "scripts" / "install-linux.sh").read_text(encoding="utf-8")
        desktop = (root / "packaging" / "linux" / "pcims.desktop").read_text(
            encoding="utf-8"
        )
        self.assertIn("XDG_DATA_HOME", installer)
        self.assertNotIn("sudo", installer)
        self.assertIn("requirements.lock", installer)
        self.assertIn("--require-hashes", installer)
        self.assertIn("staging_root", installer)
        self.assertIn("smoke-installed.py", installer)
        self.assertIn('Exec="@PCIMS_PYTHON@" -m pcims.app.application', desktop)
        self.assertIn("Terminal=false", desktop)

    def test_dependencies_have_cross_platform_hash_locks(self):
        root = Path(__file__).parents[1]
        runtime_lock = (root / "requirements.lock").read_text(encoding="utf-8")
        development_lock = (root / "requirements-dev.lock").read_text(
            encoding="utf-8"
        )
        workflow = (root / ".github" / "workflows" / "test.yml").read_text(
            encoding="utf-8"
        )
        for lock in (runtime_lock, development_lock):
            self.assertIn("--hash=sha256:", lock)
            self.assertIn("pyside6==", lock)
        self.assertIn("--require-hashes -r requirements-dev.lock", workflow)

    def test_purchase_staging_uses_immutable_domain_records(self):
        purchases = (
            Path(__file__).parents[1]
            / "pcims"
            / "app"
            / "pages"
            / "purchases.py"
        ).read_text(encoding="utf-8")
        self.assertIn("@dataclass(frozen=True, slots=True)", purchases)
        self.assertNotIn("TypedDict", purchases)
        self.assertIn("expense: NewExpense", purchases)


if __name__ == "__main__":
    unittest.main()
