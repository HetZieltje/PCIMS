"""Economic dashboard application services."""

from datetime import date, timedelta

from pcims.contracts import BalanceSnapshot
from pcims.db.connection import Database
from pcims.db.reads import ReadQueries
from pcims.models import BalanceBucket, BalancePoint


class BalanceServices:
    database: Database

    def balance_snapshot(
        self, start_date: date | None, end_date: date
    ) -> BalanceSnapshot:
        if start_date is not None and type(start_date) is not date:
            raise TypeError("Dashboard start date must be a date or None.")
        if type(end_date) is not date:
            raise TypeError("Dashboard end date must be a date.")
        with self.database.transaction() as connection:
            queries = ReadQueries(connection)
            if start_date is None:
                earliest, latest = queries.balance_date_bounds()
                start_date = earliest or end_date
                if latest is not None:
                    end_date = max(end_date, latest)
            if start_date > end_date:
                raise ValueError("Dashboard start date cannot be after its end date.")
            bucket = _balance_bucket(start_date, end_date)
            summary, sparse_points = queries.balance_series(
                start_date, end_date, bucket
            )
        return BalanceSnapshot(
            start_date,
            end_date,
            bucket,
            summary,
            _fill_balance_points(start_date, end_date, bucket, sparse_points),
        )


def _balance_bucket(start_date: date, end_date: date) -> BalanceBucket:
    day_count = (end_date - start_date).days + 1
    if day_count <= 45:
        return "day"
    if day_count <= 210:
        return "week"
    if day_count <= 365 * 6:
        return "month"
    return "year"


def _bucket_start(value: date, bucket: BalanceBucket) -> date:
    if bucket == "day":
        return value
    if bucket == "week":
        return value - timedelta(days=value.weekday())
    if bucket == "month":
        return value.replace(day=1)
    return value.replace(month=1, day=1)


def _next_bucket(value: date, bucket: BalanceBucket) -> date:
    if bucket == "day":
        return value + timedelta(days=1)
    if bucket == "week":
        return value + timedelta(days=7)
    if bucket == "month":
        return (
            value.replace(year=value.year + 1, month=1)
            if value.month == 12
            else value.replace(month=value.month + 1)
        )
    return value.replace(year=value.year + 1)


def _fill_balance_points(
    start_date: date,
    end_date: date,
    bucket: BalanceBucket,
    sparse_points: tuple[BalancePoint, ...],
) -> tuple[BalancePoint, ...]:
    by_start = {point.period_start: point for point in sparse_points}
    result: list[BalancePoint] = []
    current = _bucket_start(start_date, bucket)
    final = _bucket_start(end_date, bucket)
    while current <= final:
        result.append(by_start.get(current, BalancePoint(current, 0, 0, 0, 0, 0, 0)))
        if len(result) >= 240 and current < final:
            return sparse_points
        if current == final:
            break
        current = _next_bucket(current, bucket)
    return tuple(result)
