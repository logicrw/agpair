"""Tests for the premature-intervention liveness guard.

Covers:
  - `task retry` blocked on active acked task, allowed with --force
  - `task abandon` blocked on active acked task, allowed with --force
  - Liveness classification (silent / active_via_heartbeat / active_via_workspace / active_via_output / active_via_both)
  - Workspace activity prevents watchdog
  - Stale workspace activity does not prevent watchdog
  - DB migration adds last_workspace_activity_at column
  - `task status` surfaces workspace activity and liveness_state
  - Non-acked tasks are not guarded
"""
from __future__ import annotations

import click
from datetime import UTC, datetime, timedelta
import json
import os
from pathlib import Path
import sqlite3

import pytest
from typer.testing import CliRunner

from agpair.cli.app import app
from agpair.config import AppPaths
from agpair.models import TaskRecord
from agpair.runtime_liveness import (
    LivenessState,
    build_signal_summary,
    classify_liveness,
    is_task_live,
    recommend_controller_action,
)
from agpair.storage.db import connect, ensure_database
from agpair.storage.journal import JournalRepository
from agpair.storage.tasks import TaskRepository
from tests.fixtures.fake_agent_bus import read_calls, write_fake_agent_bus


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _to_iso(dt: datetime) -> str:
    return dt.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _make_paths(tmp_path: Path) -> AppPaths:
    return AppPaths.from_root(tmp_path / ".agpair")


def _seed_acked_task(tmp_path: Path, task_id: str = "TASK-LG1") -> TaskRepository:
    db_path = tmp_path / ".agpair" / "agpair.db"
    ensure_database(db_path)
    repo_path = tmp_path / "repo"
    repo_path.mkdir(parents=True, exist_ok=True)
    repo = TaskRepository(db_path)
    repo.create_task(task_id=task_id, repo_path=str(repo_path))
    repo.mark_acked(task_id=task_id, session_id=str(tmp_path / "session-live"))
    return repo


def _make_live_via_heartbeat(repo: TaskRepository, task_id: str) -> None:
    """Record a very recent heartbeat so the task appears live."""
    recent = _to_iso(datetime.now(UTC) - timedelta(seconds=30))
    repo.record_heartbeat(task_id=task_id, heartbeat_at=recent)


def _make_live_via_workspace(repo: TaskRepository, task_id: str) -> None:
    """Record very recent workspace activity so the task appears live."""
    recent = _to_iso(datetime.now(UTC) - timedelta(seconds=30))
    repo.update_workspace_activity(task_id=task_id, activity_at=recent)


def _make_live_via_output(tmp_path: Path, repo: TaskRepository, task_id: str, *, stale: bool = False) -> Path:
    """Create live stdout output under the task session directory."""
    session_dir = tmp_path / "session-live"
    session_dir.mkdir(exist_ok=True)
    stdout_path = session_dir / "stdout.log"
    stdout_path.write_text("executor is still writing\n", encoding="utf-8")
    if stale:
        stale_time = (datetime.now(UTC) - timedelta(seconds=900)).timestamp()
        os.utime(stdout_path, (stale_time, stale_time))
    task = repo.get_task(task_id)
    assert task is not None
    if task.antigravity_session_id != str(session_dir):
        repo.mark_acked(task_id=task_id, session_id=str(session_dir))
    return stdout_path


def _make_bootstrap_noise_stderr(tmp_path: Path, repo: TaskRepository, task_id: str) -> Path:
    """Create stderr containing only startup/plugin discovery noise."""
    task = repo.get_task(task_id)
    assert task is not None
    session_id = task.antigravity_session_id or task.executor_session_id or str(tmp_path / "session-noisy")
    session_dir = Path(str(session_id))
    session_dir.mkdir(exist_ok=True)
    stderr_path = session_dir / "stderr.log"
    stderr_path.write_text(
        "2026-06-08T09:03:49Z INFO plugin discovered name=backend-tools scope=user\n"
        "2026-06-08T09:03:50Z INFO plugin discovered name=platform-tools scope=user\n",
        encoding="utf-8",
    )
    return stderr_path


def _make_live_via_both(repo: TaskRepository, task_id: str) -> None:
    _make_live_via_heartbeat(repo, task_id)
    _make_live_via_workspace(repo, task_id)


# ---------------------------------------------------------------------------
# 1. Liveness classification
# ---------------------------------------------------------------------------


def test_classify_silent_when_no_signals(tmp_path: Path) -> None:
    repo = _seed_acked_task(tmp_path)
    task = repo.get_task("TASK-LG1")
    assert classify_liveness(task) == LivenessState.silent
    assert is_task_live(task) is False


def test_classify_active_via_heartbeat(tmp_path: Path) -> None:
    repo = _seed_acked_task(tmp_path)
    _make_live_via_heartbeat(repo, "TASK-LG1")
    task = repo.get_task("TASK-LG1")
    assert classify_liveness(task) == LivenessState.active_via_heartbeat
    assert is_task_live(task) is True


def test_classify_active_via_workspace(tmp_path: Path) -> None:
    repo = _seed_acked_task(tmp_path)
    _make_live_via_workspace(repo, "TASK-LG1")
    task = repo.get_task("TASK-LG1")
    assert classify_liveness(task) == LivenessState.active_via_workspace
    assert is_task_live(task) is True


def test_classify_active_via_output(tmp_path: Path) -> None:
    repo = _seed_acked_task(tmp_path)
    _make_live_via_output(tmp_path, repo, "TASK-LG1")
    task = repo.get_task("TASK-LG1")
    assert classify_liveness(task) == LivenessState.active_via_output
    assert is_task_live(task) is True


def test_bootstrap_stderr_noise_is_not_live_output(tmp_path: Path) -> None:
    repo = _seed_acked_task(tmp_path)
    _make_bootstrap_noise_stderr(tmp_path, repo, "TASK-LG1")
    task = repo.get_task("TASK-LG1")
    assert classify_liveness(task) == LivenessState.silent
    assert is_task_live(task) is False


def test_real_executor_startup_warnings_are_not_live_output(tmp_path: Path) -> None:
    repo = _seed_acked_task(tmp_path)
    task = repo.get_task("TASK-LG1")
    assert task is not None
    session_dir = Path(task.executor_session_id or "")
    session_dir.mkdir(parents=True, exist_ok=True)
    stderr_path = session_dir / "stderr.log"
    stderr_path.write_text(
        "WARN config.toml has unrecognized key(s): legacyProvider\n"
        "WARN config has unrecognized key(s): subagents.default_model\n"
        "WARN manifest path escapes plugin root; skipping path=/Users/example/.claude/plugins/foo\n"
        "WARN plugin name collision resolved by scope precedence name=tools scope=user\n"
        "WARN skill name does not match expected name from path: file=skills/foo/SKILL.md\n"
        "WARN skill name shadowed by a higher-precedence skill name=review-work\n"
        "WARN hooks: skipped unrecognized event names: PreToolUse\n"
        "WARN hook loading from settings file: failed to parse hook file hooks.json\n"
        "WARN shell state dump read timed out after 5s (END marker never arrived)\n",
        encoding="utf-8",
    )

    signal = build_signal_summary(task, freshness_seconds=300)

    assert classify_liveness(task) == LivenessState.silent
    assert is_task_live(task) is False
    assert signal.bootstrap_noise_only is True
    assert signal.progress_quality == "bootstrap_noise"
    assert "hook loading from settings file" in (signal.last_safe_excerpt or "")
    assert len(signal.last_safe_excerpt or "") <= 500
    assert signal.stdout_bytes == 0
    assert signal.stderr_bytes > 0


def test_stale_output_activity_is_silent(tmp_path: Path) -> None:
    repo = _seed_acked_task(tmp_path)
    _make_live_via_output(tmp_path, repo, "TASK-LG1", stale=True)
    task = repo.get_task("TASK-LG1")
    assert classify_liveness(task) == LivenessState.silent
    assert is_task_live(task) is False


def test_classify_active_via_both(tmp_path: Path) -> None:
    repo = _seed_acked_task(tmp_path)
    _make_live_via_both(repo, "TASK-LG1")
    task = repo.get_task("TASK-LG1")
    assert classify_liveness(task) == LivenessState.active_via_both
    assert is_task_live(task) is True


def test_signal_summary_reports_output_metadata_without_log_body(tmp_path: Path) -> None:
    repo = _seed_acked_task(tmp_path)
    task = repo.get_task("TASK-LG1")
    assert task is not None
    session_dir = Path(task.executor_session_id or "")
    session_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = session_dir / "stdout.log"
    stdout_path.write_text("useful progress\n", encoding="utf-8")
    signal_time = datetime(2026, 3, 24, 11, 59, tzinfo=UTC)
    os.utime(stdout_path, (signal_time.timestamp(), signal_time.timestamp()))

    signal = build_signal_summary(
        task,
        now=datetime(2026, 3, 24, 12, 0, tzinfo=UTC),
        freshness_seconds=300,
    )

    assert signal.state == LivenessState.active_via_output.value
    assert signal.last_signal_type == "stdout"
    assert signal.progress_quality == "partial_output"
    assert signal.last_safe_excerpt == "useful progress"
    assert signal.stdout_bytes == len("useful progress\n")
    assert signal.stderr_bytes == 0
    assert signal.controller_silence_seconds == 60


def test_recommend_controller_action_detaches_live_background_task(tmp_path: Path) -> None:
    repo = _seed_acked_task(tmp_path)
    with sqlite3.connect(_make_paths(tmp_path).db_path) as conn:
        conn.execute(
            "UPDATE tasks SET wait_policy=?, controller_wait_seconds=?, execution_budget_seconds=?, background_ok=? WHERE task_id=?",
            ("lease", 30, 3600, 1, "TASK-LG1"),
        )
        conn.commit()
    repo.update_workspace_activity(
        task_id="TASK-LG1",
        activity_at=datetime(2026, 3, 24, 11, 59, tzinfo=UTC).isoformat(),
    )
    task = repo.get_task("TASK-LG1")
    assert task is not None

    signal = build_signal_summary(
        task,
        now=datetime(2026, 3, 24, 12, 0, tzinfo=UTC),
        freshness_seconds=300,
    )

    assert signal.state == LivenessState.active_via_workspace.value
    assert recommend_controller_action(task, signal) == "detach_and_continue"


def test_recommend_controller_action_retries_when_only_bootstrap_noise(tmp_path: Path) -> None:
    repo = _seed_acked_task(tmp_path)
    with sqlite3.connect(_make_paths(tmp_path).db_path) as conn:
        conn.execute(
            "UPDATE tasks SET wait_policy=?, controller_wait_seconds=?, execution_budget_seconds=?, background_ok=? WHERE task_id=?",
            ("lease", 30, 3600, 1, "TASK-LG1"),
        )
        conn.commit()
    _make_bootstrap_noise_stderr(tmp_path, repo, "TASK-LG1")
    task = repo.get_task("TASK-LG1")
    assert task is not None

    signal = build_signal_summary(task, freshness_seconds=300)

    assert signal.bootstrap_noise_only is True
    assert recommend_controller_action(task, signal) == "retry_or_switch_executor"


def test_recommend_controller_action_retries_when_execution_budget_is_exhausted(tmp_path: Path) -> None:
    repo = _seed_acked_task(tmp_path)
    with sqlite3.connect(_make_paths(tmp_path).db_path) as conn:
        conn.execute(
            "UPDATE tasks SET wait_policy=?, controller_wait_seconds=?, execution_budget_seconds=?, background_ok=?, created_at=? WHERE task_id=?",
            (
                "lease",
                30,
                60,
                1,
                datetime(2026, 3, 24, 11, 58, tzinfo=UTC).isoformat(),
                "TASK-LG1",
            ),
        )
        conn.commit()
    _make_live_via_output(tmp_path, repo, "TASK-LG1")
    task = repo.get_task("TASK-LG1")
    assert task is not None

    signal = build_signal_summary(
        task,
        now=datetime(2026, 3, 24, 12, 0, tzinfo=UTC),
        freshness_seconds=300,
    )

    assert signal.execution_budget_remaining_seconds == 0.0
    assert recommend_controller_action(task, signal) == "retry_or_switch_executor"


def test_classify_stale_signals_are_silent(tmp_path: Path) -> None:
    repo = _seed_acked_task(tmp_path)
    stale = _to_iso(datetime.now(UTC) - timedelta(minutes=10))
    repo.record_heartbeat(task_id="TASK-LG1", heartbeat_at=stale)
    repo.update_workspace_activity(task_id="TASK-LG1", activity_at=stale)
    task = repo.get_task("TASK-LG1")
    assert classify_liveness(task) == LivenessState.silent
    assert is_task_live(task) is False




# ---------------------------------------------------------------------------
# 3. task retry blocked on live acked task, allowed with --force
# ---------------------------------------------------------------------------


def test_retry_blocked_on_live_acked_task(tmp_path: Path, monkeypatch) -> None:
    binary, calls_path, pull_path = write_fake_agent_bus(tmp_path)
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    monkeypatch.setenv("AGPAIR_AGENT_BUS_BIN", binary)
    monkeypatch.setenv("FAKE_AGENT_BUS_CALLS", str(calls_path))
    monkeypatch.setenv("FAKE_AGENT_BUS_PULL", str(pull_path))
    repo = _seed_acked_task(tmp_path)
    _make_live_via_workspace(repo, "TASK-LG1")

    result = CliRunner().invoke(app, [
        "task", "retry", "TASK-LG1", "--body", "retry", "--no-wait",
    ])
    assert result.exit_code == 1
    assert "Refused" in (result.stderr or "")
    assert "--force" in (result.stderr or "")


def test_retry_allowed_with_force_on_live_acked_task(tmp_path: Path, monkeypatch) -> None:
    binary, calls_path, pull_path = write_fake_agent_bus(tmp_path)
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    monkeypatch.setenv("AGPAIR_AGENT_BUS_BIN", binary)
    monkeypatch.setenv("FAKE_AGENT_BUS_CALLS", str(calls_path))
    monkeypatch.setenv("FAKE_AGENT_BUS_PULL", str(pull_path))
    repo = _seed_acked_task(tmp_path)
    _make_live_via_workspace(repo, "TASK-LG1")

    result = CliRunner().invoke(app, [
        "task", "retry", "TASK-LG1", "--body", "retry", "--force", "--no-wait",
    ])
    assert result.exit_code == 0


def test_retry_allowed_on_non_acked_task(tmp_path: Path, monkeypatch) -> None:
    """Non-acked (e.g. stuck) tasks are not guarded."""
    binary, calls_path, pull_path = write_fake_agent_bus(tmp_path)
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    monkeypatch.setenv("AGPAIR_AGENT_BUS_BIN", binary)
    monkeypatch.setenv("FAKE_AGENT_BUS_CALLS", str(calls_path))
    monkeypatch.setenv("FAKE_AGENT_BUS_PULL", str(pull_path))
    repo = _seed_acked_task(tmp_path)
    repo.mark_stuck(task_id="TASK-LG1", reason="stuck")

    result = CliRunner().invoke(app, [
        "task", "retry", "TASK-LG1", "--body", "retry", "--no-wait",
    ])
    assert result.exit_code == 0


# ---------------------------------------------------------------------------
# 4. task abandon blocked on live acked task, allowed with --force
# ---------------------------------------------------------------------------


def test_abandon_blocked_on_live_acked_task(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    repo = _seed_acked_task(tmp_path)
    _make_live_via_both(repo, "TASK-LG1")

    result = CliRunner().invoke(app, [
        "task", "abandon", "TASK-LG1",
    ])
    assert result.exit_code == 1
    assert "Refused" in (result.stderr or "")
    assert "--force" in (result.stderr or "")
    # Task should NOT be abandoned
    task = repo.get_task("TASK-LG1")
    assert task.phase == "acked"


def test_abandon_allowed_with_force_on_live_acked_task(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    repo = _seed_acked_task(tmp_path)
    _make_live_via_both(repo, "TASK-LG1")

    result = CliRunner().invoke(app, [
        "task", "abandon", "TASK-LG1", "--force",
    ])
    assert result.exit_code == 0
    task = repo.get_task("TASK-LG1")
    assert task.phase == "abandoned"


def test_abandon_allowed_on_silent_acked_task(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    repo = _seed_acked_task(tmp_path)

    result = CliRunner().invoke(app, [
        "task", "abandon", "TASK-LG1",
    ])
    assert result.exit_code == 0
    task = repo.get_task("TASK-LG1")
    assert task.phase == "abandoned"


# ---------------------------------------------------------------------------
# 5. Workspace activity prevents watchdog
# ---------------------------------------------------------------------------


class FakeClock:
    def __init__(self, start: float = 0.0):
        self._now = start

    def time(self) -> float:
        return self._now

    def sleep(self, seconds: float) -> None:
        self._now += seconds


def test_workspace_activity_prevents_watchdog_on_acked_task(tmp_path: Path) -> None:
    """Daemon watchdog should NOT fire when workspace activity is recent."""
    from agpair.daemon.loop import run_once

    paths = _make_paths(tmp_path)
    ensure_database(paths.db_path)
    repo = TaskRepository(paths.db_path)
    repo.create_task(task_id="TASK-WS1", repo_path="/tmp/repo")
    repo.mark_acked(task_id="TASK-WS1", session_id="s-ws")

    # Backdate last_activity_at to 16 min ago (past watchdog 15 min)
    old = _to_iso(datetime(2026, 3, 24, 11, 44, tzinfo=UTC))
    with sqlite3.connect(paths.db_path) as conn:
        conn.execute(
            "UPDATE tasks SET last_activity_at=?, updated_at=? WHERE task_id=?",
            (old, old, "TASK-WS1"),
        )
        conn.commit()

    # But record recent workspace activity (2 min ago)
    ws_recent = _to_iso(datetime(2026, 3, 24, 11, 58, tzinfo=UTC))
    repo.update_workspace_activity(task_id="TASK-WS1", activity_at=ws_recent)

    now = datetime(2026, 3, 24, 12, 0, tzinfo=UTC)

    class EmptyBus:
        def pull_receipts(self, **kw):
            return []

        def reserve_receipts(self, **kw):
            return []

        def settle_claims(self, **kw):
            return 0

    run_once(
        paths,
        now=now,
        bus=EmptyBus(),
        timeout_seconds=1800,
        watchdog_seconds=900,
    )

    task = repo.get_task("TASK-WS1")
    assert task.retry_recommended is False
    assert task.phase == "acked"


def test_stale_workspace_activity_does_not_prevent_watchdog(tmp_path: Path) -> None:
    """When workspace activity is old, watchdog fires normally."""
    from agpair.daemon.loop import run_once

    paths = _make_paths(tmp_path)
    ensure_database(paths.db_path)
    repo = TaskRepository(paths.db_path)
    repo.create_task(task_id="TASK-WS2", repo_path="/tmp/repo")
    repo.mark_acked(task_id="TASK-WS2", session_id="s-ws2")

    # Backdate everything to 16 min ago
    old = _to_iso(datetime(2026, 3, 24, 11, 44, tzinfo=UTC))
    with sqlite3.connect(paths.db_path) as conn:
        conn.execute(
            "UPDATE tasks SET last_activity_at=?, last_workspace_activity_at=?, updated_at=? WHERE task_id=?",
            (old, old, old, "TASK-WS2"),
        )
        conn.commit()

    now = datetime(2026, 3, 24, 12, 0, tzinfo=UTC)

    class EmptyBus:
        def pull_receipts(self, **kw):
            return []

        def reserve_receipts(self, **kw):
            return []

        def settle_claims(self, **kw):
            return 0

    run_once(
        paths,
        now=now,
        bus=EmptyBus(),
        timeout_seconds=1800,
        watchdog_seconds=900,
    )

    task = repo.get_task("TASK-WS2")
    assert task.retry_recommended is True


# ---------------------------------------------------------------------------
# 6. Wait respects workspace activity
# ---------------------------------------------------------------------------


def test_wait_does_not_watchdog_exit_with_fresh_workspace_activity(tmp_path: Path) -> None:
    """Wait should NOT trigger watchdog when task has recent workspace activity."""
    from agpair.cli.wait import wait_for_terminal_phase

    paths = _make_paths(tmp_path)
    ensure_database(paths.db_path)
    repo = TaskRepository(paths.db_path)
    repo.create_task(task_id="T-WS-W1", repo_path="/r")
    repo.mark_acked(task_id="T-WS-W1", session_id="s-1")
    repo.recommend_retry(task_id="T-WS-W1")

    # Set recent workspace activity
    ws_time = _to_iso(datetime(2026, 3, 24, 12, 0, tzinfo=UTC))
    repo.update_workspace_activity(task_id="T-WS-W1", activity_at=ws_time)

    fixed_now = datetime(2026, 3, 24, 12, 1, tzinfo=UTC)
    poll_count = 0

    class TrackingClock(FakeClock):
        def sleep(self, seconds: float) -> None:
            nonlocal poll_count
            poll_count += 1
            super().sleep(seconds)
            if poll_count == 1:
                repo.mark_evidence_ready(task_id="T-WS-W1")

    clock = TrackingClock()
    result = wait_for_terminal_phase(
        paths.db_path, "T-WS-W1",
        interval_seconds=5, timeout_seconds=60,
        heartbeat_silence_seconds=300,
        _clock=clock,
        _utcnow=lambda: fixed_now,
    )
    assert result.phase == "evidence_ready"
    assert result.watchdog_triggered is False


def test_wait_does_not_watchdog_exit_with_fresh_output_activity(tmp_path: Path) -> None:
    """Wait should NOT trigger watchdog when live stdout has recent output."""
    from agpair.cli.wait import wait_for_terminal_phase

    paths = _make_paths(tmp_path)
    ensure_database(paths.db_path)
    repo = TaskRepository(paths.db_path)
    repo.create_task(task_id="T-OUT-W1", repo_path="/r")
    repo.mark_acked(task_id="T-OUT-W1", session_id=str(tmp_path / "session-live"))
    repo.recommend_retry(task_id="T-OUT-W1")
    _make_live_via_output(tmp_path, repo, "T-OUT-W1")

    fixed_now = datetime.now(UTC)
    poll_count = 0

    class TrackingClock(FakeClock):
        def sleep(self, seconds: float) -> None:
            nonlocal poll_count
            poll_count += 1
            super().sleep(seconds)
            if poll_count == 1:
                repo.mark_evidence_ready(task_id="T-OUT-W1")

    result = wait_for_terminal_phase(
        paths.db_path,
        "T-OUT-W1",
        interval_seconds=5,
        timeout_seconds=60,
        heartbeat_silence_seconds=300,
        _clock=TrackingClock(),
        _utcnow=lambda: fixed_now,
    )
    assert result.phase == "evidence_ready"
    assert result.watchdog_triggered is False


def test_wait_watchdogs_when_only_bootstrap_stderr_is_fresh(tmp_path: Path) -> None:
    """Fresh plugin/bootstrap stderr is not progress and should not prevent watchdog."""
    from agpair.cli.wait import wait_for_terminal_phase

    paths = _make_paths(tmp_path)
    ensure_database(paths.db_path)
    repo = TaskRepository(paths.db_path)
    repo.create_task(task_id="T-NOISE-W1", repo_path="/r")
    repo.mark_acked(task_id="T-NOISE-W1", session_id=str(tmp_path / "session-noisy"))
    repo.recommend_retry(task_id="T-NOISE-W1")
    _make_bootstrap_noise_stderr(tmp_path, repo, "T-NOISE-W1")

    fixed_now = datetime.now(UTC)
    result = wait_for_terminal_phase(
        paths.db_path,
        "T-NOISE-W1",
        interval_seconds=5,
        timeout_seconds=60,
        heartbeat_silence_seconds=300,
        _clock=FakeClock(),
        _utcnow=lambda: fixed_now,
    )
    assert result.phase == "acked"
    assert result.watchdog_triggered is True


# ---------------------------------------------------------------------------
# 7. DB migration adds last_workspace_activity_at column
# ---------------------------------------------------------------------------


def test_schema_migration_adds_workspace_activity_column(tmp_path: Path) -> None:
    """An existing DB without last_workspace_activity_at should get it via migration."""
    db_path = tmp_path / "old.db"

    # Create old schema without last_workspace_activity_at (but with last_heartbeat_at)
    with sqlite3.connect(db_path) as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS tasks (
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
              last_heartbeat_at TEXT
            );
            CREATE TABLE IF NOT EXISTS receipts (
              message_id TEXT PRIMARY KEY,
              task_id TEXT NOT NULL,
              status TEXT NOT NULL,
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS journal (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              task_id TEXT NOT NULL,
              source TEXT NOT NULL,
              event TEXT NOT NULL,
              body TEXT NOT NULL,
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS daemon_health (
              name TEXT PRIMARY KEY,
              updated_at TEXT NOT NULL,
              body TEXT NOT NULL
            );
        """)
        conn.execute(
            "INSERT INTO tasks VALUES (?, ?, 'acked', 'session-1', 1, 0, NULL, NULL, 0, ?, ?, ?, NULL)",
            ("OLD-TASK", "/repo", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
        )
        conn.commit()

    # Verify old schema has no last_workspace_activity_at
    with sqlite3.connect(db_path) as conn:
        cols_before = {row[1] for row in conn.execute("PRAGMA table_info(tasks)").fetchall()}
    assert "last_workspace_activity_at" not in cols_before
    assert "last_heartbeat_at" in cols_before

    # Trigger migration
    ensure_database(db_path)

    # Verify column exists after migration
    with sqlite3.connect(db_path) as conn:
        cols_after = {row[1] for row in conn.execute("PRAGMA table_info(tasks)").fetchall()}
    assert "last_workspace_activity_at" in cols_after

    # Verify existing data is intact
    repo = TaskRepository(db_path)
    task = repo.get_task("OLD-TASK")
    assert task is not None
    assert task.phase == "acked"
    assert task.last_workspace_activity_at is None

    # Verify we can write workspace activity
    repo.update_workspace_activity(task_id="OLD-TASK", activity_at="2026-03-24T12:00:00Z")
    task = repo.get_task("OLD-TASK")
    assert task.last_workspace_activity_at == "2026-03-24T12:00:00Z"


def test_schema_migration_from_no_heartbeat_no_workspace(tmp_path: Path) -> None:
    """DB with neither last_heartbeat_at nor last_workspace_activity_at gets both."""
    db_path = tmp_path / "ancient.db"

    with sqlite3.connect(db_path) as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS tasks (
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
              updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS receipts (
              message_id TEXT PRIMARY KEY,
              task_id TEXT NOT NULL,
              status TEXT NOT NULL,
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS journal (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              task_id TEXT NOT NULL,
              source TEXT NOT NULL,
              event TEXT NOT NULL,
              body TEXT NOT NULL,
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS daemon_health (
              name TEXT PRIMARY KEY,
              updated_at TEXT NOT NULL,
              body TEXT NOT NULL
            );
        """)
        conn.commit()

    ensure_database(db_path)

    with sqlite3.connect(db_path) as conn:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(tasks)").fetchall()}
    assert "last_heartbeat_at" in cols
    assert "last_workspace_activity_at" in cols


# ---------------------------------------------------------------------------
# 8. task status surfaces workspace activity and liveness_state
# ---------------------------------------------------------------------------


def test_task_status_shows_workspace_activity_on_acked(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    repo = _seed_acked_task(tmp_path)
    _make_live_via_workspace(repo, "TASK-LG1")

    result = CliRunner().invoke(app, ["task", "status", "TASK-LG1"])
    assert result.exit_code == 0
    assert "last_workspace_activity_at:" in result.stdout
    assert "liveness_state: active_via_workspace" in result.stdout


def test_task_status_shows_silent_liveness_for_acked(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    _seed_acked_task(tmp_path)

    result = CliRunner().invoke(app, ["task", "status", "TASK-LG1"])
    assert result.exit_code == 0
    assert "liveness_state: silent" in result.stdout


def test_task_status_json_includes_signal_state_and_controller_action(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    repo = _seed_acked_task(tmp_path)
    with sqlite3.connect(_make_paths(tmp_path).db_path) as conn:
        conn.execute(
            "UPDATE tasks SET wait_policy=?, controller_wait_seconds=?, execution_budget_seconds=?, background_ok=? WHERE task_id=?",
            ("lease", 30, 3600, 1, "TASK-LG1"),
        )
        conn.commit()
    repo.update_workspace_activity(
        task_id="TASK-LG1",
        activity_at=datetime.now(UTC).isoformat(),
    )

    result = CliRunner().invoke(app, ["task", "status", "TASK-LG1", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["signal_state"]["state"] == "active_via_workspace"
    assert payload["signal_state"]["last_signal_type"] == "workspace"
    assert payload["controller_action"] == "continue_waiting"


def test_task_status_no_liveness_for_non_acked(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    repo = _seed_acked_task(tmp_path)
    repo.mark_evidence_ready(task_id="TASK-LG1")

    result = CliRunner().invoke(app, ["task", "status", "TASK-LG1"])
    assert result.exit_code == 0
    assert "liveness_state:" not in result.stdout


# ---------------------------------------------------------------------------
# 9. retry dispatch resets workspace activity
# ---------------------------------------------------------------------------


def test_retry_dispatch_resets_workspace_activity(tmp_path: Path) -> None:
    paths = _make_paths(tmp_path)
    ensure_database(paths.db_path)
    repo = TaskRepository(paths.db_path)
    repo.create_task(task_id="T-RTY-WS", repo_path="/r")
    repo.mark_acked(task_id="T-RTY-WS", session_id="s-1")
    repo.update_workspace_activity(task_id="T-RTY-WS", activity_at="2026-03-24T12:00:00Z")

    task = repo.get_task("T-RTY-WS")
    assert task.last_workspace_activity_at is not None

    updated = repo.apply_retry_dispatch(task_id="T-RTY-WS")
    assert updated.last_workspace_activity_at is None
    assert updated.last_heartbeat_at is None
    assert updated.phase == "new"




# ---------------------------------------------------------------------------
# 11. Help shows --force for guarded commands
# ---------------------------------------------------------------------------


def test_force_flag_in_help() -> None:
    runner = CliRunner()
    for cmd in ("retry", "abandon"):
        result = runner.invoke(app, ["task", cmd, "--help"])
        assert result.exit_code == 0, f"{cmd} --help failed"
        assert "--force" in click.unstyle(result.stdout), f"{cmd} missing --force"
