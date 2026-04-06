from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import json

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


def make_paths(tmp_path: Path) -> AppPaths:
    return AppPaths.from_root(tmp_path / ".agpair")


def seed_task(tmp_path: Path, task_id: str = "TASK-1", completion_policy: str = "direct_commit") -> AppPaths:
    paths = make_paths(tmp_path)
    ensure_database(paths.db_path)
    TaskRepository(paths.db_path).create_task(
        task_id=task_id, 
        repo_path=str(tmp_path / "repo"), 
        completion_policy=completion_policy
    )
    return paths


def test_direct_commit_policy_rejects_evidence_pack(tmp_path: Path) -> None:
    from agpair.daemon.loop import run_once

    paths = seed_task(tmp_path, completion_policy="direct_commit")
    repo = TaskRepository(paths.db_path)
    repo.mark_acked(task_id="TASK-1", session_id="session-123")
    
    bus = FakePullBus(
        [
            {
                "id": 1,
                "task_id": "TASK-1",
                "status": "EVIDENCE_PACK",
                "body": "{}",
            }
        ]
    )

    run_once(paths, now=datetime(2026, 3, 21, 12, 0, tzinfo=UTC), bus=bus)

    task = repo.get_task("TASK-1")
    assert task is not None
    assert task.phase == "acked"
    
    rows = JournalRepository(paths.db_path).tail("TASK-1", limit=1)
    assert rows[0].event == "policy_rejection"
    assert "EVIDENCE_PACK not permitted" in rows[0].body


def test_review_then_commit_policy_rejects_committed_before_approval(tmp_path: Path) -> None:
    from agpair.daemon.loop import run_once

    paths = seed_task(tmp_path, completion_policy="review_then_commit")
    repo = TaskRepository(paths.db_path)
    repo.mark_acked(task_id="TASK-1", session_id="session-123")
    
    bus = FakePullBus(
        [
            {
                "id": 1,
                "task_id": "TASK-1",
                "status": "COMMITTED",
                "body": "{}",
            }
        ]
    )

    run_once(paths, now=datetime(2026, 3, 21, 12, 0, tzinfo=UTC), bus=bus)

    task = repo.get_task("TASK-1")
    assert task is not None
    assert task.phase == "acked"
    
    rows = JournalRepository(paths.db_path).tail("TASK-1", limit=1)
    assert rows[0].event == "policy_rejection"
    assert "COMMITTED not permitted" in rows[0].body





def test_repo_evidence_fallback(tmp_path: Path, monkeypatch) -> None:
    from agpair.daemon.loop import auto_close_evidence_ready_tasks

    paths = seed_task(tmp_path, completion_policy="review_then_commit")
    repo = TaskRepository(paths.db_path)
    repo.mark_acked(task_id="TASK-1", session_id="session-123")
    repo.mark_evidence_ready(task_id="TASK-1", last_receipt_id="1")
    
    import agpair.daemon.loop
    monkeypatch.setattr(agpair.daemon.loop, "detect_committed_task_in_repo", lambda repo_path, task_id, **kw: "abcdef123456")
    
    closed_count = auto_close_evidence_ready_tasks(paths)
    assert closed_count == 1
    
    task = repo.get_task("TASK-1")
    assert task.phase == "committed"
    assert task.terminal_source == "repo_evidence"


def test_repo_evidence_fallback_direct_commit_acked(tmp_path: Path, monkeypatch) -> None:
    from agpair.daemon.loop import auto_close_evidence_ready_tasks

    paths = seed_task(tmp_path, completion_policy="direct_commit")
    repo = TaskRepository(paths.db_path)
    repo.mark_acked(task_id="TASK-1", session_id="session-123")
    
    import agpair.daemon.loop
    monkeypatch.setattr(agpair.daemon.loop, "detect_committed_task_in_repo", lambda repo_path, task_id, **kw: "abcdef123456")
    
    closed_count = auto_close_evidence_ready_tasks(paths)
    assert closed_count == 1
    
    task = repo.get_task("TASK-1")
    assert task.phase == "committed"
    assert task.terminal_source == "repo_evidence"


def test_detect_committed_task_in_repo_false_positive(tmp_path: Path, monkeypatch) -> None:
    from agpair.daemon.loop import detect_committed_task_in_repo
    import subprocess
    from unittest.mock import MagicMock

    # Setup a mock subprocess result where task_id is just a substring of another task id
    # Searching for TASK-1, but the commit only mentions TASK-100
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = "abcdef123456\x00implemented TASK-100 feature\n"
    
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: mock_result)
    
    # It should not match TASK-1 because of word boundaries
    sha = detect_committed_task_in_repo(str(tmp_path), "TASK-1")
    assert sha is None
    
    # But it should match TASK-100
    sha_100 = detect_committed_task_in_repo(str(tmp_path), "TASK-100")
    assert sha_100 == "abcdef123456"

    # Also test valid match for TASK-1 with word boundary
    mock_result.stdout = "fedcba654321\x00implemented (TASK-1) feature\n"
    sha_exact = detect_committed_task_in_repo(str(tmp_path), "TASK-1")
    assert sha_exact == "fedcba654321"


def test_repo_evidence_fallback_skips_review_then_commit_acked(tmp_path: Path, monkeypatch) -> None:
    from agpair.daemon.loop import auto_close_evidence_ready_tasks

    paths = seed_task(tmp_path, completion_policy="review_then_commit")
    repo = TaskRepository(paths.db_path)
    repo.mark_acked(task_id="TASK-1", session_id="session-123")
    
    import agpair.daemon.loop
    monkeypatch.setattr(agpair.daemon.loop, "detect_committed_task_in_repo", lambda repo_path, task_id, **kw: "abcdef123456")
    
    closed_count = auto_close_evidence_ready_tasks(paths)
    # Shouldn't close because it's review_then_commit and in acked, must be evidence_ready
    assert closed_count == 0
    task = repo.get_task("TASK-1")
    assert task.phase == "acked"
