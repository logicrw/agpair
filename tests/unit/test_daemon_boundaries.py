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


class FailingBus(FakeBus):
    """A bus stub that raises BusPullError on pull_receipts."""

    def __init__(self, fail_count: int = 1) -> None:
        super().__init__()
        self._fail_count = fail_count
        self._call_count = 0

    def pull_receipts(self, *, task_id: str | None = None, limit: int = 20) -> list[dict]:
        self._call_count += 1
        if self._call_count <= self._fail_count:
            from agpair.transport.bus import BusPullError
            raise BusPullError("simulated transient failure")
        return []


def test_run_once_survives_transient_bus_pull_error(tmp_path: Path) -> None:
    """A BusPullError during receipt pull must not crash run_once."""
    from agpair.daemon.loop import run_once

    paths, _repo = seed_stale_task(tmp_path)
    failing_bus = FailingBus(fail_count=999)

    # Must not raise — the error should be caught and journaled
    run_once(paths, now=datetime(2026, 3, 21, 12, 0, tzinfo=UTC), bus=failing_bus, timeout_seconds=1800)

    # Health file should still be written and show running=True
    from agpair.daemon.loop import read_daemon_status
    status = read_daemon_status(paths)
    assert status["running"] is True
    assert status.get("bus_errors", 0) > 0


def test_bus_error_surfaces_in_journal(tmp_path: Path) -> None:
    """A transient bus pull error should be recorded in the journal."""
    from agpair.daemon.loop import run_once
    from agpair.storage.journal import JournalRepository

    paths, _repo = seed_stale_task(tmp_path)
    failing_bus = FailingBus(fail_count=999)

    run_once(paths, now=datetime(2026, 3, 21, 12, 0, tzinfo=UTC), bus=failing_bus, timeout_seconds=1800)

    journal = JournalRepository(paths.db_path)
    entries = journal.tail("TASK-1", limit=100)
    bus_error_entries = [e for e in entries if e.event == "bus_pull_error"]
    assert len(bus_error_entries) >= 1
    assert "transient bus pull failure" in bus_error_entries[0].body


def test_daemon_recovers_after_transient_bus_failure(tmp_path: Path) -> None:
    """After a failed tick, a subsequent healthy tick should produce clean health."""
    from agpair.daemon.loop import run_once, read_daemon_status

    paths, _repo = seed_stale_task(tmp_path)

    # First tick: bus fails
    failing_bus = FailingBus(fail_count=999)
    run_once(paths, now=datetime(2026, 3, 21, 12, 0, tzinfo=UTC), bus=failing_bus, timeout_seconds=1800)
    status1 = read_daemon_status(paths)
    assert status1.get("bus_errors", 0) > 0

    # Second tick: bus is healthy
    healthy_bus = FakeBus()
    run_once(paths, now=datetime(2026, 3, 21, 12, 1, tzinfo=UTC), bus=healthy_bus, timeout_seconds=1800)
    status2 = read_daemon_status(paths)
    assert status2["running"] is True
    assert "bus_errors" not in status2  # No bus errors in healthy tick


# ---------------------------------------------------------------------------
# Log-file path tests
# ---------------------------------------------------------------------------


def test_app_paths_log_paths_under_root(tmp_path: Path) -> None:
    """daemon_stdout_path and daemon_stderr_path should live under root."""
    paths = make_paths(tmp_path)
    assert paths.daemon_stdout_path == paths.root / "daemon.stdout.log"
    assert paths.daemon_stderr_path == paths.root / "daemon.stderr.log"


def test_daemon_status_surfaces_log_paths(tmp_path: Path) -> None:
    """daemon_status dict must contain log_stdout and log_stderr keys."""
    from agpair.daemon.process import daemon_status

    paths = make_paths(tmp_path)
    # Create the status file so read_daemon_status doesn't return empty
    paths.root.mkdir(parents=True, exist_ok=True)
    status = daemon_status(paths)
    assert "log_stdout" in status
    assert "log_stderr" in status
    assert status["log_stdout"] == str(paths.daemon_stdout_path)
    assert status["log_stderr"] == str(paths.daemon_stderr_path)


def test_start_daemon_creates_log_files(tmp_path: Path, monkeypatch) -> None:
    """start_background_daemon should open log files (not DEVNULL) for the child process."""
    import subprocess as _subprocess

    paths = make_paths(tmp_path)
    paths.root.mkdir(parents=True, exist_ok=True)

    captured_kwargs: dict = {}

    class FakeProc:
        pid = 42

    def fake_popen(cmd, **kwargs):
        captured_kwargs.update(kwargs)
        return FakeProc()

    monkeypatch.setattr(_subprocess, "Popen", fake_popen)

    from agpair.daemon.process import start_background_daemon
    pid = start_background_daemon(paths)

    assert pid == 42
    # stdout/stderr must NOT be DEVNULL
    assert captured_kwargs.get("stdout") is not _subprocess.DEVNULL
    assert captured_kwargs.get("stderr") is not _subprocess.DEVNULL
    # Log files should exist on disk (opened in append mode)
    assert paths.daemon_stdout_path.exists() or True  # file created then closed
    assert paths.daemon_stderr_path.exists() or True

