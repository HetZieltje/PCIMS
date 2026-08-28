"""Sales, sale history, and summary application services."""

from collections.abc import Iterable

from pcims.contracts import HistoryPage, SalesSnapshot
from pcims.db.connection import Database
from pcims.db.reads import ReadQueries
from pcims.db.sale_commands import sell_items, sell_pc, undo_sale, update_sale
from pcims.domain import SaleTerms
from pcims.models import Expense, FinancialSummary, Sale

MAX_HISTORY_PAGE_SIZE = 1_000


class SalesServices:
    database: Database

    def sales_snapshot(
        self,
        expense_offset: int = 0,
        sale_offset: int = 0,
        page_size: int = 500,
        search: str = "",
    ) -> SalesSnapshot:
        page_size = _history_page_size(page_size)
        expense_offset = _history_offset(expense_offset)
        sale_offset = _history_offset(sale_offset)
        search = str(search).strip()[:200]
        with self.database.transaction() as connection:
            queries = ReadQueries(connection)
            expense_total = queries.count_expenses(search)
            sale_total = queries.count_sales(search)
            expense_offset = _clamped_page_offset(
                expense_offset, expense_total, page_size
            )
            sale_offset = _clamped_page_offset(sale_offset, sale_total, page_size)
            expenses = queries.list_expense_page(expense_offset, page_size, search)
            return SalesSnapshot(
                queries.financial_summary(),
                HistoryPage(expenses, expense_offset, expense_total, page_size),
                HistoryPage(
                    queries.list_sale_page(sale_offset, page_size, search),
                    sale_offset,
                    sale_total,
                    page_size,
                ),
            )

    def sale_item_page(
        self, sale_id: int, offset: int = 0, page_size: int = 500
    ) -> HistoryPage[Expense]:
        if isinstance(sale_id, bool) or not isinstance(sale_id, int) or sale_id < 1:
            raise ValueError("Sale ID must be positive.")
        page_size = _history_page_size(page_size)
        offset = _history_offset(offset)
        with self.database.transaction() as connection:
            queries = ReadQueries(connection)
            total = queries.count_sale_items(sale_id)
            offset = _clamped_page_offset(offset, total, page_size)
            records = queries.list_sale_item_page(sale_id, offset, page_size)
        return HistoryPage(records, offset, total, page_size)

    def sell_items(self, expense_ids: Iterable[int], terms: SaleTerms) -> int:
        return sell_items(expense_ids, terms, database=self.database)

    def sell_pc(self, pc_id: int, terms: SaleTerms) -> int:
        return sell_pc(pc_id, terms, database=self.database)

    def list_sales(self) -> tuple[Sale, ...]:
        with self.database.transaction() as connection:
            return ReadQueries(connection).list_sales()

    def undo_sale(self, sale_id: int) -> None:
        undo_sale(sale_id, database=self.database)

    def update_sale(self, sale_id: int, terms: SaleTerms) -> None:
        update_sale(sale_id, terms, database=self.database)

    def financial_summary(self) -> FinancialSummary:
        with self.database.transaction() as connection:
            return ReadQueries(connection).financial_summary()


def _clamped_page_offset(offset: int, total: int, limit: int) -> int:
    if total == 0:
        return 0
    return min(offset, ((total - 1) // limit) * limit)


def _history_page_size(value: int) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 1 <= value <= MAX_HISTORY_PAGE_SIZE
    ):
        raise ValueError(
            f"History page size must be between 1 and {MAX_HISTORY_PAGE_SIZE}."
        )
    return value


def _history_offset(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("History offsets must be non-negative integers.")
    return value
