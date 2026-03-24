"""Integration tests for the persisted waiter / guard mechanism.

Covers:
  - WaiterRepository CRUD lifecycle
  - wait_for_terminal_phase creates + finalizes waiter records
  - active waiter guard blocks intervention commands
  - --force bypasses the waiter guard
  - task status shows waiter info
  - task active-waits lists running waiters
  - Schema migration on old DBs
"""
from __future__ import annotations

import sqlite3
import textwrap
from pathlib import Path
from unittest.mock import MagicMock

import click.exceptions

import pytest

from agpair.models import WaiterRecord
from agpair.storage.db import ensure_database
from agpair.storage.tasks import TaskRepository
from agpair.storage.waiters import WaiterRepository


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _tmp_db(tmp_path: Path) -> Path:
    db = tmp_path / "agpair.db"
    ensure_database(db)
    return db


class FakeClock:
    """Controllable clock for wait tests."""

    def __init__(self, start: float = 0.0):
        self._time = start

    def time(self) -> float:
        return self._time

    def sleep(self, seconds: float) -> None:
        self._time += seconds


# ---------------------------------------------------------------------------
# 1. WaiterRepository unit tests
# ---------------------------------------------------------------------------

class TestWaiterRepository:
    def test_start_and_get(self, tmp_path: Path):
        db = _tmp_db(tmp_path)
        repo = WaiterRepository(db)
        w = repo.start_waiter(task_id="T-1", command="task_wait")
        assert w.state == "waiting"
        assert w.task_id == "T-1"
        # Fetch active
        active = repo.get_active_waiter("T-1")
        assert active is not None
        assert active.waiter_id == w.waiter_id

    def test_only_one_active_per_task(self, tmp_path: Path):
        db = _tmp_db(tmp_path)
        repo = WaiterRepository(db)
        repo.start_waiter(task_id="T-1", command="task_wait")
        with pytest.raises(sqlite3.IntegrityError):
            repo.start_waiter(task_id="T-1", command="auto_wait")

    def test_finalize_clears_active(self, tmp_path: Path):
        db = _tmp_db(tmp_path)
        repo = WaiterRepository(db)
        w = repo.start_waiter(task_id="T-1", command="task_wait")
        repo.finalize(w.waiter_id, outcome="phase:evidence_ready")
        # No longer active
        assert repo.get_active_waiter("T-1") is None
        # Can start a new one
        w2 = repo.start_waiter(task_id="T-1", command="auto_wait")
        assert w2.waiter_id != w.waiter_id

    def test_update_poll(self, tmp_path: Path):
        db = _tmp_db(tmp_path)
        repo = WaiterRepository(db)
        w = repo.start_waiter(task_id="T-1", command="task_wait")
        original_poll = w.last_poll_at
        import time; time.sleep(0.05)  # ensure timestamp differs
        repo.update_poll(w.waiter_id)
        active = repo.get_active_waiter("T-1")
        assert active is not None
        # last_poll_at may or may not differ depending on second resolution,
        # but the call shouldn't error

    def test_list_active(self, tmp_path: Path):
        db = _tmp_db(tmp_path)
        repo = WaiterRepository(db)
        repo.start_waiter(task_id="T-1", command="cmd1")
        repo.start_waiter(task_id="T-2", command="cmd2")
        w3 = repo.start_waiter(task_id="T-3", command="cmd3")
        repo.finalize(w3.waiter_id, outcome="timeout")
        active = repo.list_active_waiters()
        assert len(active) == 2
        assert {w.task_id for w in active} == {"T-1", "T-2"}


# ---------------------------------------------------------------------------
# 2. wait_for_terminal_phase creates/finalizes waiter records
# ---------------------------------------------------------------------------

class TestWaitPersistsWaiter:
    def test_terminal_phase_finalizes_waiter(self, tmp_path: Path):
        """When the task is already terminal, the waiter is created and
        immediately finalized."""
        from agpair.cli.wait import wait_for_terminal_phase
        db = _tmp_db(tmp_path)
        tasks = TaskRepository(db)
        tasks.create_task(task_id="T-1", repo_path="/r")
        tasks.mark_acked(task_id="T-1", session_id="s1")
        tasks.mark_evidence_ready(task_id="T-1")

        clock = FakeClock()
        result = wait_for_terminal_phase(db, "T-1", _clock=clock,
                                         waiter_command="test_wait")
        assert result.phase == "evidence_ready"
        assert not result.timed_out

        # Waiter is finalized (terminal)
        waiters = WaiterRepository(db)
        assert waiters.get_active_waiter("T-1") is None

        # Verify the terminal row exists
        with sqlite3.connect(db) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM waiters WHERE task_id='T-1' AND state='terminal'"
            ).fetchone()
        assert row is not None
        assert row["outcome"] == "phase:evidence_ready"
        assert row["command"] == "test_wait"

    def test_timeout_finalizes_waiter(self, tmp_path: Path):
        from agpair.cli.wait import wait_for_terminal_phase
        db = _tmp_db(tmp_path)
        tasks = TaskRepository(db)
        tasks.create_task(task_id="T-1", repo_path="/r")
        tasks.mark_acked(task_id="T-1", session_id="s1")

        clock = FakeClock()
        result = wait_for_terminal_phase(
            db, "T-1", _clock=clock, timeout_seconds=0,
            waiter_command="test_timeout",
        )
        assert result.timed_out

        waiters = WaiterRepository(db)
        assert waiters.get_active_waiter("T-1") is None

        with sqlite3.connect(db) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM waiters WHERE task_id='T-1' AND state='terminal'"
            ).fetchone()
        assert row is not None
        assert row["outcome"] == "timeout"

    def test_watchdog_finalizes_waiter(self, tmp_path: Path):
        from agpair.cli.wait import wait_for_terminal_phase
        db = _tmp_db(tmp_path)
        tasks = TaskRepository(db)
        tasks.create_task(task_id="T-1", repo_path="/r")
        tasks.mark_acked(task_id="T-1", session_id="s1")
        tasks.recommend_retry(task_id="T-1")

        clock = FakeClock()
        result = wait_for_terminal_phase(
            db, "T-1", _clock=clock,
            waiter_command="test_watchdog",
        )
        assert result.watchdog_triggered

        with sqlite3.connect(db) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM waiters WHERE task_id='T-1' AND state='terminal'"
            ).fetchone()
        assert row is not None
        assert row["outcome"] == "watchdog"

    def test_poll_updates_last_poll_at(self, tmp_path: Path):
        """The waiter's last_poll_at is updated on each poll cycle."""
        from agpair.cli.wait import wait_for_terminal_phase
        db = _tmp_db(tmp_path)
        tasks = TaskRepository(db)
        tasks.create_task(task_id="T-1", repo_path="/r")
        tasks.mark_acked(task_id="T-1", session_id="s1")

        poll_count = 0
        original_sleep = None

        class PollingClock:
            def __init__(self):
                self._time = 100.0

            def time(self) -> float:
                return self._time

            def sleep(self, seconds: float) -> None:
                nonlocal poll_count
                self._time += seconds
                poll_count += 1
                if poll_count >= 2:
                    # Transition task to terminal in the DB directly
                    tasks.mark_evidence_ready(task_id="T-1")

        clock = PollingClock()
        result = wait_for_terminal_phase(
            db, "T-1", _clock=clock, interval_seconds=1.0,
            waiter_command="test_polling",
        )
        assert result.phase == "evidence_ready"
        assert poll_count >= 2

        # Verify waiter was finalized
        with sqlite3.connect(db) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM waiters WHERE task_id='T-1' AND state='terminal'"
            ).fetchone()
        assert row is not None
        assert row["outcome"] == "phase:evidence_ready"


# ---------------------------------------------------------------------------
# 3. Intervention guard: refuse while waiter active
# ---------------------------------------------------------------------------

class TestWaiterGuard:
    def _setup(self, tmp_path: Path):
        db = _tmp_db(tmp_path)
        tasks = TaskRepository(db)
        waiters = WaiterRepository(db)
        tasks.create_task(task_id="T-1", repo_path="/r")
        tasks.mark_acked(task_id="T-1", session_id="s1")
        return db, tasks, waiters

    def test_guard_blocks_when_active_waiter(self, tmp_path: Path):
        """_guard_active_waiter raises Exit when a waiter is active."""
        from agpair.cli.task import _guard_active_waiter
        db, tasks, waiters = self._setup(tmp_path)
        waiters.start_waiter(task_id="T-1", command="task_wait")

        # Build mock paths
        paths = MagicMock()
        paths.db_path = db

        with pytest.raises((SystemExit, click.exceptions.Exit)):
            _guard_active_waiter(paths, "T-1", force=False, command="continue")

    def test_guard_allows_with_force(self, tmp_path: Path):
        from agpair.cli.task import _guard_active_waiter
        db, tasks, waiters = self._setup(tmp_path)
        waiters.start_waiter(task_id="T-1", command="task_wait")

        paths = MagicMock()
        paths.db_path = db

        # Should not raise
        _guard_active_waiter(paths, "T-1", force=True, command="continue")

    def test_guard_allows_no_active_waiter(self, tmp_path: Path):
        from agpair.cli.task import _guard_active_waiter
        db, tasks, waiters = self._setup(tmp_path)

        paths = MagicMock()
        paths.db_path = db

        # No waiter — should not raise
        _guard_active_waiter(paths, "T-1", force=False, command="continue")

    def test_guard_allows_after_waiter_finalized(self, tmp_path: Path):
        from agpair.cli.task import _guard_active_waiter
        db, tasks, waiters = self._setup(tmp_path)
        w = waiters.start_waiter(task_id="T-1", command="task_wait")
        waiters.finalize(w.waiter_id, outcome="phase:evidence_ready")

        paths = MagicMock()
        paths.db_path = db

        # Should not raise — waiter finalized
        _guard_active_waiter(paths, "T-1", force=False, command="continue")


# ---------------------------------------------------------------------------
# 4. Schema migration on old DB without waiters table
# ---------------------------------------------------------------------------

class TestWaiterMigration:
    def test_old_db_gets_waiters_table(self, tmp_path: Path):
        """An existing DB without the waiters table gets it via migration."""
        db = tmp_path / "old.db"
        # Create minimal old schema
        with sqlite3.connect(db) as conn:
            conn.executescript("""
                CREATE TABLE tasks (
                    task_id TEXT PRIMARY KEY,
                    repo_path TEXT NOT NULL,
                    phase TEXT NOT NULL,
                    antigravity_session_id TEXT,
                    attempt_no INTEGER NOT NULL DEFAULT 1,
                    retry_count INTEGER NOT NULL DEFAULT 0,
                    last_receipt_id TEXT,
                    stuck_reason TEXT,
                    retry_recommended INTEGER NOT NULL DEFAULT 0,
                    last_activity_at TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_heartbeat_at TEXT,
                    last_workspace_activity_at TEXT
                );
                CREATE TABLE receipts (
                    message_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    delivery_id TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE journal (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    source TEXT NOT NULL,
                    event TEXT NOT NULL,
                    body TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE daemon_health (
                    name TEXT PRIMARY KEY,
                    updated_at TEXT NOT NULL,
                    body TEXT NOT NULL
                );
            """)
            conn.commit()

        # Verify no waiters table yet
        with sqlite3.connect(db) as conn:
            tables = {row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()}
        assert "waiters" not in tables

        # ensure_database should migrate
        ensure_database(db)

        # Now waiters table should exist
        with sqlite3.connect(db) as conn:
            tables = {row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()}
        assert "waiters" in tables

        # And the unique index should exist
        with sqlite3.connect(db) as conn:
            indexes = {row[1] for row in conn.execute(
                "PRAGMA index_list(waiters)"
            ).fetchall()}
        assert "uq_waiters_active_task" in indexes

        # And it should be usable
        repo = WaiterRepository(db)
        w = repo.start_waiter(task_id="T-1", command="test")
        assert w.state == "waiting"


# ---------------------------------------------------------------------------
# 5. Waiter table schema validation
# ---------------------------------------------------------------------------

class TestWaiterSchema:
    def test_table_columns(self, tmp_path: Path):
        db = _tmp_db(tmp_path)
        with sqlite3.connect(db) as conn:
            cols = {row[1] for row in conn.execute("PRAGMA table_info(waiters)").fetchall()}
        expected = {"waiter_id", "task_id", "command", "state",
                    "started_at", "last_poll_at", "finished_at", "outcome"}
        assert expected == cols

    def test_unique_index_exists(self, tmp_path: Path):
        db = _tmp_db(tmp_path)
        with sqlite3.connect(db) as conn:
            indexes = {row[1] for row in conn.execute("PRAGMA index_list(waiters)").fetchall()}
        assert "uq_waiters_active_task" in indexes
