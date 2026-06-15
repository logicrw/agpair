"""Tests for ``agpair task wait`` and default auto-wait behaviour."""
from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
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
    _adaptive_poll_interval_seconds,
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


def _write_fake_antigravity_cli(tmp_path: Path) -> Path:
    bin_path = tmp_path / "fake-antigravity"
    bin_path.write_text("#!/usr/bin/env sh\nprintf '{}\\n'\n", encoding="utf-8")
    bin_path.chmod(0o755)
    return bin_path


def _make_execution_repo(tmp_path: Path, monkeypatch) -> Path:
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    monkeypatch.setenv("AGPAIR_ANTIGRAVITY_CLI", str(_write_fake_antigravity_cli(tmp_path)))
    return repo_path


class FakeClock:
    """Injectable clock that advances time on each ``sleep()`` call."""

    def __init__(self, start: float = 0.0):
        self._now = start
        self.sleeps: list[float] = []

    def time(self) -> float:
        return self._now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self._now += seconds


# ---------------------------------------------------------------------------
# Unit: TERMINAL_PHASES constants
# ---------------------------------------------------------------------------


def test_terminal_phases_contain_required_values():
    assert TERMINAL_PHASES == {"ready_for_review", "evidence_ready", "blocked", "committed", "stuck", "abandoned"}


def test_dispatch_success_phases():
    assert DISPATCH_SUCCESS_PHASES == {"ready_for_review", "evidence_ready", "committed"}


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


def test_wait_uses_fast_polling_during_initial_window(tmp_path: Path):
    repo = _make_repo(tmp_path)
    repo.create_task(task_id="T-FAST-POLL", repo_path="/r")
    repo.mark_acked(task_id="T-FAST-POLL", session_id="s-fast")

    paths = _make_paths(tmp_path)
    poll_count = 0

    class TrackingClock(FakeClock):
        def sleep(self, seconds: float) -> None:
            nonlocal poll_count
            poll_count += 1
            super().sleep(seconds)
            if poll_count == 3:
                repo.mark_committed(task_id="T-FAST-POLL")

    clock = TrackingClock()
    result = wait_for_terminal_phase(
        paths.db_path, "T-FAST-POLL", interval_seconds=5, timeout_seconds=60, _clock=clock,
    )

    assert result.phase == "committed"
    assert clock.sleeps == [1.0, 1.0, 1.0]
    assert clock.time() == 3.0


def test_wait_does_not_oversleep_timeout_deadline(tmp_path: Path):
    repo = _make_repo(tmp_path)
    repo.create_task(task_id="T-NO-OVERSLEEP", repo_path="/r")
    repo.mark_acked(task_id="T-NO-OVERSLEEP", session_id="s-slow")

    clock = FakeClock()
    result = wait_for_terminal_phase(
        _make_paths(tmp_path).db_path,
        "T-NO-OVERSLEEP",
        interval_seconds=5,
        timeout_seconds=2.5,
        _clock=clock,
    )

    assert result.timed_out is True
    assert clock.sleeps == [1.0, 1.0, 0.5]
    assert clock.time() == 2.5


def test_adaptive_polling_respects_explicit_fast_interval() -> None:
    assert _adaptive_poll_interval_seconds(
        elapsed_seconds=0,
        requested_interval_seconds=0.5,
        remaining_seconds=60,
    ) == 0.5


def test_adaptive_polling_uses_requested_interval_after_fast_window() -> None:
    assert _adaptive_poll_interval_seconds(
        elapsed_seconds=90,
        requested_interval_seconds=5,
        remaining_seconds=60,
    ) == 5


def test_adaptive_polling_caps_sleep_to_remaining_deadline() -> None:
    assert _adaptive_poll_interval_seconds(
        elapsed_seconds=0,
        requested_interval_seconds=5,
        remaining_seconds=0.25,
    ) == 0.25


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


def test_wait_lease_expiry_is_success_for_background_task(tmp_path: Path):
    repo = _make_repo(tmp_path)
    repo.create_task(
        task_id="T-LEASE",
        repo_path="/r",
        wait_policy="lease",
        controller_wait_seconds=10,
        execution_budget_seconds=3600,
        background_ok=True,
    )
    repo.mark_acked(task_id="T-LEASE", session_id="s-1")

    result = wait_for_terminal_phase(
        _make_paths(tmp_path).db_path,
        "T-LEASE",
        interval_seconds=5,
        timeout_seconds=60,
        controller_wait_seconds=10,
        background_ok=True,
        strict_watchdog=False,
        _clock=FakeClock(),
    )

    assert result.phase == "acked"
    assert result.outcome == "controller_lease_expired"
    assert result.controller_lease_expired is True
    assert result.recommended_action == "detach_and_continue"
    assert exit_code_for_dispatch(result) == 0


def test_wait_soft_no_progress_is_success_for_background_task(tmp_path: Path):
    repo = _make_repo(tmp_path)
    repo.create_task(
        task_id="T-SOFT-NO-PROGRESS",
        repo_path="/r",
        wait_policy="lease",
        controller_wait_seconds=300,
        execution_budget_seconds=3600,
        background_ok=True,
    )
    repo.mark_acked(task_id="T-SOFT-NO-PROGRESS", session_id="s-1")
    old_time = datetime(2026, 3, 24, 11, 44, tzinfo=UTC).isoformat()
    with sqlite3.connect(_make_paths(tmp_path).db_path) as conn:
        conn.execute(
            "UPDATE tasks SET last_activity_at=?, updated_at=? WHERE task_id=?",
            (old_time, old_time, "T-SOFT-NO-PROGRESS"),
        )
        conn.commit()

    result = wait_for_terminal_phase(
        _make_paths(tmp_path).db_path,
        "T-SOFT-NO-PROGRESS",
        interval_seconds=5,
        timeout_seconds=60,
        controller_wait_seconds=300,
        background_ok=True,
        strict_watchdog=False,
        heartbeat_silence_seconds=300,
        _clock=FakeClock(),
        _utcnow=lambda: datetime(2026, 3, 24, 12, 0, tzinfo=UTC),
    )

    assert result.outcome == "soft_no_progress"
    assert result.watchdog_triggered is True
    assert result.recommended_action == "inspect_logs_or_continue_background"
    assert exit_code_for_dispatch(result) == 0


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
    repo.update_attempt_adoption(
        task_id="T-WJ1",
        attempt_no=1,
        protocol_warnings_json="[]",
        protocol_errors_json="[]",
        adoptable_result="yes",
        adoption_evidence_json=json.dumps(
            {
                "adoptable_result": "yes",
                "blockers": [],
                "warnings": [],
                "evidence": {"has_commit": True},
                "controller_rework": "none",
                "agent_result": {
                    "state": "usable",
                    "controller_action": "review_then_apply",
                    "summary": "ready",
                    "hard_blockers": [],
                    "soft_warnings": [],
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
    assert payload["agent_result"]["state"] == "usable"
    assert payload["recommended_action"] == "review_then_apply"
    assert payload["status_command"] == "agpair task status T-WJ1 --json"
    assert "agpair task apply T-WJ1 --check" in payload["evidence_commands"]
    assert payload["task"]["task_id"] == "T-WJ1"
    assert payload["task"]["phase"] == "committed"
    assert payload["task"]["controller_action"] == "review_then_apply"
    assert payload["task"]["terminal_receipt"]["summary"] == "Committed cleanly"
    assert payload["task"]["terminal_receipt"]["payload"]["commit_sha"] == "abc1234"
    assert payload["committed_result"]["commit_sha"] == "abc1234"
    assert payload["committed_result"]["changed_files"] == ["companion-extension/src/services/taskExecutionService.ts"]
    assert payload["committed_result"]["validation"] == ["npm test"]


def test_task_wait_json_reports_lease_outcome(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    repo = _make_repo(tmp_path)
    repo.create_task(
        task_id="T-WJ-LEASE",
        repo_path="/r",
        wait_policy="lease",
        controller_wait_seconds=0,
        execution_budget_seconds=3600,
        background_ok=True,
    )
    repo.mark_acked(task_id="T-WJ-LEASE", session_id="session-json-lease")

    result = CliRunner().invoke(app, ["task", "wait", "T-WJ-LEASE", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["phase"] == "acked"
    assert payload["outcome"] == "controller_lease_expired"
    assert payload["controller_lease_expired"] is True
    assert payload["recommended_action"] == "detach_and_continue"
    assert payload["background_ok"] is True
    assert payload["task"]["controller_action"] in {"detach_and_continue", "inspect_logs_or_continue_background"}


def test_task_wait_json_uses_same_recovery_decision_for_soft_no_progress(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    repo_path = _make_execution_repo(tmp_path, monkeypatch)
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "task",
            "start",
            "--repo-path",
            str(repo_path),
            "--controller",
            "codex",
            "--executor",
            "antigravity-cli",
            "--task-id",
            "TASK-WAIT-RECOVERY",
            "--task-kind",
            "quick_review",
            "--wait-policy",
            "lease",
            "--controller-wait-seconds",
            "0",
            "--authorization-profile",
            "local_readonly",
            "--completion-policy",
            "report",
            "--no-wait",
            "--body",
            "Goal: Summarize the repo.\nScope: repo only.\nRequired changes: none.\nExit criteria: report findings.",
        ],
    )
    assert result.exit_code == 0, result.output

    wait = runner.invoke(
        app,
        ["task", "wait", "TASK-WAIT-RECOVERY", "--json", "--timeout-seconds", "1"],
    )

    assert wait.exit_code == 0, wait.output
    payload = json.loads(wait.stdout)
    assert payload["recommended_action"] in {"detach_and_continue", "wait_background"}
    assert payload["recovery_decision"]["action"] == "wait_background"


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
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    repo_path = _make_execution_repo(tmp_path, monkeypatch)

    result = CliRunner().invoke(
        app,
        [
            "task", "start",
            "--repo-path", str(repo_path),
            "--body", "Goal: test\nScope: test\nRequired changes: test\nExit criteria: test",
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
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    repo_path = _make_execution_repo(tmp_path, monkeypatch)

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
        repo = TaskRepository(db_path)
        task = repo.get_task(task_id)
        if task is not None and task.phase == "new":
            repo.mark_acked(task_id=task_id, session_id="test-session")
        repo.mark_evidence_ready(task_id=task_id)
        return original_auto_wait(db_path, task_id, **kw)

    monkeypatch.setattr(task_mod, "maybe_auto_wait", patched_auto_wait)

    result = CliRunner().invoke(
        app,
        [
            "task", "start",
            "--repo-path", str(repo_path),
            "--body", "Goal: test\nScope: test\nRequired changes: test\nExit criteria: test",
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
    for cmd in ("start", "retry"):
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


def test_plain_acked_no_progress_exits_before_hard_timeout(tmp_path: Path):
    """acked without retry_recommended should still watchdog when all progress signals are stale."""
    repo = _make_repo(tmp_path)
    repo.create_task(task_id="T-WD-NOPROGRESS", repo_path="/r")
    repo.mark_acked(task_id="T-WD-NOPROGRESS", session_id="s-1")

    old_time = datetime(2026, 3, 24, 11, 44, tzinfo=UTC).isoformat()
    with sqlite3.connect(_make_paths(tmp_path).db_path) as conn:
        conn.execute(
            "UPDATE tasks SET last_activity_at=?, updated_at=? WHERE task_id=?",
            (old_time, old_time, "T-WD-NOPROGRESS"),
        )
        conn.commit()

    result = wait_for_terminal_phase(
        _make_paths(tmp_path).db_path,
        "T-WD-NOPROGRESS",
        interval_seconds=5,
        timeout_seconds=60,
        heartbeat_silence_seconds=300,
        _clock=FakeClock(),
        _utcnow=lambda: datetime(2026, 3, 24, 12, 0, tzinfo=UTC),
    )

    assert result.timed_out is False
    assert result.phase == "acked"
    assert result.watchdog_triggered is True


def test_report_only_task_uses_shorter_no_progress_window(tmp_path: Path):
    """Report-only/local_readonly tasks should not wait for the generic 300s silence window."""
    repo = _make_repo(tmp_path)
    repo.create_task(
        task_id="T-WD-REPORT-NOPROGRESS",
        repo_path="/r",
        authorization_profile="local_readonly",
        completion_policy="report",
    )
    repo.mark_acked(task_id="T-WD-REPORT-NOPROGRESS", session_id="s-report")

    started_at = datetime(2026, 3, 24, 11, 56, tzinfo=UTC).isoformat()
    with sqlite3.connect(_make_paths(tmp_path).db_path) as conn:
        conn.execute(
            "UPDATE tasks SET last_activity_at=?, updated_at=? WHERE task_id=?",
            (started_at, started_at, "T-WD-REPORT-NOPROGRESS"),
        )
        conn.commit()

    result = wait_for_terminal_phase(
        _make_paths(tmp_path).db_path,
        "T-WD-REPORT-NOPROGRESS",
        interval_seconds=5,
        timeout_seconds=600,
        heartbeat_silence_seconds=300,
        _clock=FakeClock(),
        _utcnow=lambda: datetime(2026, 3, 24, 12, 0, tzinfo=UTC),
    )

    assert result.timed_out is False
    assert result.phase == "acked"
    assert result.watchdog_triggered is True


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
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    repo_path = _make_execution_repo(tmp_path, monkeypatch)

    from agpair.storage.tasks import TaskRepository
    from agpair.storage.db import ensure_database as ed
    paths = _make_paths(tmp_path)
    ed(paths.db_path)

    import agpair.cli.task as task_mod

    original_auto_wait = task_mod.maybe_auto_wait

    def patched_auto_wait(db_path, task_id, **kw):
        # Simulate daemon marking retry_recommended before wait polls
        repo = TaskRepository(db_path)
        task = repo.get_task(task_id)
        if task is not None and task.phase == "new":
            repo.mark_acked(task_id=task_id, session_id="s-auto")
        repo.recommend_retry(task_id=task_id)
        return original_auto_wait(db_path, task_id, **kw)

    monkeypatch.setattr(task_mod, "maybe_auto_wait", patched_auto_wait)

    result = CliRunner().invoke(
        app,
        [
            "task", "start",
            "--repo-path", str(repo_path),
            "--body", "Goal: test\nScope: test\nRequired changes: test\nExit criteria: test",
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
    assert lines
    parsed = [json.loads(line) for line in lines]

    events = [item["event_type"] for item in parsed]
    assert "terminal" in events
    assert "agent_result_changed" in [item["event"] for item in parsed]

    phases = [item["phase"] for item in parsed]
    assert "committed" in phases


def test_task_watch_json_emits_live_attempt_artifact_metadata(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    repo = _make_repo(tmp_path)
    repo.create_task(task_id="T-WATCH-LIVE", repo_path="/r")
    session_dir = tmp_path / "watch-live-session"
    session_dir.mkdir()
    stdout_path = session_dir / "stdout.log"
    stdout_path.write_text("watch live output\n", encoding="utf-8")
    repo.mark_acked(task_id="T-WATCH-LIVE", session_id=str(session_dir))

    def noop_inline_poll(*args, **kwargs):
        return None

    monkeypatch.setattr("agpair.cli.wait._try_inline_poll", noop_inline_poll)

    result = CliRunner().invoke(app, [
        "task", "watch", "T-WATCH-LIVE", "--json",
        "--interval-seconds", "0.01",
        "--timeout-seconds", "0.05",
    ])

    assert result.exit_code == 1
    parsed = [json.loads(line) for line in result.stdout.strip().splitlines() if line]
    live_events = [item for item in parsed if item["phase"] == "acked"]
    assert live_events
    live_event = live_events[0]
    assert live_event["event_type"] == "artifact_progress"
    assert live_event["stdout_path"] == str(stdout_path)
    assert live_event["last_executor_output_at"] is not None
    assert live_event["stdout_size"] > 0
    assert live_event["useful_progress"] is True
    assert live_event["last_output_excerpt"] == "watch live output"
    assert live_event["active_attempt_artifacts"]["stdout"]["path"] == str(stdout_path)
    assert live_event["active_attempt_artifacts"]["stdout"]["excerpt"] == "watch live output"


def test_task_watch_json_marks_bootstrap_stderr_as_not_useful_progress(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    repo = _make_repo(tmp_path)
    repo.create_task(task_id="T-WATCH-NOISE", repo_path="/r")
    session_dir = tmp_path / "watch-noise-session"
    session_dir.mkdir()
    stderr_path = session_dir / "stderr.log"
    stderr_path.write_text(
        (Path("tests/fixtures/terminal_receipts/bootstrap_noise_only.stderr")).read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    repo.mark_acked(task_id="T-WATCH-NOISE", session_id=str(session_dir))

    def noop_inline_poll(*args, **kwargs):
        return None

    monkeypatch.setattr("agpair.cli.wait._try_inline_poll", noop_inline_poll)

    result = CliRunner().invoke(app, [
        "task", "watch", "T-WATCH-NOISE", "--json",
        "--interval-seconds", "0.01",
        "--timeout-seconds", "0.05",
    ])

    assert result.exit_code == 1
    parsed = [json.loads(line) for line in result.stdout.strip().splitlines() if line]
    live_event = next(item for item in parsed if item["phase"] == "acked")
    assert live_event["event_type"] == "artifact_progress"
    assert live_event["stderr_path"] == str(stderr_path)
    assert live_event["stderr_size"] > 0
    assert live_event["useful_progress"] is False
    assert "plugin manifest" in live_event["last_output_excerpt"]


def test_task_watch_json_emits_environment_metadata(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    repo = _make_repo(tmp_path)
    repo.create_task(
        task_id="T-WATCH-ENV",
        repo_path="/r",
        executor_backend="grok-cli",
    )

    result = CliRunner().invoke(app, [
        "task", "watch", "T-WATCH-ENV", "--json",
        "--interval-seconds", "0.01",
        "--timeout-seconds", "0.05",
    ])

    assert result.exit_code == 1
    parsed = [json.loads(line) for line in result.stdout.strip().splitlines() if line]
    assert parsed
    event = parsed[0]
    assert event["environment_mode"] == "managed-natural"
    assert event["environment_mode_source"] == "executor_default"
    assert event["skill_policy"] == "inherit"
    assert event["mcp_policy"] == "inherit"
    assert event["payload"]["environment_mode"] == "managed-natural"


def test_task_watch_deduplicates_output(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    repo = _make_repo(tmp_path)
    repo.create_task(task_id="T-WATCH-DEDUP", repo_path="/r")
    repo.mark_acked(task_id="T-WATCH-DEDUP", session_id="s-del")
    repo.record_heartbeat(task_id="T-WATCH-DEDUP")

    import threading
    import time

    def advance_state():
        time.sleep(0.4)
        repo.mark_committed(task_id="T-WATCH-DEDUP")

    threading.Thread(target=advance_state, daemon=True).start()

    result = CliRunner().invoke(app, [
        "task", "watch", "T-WATCH-DEDUP",
            "--interval-seconds", "0.02",
            "--timeout-seconds", "2",
        ])
    assert result.exit_code == 0

    stdout = result.stdout
    assert "Watching task T-WATCH-DEDUP" in stdout
    assert stdout.count("phase: acked") == 1
    assert stdout.count("phase: committed") == 1
    assert stdout.count("Heartbeat:") == 1
