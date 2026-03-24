from __future__ import annotations

from pathlib import Path
import sqlite3

from agpair.models import utcnow_iso
from agpair.storage.db import connect


class ReceiptRepository:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def delete_older_than(self, cutoff_iso: str) -> int:
        """Delete receipts older than cutoff. Returns count deleted."""
        with connect(self.db_path) as conn:
            cursor = conn.execute(
                "DELETE FROM receipts WHERE created_at < ?", (cutoff_iso,)
            )
            conn.commit()
            return cursor.rowcount

    def count_older_than(self, cutoff_iso: str) -> int:
        """Count receipts that would be deleted by cleanup."""
        with connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM receipts WHERE created_at < ?", (cutoff_iso,)
            ).fetchone()
            return row[0]

    def record(
        self,
        message_id: str,
        task_id: str,
        status: str,
        *,
        delivery_id: str | None = None,
    ) -> bool:
        """Insert a receipt row and return ``True`` if it was new.

        Deduplication layers:
        1. ``message_id`` PK  – rejects exact message replays.
        2. ``(task_id, delivery_id)`` unique index (when delivery_id is not
           NULL) – rejects different messages that carry the same logical
           terminal delivery identity.

        Returns ``False`` (and does not insert) on either collision.
        """
        with connect(self.db_path) as conn:
            try:
                conn.execute(
                    "INSERT INTO receipts (message_id, task_id, status, delivery_id, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (message_id, task_id, status, delivery_id, utcnow_iso()),
                )
                conn.commit()
                return True
            except sqlite3.IntegrityError as exc:
                msg = str(exc)
                if "UNIQUE constraint failed" in msg:
                    return False
                raise
