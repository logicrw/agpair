from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
import sqlite3

from agpair.config import AppPaths
from agpair.storage.db import ensure_database
from agpair.storage.tasks import TaskRepository


class EmptyBus:
    def __init__(self) -> None:
        self.sent_messages: list[tuple[str, tuple, dict]] = []
        self.settled_claims: list[tuple[str, list[str]]] = []

    def pull_receipts(self, *, task_id: str | None = None, limit: int = 20) -> list[dict]:
        return []

    def reserve_receipts(self, *, task_id: str | None = None, limit: int = 20, lease_ms: int = 30000) -> list[dict]:
        return []

    def settle_claims(self, *, reader: str, claims: list[str]) -> int:
        self.settled_claims.append((reader, list(claims)))
        return len(claims)

    def send_task(self, *args, **kwargs):
        self.sent_messages.append(("send_task", args, kwargs))


def make_paths(tmp_path: Path) -> AppPaths:
    return AppPaths.from_root(tmp_path / ".agpair")


def seed_acked_task_with_old_activity(
    tmp_path: Path,
    task_id: str = "TASK-1",
    *,
    age_minutes: int = 31,
) -> AppPaths:
    paths = make_paths(tmp_path)
    ensure_database(paths.db_path)
    repo = TaskRepository(paths.db_path)
    repo.create_task(task_id=task_id, repo_path="/tmp/repo")
    repo.mark_acked(task_id=task_id, session_id="session-123")
    old = (datetime(2026, 3, 21, 12, 0, tzinfo=UTC) - timedelta(minutes=age_minutes)).isoformat().replace("+00:00", "Z")
    with sqlite3.connect(paths.db_path) as conn:
        conn.execute(
            "UPDATE tasks SET last_activity_at=?, updated_at=? WHERE task_id=?",
            (old, old, task_id),
        )
        conn.commit()
    return paths


def test_daemon_marks_stuck_and_retry_recommended_after_timeout(tmp_path: Path) -> None:
    from agpair.daemon.loop import run_once

    paths = seed_acked_task_with_old_activity(tmp_path)
    repo = TaskRepository(paths.db_path)
    bus = EmptyBus()

    run_once(paths, now=datetime(2026, 3, 21, 12, 0, tzinfo=UTC), bus=bus, timeout_seconds=1800)

    task = repo.get_task("TASK-1")
    assert task is not None
    assert task.phase == "stuck"
    assert task.retry_recommended is True
    assert task.attempt_no == 1
    assert task.retry_count == 0


def test_daemon_marks_retry_recommended_before_hard_timeout(tmp_path: Path) -> None:
    from agpair.daemon.loop import run_once
    from agpair.storage.journal import JournalRepository

    paths = seed_acked_task_with_old_activity(tmp_path, age_minutes=16)
    repo = TaskRepository(paths.db_path)

    run_once(
        paths,
        now=datetime(2026, 3, 21, 12, 0, tzinfo=UTC),
        bus=EmptyBus(),
        timeout_seconds=1800,
        watchdog_seconds=900,
    )

    task = repo.get_task("TASK-1")
    assert task is not None
    assert task.phase == "acked"
    assert task.retry_recommended is True
    rows = JournalRepository(paths.db_path).tail("TASK-1", limit=5)
    assert any(row.event == "watchdog_retry_recommended" for row in rows)


def test_daemon_soft_watchdog_does_not_stuck_background_task(tmp_path: Path) -> None:
    from agpair.daemon.loop import run_once
    from agpair.storage.journal import JournalRepository

    paths = make_paths(tmp_path)
    ensure_database(paths.db_path)
    repo = TaskRepository(paths.db_path)
    repo.create_task(
        task_id="TASK-BG",
        repo_path="/tmp/repo",
        wait_policy="lease",
        controller_wait_seconds=300,
        execution_budget_seconds=3600,
        background_ok=True,
    )
    repo.mark_acked(task_id="TASK-BG", session_id="session-bg")
    old = (datetime(2026, 3, 21, 12, 0, tzinfo=UTC) - timedelta(minutes=16)).isoformat().replace("+00:00", "Z")
    with sqlite3.connect(paths.db_path) as conn:
        conn.execute(
            "UPDATE tasks SET last_activity_at=?, updated_at=? WHERE task_id=?",
            (old, old, "TASK-BG"),
        )
        conn.commit()

    run_once(
        paths,
        now=datetime(2026, 3, 21, 12, 0, tzinfo=UTC),
        bus=EmptyBus(),
        timeout_seconds=1800,
        watchdog_seconds=900,
    )

    task = repo.get_task("TASK-BG")
    assert task is not None
    assert task.phase == "acked"
    assert task.retry_recommended is True
    rows = JournalRepository(paths.db_path).tail("TASK-BG", limit=5)
    assert any(row.event == "soft_no_progress_recommended" for row in rows)


def test_daemon_uses_execution_budget_for_background_stuck(tmp_path: Path) -> None:
    from agpair.daemon.loop import run_once
    from agpair.storage.journal import JournalRepository

    paths = make_paths(tmp_path)
    ensure_database(paths.db_path)
    repo = TaskRepository(paths.db_path)
    repo.create_task(
        task_id="TASK-BUDGET",
        repo_path="/tmp/repo",
        wait_policy="lease",
        controller_wait_seconds=300,
        execution_budget_seconds=600,
        background_ok=True,
    )
    repo.mark_acked(task_id="TASK-BUDGET", session_id="session-budget")
    created = (datetime(2026, 3, 21, 12, 0, tzinfo=UTC) - timedelta(minutes=11)).isoformat().replace("+00:00", "Z")
    with sqlite3.connect(paths.db_path) as conn:
        conn.execute(
            "UPDATE tasks SET created_at=?, last_activity_at=?, updated_at=? WHERE task_id=?",
            (created, created, created, "TASK-BUDGET"),
        )
        conn.commit()

    run_once(
        paths,
        now=datetime(2026, 3, 21, 12, 0, tzinfo=UTC),
        bus=EmptyBus(),
        timeout_seconds=1800,
        watchdog_seconds=900,
    )

    task = repo.get_task("TASK-BUDGET")
    assert task is not None
    assert task.phase == "stuck"
    assert task.stuck_reason == "no_progress_budget_exceeded"
    assert task.retry_recommended is True
    rows = JournalRepository(paths.db_path).tail("TASK-BUDGET", limit=5)
    assert any(row.event == "execution_budget_stuck" for row in rows)


def test_daemon_does_not_mark_stuck_on_same_tick_as_soft_watchdog(tmp_path: Path) -> None:
    from agpair.daemon.loop import run_once

    paths = seed_acked_task_with_old_activity(tmp_path, age_minutes=16)
    repo = TaskRepository(paths.db_path)

    run_once(
        paths,
        now=datetime(2026, 3, 21, 12, 0, tzinfo=UTC),
        bus=EmptyBus(),
        timeout_seconds=1800,
        watchdog_seconds=900,
    )
    run_once(
        paths,
        now=datetime(2026, 3, 21, 12, 0, tzinfo=UTC),
        bus=EmptyBus(),
        timeout_seconds=1800,
        watchdog_seconds=900,
    )

    task = repo.get_task("TASK-1")
    assert task is not None
    assert task.phase == "acked"
    assert task.retry_recommended is True
