"""Append-only application activity recording inside caller transactions."""

import sqlite3


def record_audit_event(
    connection: sqlite3.Connection,
    action: str,
    entity_type: str,
    entity_id: int | None,
    summary: str,
) -> None:
    """Record a concise event atomically with the business change."""
    connection.execute(
        """INSERT INTO audit_events
           (occurred_at,action,entity_type,entity_id,summary)
           VALUES (strftime('%Y-%m-%dT%H:%M:%SZ','now'),?,?,?,?)""",
        (
            action,
            entity_type,
            entity_id,
            summary,
        ),
    )
