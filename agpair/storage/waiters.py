"""Repository for persisted waiter state.

Each record tracks a single blocking wait (from ``task wait``, or the
default auto-wait on ``task start/continue/approve/reject/retry``).
Only one active waiter (`state='waiting'`) is allowed per task at a time,
enforced by a partial unique index.
"""
from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from agpair.models import WaiterRecord, utcnow_iso
from agpair.storage.db import connect


class WaiterRepository:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def start_waiter(self, *, task_id: str, command: str) -> WaiterRecord:
        """Create an active waiter for *task_id*.

        If an existing active waiter for the same task exists the unique
        index will cause an IntegrityError — callers should handle that.
        """
        now = utcnow_iso()
        waiter_id = f"W-{uuid4().hex[:12].upper()}"
        with connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO waiters (waiter_id, task_id, command, state, started_at, last_poll_at)
                VALUES (?, ?, ?, 'waiting', ?, ?)
                """,
                (waiter_id, task_id, command, now, now),
            )
            conn.commit()
        return WaiterRecord(
            waiter_id=waiter_id,
            task_id=task_id,
            command=command,
            state="waiting",
            started_at=now,
            last_poll_at=now,
        )

    def update_poll(self, waiter_id: str) -> None:
        """Bump *last_poll_at* to now."""
        now = utcnow_iso()
        with connect(self.db_path) as conn:
            conn.execute(
                "UPDATE waiters SET last_poll_at=? WHERE waiter_id=?",
                (now, waiter_id),
            )
            conn.commit()

    def finalize(self, waiter_id: str, *, outcome: str) -> None:
        """Mark a waiter terminal with the given outcome string."""
        now = utcnow_iso()
        with connect(self.db_path) as conn:
            conn.execute(
                """
                UPDATE waiters SET state='terminal', finished_at=?, outcome=?
                WHERE waiter_id=?
                """,
                (now, outcome, waiter_id),
            )
            conn.commit()

    def get_active_waiter(self, task_id: str) -> WaiterRecord | None:
        """Return the current active waiter for *task_id*, or None."""
        with connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM waiters WHERE task_id=? AND state='waiting'",
                (task_id,),
            ).fetchone()
        if row is None:
            return None
        return self._from_row(row)

    def list_active_waiters(self) -> list[WaiterRecord]:
        """Return all active (waiting) waiters across all tasks."""
        with connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM waiters WHERE state='waiting' ORDER BY started_at ASC"
            ).fetchall()
        return [self._from_row(r) for r in rows]

    @staticmethod
    def _from_row(row) -> WaiterRecord:
        return WaiterRecord(
            waiter_id=row["waiter_id"],
            task_id=row["task_id"],
            command=row["command"],
            state=row["state"],
            started_at=row["started_at"],
            last_poll_at=row["last_poll_at"],
            finished_at=row["finished_at"],
            outcome=row["outcome"],
        )
