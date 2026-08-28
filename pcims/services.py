"""Application service composition root.

The presentation layer keeps one stable capability object, while implementation is
split by domain so inventory, laptops, sales, balance, and maintenance can evolve and
be tested independently.
"""

from dataclasses import dataclass

from pcims.db.connection import Database, default_database
from pcims.db.schema import initialize_database
from pcims.service_layer.balance import BalanceServices
from pcims.service_layer.inventory import InventoryServices
from pcims.service_layer.laptops import LaptopServices
from pcims.service_layer.maintenance import MaintenanceServices
from pcims.service_layer.sales import SalesServices


@dataclass(frozen=True, slots=True)
class ApplicationServices(
    InventoryServices,
    LaptopServices,
    SalesServices,
    BalanceServices,
    MaintenanceServices,
):
    """All technical operations available to the Qt presentation layer."""

    database: Database

    def initialize(self) -> None:
        initialize_database(self.database)


def default_services() -> ApplicationServices:
    """Build services once at the application composition root."""
    return ApplicationServices(default_database())
