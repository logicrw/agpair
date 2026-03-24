from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
import sqlite3

from agpair.config import AppPaths
from agpair.storage.db import ensure_database
from agpair.storage.tasks import TaskRepository


class FakeBus:
    def __init__(self) -> None:
        self.sent_messages: list[tuple[str, tuple, dict]] = []

    def pull_receipts(self, *, task_id: str | None = None, limit: int = 20) -> list[dict]:
        return []

    def send_task(self, *args, **kwargs):
        self.sent_messages.append(("send_task", args, kwargs))

    def send_review(self, *args, **kwargs):
        self.sent_messages.append(("send_review", args, kwargs))

    def send_approved(self, *args, **kwargs):
        self.sent_messages.append(("send_approved", args, kwargs))


def make_paths(tmp_path: Path) -> AppPaths:
    return AppPaths.from_root(tmp_path / ".agpair")


def seed_stale_task(tmp_path: Path, task_id: str = "TASK-1") -> tuple[AppPaths, TaskRepository]:
    paths = make_paths(tmp_path)
    ensure_database(paths.db_path)
    repo = TaskRepository(paths.db_path)
    repo.create_task(task_id=task_id, repo_path="/tmp/repo")
    repo.mark_acked(task_id=task_id, session_id="session-123")
    old = (datetime(2026, 3, 21, 12, 0, tzinfo=UTC) - timedelta(minutes=31)).isoformat().replace("+00:00", "Z")
    with sqlite3.connect(paths.db_path) as conn:
        conn.execute(
            "UPDATE tasks SET last_activity_at=?, updated_at=? WHERE task_id=?",
            (old, old, task_id),
        )
        conn.commit()
    return paths, repo


def test_daemon_does_not_send_semantic_messages(tmp_path: Path) -> None:
    from agpair.daemon.loop import run_once

    paths, _repo = seed_stale_task(tmp_path)
    fake_bus = FakeBus()

    run_once(paths, now=datetime(2026, 3, 21, 12, 0, tzinfo=UTC), bus=fake_bus, timeout_seconds=1800)

    assert fake_bus.sent_messages == []


def test_daemon_does_not_create_fresh_retry_attempt(tmp_path: Path) -> None:
    from agpair.daemon.loop import run_once

    paths, repo = seed_stale_task(tmp_path)
    fake_bus = FakeBus()

    run_once(paths, now=datetime(2026, 3, 21, 12, 0, tzinfo=UTC), bus=fake_bus, timeout_seconds=1800)

    task = repo.get_task("TASK-1")
    assert task is not None
    assert task.attempt_no == 1
