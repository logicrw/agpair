"""Tests for ``agpair task wait`` and default auto-wait behaviour."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import click
import pytest
from typer.testing import CliRunner

from agpair.cli.app import app
from agpair.cli.wait import (
    APPROVE_SUCCESS_PHASES,
    APPROVE_TERMINAL_PHASES,
    DISPATCH_SUCCESS_PHASES,
    FAILURE_PHASES,
    TERMINAL_PHASES,
    WaitResult,
    exit_code_for_approve,
    exit_code_for_dispatch,
    wait_for_terminal_phase,
)
from agpair.config import AppPaths
from agpair.storage.db import ensure_database
from agpair.storage.journal import JournalRepository
from agpair.storage.tasks import TaskRepository
from tests.fixtures.fake_agent_bus import write_fake_agent_bus


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
# Unit: TERMINAL_PHASES constants
# ---------------------------------------------------------------------------


def test_terminal_phases_contain_required_values():
    assert TERMINAL_PHASES == {"evidence_ready", "blocked", "committed", "stuck", "abandoned"}


def test_dispatch_success_phases():
    assert DISPATCH_SUCCESS_PHASES == {"evidence_ready", "committed"}


def test_approve_success_phases():
    assert APPROVE_SUCCESS_PHASES == {"committed"}


def test_failure_phases():
    assert FAILURE_PHASES == {"blocked", "stuck", "abandoned"}


def test_approve_terminal_phases_exclude_evidence_ready():
    assert "evidence_ready" not in APPROVE_TERMINAL_PHASES
    assert APPROVE_TERMINAL_PHASES == {"blocked", "committed", "stuck", "abandoned"}


# ---------------------------------------------------------------------------
# Unit: exit_code helpers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("phase,expected", [
    ("evidence_ready", 0),
    ("committed", 0),
    ("blocked", 1),
    ("stuck", 1),
    ("abandoned", 1),
])
def test_exit_code_for_dispatch(phase: str, expected: int):
    assert exit_code_for_dispatch(WaitResult(phase=phase, timed_out=False)) == expected


def test_exit_code_for_dispatch_timeout():
    assert exit_code_for_dispatch(WaitResult(phase="acked", timed_out=True)) == 1


@pytest.mark.parametrize("phase,expected", [
    ("committed", 0),
    ("evidence_ready", 1),
    ("blocked", 1),
    ("stuck", 1),
    ("abandoned", 1),
])
def test_exit_code_for_approve(phase: str, expected: int):
    assert exit_code_for_approve(WaitResult(phase=phase, timed_out=False)) == expected


# ---------------------------------------------------------------------------
# Unit: wait_for_terminal_phase with FakeClock
# ---------------------------------------------------------------------------


def test_wait_returns_immediately_on_terminal_phase(tmp_path: Path):
    repo = _make_repo(tmp_path)
    repo.create_task(task_id="T-1", repo_path="/r")
    repo.mark_acked(task_id="T-1", session_id="test-session")
    repo.mark_evidence_ready(task_id="T-1")

    clock = FakeClock()
    paths = _make_paths(tmp_path)
    result = wait_for_terminal_phase(
        paths.db_path, "T-1", interval_seconds=1, timeout_seconds=30, _clock=clock,
    )
    assert result.phase == "evidence_ready"
    assert result.timed_out is False
    # Should not have slept at all
    assert clock.time() == 0.0


def test_wait_polls_until_phase_changes(tmp_path: Path):
    repo = _make_repo(tmp_path)
    repo.create_task(task_id="T-2", repo_path="/r")
    repo.mark_acked(task_id="T-2", session_id="s-1")

    paths = _make_paths(tmp_path)
    poll_count = 0
    original_sleep = FakeClock.sleep

    class TrackingClock(FakeClock):
        def sleep(self, seconds: float) -> None:
            nonlocal poll_count
            poll_count += 1
            super().sleep(seconds)
            # After 2 polls, simulate the daemon marking the task committed
            if poll_count == 2:
                repo.mark_committed(task_id="T-2")

    clock = TrackingClock()
    result = wait_for_terminal_phase(
        paths.db_path, "T-2", interval_seconds=5, timeout_seconds=60, _clock=clock,
    )
    assert result.phase == "committed"
    assert result.timed_out is False
    assert poll_count == 2


def test_wait_times_out(tmp_path: Path):
    repo = _make_repo(tmp_path)
    repo.create_task(task_id="T-3", repo_path="/r")
    repo.mark_acked(task_id="T-3", session_id="s-1")

    paths = _make_paths(tmp_path)
    clock = FakeClock()
    result = wait_for_terminal_phase(
        paths.db_path, "T-3", interval_seconds=5, timeout_seconds=10, _clock=clock,
    )
    assert result.timed_out is True
    assert result.phase == "acked"


def test_wait_blocked_is_terminal(tmp_path: Path):
    repo = _make_repo(tmp_path)
    repo.create_task(task_id="T-4", repo_path="/r")
    repo.mark_acked(task_id="T-4", session_id="test-session")
    repo.mark_blocked(task_id="T-4", reason="transport error")

    paths = _make_paths(tmp_path)
    clock = FakeClock()
    result = wait_for_terminal_phase(
        paths.db_path, "T-4", interval_seconds=1, timeout_seconds=60, _clock=clock,
    )
    assert result.phase == "blocked"
    assert result.timed_out is False


def test_wait_stuck_is_terminal(tmp_path: Path):
    repo = _make_repo(tmp_path)
    repo.create_task(task_id="T-5", repo_path="/r")
    repo.mark_acked(task_id="T-5", session_id="test-session")
    repo.mark_stuck(task_id="T-5", reason="no activity")

    paths = _make_paths(tmp_path)
    clock = FakeClock()
    result = wait_for_terminal_phase(
        paths.db_path, "T-5", interval_seconds=1, timeout_seconds=60, _clock=clock,
    )
    assert result.phase == "stuck"
    assert result.timed_out is False


def test_wait_abandoned_is_terminal(tmp_path: Path):
    repo = _make_repo(tmp_path)
    repo.create_task(task_id="T-5B", repo_path="/r")
    repo.mark_abandoned(task_id="T-5B", reason="manual cleanup")

    paths = _make_paths(tmp_path)
    clock = FakeClock()
    result = wait_for_terminal_phase(
        paths.db_path, "T-5B", interval_seconds=1, timeout_seconds=60, _clock=clock,
    )
    assert result.phase == "abandoned"
    assert result.timed_out is False


def test_wait_approve_skips_evidence_ready(tmp_path: Path):
    """When using APPROVE_TERMINAL_PHASES, evidence_ready is NOT terminal."""
    repo = _make_repo(tmp_path)
    repo.create_task(task_id="T-6", repo_path="/r")
    repo.mark_acked(task_id="T-6", session_id="s-1")
    repo.mark_evidence_ready(task_id="T-6")

    paths = _make_paths(tmp_path)
    poll_count = 0

    class TrackingClock2(FakeClock):
        def sleep(self, seconds: float) -> None:
            nonlocal poll_count
            poll_count += 1
            super().sleep(seconds)
            # After 1 poll, simulate the daemon marking committed
            if poll_count == 1:
                repo.mark_committed(task_id="T-6")

    clock = TrackingClock2()
    result = wait_for_terminal_phase(
        paths.db_path, "T-6", interval_seconds=1, timeout_seconds=60,
        terminal_phases=APPROVE_TERMINAL_PHASES, _clock=clock,
    )
    assert result.phase == "committed"
    assert result.timed_out is False
    assert poll_count == 1  # polled once, then saw committed


# ---------------------------------------------------------------------------
# CLI: task wait
# ---------------------------------------------------------------------------


def test_task_wait_exits_0_on_evidence_ready(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    repo = _make_repo(tmp_path)
    repo.create_task(task_id="T-W1", repo_path="/r")
    repo.mark_acked(task_id="T-W1", session_id="test-session")
    repo.mark_evidence_ready(task_id="T-W1")

    result = CliRunner().invoke(app, [
        "task", "wait", "T-W1",
        "--interval-seconds", "0.01",
        "--timeout-seconds", "5",
    ])
    assert result.exit_code == 0
    assert "evidence_ready" in result.stdout


def test_task_wait_json_returns_structured_terminal_payload(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    repo = _make_repo(tmp_path)
    repo.create_task(task_id="T-WJ1", repo_path="/r")
    repo.mark_acked(task_id="T-WJ1", session_id="session-json-wait")
    repo.mark_committed(task_id="T-WJ1")
    JournalRepository(_make_paths(tmp_path).db_path).append(
        "T-WJ1",
        "daemon",
        "committed",
        json.dumps(
            {
                "schema_version": "1",
                "task_id": "T-WJ1",
                "attempt_no": 1,
                "review_round": 0,
                "status": "COMMITTED",
                "summary": "Committed cleanly",
                "payload": {
                    "commit_sha": "abc1234",
                    "branch": "main",
                    "diff_stat": "1 file changed",
                    "changed_files": ["companion-extension/src/services/taskExecutionService.ts"],
                    "validation": ["npm test"],
                    "residual_risks": ["none"],
                },
            }
        ),
    )

    result = CliRunner().invoke(app, ["task", "wait", "T-WJ1", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["task_id"] == "T-WJ1"
    assert payload["phase"] == "committed"
    assert payload["timed_out"] is False
    assert payload["watchdog_triggered"] is False
    assert payload["exit_code"] == 0
    assert payload["task"]["task_id"] == "T-WJ1"
    assert payload["task"]["phase"] == "committed"
    assert payload["task"]["terminal_receipt"]["summary"] == "Committed cleanly"
    assert payload["task"]["terminal_receipt"]["payload"]["commit_sha"] == "abc1234"
    assert payload["committed_result"]["commit_sha"] == "abc1234"
    assert payload["committed_result"]["changed_files"] == ["companion-extension/src/services/taskExecutionService.ts"]
    assert payload["committed_result"]["validation"] == ["npm test"]


def test_task_wait_json_normalizes_committed_result_list_fields(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    repo = _make_repo(tmp_path)
    repo.create_task(task_id="T-WJ2", repo_path="/r")
    repo.mark_acked(task_id="T-WJ2", session_id="session-json-wait-2")
    repo.mark_committed(task_id="T-WJ2")
    JournalRepository(_make_paths(tmp_path).db_path).append(
        "T-WJ2",
        "daemon",
        "committed",
        json.dumps(
            {
                "schema_version": "1",
                "task_id": "T-WJ2",
                "attempt_no": 1,
                "review_round": 0,
                "status": "COMMITTED",
                "summary": "Committed cleanly",
                "payload": {
                    "commit_sha": "abc1234",
                    "changed_files": "companion-extension/src/services/taskExecutionService.ts",
                    "validation": "npm test",
                    "residual_risks": "none",
                },
            }
        ),
    )

    result = CliRunner().invoke(app, ["task", "wait", "T-WJ2", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["committed_result"]["changed_files"] == ["companion-extension/src/services/taskExecutionService.ts"]
    assert payload["committed_result"]["validation"] == ["npm test"]
    assert payload["committed_result"]["residual_risks"] == ["none"]


def test_task_wait_json_ignores_malformed_structured_receipt(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    repo = _make_repo(tmp_path)
    repo.create_task(task_id="T-WJ-MALFORMED", repo_path="/r")
    repo.mark_acked(task_id="T-WJ-MALFORMED", session_id="session-json-wait-malformed")
    repo.mark_committed(task_id="T-WJ-MALFORMED")
    JournalRepository(_make_paths(tmp_path).db_path).append(
        "T-WJ-MALFORMED",
        "daemon",
        "committed",
        json.dumps(
            {
                "schema_version": "1",
                "task_id": "T-WJ-MALFORMED",
                "attempt_no": "BAD",
                "review_round": 0,
                "status": "COMMITTED",
                "summary": "Committed cleanly",
                "payload": {"commit_sha": "abc1234"},
            }
        ),
    )

    result = CliRunner().invoke(app, ["task", "wait", "T-WJ-MALFORMED", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["task"]["terminal_receipt"] is None
    assert payload["committed_result"] is None


def test_task_wait_json_returns_not_found_error(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))

    result = CliRunner().invoke(app, ["task", "wait", "T-W404", "--json"])

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload == {
        "ok": False,
        "error": "task_not_found",
        "task_id": "T-W404",
    }


def test_task_wait_json_includes_failure_context_for_stuck_task(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    repo = _make_repo(tmp_path)
    repo.create_task(task_id="T-W-STUCK", repo_path="/r")
    repo.mark_acked(task_id="T-W-STUCK", session_id="test-session")
    repo.mark_stuck(task_id="T-W-STUCK", reason="no progress before timeout")

    result = CliRunner().invoke(app, ["task", "wait", "T-W-STUCK", "--json"])

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["phase"] == "stuck"
    assert payload["a2a_state_hint"] == "failed"
    assert payload["failure_context"]["blocker_type"] == "executor_runtime_failure"
    assert payload["failure_context"]["recoverable"] is True
    assert payload["failure_context"]["recommended_next_action"] == "retry"
    assert payload["failure_context"]["last_error_excerpt"] == "no progress before timeout"


def test_task_wait_json_maps_auth_blocker_to_auth_required(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    repo = _make_repo(tmp_path)
    repo.create_task(task_id="T-WJ-AUTH", repo_path="/r")
    repo.mark_acked(task_id="T-WJ-AUTH", session_id="session-json-wait-auth")
    repo.mark_blocked(task_id="T-WJ-AUTH", reason="Browser requested human solve")
    JournalRepository(_make_paths(tmp_path).db_path).append(
        "T-WJ-AUTH",
        "daemon",
        "blocked",
        json.dumps(
            {
                "schema_version": "1",
                "task_id": "T-WJ-AUTH",
                "attempt_no": 1,
                "review_round": 0,
                "status": "BLOCKED",
                "summary": "Need human auth",
                "payload": {
                    "blocker_type": "auth",
                },
            }
        ),
    )

    result = CliRunner().invoke(app, ["task", "wait", "T-WJ-AUTH", "--json"])

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["phase"] == "blocked"
    assert payload["a2a_state_hint"] == "auth-required"
    assert payload["failure_context"]["blocker_type"] == "auth"


def test_task_wait_exits_1_on_blocked(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    repo = _make_repo(tmp_path)
    repo.create_task(task_id="T-W2", repo_path="/r")
    repo.mark_acked(task_id="T-W2", session_id="test-session")
    repo.mark_blocked(task_id="T-W2", reason="fail")

    result = CliRunner().invoke(app, [
        "task", "wait", "T-W2",
        "--interval-seconds", "0.01",
        "--timeout-seconds", "5",
    ])
    assert result.exit_code == 1


def test_task_wait_exits_0_on_committed(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    repo = _make_repo(tmp_path)
    repo.create_task(task_id="T-W3", repo_path="/r")
    repo.mark_acked(task_id="T-W3", session_id="test-session")
    repo.mark_committed(task_id="T-W3")

    result = CliRunner().invoke(app, [
        "task", "wait", "T-W3",
        "--interval-seconds", "0.01",
        "--timeout-seconds", "5",
    ])
    assert result.exit_code == 0
    assert "committed" in result.stdout


def test_task_wait_exits_1_on_missing_task(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    ensure_database(_make_paths(tmp_path).db_path)

    result = CliRunner().invoke(app, [
        "task", "wait", "T-MISSING",
        "--interval-seconds", "0.01",
        "--timeout-seconds", "1",
    ])
    assert result.exit_code == 1


def test_task_wait_exits_1_on_abandoned(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    repo = _make_repo(tmp_path)
    repo.create_task(task_id="T-WA", repo_path="/r")
    repo.mark_abandoned(task_id="T-WA", reason="manual cleanup")

    result = CliRunner().invoke(app, [
        "task", "wait", "T-WA",
        "--interval-seconds", "0.01",
        "--timeout-seconds", "5",
    ])
    assert result.exit_code == 1
    assert "abandoned" in result.stdout or "abandoned" in result.stderr


# ---------------------------------------------------------------------------
# CLI: task wait --help
# ---------------------------------------------------------------------------


def test_task_wait_help():
    result = CliRunner().invoke(app, ["task", "wait", "--help"])
    assert result.exit_code == 0
    stdout = click.unstyle(result.stdout)
    assert "--interval-seconds" in stdout
    assert "--timeout-seconds" in stdout


# ---------------------------------------------------------------------------
# CLI: auto-wait on task start (with --no-wait)
# ---------------------------------------------------------------------------


def test_task_start_no_wait_returns_immediately(tmp_path: Path, monkeypatch):
    binary, calls_path, pull_path = write_fake_agent_bus(tmp_path)
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    monkeypatch.setenv("AGPAIR_AGENT_BUS_BIN", binary)
    monkeypatch.setenv("FAKE_AGENT_BUS_CALLS", str(calls_path))
    monkeypatch.setenv("FAKE_AGENT_BUS_PULL", str(pull_path))

    result = CliRunner().invoke(
        app,
        [
            "task", "start",
            "--repo-path", "/tmp/repo",
            "--body", "Goal: test",
            "--task-id", "T-NW1",
            "--no-wait",
        ],
    )
    assert result.exit_code == 0
    assert "T-NW1" in result.stdout
    # Should NOT contain waiting message
    assert "Waiting for" not in result.stdout


def test_task_start_auto_wait_exits_0_when_terminal(tmp_path: Path, monkeypatch):
    """task start with auto-wait: simulate daemon marking evidence_ready."""
    binary, calls_path, pull_path = write_fake_agent_bus(tmp_path)
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    monkeypatch.setenv("AGPAIR_AGENT_BUS_BIN", binary)
    monkeypatch.setenv("FAKE_AGENT_BUS_CALLS", str(calls_path))
    monkeypatch.setenv("FAKE_AGENT_BUS_PULL", str(pull_path))

    # Pre-mark the task as evidence_ready BEFORE the wait loop checks
    # We do this by first creating the task in the DB, then running
    # the command. Since the fake bus succeeds immediately and the task
    # is created by the command itself, we need the task to reach
    # evidence_ready before wait polls.
    #
    # Simplest approach: mark evidence_ready immediately after task creation
    # by patching maybe_auto_wait to call mark first.
    from agpair.storage.tasks import TaskRepository
    from agpair.storage.db import ensure_database as ed
    paths = _make_paths(tmp_path)
    ed(paths.db_path)

    import agpair.cli.task as task_mod

    original_auto_wait = task_mod.maybe_auto_wait

    def patched_auto_wait(db_path, task_id, **kw):
        # Simulate daemon marking evidence_ready before wait polls
        TaskRepository(db_path).mark_acked(task_id=task_id, session_id="test-session")
        TaskRepository(db_path).mark_evidence_ready(task_id=task_id)
        return original_auto_wait(db_path, task_id, **kw)

    monkeypatch.setattr(task_mod, "maybe_auto_wait", patched_auto_wait)

    result = CliRunner().invoke(
        app,
        [
            "task", "start",
            "--repo-path", "/tmp/repo",
            "--body", "Goal: test",
            "--task-id", "T-AW1",
            "--interval-seconds", "0.01",
            "--timeout-seconds", "5",
        ],
    )
    assert result.exit_code == 0
    assert "Waiting for" in result.stdout
    assert "evidence_ready" in result.stdout


# ---------------------------------------------------------------------------
# CLI: auto-wait wired on all semantic commands
# ---------------------------------------------------------------------------


def test_task_help_shows_wait_options():
    """All dispatch commands should show --wait/--no-wait."""
    runner = CliRunner()
    for cmd in ("start", "continue", "approve", "reject", "retry"):
        result = runner.invoke(app, ["task", cmd, "--help"])
        assert result.exit_code == 0, f"{cmd} --help failed"
        stdout = click.unstyle(result.stdout)
        assert "--wait" in stdout, f"{cmd} missing --wait"
        assert "--no-wait" in stdout, f"{cmd} missing --no-wait"
        assert "--interval-seconds" in stdout, f"{cmd} missing --interval-seconds"
        assert "--timeout-seconds" in stdout, f"{cmd} missing --timeout-seconds"


def test_task_help_does_not_show_wait_on_status_and_logs():
    """status and logs should NOT have --wait."""
    runner = CliRunner()
    for cmd in ("status", "logs"):
        result = runner.invoke(app, ["task", cmd, "--help"])
        assert result.exit_code == 0
        assert "--wait" not in click.unstyle(result.stdout)


# ---------------------------------------------------------------------------
# Watchdog-aware wait: unit tests for wait_for_terminal_phase
# ---------------------------------------------------------------------------


def test_plain_acked_still_waits_normally(tmp_path: Path):
    """acked without retry_recommended should continue polling until timeout."""
    repo = _make_repo(tmp_path)
    repo.create_task(task_id="T-WD1", repo_path="/r")
    repo.mark_acked(task_id="T-WD1", session_id="s-1")

    paths = _make_paths(tmp_path)
    clock = FakeClock()
    result = wait_for_terminal_phase(
        paths.db_path, "T-WD1", interval_seconds=5, timeout_seconds=10, _clock=clock,
    )
    # Should time out because acked (not retry_recommended) is NOT terminal
    assert result.timed_out is True
    assert result.phase == "acked"
    assert result.watchdog_triggered is False


def test_acked_plus_retry_recommended_exits_early(tmp_path: Path):
    """acked + retry_recommended=true should exit early as watchdog failure."""
    repo = _make_repo(tmp_path)
    repo.create_task(task_id="T-WD2", repo_path="/r")
    repo.mark_acked(task_id="T-WD2", session_id="s-1")
    repo.recommend_retry(task_id="T-WD2")

    paths = _make_paths(tmp_path)
    clock = FakeClock()
    result = wait_for_terminal_phase(
        paths.db_path, "T-WD2", interval_seconds=5, timeout_seconds=60, _clock=clock,
    )
    # Should NOT time out — should return early with watchdog_triggered
    assert result.timed_out is False
    assert result.phase == "acked"
    assert result.watchdog_triggered is True


def test_acked_becomes_retry_recommended_mid_wait(tmp_path: Path):
    """If retry_recommended is set during polling, wait exits early."""
    repo = _make_repo(tmp_path)
    repo.create_task(task_id="T-WD3", repo_path="/r")
    repo.mark_acked(task_id="T-WD3", session_id="s-1")

    paths = _make_paths(tmp_path)
    poll_count = 0

    class WatchdogClock(FakeClock):
        def sleep(self, seconds: float) -> None:
            nonlocal poll_count
            poll_count += 1
            super().sleep(seconds)
            # Simulate daemon setting retry_recommended after 2 polls
            if poll_count == 2:
                repo.recommend_retry(task_id="T-WD3")

    clock = WatchdogClock()
    result = wait_for_terminal_phase(
        paths.db_path, "T-WD3", interval_seconds=5, timeout_seconds=120, _clock=clock,
    )
    assert result.phase == "acked"
    assert result.watchdog_triggered is True
    assert result.timed_out is False
    assert poll_count == 2


def test_hard_stuck_still_works_after_watchdog_change(tmp_path: Path):
    """Hard stuck transition still produces a terminal result (not watchdog)."""
    repo = _make_repo(tmp_path)
    repo.create_task(task_id="T-WD4", repo_path="/r")
    repo.mark_acked(task_id="T-WD4", session_id="s-1")
    # Mark stuck (hard timeout by daemon)
    repo.mark_stuck(task_id="T-WD4", reason="no progress before timeout")
    repo.recommend_retry(task_id="T-WD4")

    paths = _make_paths(tmp_path)
    clock = FakeClock()
    result = wait_for_terminal_phase(
        paths.db_path, "T-WD4", interval_seconds=5, timeout_seconds=60, _clock=clock,
    )
    # stuck is a terminal phase — watchdog_triggered should be False
    assert result.phase == "stuck"
    assert result.timed_out is False
    assert result.watchdog_triggered is False


def test_approve_ignores_watchdog_on_acked(tmp_path: Path):
    """approve uses APPROVE_TERMINAL_PHASES — acked+retry_recommended still triggers watchdog."""
    repo = _make_repo(tmp_path)
    repo.create_task(task_id="T-WD5", repo_path="/r")
    repo.mark_acked(task_id="T-WD5", session_id="s-1")
    repo.recommend_retry(task_id="T-WD5")

    paths = _make_paths(tmp_path)
    clock = FakeClock()
    result = wait_for_terminal_phase(
        paths.db_path, "T-WD5", interval_seconds=5, timeout_seconds=60,
        terminal_phases=APPROVE_TERMINAL_PHASES, _clock=clock,
    )
    # Even with approve semantics, watchdog fires because acked+retry_recommended
    assert result.phase == "acked"
    assert result.watchdog_triggered is True
    assert result.timed_out is False


# ---------------------------------------------------------------------------
# Watchdog-aware wait: exit_code helpers
# ---------------------------------------------------------------------------


def test_exit_code_for_dispatch_watchdog():
    result = WaitResult(phase="acked", timed_out=False, watchdog_triggered=True)
    assert exit_code_for_dispatch(result) == 1


def test_exit_code_for_approve_watchdog():
    result = WaitResult(phase="acked", timed_out=False, watchdog_triggered=True)
    assert exit_code_for_approve(result) == 1


# ---------------------------------------------------------------------------
# Watchdog-aware wait: WaitResult backwards compat
# ---------------------------------------------------------------------------


def test_wait_result_watchdog_defaults_false():
    """Existing WaitResult usage without watchdog_triggered should still work."""
    result = WaitResult(phase="committed", timed_out=False)
    assert result.watchdog_triggered is False


# ---------------------------------------------------------------------------
# Watchdog-aware wait: CLI integration
# ---------------------------------------------------------------------------


def test_task_wait_exits_1_on_watchdog(tmp_path: Path, monkeypatch):
    """task wait exits 1 with clear message for acked + retry_recommended."""
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    repo = _make_repo(tmp_path)
    repo.create_task(task_id="T-WWD", repo_path="/r")
    repo.mark_acked(task_id="T-WWD", session_id="s-1")
    repo.recommend_retry(task_id="T-WWD")

    result = CliRunner().invoke(app, [
        "task", "wait", "T-WWD",
        "--interval-seconds", "0.01",
        "--timeout-seconds", "5",
    ])
    assert result.exit_code == 1
    # Should contain watchdog-specific messaging
    assert "watchdog" in result.stdout.lower() or "watchdog" in (result.stderr or "").lower()
    assert "retry" in result.stdout.lower() or "retry" in (result.stderr or "").lower()


def test_auto_wait_exits_1_on_watchdog(tmp_path: Path, monkeypatch):
    """Default auto-wait on start also exits 1 for watchdog-marked tasks."""
    binary, calls_path, pull_path = write_fake_agent_bus(tmp_path)
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    monkeypatch.setenv("AGPAIR_AGENT_BUS_BIN", binary)
    monkeypatch.setenv("FAKE_AGENT_BUS_CALLS", str(calls_path))
    monkeypatch.setenv("FAKE_AGENT_BUS_PULL", str(pull_path))

    from agpair.storage.tasks import TaskRepository
    from agpair.storage.db import ensure_database as ed
    paths = _make_paths(tmp_path)
    ed(paths.db_path)

    import agpair.cli.task as task_mod

    original_auto_wait = task_mod.maybe_auto_wait

    def patched_auto_wait(db_path, task_id, **kw):
        # Simulate daemon marking retry_recommended before wait polls
        TaskRepository(db_path).mark_acked(task_id=task_id, session_id="s-auto")
        TaskRepository(db_path).recommend_retry(task_id=task_id)
        return original_auto_wait(db_path, task_id, **kw)

    monkeypatch.setattr(task_mod, "maybe_auto_wait", patched_auto_wait)

    result = CliRunner().invoke(
        app,
        [
            "task", "start",
            "--repo-path", "/tmp/repo",
            "--body", "Goal: test",
            "--task-id", "T-AWD1",
            "--interval-seconds", "0.01",
            "--timeout-seconds", "5",
        ],
    )
    assert result.exit_code == 1
    assert "watchdog" in result.stdout.lower() or "watchdog" in (result.stderr or "").lower()


# ---------------------------------------------------------------------------
# CLI: task watch
# ---------------------------------------------------------------------------


def test_task_watch_exits_1_on_missing_task(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    ensure_database(_make_paths(tmp_path).db_path)

    result = CliRunner().invoke(app, [
        "task", "watch", "T-WATCH-404",
        "--interval-seconds", "0.01",
        "--timeout-seconds", "1",
    ])
    assert result.exit_code == 1
    assert "task not found" in result.stdout or "task not found" in result.stderr


def test_task_watch_json_not_found(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    ensure_database(_make_paths(tmp_path).db_path)

    result = CliRunner().invoke(app, [
        "task", "watch", "T-WATCH-404-JSON", "--json",
        "--interval-seconds", "0.01",
        "--timeout-seconds", "1",
    ])
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["error"] == "task_not_found"


def test_task_watch_terminal_success(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    repo = _make_repo(tmp_path)
    repo.create_task(task_id="T-WATCH-OK", repo_path="/r")
    repo.mark_acked(task_id="T-WATCH-OK", session_id="s-1")
    repo.mark_committed(task_id="T-WATCH-OK")

    result = CliRunner().invoke(app, [
        "task", "watch", "T-WATCH-OK",
        "--interval-seconds", "0.01",
        "--timeout-seconds", "1",
    ])
    assert result.exit_code == 0
    assert "Task T-WATCH-OK phase: committed" in result.stdout


def test_task_watch_terminal_blocked(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    repo = _make_repo(tmp_path)
    repo.create_task(task_id="T-WATCH-BL", repo_path="/r")
    repo.mark_blocked(task_id="T-WATCH-BL", reason="locked")

    result = CliRunner().invoke(app, [
        "task", "watch", "T-WATCH-BL",
        "--interval-seconds", "0.01",
        "--timeout-seconds", "1",
    ])
    assert result.exit_code == 1
    assert "Task T-WATCH-BL phase: blocked" in result.stdout


def test_task_watch_timeout(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    repo = _make_repo(tmp_path)
    repo.create_task(task_id="T-WATCH-TO", repo_path="/r")

    # The command uses real time.sleep, not mock. Set low timeout.
    result = CliRunner().invoke(app, [
        "task", "watch", "T-WATCH-TO",
        "--interval-seconds", "0.01",
        "--timeout-seconds", "0.05",
    ])
    assert result.exit_code == 1
    assert "Timed out after 0.05s" in result.stderr


def test_task_watch_json_emits_ndjson(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    repo = _make_repo(tmp_path)
    repo.create_task(task_id="T-WATCH-NDJSON", repo_path="/r")

    import threading
    import time

    def advance_state():
        time.sleep(0.02)
        repo.mark_acked(task_id="T-WATCH-NDJSON", session_id="s-2")
        time.sleep(0.04)
        repo.mark_committed(task_id="T-WATCH-NDJSON")

    threading.Thread(target=advance_state, daemon=True).start()

    result = CliRunner().invoke(app, [
        "task", "watch", "T-WATCH-NDJSON", "--json",
        "--interval-seconds", "0.01",
        "--timeout-seconds", "1",
    ])

    assert result.exit_code == 0

    lines = [line for line in result.stdout.strip().splitlines() if line]
    assert len(lines) >= 3
    parsed = [json.loads(line) for line in lines]

    events = [item["event_type"] for item in parsed]
    assert "status_update" in events
    assert "terminal" in events

    phases = [item["phase"] for item in parsed]
    assert "new" in phases
    assert "acked" in phases
    assert "committed" in phases


def test_task_watch_deduplicates_output(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    repo = _make_repo(tmp_path)
    repo.create_task(task_id="T-WATCH-DEDUP", repo_path="/r")

    import threading
    import time

    def advance_state():
        time.sleep(0.05)
        # Heartbeat change
        repo.mark_acked(task_id="T-WATCH-DEDUP", session_id="s-del")
        repo.record_heartbeat(task_id="T-WATCH-DEDUP")
        time.sleep(0.05)
        repo.mark_committed(task_id="T-WATCH-DEDUP")

    threading.Thread(target=advance_state, daemon=True).start()

    result = CliRunner().invoke(app, [
        "task", "watch", "T-WATCH-DEDUP",
        "--interval-seconds", "0.02",
        "--timeout-seconds", "1",
    ])
    assert result.exit_code == 0

    # We should see the transitions and no duplicate "phase: acked" outputs
    stdout = result.stdout
    assert "Watching task T-WATCH-DEDUP" in stdout
    # Ensure it only prints "phase: acked" once, even though it loops multiple times while waiting for committed
    assert stdout.count("phase: acked") == 1
    assert stdout.count("Heartbeat:") == 1
