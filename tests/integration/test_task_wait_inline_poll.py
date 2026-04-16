"""Tests for inline executor polling in ``agpair task wait``."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from agpair.cli.wait import wait_for_terminal_phase
from agpair.config import AppPaths
from agpair.executors.base import TaskState
from agpair.storage.db import ensure_database
from agpair.storage.journal import JournalRepository
from agpair.storage.tasks import TaskRepository
from agpair.transport import messages


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_paths(tmp_path: Path) -> AppPaths:
    return AppPaths.from_root(tmp_path / ".agpair")


def _make_repo(tmp_path: Path) -> TaskRepository:
    paths = _make_paths(tmp_path)
    ensure_database(paths.db_path)
    return TaskRepository(paths.db_path)


class FakeClock:
    """Injectable clock that advances time on each ``sleep()`` call."""

    def __init__(self, start: float = 0.0):
        self._now = start

    def time(self) -> float:
        return self._now

    def sleep(self, seconds: float) -> None:
        self._now += seconds


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_wait_inline_poll_committed(tmp_path: Path, monkeypatch):
    """Wait loop should poll executor and transition to committed on success."""
    repo = _make_repo(tmp_path)
    repo.create_task(task_id="T-INLINE-1", repo_path="/r", executor_backend="gemini_cli")
    repo.mark_acked(task_id="T-INLINE-1", session_id="s-1")

    mock_executor = MagicMock()
    mock_executor.poll.return_value = TaskState(
        is_done=True,
        receipt={
            "schema_version": "1",
            "task_id": "T-INLINE-1",
            "attempt_no": 1,
            "review_round": 0,
            "status": "COMMITTED",
            "summary": "Inline poll success",
            "payload": {"exit_code": 0}
        }
    )
    
    monkeypatch.setattr("agpair.cli.wait.get_executor", lambda backend: mock_executor)
    monkeypatch.setattr("agpair.cli.wait.is_local_cli_backend", lambda backend_id: True)

    clock = FakeClock()
    paths = _make_paths(tmp_path)
    result = wait_for_terminal_phase(
        paths.db_path, "T-INLINE-1", interval_seconds=1, timeout_seconds=30, _clock=clock,
    )
    
    assert result.phase == "committed"
    assert result.timed_out is False
    mock_executor.poll.assert_called_once()
    mock_executor.cleanup.assert_called_once_with("s-1")
    
    # Check journal
    journal = JournalRepository(paths.db_path)
    entries = journal.tail("T-INLINE-1")
    assert any(e.event == "inline_poll_closed" and e.source == "wait" for e in entries)
    
    # Check task terminal_source and session_id cleared
    task = repo.get_task("T-INLINE-1")
    assert task.phase == "committed"
    assert task.terminal_source == "inline_poll"
    assert task.antigravity_session_id is None


def test_wait_inline_poll_blocked(tmp_path: Path, monkeypatch):
    """Wait loop should poll executor and transition to blocked on failure."""
    repo = _make_repo(tmp_path)
    repo.create_task(task_id="T-INLINE-2", repo_path="/r", executor_backend="codex_cli")
    repo.mark_acked(task_id="T-INLINE-2", session_id="s-2")

    mock_executor = MagicMock()
    mock_executor.poll.return_value = TaskState(
        is_done=True,
        receipt={
            "schema_version": "1",
            "task_id": "T-INLINE-2",
            "attempt_no": 1,
            "review_round": 0,
            "status": "BLOCKED",
            "summary": "Crash",
            "payload": {"exit_code": 1, "blocker_type": "execution_error"}
        }
    )
    
    monkeypatch.setattr("agpair.cli.wait.get_executor", lambda backend: mock_executor)
    monkeypatch.setattr("agpair.cli.wait.is_local_cli_backend", lambda backend_id: True)

    clock = FakeClock()
    paths = _make_paths(tmp_path)
    result = wait_for_terminal_phase(
        paths.db_path, "T-INLINE-2", interval_seconds=1, timeout_seconds=30, _clock=clock,
    )
    
    assert result.phase == "blocked"
    assert result.timed_out is False
    mock_executor.poll.assert_called_once()
    mock_executor.cleanup.assert_called_once_with("s-2")
    
    task = repo.get_task("T-INLINE-2")
    assert task.phase == "blocked"
    assert task.stuck_reason == "Crash"
    assert task.antigravity_session_id is None


def test_wait_inline_poll_evidence_ready(tmp_path: Path, monkeypatch):
    """Wait loop should poll executor and transition to evidence_ready when policy allows."""
    repo = _make_repo(tmp_path)
    # Patch TaskRepository.create_task default to allow evidence_ready if needed,
    # but here we just manually update completion_policy if needed.
    # Actually create_task default is 'direct_commit'.
    repo.create_task(task_id="T-INLINE-3", repo_path="/r", executor_backend="gemini_cli")
    
    # Update policy to allow evidence_ready
    from agpair.storage.db import connect
    paths = _make_paths(tmp_path)
    with connect(paths.db_path) as conn:
        conn.execute("UPDATE tasks SET completion_policy='evidence_ready' WHERE task_id='T-INLINE-3'")
        conn.commit()
    
    repo.mark_acked(task_id="T-INLINE-3", session_id="s-3")

    mock_executor = MagicMock()
    mock_executor.poll.return_value = TaskState(
        is_done=True,
        receipt={
            "schema_version": "1",
            "task_id": "T-INLINE-3",
            "attempt_no": 1,
            "review_round": 0,
            "status": "EVIDENCE_PACK",
            "summary": "Ready for review",
            "payload": {"evidence_path": "/tmp/evidence"}
        }
    )
    
    monkeypatch.setattr("agpair.cli.wait.get_executor", lambda backend: mock_executor)
    monkeypatch.setattr("agpair.cli.wait.is_local_cli_backend", lambda backend_id: True)

    clock = FakeClock()
    result = wait_for_terminal_phase(
        paths.db_path, "T-INLINE-3", interval_seconds=1, timeout_seconds=30, _clock=clock,
    )
    
    assert result.phase == "evidence_ready"
    assert result.timed_out is False
    
    task = repo.get_task("T-INLINE-3")
    assert task.phase == "evidence_ready"


def test_wait_inline_poll_skips_non_local(tmp_path: Path, monkeypatch):
    """Inline poll should be skipped for antigravity (non-local) backends."""
    repo = _make_repo(tmp_path)
    repo.create_task(task_id="T-INLINE-4", repo_path="/r", executor_backend="antigravity")
    repo.mark_acked(task_id="T-INLINE-4", session_id="bus-session")

    mock_get_executor = MagicMock()
    monkeypatch.setattr("agpair.cli.wait.get_executor", mock_get_executor)
    # is_local_cli_backend returns False for antigravity
    
    clock = FakeClock()
    paths = _make_paths(tmp_path)
    result = wait_for_terminal_phase(
        paths.db_path, "T-INLINE-4", interval_seconds=1, timeout_seconds=2, _clock=clock,
    )
    
    assert result.timed_out is True
    mock_get_executor.assert_not_called()


def test_wait_inline_poll_exception_handled(tmp_path: Path, monkeypatch):
    """Exceptions in inline poll should be journaled and loop should continue."""
    repo = _make_repo(tmp_path)
    repo.create_task(task_id="T-INLINE-5", repo_path="/r", executor_backend="gemini_cli")
    repo.mark_acked(task_id="T-INLINE-5", session_id="s-5")

    mock_executor = MagicMock()
    mock_executor.poll.side_effect = Exception("Disk full")
    
    monkeypatch.setattr("agpair.cli.wait.get_executor", lambda backend: mock_executor)
    monkeypatch.setattr("agpair.cli.wait.is_local_cli_backend", lambda backend_id: True)

    clock = FakeClock()
    paths = _make_paths(tmp_path)
    result = wait_for_terminal_phase(
        paths.db_path, "T-INLINE-5", interval_seconds=1, timeout_seconds=2, _clock=clock,
    )
    
    assert result.timed_out is True
    
    # Check journal for warning
    journal = JournalRepository(paths.db_path)
    entries = journal.tail("T-INLINE-5")
    assert any(e.event == "inline_poll_error" and "Disk full" in e.body for e in entries)
