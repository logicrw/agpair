from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from agpair.config import AppPaths
from agpair.storage.db import ensure_database
from agpair.storage.journal import JournalRepository
from agpair.storage.tasks import TaskRepository


class FakePullBus:
    def __init__(self, receipts: list[dict]) -> None:
        self._receipts = receipts
        self.sent_messages: list[tuple[str, tuple, dict]] = []

    def pull_receipts(self, *, task_id: str | None = None, limit: int = 20) -> list[dict]:
        return list(self._receipts)

    def send_task(self, *args, **kwargs):
        self.sent_messages.append(("send_task", args, kwargs))

    def send_review(self, *args, **kwargs):
        self.sent_messages.append(("send_review", args, kwargs))

    def send_approved(self, *args, **kwargs):
        self.sent_messages.append(("send_approved", args, kwargs))


def make_paths(tmp_path: Path) -> AppPaths:
    return AppPaths.from_root(tmp_path / ".agpair")


def seed_task(tmp_path: Path, task_id: str = "TASK-1") -> AppPaths:
    paths = make_paths(tmp_path)
    ensure_database(paths.db_path)
    TaskRepository(paths.db_path).create_task(task_id=task_id, repo_path="/tmp/repo")
    return paths


def test_daemon_ingests_ack_and_updates_session_mapping(tmp_path: Path) -> None:
    from agpair.daemon.loop import run_once

    paths = seed_task(tmp_path)
    bus = FakePullBus(
        [
            {
                "id": 1,
                "task_id": "TASK-1",
                "status": "ACK",
                "body": "session_id=session-123\nrepo_path=/tmp/repo",
            }
        ]
    )

    run_once(paths, now=datetime(2026, 3, 21, 12, 0, tzinfo=UTC), bus=bus)

    task = TaskRepository(paths.db_path).get_task("TASK-1")
    assert task is not None
    assert task.phase == "acked"
    assert task.antigravity_session_id == "session-123"
    rows = JournalRepository(paths.db_path).tail("TASK-1", limit=2)
    assert rows[0].event == "acked"
    assert "session-123" in rows[0].body


def test_daemon_ingests_evidence_pack_marks_task_ready(tmp_path: Path) -> None:
    from agpair.daemon.loop import run_once

    paths = seed_task(tmp_path)
    repo = TaskRepository(paths.db_path)
    repo.mark_acked(task_id="TASK-1", session_id="session-123")
    bus = FakePullBus(
        [
            {
                "id": 2,
                "task_id": "TASK-1",
                "status": "EVIDENCE_PACK",
                "body": "git diff --stat\n 1 file changed",
            }
        ]
    )

    run_once(paths, now=datetime(2026, 3, 21, 12, 1, tzinfo=UTC), bus=bus)

    task = repo.get_task("TASK-1")
    assert task is not None
    assert task.phase == "evidence_ready"
    assert task.last_receipt_id == "2"


def test_daemon_accepts_colon_space_session_id_format(tmp_path: Path) -> None:
    from agpair.daemon.loop import run_once

    paths = seed_task(tmp_path)
    bus = FakePullBus(
        [
            {
                "id": 3,
                "task_id": "TASK-1",
                "status": "ACK",
                "body": "Accepted\nsession_id: session-456\nrepo_path: /tmp/repo",
            }
        ]
    )

    run_once(paths, now=datetime(2026, 3, 21, 12, 2, tzinfo=UTC), bus=bus)

    task = TaskRepository(paths.db_path).get_task("TASK-1")
    assert task is not None
    assert task.phase == "acked"
    assert task.antigravity_session_id == "session-456"
