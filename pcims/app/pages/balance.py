"""Period-based economic dashboard for inventory purchases and sales."""

from datetime import date, datetime, timedelta
from typing import cast

from PySide6.QtCore import QDate, Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDateEdit,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QSplitter,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from pcims.app.balance_chart import BalanceChart
from pcims.app.formatting import format_cents, format_percentage_basis_points
from pcims.app.table_model import Column, RecordTableModel, configure_table_view
from pcims.app.tasks import TaskManager
from pcims.contracts import BalanceOperations, BalanceSnapshot
from pcims.models import BalanceBucket, BalancePoint


class BalancePage(QWidget):
    period_changed = Signal()

    def __init__(
        self,
        services: BalanceOperations,
        *,
        tasks: TaskManager,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.services = services
        self.tasks = tasks
        self._bucket: BalanceBucket = "month"
        today = _today()
        self._requested_range: tuple[date | None, date] = (
            today - timedelta(days=364),
            today,
        )
        self._updating_dates = False

        self.period = QComboBox()
        for label, key in (
            ("This month", "month"),
            ("Last 30 days", "30_days"),
            ("Last 90 days", "90_days"),
            ("This year", "year"),
            ("Last 12 months", "12_months"),
            ("All time", "all"),
            ("Custom", "custom"),
        ):
            self.period.addItem(label, key)
        self.period.setCurrentIndex(self.period.findData("12_months"))
        self.start_date = _date_edit(self._requested_range[0] or today)
        self.end_date = _date_edit(today)
        self.range_label = QLabel()
        self.range_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.period.currentIndexChanged.connect(self._preset_changed)
        self.start_date.dateChanged.connect(self._custom_start_changed)
        self.end_date.dateChanged.connect(self._custom_end_changed)
        self._set_custom_enabled(False)

        controls = QHBoxLayout()
        controls.addWidget(QLabel("Period"))
        controls.addWidget(self.period)
        controls.addSpacing(16)
        controls.addWidget(QLabel("From"))
        controls.addWidget(self.start_date)
        controls.addWidget(QLabel("Through"))
        controls.addWidget(self.end_date)
        controls.addSpacing(16)
        controls.addWidget(self.range_label, 1)

        self.metric_labels: dict[str, QLabel] = {}
        metrics = QGridLayout()
        cards = (
            ("purchase", "Purchases", "Purchase cost recorded in this period."),
            ("revenue", "Revenue", "Selling prices recorded in this period."),
            (
                "cost",
                "Cost of sold items",
                "Current purchase cost of items sold in this period.",
            ),
            ("profit", "Realized profit", "Revenue minus cost of sold items."),
            ("cash", "Cash flow", "Revenue minus purchases in this period."),
            ("roi", "ROI on cost", "Profit divided by cost of sold items."),
            ("margin", "Profit margin", "Profit divided by revenue."),
            ("sales", "Sales", "Number of sales recorded in this period."),
            ("items", "Items sold", "Number of individual inventory items sold."),
            (
                "inventory",
                "Current inventory",
                "Current purchase cost of all unsold inventory; not period-specific.",
            ),
        )
        for index, (key, title, tooltip) in enumerate(cards):
            card = QGroupBox(title)
            card.setToolTip(tooltip)
            card_layout = QVBoxLayout(card)
            value = QLabel("—")
            value.setStyleSheet("font-size: 19px; font-weight: 600")
            value.setToolTip(tooltip)
            card_layout.addWidget(value)
            self.metric_labels[key] = value
            metrics.addWidget(card, index // 5, index % 5)

        self.chart = BalanceChart()
        chart_box = QGroupBox("Economic trend")
        chart_layout = QVBoxLayout(chart_box)
        chart_layout.addWidget(self.chart)

        self.table_model = RecordTableModel[BalancePoint](
            (
                Column(
                    "Period",
                    lambda point: _point_label(point, self._bucket),
                    lambda point: point.period_start.toordinal(),
                ),
                Column(
                    "Purchases",
                    lambda point: format_cents(point.purchase_cents),
                    lambda point: point.purchase_cents,
                ),
                Column(
                    "Revenue",
                    lambda point: format_cents(point.revenue_cents),
                    lambda point: point.revenue_cents,
                ),
                Column(
                    "Cost sold",
                    lambda point: format_cents(point.realized_cost_cents),
                    lambda point: point.realized_cost_cents,
                ),
                Column(
                    "Profit",
                    lambda point: format_cents(point.profit_cents),
                    lambda point: point.profit_cents,
                ),
                Column(
                    "Cash flow",
                    lambda point: format_cents(point.cash_flow_cents),
                    lambda point: point.cash_flow_cents,
                ),
                Column(
                    "ROI",
                    lambda point: format_percentage_basis_points(
                        point.roi_basis_points
                    ),
                    lambda point: point.roi_basis_points or 0,
                ),
                Column(
                    "Purchased items",
                    lambda point: str(point.purchase_count),
                    lambda point: point.purchase_count,
                ),
                Column(
                    "Sales",
                    lambda point: str(point.sale_count),
                    lambda point: point.sale_count,
                ),
                Column(
                    "Items sold",
                    lambda point: str(point.sold_item_count),
                    lambda point: point.sold_item_count,
                ),
            ),
            lambda point: point.period_start.toordinal(),
        )
        self.table = QTableView()
        configure_table_view(self.table, self.table_model, stretch_column=0)
        table_box = QGroupBox("Period breakdown")
        table_layout = QVBoxLayout(table_box)
        table_layout.addWidget(self.table)

        self.splitter = QSplitter(Qt.Orientation.Vertical)
        self.splitter.addWidget(chart_box)
        self.splitter.addWidget(table_box)
        self.splitter.setSizes((300, 210))

        layout = QVBoxLayout(self)
        layout.addLayout(controls)
        layout.addLayout(metrics)
        layout.addWidget(self.splitter, 1)

    def load_snapshot(self) -> BalanceSnapshot:
        selected_range = self._requested_range
        return self.services.balance_snapshot(*selected_range)

    def apply_snapshot(self, snapshot: BalanceSnapshot) -> None:
        summary = snapshot.summary
        for key, cents in (
            ("purchase", summary.purchase_cents),
            ("revenue", summary.revenue_cents),
            ("cost", summary.realized_cost_cents),
            ("profit", summary.profit_cents),
            ("cash", summary.cash_flow_cents),
            ("inventory", summary.current_inventory_cents),
        ):
            self.metric_labels[key].setText(format_cents(cents))
        self.metric_labels["roi"].setText(
            format_percentage_basis_points(summary.roi_basis_points)
        )
        self.metric_labels["margin"].setText(
            format_percentage_basis_points(summary.profit_margin_basis_points)
        )
        self.metric_labels["sales"].setText(f"{summary.sale_count:,}")
        self.metric_labels["items"].setText(f"{summary.sold_item_count:,}")
        self.range_label.setText(
            f"{snapshot.start_date.isoformat()} through {snapshot.end_date.isoformat()}"
            f" · grouped by {_bucket_name(snapshot.bucket)}"
        )
        self.chart.set_series(snapshot.points, snapshot.bucket)
        self._bucket = snapshot.bucket
        self.table_model.set_records(snapshot.points)

    def _preset_changed(self) -> None:
        key = str(self.period.currentData())
        if key == "custom":
            self._set_custom_enabled(True)
            self._custom_range_changed()
            return
        self._set_custom_enabled(False)
        today = _today()
        if key == "month":
            start: date | None = today.replace(day=1)
        elif key == "30_days":
            start = today - timedelta(days=29)
        elif key == "90_days":
            start = today - timedelta(days=89)
        elif key == "year":
            start = today.replace(month=1, day=1)
        elif key == "12_months":
            start = today - timedelta(days=364)
        else:
            start = None
        self._requested_range = (start, today)
        if start is not None:
            self._set_dates(start, today)
        self.period_changed.emit()

    def _custom_start_changed(self) -> None:
        if self._updating_dates or self.period.currentData() != "custom":
            return
        start = cast(date, self.start_date.date().toPython())
        end = cast(date, self.end_date.date().toPython())
        if start > end:
            end = start
            self._set_dates(start, end)
        self._requested_range = (start, end)
        self.period_changed.emit()

    def _custom_end_changed(self) -> None:
        if self._updating_dates or self.period.currentData() != "custom":
            return
        start = cast(date, self.start_date.date().toPython())
        end = cast(date, self.end_date.date().toPython())
        if end < start:
            start = end
            self._set_dates(start, end)
        self._requested_range = (start, end)
        self.period_changed.emit()

    def _custom_range_changed(self) -> None:
        start = cast(date, self.start_date.date().toPython())
        end = cast(date, self.end_date.date().toPython())
        self._requested_range = (min(start, end), max(start, end))
        self.period_changed.emit()

    def _set_dates(self, start: date, end: date) -> None:
        self._updating_dates = True
        try:
            self.start_date.setDate(QDate(start.year, start.month, start.day))
            self.end_date.setDate(QDate(end.year, end.month, end.day))
        finally:
            self._updating_dates = False

    def _set_custom_enabled(self, enabled: bool) -> None:
        self.start_date.setEnabled(enabled)
        self.end_date.setEnabled(enabled)


def _date_edit(value: date) -> QDateEdit:
    widget = QDateEdit(QDate(value.year, value.month, value.day))
    widget.setCalendarPopup(True)
    widget.setDisplayFormat("yyyy-MM-dd")
    return widget


def _today() -> date:
    return datetime.now().astimezone().date()


def _bucket_name(bucket: BalanceBucket) -> str:
    return {"day": "day", "week": "week", "month": "month", "year": "year"}[bucket]


def _point_label(point: BalancePoint, bucket: BalanceBucket) -> str:
    value = point.period_start
    if bucket == "day":
        return value.isoformat()
    if bucket == "week":
        return f"Week of {value.isoformat()}"
    if bucket == "month":
        return value.strftime("%B %Y")
    return str(value.year)
