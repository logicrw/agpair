from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
import sqlite3
import os

from typer.testing import CliRunner

from agpair.cli.app import app
from agpair.config import AppPaths
from agpair.storage.db import ensure_database
from agpair.storage.journal import JournalRepository
from agpair.storage.tasks import TaskRepository


class FakeBus:
    def __init__(self, receipts: list[dict] | None = None) -> None:
        self._receipts = receipts or []
        self.sent_messages: list[tuple[str, tuple, dict]] = []
        self.settled_claims: list[tuple[str, list[str]]] = []

    def pull_receipts(self, *, task_id: str | None = None, limit: int = 20) -> list[dict]:
        return list(self._receipts)

    def reserve_receipts(self, *, task_id: str | None = None, limit: int = 20, lease_ms: int = 30000) -> list[dict]:
        return [{**receipt, "claim_id": f"clm-{idx}"} for idx, receipt in enumerate(self._receipts, start=1)]

    def settle_claims(self, *, reader: str, claims: list[str]) -> int:
        self.settled_claims.append((reader, list(claims)))
        return len(claims)

    def send_task(self, *args, **kwargs):
        self.sent_messages.append(("send_task", args, kwargs))


def make_paths(tmp_path: Path) -> AppPaths:
    return AppPaths.from_root(tmp_path / ".agpair")


def seed_task(tmp_path: Path, task_id: str = "TASK-1") -> tuple[AppPaths, TaskRepository]:
    paths = make_paths(tmp_path)
    ensure_database(paths.db_path)
    repo = TaskRepository(paths.db_path)
    repo.create_task(task_id=task_id, repo_path="/tmp/repo")
    return paths, repo


def seed_stale_acked_task(tmp_path: Path, task_id: str = "TASK-1") -> tuple[AppPaths, TaskRepository]:
    paths, repo = seed_task(tmp_path, task_id=task_id)
    repo.mark_acked(task_id=task_id, session_id="session-123")
    old = (datetime(2026, 3, 21, 12, 0, tzinfo=UTC) - timedelta(minutes=31)).isoformat().replace("+00:00", "Z")
    with sqlite3.connect(paths.db_path) as conn:
        conn.execute(
            "UPDATE tasks SET last_activity_at=?, updated_at=? WHERE task_id=?",
            (old, old, task_id),
        )
        conn.commit()
    return paths, repo


def test_duplicate_receipt_is_ignored(tmp_path: Path) -> None:
    from agpair.daemon.loop import run_once

    paths, repo = seed_task(tmp_path)
    bus = FakeBus(
        [
            {"id": 1, "task_id": "TASK-1", "status": "ACK", "body": "session_id=session-123"},
            {"id": 1, "task_id": "TASK-1", "status": "ACK", "body": "session_id=session-123"},
        ]
    )

    run_once(paths, now=datetime(2026, 3, 21, 12, 0, tzinfo=UTC), bus=bus)

    task = repo.get_task("TASK-1")
    assert task is not None
    assert task.phase == "acked"
    rows = JournalRepository(paths.db_path).tail("TASK-1", limit=10)
    acked_rows = [row for row in rows if row.event == "acked"]
    assert len(acked_rows) == 1


def test_stale_receipt_is_ignored(tmp_path: Path) -> None:
    from agpair.daemon.loop import run_once

    paths, repo = seed_task(tmp_path)
    repo.mark_acked(task_id="TASK-1", session_id="session-123")
    repo.mark_evidence_ready(task_id="TASK-1", last_receipt_id="10")
    bus = FakeBus(
        [
            {"id": 9, "task_id": "TASK-1", "status": "BLOCKED", "body": "older blocked receipt"},
        ]
    )

    run_once(paths, now=datetime(2026, 3, 21, 12, 5, tzinfo=UTC), bus=bus)

    task = repo.get_task("TASK-1")
    assert task is not None
    assert task.phase == "evidence_ready"
    assert task.last_receipt_id == "10"





def test_retry_exhaustion_stops_automatic_recovery(tmp_path: Path) -> None:
    from agpair.daemon.loop import run_once

    paths, repo = seed_stale_acked_task(tmp_path)
    bus = FakeBus()

    run_once(paths, now=datetime(2026, 3, 21, 12, 0, tzinfo=UTC), bus=bus, timeout_seconds=1800)
    run_once(paths, now=datetime(2026, 3, 21, 12, 1, tzinfo=UTC), bus=bus, timeout_seconds=1800)

    task = repo.get_task("TASK-1")
    assert task is not None
    assert task.phase == "stuck"
    assert task.retry_recommended is True
    assert task.attempt_no == 1
    assert task.retry_count == 0
    assert bus.sent_messages == []

