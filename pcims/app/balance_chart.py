"""Small palette-aware trend chart without an additional charting dependency."""

from collections.abc import Callable

from PySide6.QtCore import QLineF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPaintEvent, QPen
from PySide6.QtWidgets import QWidget

from pcims.models import BalanceBucket, BalancePoint


class BalanceChart(QWidget):
    """Render purchase, revenue, and profit trends from bounded dashboard points."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._points: tuple[BalancePoint, ...] = ()
        self._bucket: BalanceBucket = "month"
        self.setMinimumHeight(190)

    @property
    def points(self) -> tuple[BalancePoint, ...]:
        return self._points

    def set_series(
        self,
        points: tuple[BalancePoint, ...],
        bucket: BalanceBucket,
    ) -> None:
        self._points = points
        self._bucket = bucket
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        palette = self.palette()
        text_color = palette.color(palette.ColorRole.Text)
        muted = palette.color(palette.ColorRole.PlaceholderText)
        grid = palette.color(palette.ColorRole.Mid)
        plot = QRectF(
            70.0, 34.0, max(1.0, self.width() - 92.0), max(1.0, self.height() - 82.0)
        )
        series: tuple[tuple[str, QColor, Callable[[BalancePoint], int]], ...] = (
            ("Purchases", QColor("#e69f00"), lambda point: point.purchase_cents),
            ("Revenue", QColor("#3b82f6"), lambda point: point.revenue_cents),
            ("Profit", QColor("#22a06b"), lambda point: point.profit_cents),
        )
        self._draw_legend(painter, series, text_color)
        values = [getter(point) for _, _, getter in series for point in self._points]
        if not self._points or not any(values):
            painter.setPen(muted)
            painter.drawText(
                plot,
                Qt.AlignmentFlag.AlignCenter,
                "No purchases or sales in this period",
            )
            return

        minimum = min(0, *values)
        maximum = max(0, *values)
        span = max(1, maximum - minimum)
        padding = max(1, span // 12)
        minimum -= padding
        maximum += padding
        span = maximum - minimum

        def y_for(value: int) -> float:
            return plot.bottom() - ((value - minimum) / span) * plot.height()

        painter.setFont(self.font())
        for line in range(5):
            ratio = line / 4
            y = plot.top() + ratio * plot.height()
            value = round(maximum - ratio * span)
            painter.setPen(QPen(grid, 1, Qt.PenStyle.DotLine))
            painter.drawLine(QLineF(plot.left(), y, plot.right(), y))
            painter.setPen(muted)
            painter.drawText(
                QRectF(0.0, y - 10.0, 64.0, 20.0),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                _compact_money(value),
            )

        zero_y = y_for(0)
        painter.setPen(QPen(grid, 1.5))
        painter.drawLine(QLineF(plot.left(), zero_y, plot.right(), zero_y))

        count = len(self._points)

        def x_for(index: int) -> float:
            if count == 1:
                return plot.center().x()
            return plot.left() + (index / (count - 1)) * plot.width()

        for _label, color, getter in series:
            path = QPainterPath()
            for index, point in enumerate(self._points):
                x = x_for(index)
                y = y_for(getter(point))
                if index == 0:
                    path.moveTo(x, y)
                else:
                    path.lineTo(x, y)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(color, 2.2))
            painter.drawPath(path)
            if count <= 36:
                painter.setBrush(color)
                painter.setPen(Qt.PenStyle.NoPen)
                for index, point in enumerate(self._points):
                    painter.drawEllipse(
                        QRectF(
                            x_for(index) - 2.8,
                            y_for(getter(point)) - 2.8,
                            5.6,
                            5.6,
                        )
                    )

        painter.setPen(text_color)
        label_indexes = sorted({0, count // 2, count - 1})
        for index in label_indexes:
            x = x_for(index)
            painter.drawText(
                QRectF(x - 65.0, plot.bottom() + 8.0, 130.0, 24.0),
                Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
                _period_label(self._points[index], self._bucket),
            )

    @staticmethod
    def _draw_legend(
        painter: QPainter,
        series: tuple[tuple[str, QColor, Callable[[BalancePoint], int]], ...],
        text_color: QColor,
    ) -> None:
        x = 74.0
        for label, color, _getter in series:
            painter.setPen(QPen(color, 3))
            painter.drawLine(QLineF(x, 16.0, x + 20.0, 16.0))
            painter.setPen(text_color)
            painter.drawText(QRectF(x + 26.0, 5.0, 92.0, 22.0), label)
            x += 122.0


def _period_label(point: BalancePoint, bucket: BalanceBucket) -> str:
    value = point.period_start
    if bucket == "day":
        return value.strftime("%d %b")
    if bucket == "week":
        return f"Week of {value.strftime('%d %b')}"
    if bucket == "month":
        return value.strftime("%b %Y")
    return str(value.year)


def _compact_money(cents: int) -> str:
    euros = cents / 100
    absolute = abs(euros)
    if absolute >= 1_000_000:
        value = f"{absolute / 1_000_000:.1f}m"
    elif absolute >= 1_000:
        value = f"{absolute / 1_000:.1f}k"
    else:
        value = f"{absolute:.0f}"
    return f"−€{value}" if cents < 0 else f"€{value}"
