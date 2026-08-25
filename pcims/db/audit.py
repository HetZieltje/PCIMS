"""Lightweight application activity recording inside caller transactions."""

import sqlite3

from pcims.db.connection import Database


def record_audit_event(
    connection: sqlite3.Connection,
    action: str,
    entity_type: str,
    entity_id: int | None,
    summary: str,
) -> None:
    """Record a concise event atomically with the business change."""
    connection.execute(
        """INSERT INTO activity_events
           (occurred_at,action,entity_type,entity_id,summary)
           VALUES (strftime('%Y-%m-%dT%H:%M:%SZ','now'),?,?,?,?)""",
        (
            action,
            entity_type,
            entity_id,
            summary,
        ),
    )


def clear_activity(*, database: Database) -> None:
    """Clear the optional activity feed without touching inventory records."""
    with database.transaction(write=True) as connection:
        connection.execute("DELETE FROM activity_events")
