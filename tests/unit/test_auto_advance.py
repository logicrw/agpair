"""Tests for serial task chain auto-advance feature.

Covers:
  - task start with --depends-on defers dispatch when deps unsatisfied
  - daemon auto_advance_dependent_tasks dispatches when deps committed
  - no-op when deps are still pending
  - marks blocked when dispatch fails
  - multi-step chain (A→B→C) advances incrementally
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agpair.config import AppPaths
from agpair.daemon.loop import auto_advance_dependent_tasks, _get_task_body_from_journal
from agpair.storage.db import ensure_database
from agpair.storage.journal import JournalRepository
from agpair.storage.tasks import TaskRepository


@pytest.fixture
def tmp_paths(tmp_path: Path) -> AppPaths:
    paths = AppPaths.from_root(tmp_path)
    ensure_database(paths.db_path)
    return paths


@pytest.fixture
def tasks(tmp_paths: AppPaths) -> TaskRepository:
    return TaskRepository(tmp_paths.db_path)


@pytest.fixture
def journal(tmp_paths: AppPaths) -> JournalRepository:
    return JournalRepository(tmp_paths.db_path)


def _create_task(tasks, journal, task_id, repo_path="/repo", depends_on=None, executor="codex_cli"):
    tasks.create_task(
        task_id=task_id,
        repo_path=repo_path,
        executor_backend=executor,
        depends_on=json.dumps(depends_on) if depends_on else None,
    )
    journal.append(task_id, "cli", "created", f"Goal: test task {task_id}. Scope: test. Required changes: none. Exit criteria: pass.")


class TestGetTaskBodyFromJournal:
    def test_retrieves_body(self, journal):
        journal.append("T-1", "cli", "created", "the body text")
        assert _get_task_body_from_journal(journal, "T-1") == "the body text"

    def test_returns_none_when_no_created_event(self, journal):
        journal.append("T-1", "daemon", "acked", "something else")
        assert _get_task_body_from_journal(journal, "T-1") is None


class TestAutoAdvanceDependentTasks:
    def test_no_tasks_returns_zero(self, tmp_paths):
        assert auto_advance_dependent_tasks(tmp_paths) == 0

    def test_no_deps_tasks_returns_zero(self, tmp_paths, tasks, journal):
        _create_task(tasks, journal, "T-1")
        assert auto_advance_dependent_tasks(tmp_paths) == 0

    def test_deps_not_satisfied_skips(self, tmp_paths, tasks, journal):
        """Task B depends on A which is still 'new' → no advance."""
        _create_task(tasks, journal, "T-A")
        _create_task(tasks, journal, "T-B", depends_on=["T-A"])
        result = auto_advance_dependent_tasks(tmp_paths)
        assert result == 0
        # T-B should still be 'new'
        assert tasks.get_task("T-B").phase == "new"

    @patch("agpair.executors.get_executor")
    def test_deps_satisfied_dispatches(self, mock_get_executor, tmp_paths, tasks, journal):
        """Task B depends on A which is committed → auto-dispatch B."""
        from agpair.executors.base import DispatchResult

        mock_exec = MagicMock()
        mock_exec.dispatch.return_value = DispatchResult(session_id="sess-b")
        mock_get_executor.return_value = mock_exec

        _create_task(tasks, journal, "T-A")
        tasks.mark_acked(task_id="T-A", session_id="sess-a")
        tasks.mark_committed(task_id="T-A", terminal_source="test")

        _create_task(tasks, journal, "T-B", depends_on=["T-A"])

        result = auto_advance_dependent_tasks(tmp_paths)
        assert result == 1
        task_b = tasks.get_task("T-B")
        assert task_b.phase == "acked"
        assert task_b.antigravity_session_id == "sess-b"

    @patch("agpair.executors.get_executor")
    def test_dispatch_failure_marks_blocked(self, mock_get_executor, tmp_paths, tasks, journal):
        """When dispatch fails, task is marked blocked, not stuck in new."""
        mock_exec = MagicMock()
        mock_exec.dispatch.side_effect = RuntimeError("executor down")
        mock_get_executor.return_value = mock_exec

        _create_task(tasks, journal, "T-A")
        tasks.mark_acked(task_id="T-A", session_id="sess-a")
        tasks.mark_committed(task_id="T-A", terminal_source="test")

        _create_task(tasks, journal, "T-B", depends_on=["T-A"])

        result = auto_advance_dependent_tasks(tmp_paths)
        assert result == 0
        task_b = tasks.get_task("T-B")
        assert task_b.phase == "blocked"
        assert "auto-advance dispatch failed" in task_b.stuck_reason

    @patch("agpair.executors.get_executor")
    def test_multi_step_chain(self, mock_get_executor, tmp_paths, tasks, journal):
        """A→B→C chain: only B dispatches when A commits; C stays deferred."""
        from agpair.executors.base import DispatchResult

        mock_exec = MagicMock()
        mock_exec.dispatch.return_value = DispatchResult(session_id="sess-auto")
        mock_get_executor.return_value = mock_exec

        _create_task(tasks, journal, "T-A")
        _create_task(tasks, journal, "T-B", depends_on=["T-A"])
        _create_task(tasks, journal, "T-C", depends_on=["T-B"])

        # Commit A
        tasks.mark_acked(task_id="T-A", session_id="sess-a")
        tasks.mark_committed(task_id="T-A", terminal_source="test")

        # First advance: only B should dispatch
        result = auto_advance_dependent_tasks(tmp_paths)
        assert result == 1
        assert tasks.get_task("T-B").phase == "acked"
        assert tasks.get_task("T-C").phase == "new"

        # Commit B
        tasks.mark_committed(task_id="T-B", terminal_source="test")

        # Second advance: now C dispatches
        result = auto_advance_dependent_tasks(tmp_paths)
        assert result == 1
        assert tasks.get_task("T-C").phase == "acked"

    @patch("agpair.executors.get_executor")
    def test_parallel_deps_all_must_commit(self, mock_get_executor, tmp_paths, tasks, journal):
        """C depends on [A, B]; only dispatches when both are committed."""
        from agpair.executors.base import DispatchResult

        mock_exec = MagicMock()
        mock_exec.dispatch.return_value = DispatchResult(session_id="sess-c")
        mock_get_executor.return_value = mock_exec

        _create_task(tasks, journal, "T-A")
        _create_task(tasks, journal, "T-B")
        _create_task(tasks, journal, "T-C", depends_on=["T-A", "T-B"])

        # Only A committed
        tasks.mark_acked(task_id="T-A", session_id="s-a")
        tasks.mark_committed(task_id="T-A", terminal_source="test")
        assert auto_advance_dependent_tasks(tmp_paths) == 0
        assert tasks.get_task("T-C").phase == "new"

        # Now B also committed
        tasks.mark_acked(task_id="T-B", session_id="s-b")
        tasks.mark_committed(task_id="T-B", terminal_source="test")
        assert auto_advance_dependent_tasks(tmp_paths) == 1
        assert tasks.get_task("T-C").phase == "acked"

    def test_no_body_skips_with_journal_entry(self, tmp_paths, tasks, journal):
        """Task with no journal body gets skipped, not crashed."""
        _create_task(tasks, journal, "T-A")
        tasks.mark_acked(task_id="T-A", session_id="s-a")
        tasks.mark_committed(task_id="T-A", terminal_source="test")

        # Create T-B but WITHOUT journal body
        tasks.create_task(
            task_id="T-B",
            repo_path="/repo",
            executor_backend="codex_cli",
            depends_on=json.dumps(["T-A"]),
        )

        result = auto_advance_dependent_tasks(tmp_paths)
        assert result == 0
        # Should have logged the skip
        entries = journal.tail("T-B", limit=10)
        assert any("auto_advance_skipped" in e.event for e in entries)
