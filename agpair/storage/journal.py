from __future__ import annotations

from pathlib import Path

from agpair.models import JournalRecord, utcnow_iso
from agpair.storage.db import connect


class JournalRepository:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def append(self, task_id: str, source: str, event: str, body: str, classification: str = "normal") -> None:
        with connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO journal (task_id, source, event, body, classification, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (task_id, source, event, body, classification, utcnow_iso()),
            )
            conn.commit()

    def delete_older_than(self, cutoff_iso: str) -> int:
        """Delete journal entries older than cutoff. Returns count deleted."""
        with connect(self.db_path) as conn:
            cursor = conn.execute(
                "DELETE FROM journal WHERE created_at < ?", (cutoff_iso,)
            )
            conn.commit()
            return cursor.rowcount

    def count_older_than(self, cutoff_iso: str) -> int:
        """Count journal entries that would be deleted by cleanup."""
        with connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM journal WHERE created_at < ?", (cutoff_iso,)
            ).fetchone()
            return row[0]

    def tail(self, task_id: str, limit: int = 20) -> list[JournalRecord]:
        with connect(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT task_id, source, event, body, classification, created_at
                FROM journal
                WHERE task_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (task_id, limit),
            ).fetchall()
        return [
            JournalRecord(
                task_id=row["task_id"],
                source=row["source"],
                event=row["event"],
                body=row["body"],
                created_at=row["created_at"],
                classification=row["classification"] if "classification" in row.keys() else "normal",
            )
            for row in rows
        ]
