from __future__ import annotations

from pathlib import Path

from agpair.models import TaskRecord, utcnow_iso
from agpair.storage.db import connect


class TaskNotFoundError(RuntimeError):
    """Raised when a requested task does not exist."""


class IllegalTransitionError(RuntimeError):
    """Raised when a phase transition is not allowed."""


# Valid source phases for each mark_* transition.
# None means any phase is allowed (e.g. abandon from anywhere).
_VALID_TRANSITIONS: dict[str, set[str] | None] = {
    "acked": {"new", "evidence_ready", "blocked", "committed", "stuck", "abandoned"},
    "evidence_ready": {"acked"},
    "blocked": {"acked", "new"},
    "committed": {"acked", "evidence_ready"},
    "stuck": {"acked"},
    "abandoned": None,  # can abandon from any phase
    "new": None,  # apply_retry_dispatch resets to new
}


def _check_transition(task: TaskRecord, target_phase: str) -> None:
    valid_sources = _VALID_TRANSITIONS.get(target_phase)
    if valid_sources is not None and task.phase not in valid_sources:
        raise IllegalTransitionError(
            f"cannot transition {task.task_id!r} from {task.phase!r} to {target_phase!r}"
        )


class TaskRepository:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def create_task(self, *, task_id: str, repo_path: str, client_idempotency_key: str | None = None, executor_backend: str | None = None) -> None:
        now = utcnow_iso()
        with connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO tasks (
                  task_id, repo_path, phase, antigravity_session_id, attempt_no, retry_count,
                  last_receipt_id, stuck_reason, retry_recommended, last_activity_at, created_at, updated_at,
                  last_heartbeat_at, last_workspace_activity_at, client_idempotency_key, executor_backend
                ) VALUES (?, ?, 'new', NULL, 1, 0, NULL, NULL, 0, ?, ?, ?, NULL, NULL, ?, ?)
                """,
                (task_id, repo_path, now, now, now, client_idempotency_key, executor_backend),
            )
            conn.commit()

    def get_task_by_idempotency_key(self, *, repo_path: str, client_idempotency_key: str) -> TaskRecord | None:
        with connect(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT * FROM tasks
                WHERE repo_path = ? AND client_idempotency_key = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (repo_path, client_idempotency_key),
            ).fetchone()
        if row is None:
            return None
        return self._task_from_row(row)

    def mark_acked(self, *, task_id: str, session_id: str) -> None:
        task = self.get_task(task_id)
        if task is None:
            raise TaskNotFoundError(f"task not found: {task_id}")
        _check_transition(task, "acked")
        now = utcnow_iso()
        self._update(
            task_id,
            """
            UPDATE tasks
            SET phase='acked', antigravity_session_id=?,
                stuck_reason=NULL, retry_recommended=0,
                last_receipt_id=NULL, last_heartbeat_at=NULL,
                last_workspace_activity_at=NULL,
                last_activity_at=?, updated_at=?
            WHERE task_id=?
            """,
            (session_id, now, now, task_id),
        )

    def mark_evidence_ready(self, *, task_id: str, last_receipt_id: str | None = None) -> None:
        task = self.get_task(task_id)
        if task is None:
            raise TaskNotFoundError(f"task not found: {task_id}")
        _check_transition(task, "evidence_ready")
        now = utcnow_iso()
        self._update(
            task_id,
            """
            UPDATE tasks
            SET phase='evidence_ready', last_receipt_id=?, last_activity_at=?, updated_at=?
            WHERE task_id=?
            """,
            (last_receipt_id, now, now, task_id),
        )

    def mark_blocked(self, *, task_id: str, reason: str | None = None) -> None:
        task = self.get_task(task_id)
        if task is None:
            raise TaskNotFoundError(f"task not found: {task_id}")
        _check_transition(task, "blocked")
        now = utcnow_iso()
        self._update(
            task_id,
            """
            UPDATE tasks
            SET phase='blocked', stuck_reason=?, last_activity_at=?, updated_at=?
            WHERE task_id=?
            """,
            (reason, now, now, task_id),
        )

    def mark_committed(self, *, task_id: str, last_receipt_id: str | None = None) -> None:
        task = self.get_task(task_id)
        if task is None:
            raise TaskNotFoundError(f"task not found: {task_id}")
        _check_transition(task, "committed")
        now = utcnow_iso()
        self._update(
            task_id,
            """
            UPDATE tasks
            SET phase='committed', last_receipt_id=?, last_activity_at=?, updated_at=?
            WHERE task_id=?
            """,
            (last_receipt_id, now, now, task_id),
        )

    def mark_stuck(self, *, task_id: str, reason: str) -> None:
        task = self.get_task(task_id)
        if task is None:
            raise TaskNotFoundError(f"task not found: {task_id}")
        _check_transition(task, "stuck")
        now = utcnow_iso()
        self._update(
            task_id,
            """
            UPDATE tasks
            SET phase='stuck', stuck_reason=?, last_activity_at=?, updated_at=?
            WHERE task_id=?
            """,
            (reason, now, now, task_id),
        )

    def mark_abandoned(self, *, task_id: str, reason: str) -> None:
        task = self.get_task(task_id)
        if task is None:
            raise TaskNotFoundError(f"task not found: {task_id}")
        _check_transition(task, "abandoned")
        now = utcnow_iso()
        self._update(
            task_id,
            """
            UPDATE tasks
            SET phase='abandoned', stuck_reason=?, last_activity_at=?, updated_at=?
            WHERE task_id=?
            """,
            (reason, now, now, task_id),
        )

    def delete_terminal_older_than(self, cutoff_iso: str) -> int:
        """Delete tasks in terminal phase older than cutoff. Returns count deleted."""
        from agpair.models import TERMINAL_PHASES

        with connect(self.db_path) as conn:
            placeholders = ",".join("?" for _ in TERMINAL_PHASES)
            # Clean orphaned waiters first
            conn.execute(
                f"DELETE FROM waiters WHERE task_id IN "
                f"(SELECT task_id FROM tasks WHERE phase IN ({placeholders}) AND created_at < ?)",
                (*TERMINAL_PHASES, cutoff_iso),
            )
            cursor = conn.execute(
                f"DELETE FROM tasks WHERE phase IN ({placeholders}) AND created_at < ?",
                (*TERMINAL_PHASES, cutoff_iso),
            )
            conn.commit()
            return cursor.rowcount

    def count_terminal_older_than(self, cutoff_iso: str) -> int:
        """Count tasks that would be deleted by cleanup."""
        from agpair.models import TERMINAL_PHASES

        with connect(self.db_path) as conn:
            placeholders = ",".join("?" for _ in TERMINAL_PHASES)
            row = conn.execute(
                f"SELECT COUNT(*) FROM tasks WHERE phase IN ({placeholders}) AND created_at < ?",
                (*TERMINAL_PHASES, cutoff_iso),
            ).fetchone()
            return row[0]

    def record_heartbeat(self, *, task_id: str, heartbeat_at: str | None = None) -> None:
        """Record a RUNNING heartbeat — updates liveness without changing phase.

        This deliberately does NOT touch last_activity_at, preserving the
        distinction between heartbeat liveness (last_heartbeat_at) and real task
        progress (last_activity_at / phase transitions).
        """
        now = heartbeat_at or utcnow_iso()
        self._update(
            task_id,
            """
            UPDATE tasks
            SET last_heartbeat_at=?, updated_at=?
            WHERE task_id=?
            """,
            (now, now, task_id),
        )

    def update_workspace_activity(self, *, task_id: str, activity_at: str) -> None:
        """Update last_workspace_activity_at for an acked task."""
        self._update(
            task_id,
            """
            UPDATE tasks
            SET last_workspace_activity_at=?, updated_at=?
            WHERE task_id=?
            """,
            (activity_at, utcnow_iso(), task_id),
        )

    def recommend_retry(self, *, task_id: str, retry_count: int | None = None) -> None:
        now = utcnow_iso()
        if retry_count is None:
            sql = """
            UPDATE tasks
            SET retry_recommended=1, updated_at=?
            WHERE task_id=?
            """
            params = (now, task_id)
        else:
            sql = """
            UPDATE tasks
            SET retry_recommended=1, retry_count=?, updated_at=?
            WHERE task_id=?
            """
            params = (retry_count, now, task_id)
        self._update(task_id, sql, params)

    def prepare_retry(self, *, task_id: str) -> TaskRecord:
        task = self.get_task(task_id)
        if task is None:
            raise TaskNotFoundError(f"task not found: {task_id}")
        return task

    def apply_retry_dispatch(self, *, task_id: str) -> TaskRecord:
        task = self.get_task(task_id)
        if task is None:
            raise TaskNotFoundError(f"task not found: {task_id}")
        now = utcnow_iso()
        next_attempt = task.attempt_no + 1
        next_retry_count = task.retry_count + 1
        with connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                UPDATE tasks
                SET phase='new',
                    antigravity_session_id=NULL,
                    attempt_no=?,
                    retry_count=?,
                    last_receipt_id=NULL,
                    stuck_reason=NULL,
                    retry_recommended=0,
                    last_activity_at=?,
                    updated_at=?,
                    last_heartbeat_at=NULL,
                    last_workspace_activity_at=NULL
                WHERE task_id=?
                """,
                (next_attempt, next_retry_count, now, now, task_id),
            )
            if cursor.rowcount == 0:
                raise TaskNotFoundError(f"task not found: {task_id}")
            conn.commit()
        updated = self.get_task(task_id)
        assert updated is not None
        return updated

    def get_most_relevant_active_task(self, repo_path: str) -> TaskRecord | None:
        """Find the most relevant active or recently active task for a repo."""
        with connect(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT * FROM tasks
                WHERE repo_path = ?
                ORDER BY
                  CASE
                    WHEN phase IN ('new', 'acked', 'evidence_ready') THEN 0
                    WHEN phase IN ('blocked', 'stuck') THEN 1
                    ELSE 2
                  END ASC,
                  updated_at DESC
                LIMIT 1
                """,
                (repo_path,),
            ).fetchone()
        if row is None:
            return None
        return self._task_from_row(row)

    def get_task(self, task_id: str) -> TaskRecord | None:
        with connect(self.db_path) as conn:
            row = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        if row is None:
            return None
        return self._task_from_row(row)

    def list_stale_acked_tasks(self, cutoff_iso: str) -> list[TaskRecord]:
        """List acked tasks where ALL liveness signals (last_activity_at,
        last_heartbeat_at, last_workspace_activity_at) are older than the
        cutoff (or NULL).

        A task with recent heartbeats or workspace activity is NOT stale.
        """
        return self._query_tasks(
            """
            SELECT * FROM tasks
            WHERE phase='acked'
              AND last_activity_at < ?
              AND (last_heartbeat_at IS NULL OR last_heartbeat_at < ?)
              AND (last_workspace_activity_at IS NULL OR last_workspace_activity_at < ?)
            ORDER BY last_activity_at ASC
            """,
            (cutoff_iso, cutoff_iso, cutoff_iso),
        )

    def list_watchdog_candidates(self, *, watchdog_cutoff_iso: str, hard_timeout_cutoff_iso: str) -> list[TaskRecord]:
        """List acked tasks eligible for soft watchdog retry recommendation.

        Only tasks that are truly silent (no recent heartbeats AND no recent
        activity AND no recent workspace activity) qualify.
        """
        return self._query_tasks(
            """
            SELECT * FROM tasks
            WHERE phase='acked'
              AND retry_recommended=0
              AND last_activity_at < ?
              AND last_activity_at >= ?
              AND (last_heartbeat_at IS NULL OR last_heartbeat_at < ?)
              AND (last_workspace_activity_at IS NULL OR last_workspace_activity_at < ?)
            ORDER BY last_activity_at ASC
            """,
            (watchdog_cutoff_iso, hard_timeout_cutoff_iso, watchdog_cutoff_iso, watchdog_cutoff_iso),
        )

    def list_tasks(self, *, phase: str | None = None, limit: int = 20) -> list[TaskRecord]:
        sql = """
        SELECT * FROM tasks
        """
        params: tuple[object, ...]
        if phase is None:
            sql += " ORDER BY updated_at DESC, task_id DESC LIMIT ?"
            params = (limit,)
        else:
            sql += " WHERE phase=? ORDER BY updated_at DESC, task_id DESC LIMIT ?"
            params = (phase, limit)
        with connect(self.db_path) as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._task_from_row(row) for row in rows]

    def _update(self, task_id: str, sql: str, params: tuple[object, ...]) -> None:
        with connect(self.db_path) as conn:
            cursor = conn.execute(sql, params)
            if cursor.rowcount == 0:
                raise TaskNotFoundError(f"task not found: {task_id}")
            conn.commit()

    def _query_tasks(self, sql: str, params: tuple[object, ...]) -> list[TaskRecord]:
        with connect(self.db_path) as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._task_from_row(row) for row in rows]

    @staticmethod
    def _task_from_row(row) -> TaskRecord:
        # Gracefully handle old DBs that may not have the workspace column yet
        try:
            ws_activity = row["last_workspace_activity_at"]
        except (IndexError, KeyError):
            ws_activity = None
        try:
            idempotency_key = row["client_idempotency_key"]
        except (IndexError, KeyError):
            idempotency_key = None
        try:
            executor_backend = row["executor_backend"]
        except (IndexError, KeyError):
            executor_backend = None
        return TaskRecord(
            task_id=row["task_id"],
            repo_path=row["repo_path"],
            phase=row["phase"],
            antigravity_session_id=row["antigravity_session_id"],
            attempt_no=row["attempt_no"],
            retry_count=row["retry_count"],
            last_receipt_id=row["last_receipt_id"],
            stuck_reason=row["stuck_reason"],
            retry_recommended=bool(row["retry_recommended"]),
            last_activity_at=row["last_activity_at"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            last_heartbeat_at=row["last_heartbeat_at"],
            last_workspace_activity_at=ws_activity,
            client_idempotency_key=idempotency_key,
            executor_backend=executor_backend,
        )
