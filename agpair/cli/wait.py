"""Shared wait-for-terminal-phase logic.

This module provides a single implementation path for polling local SQLite task
state until a terminal phase is observed, used by both ``agpair task wait`` and
the default auto-wait behaviour on dispatching / semantic commands.

Waiter state is persisted to the ``waiters`` table so that other processes
(e.g. a fresh AI agent window) can see that a wait is in progress.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

import typer

from agpair.storage.tasks import TaskRepository
from agpair.storage.waiters import WaiterRepository

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Phases treated as terminal by the wait logic (default set).
#: Single source of truth in models.py.
from agpair.models import TERMINAL_PHASES  # noqa: E402

#: Terminal phases for the approve command — evidence_ready is NOT terminal
#: because approve starts from evidence_ready and waits for committed.
APPROVE_TERMINAL_PHASES: frozenset[str] = TERMINAL_PHASES - {"evidence_ready"}

#: Terminal phases considered *successful* for dispatch commands
#: (start / continue / reject / retry).
DISPATCH_SUCCESS_PHASES: frozenset[str] = frozenset(
    {"evidence_ready", "committed"}
)

#: Terminal phases considered *successful* for the approve command.
APPROVE_SUCCESS_PHASES: frozenset[str] = frozenset({"committed"})

#: Terminal phases that always indicate failure.
FAILURE_PHASES: frozenset[str] = frozenset({"blocked", "stuck", "abandoned"})

# Default polling parameters
DEFAULT_INTERVAL_SECONDS: float = 5.0
DEFAULT_TIMEOUT_SECONDS: float = 3600.0  # 60 min — intentionally > daemon stuck timeout (1800s)
DEFAULT_HEARTBEAT_SILENCE_SECONDS: float = 300.0  # 5 min — if no heartbeat for this long, treat as silent


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WaitResult:
    """Outcome of a wait operation."""

    phase: str
    timed_out: bool
    watchdog_triggered: bool = False


# ---------------------------------------------------------------------------
# Core wait function
# ---------------------------------------------------------------------------


def wait_for_terminal_phase(
    db_path: Path,
    task_id: str,
    *,
    interval_seconds: float = DEFAULT_INTERVAL_SECONDS,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    terminal_phases: frozenset[str] = TERMINAL_PHASES,
    heartbeat_silence_seconds: float = DEFAULT_HEARTBEAT_SILENCE_SECONDS,
    waiter_command: str = "task_wait",
    _clock: object | None = None,
    _utcnow: object | None = None,
) -> WaitResult:
    """Poll local task state until a terminal phase is reached or timeout.

    Parameters
    ----------
    db_path:
        Path to the agpair SQLite database.
    task_id:
        The task identifier to watch.
    interval_seconds:
        Seconds between polls.  Must be > 0.
    timeout_seconds:
        Maximum seconds to wait.  0 = check once and return immediately.
    terminal_phases:
        Which phases to treat as terminal.  Defaults to TERMINAL_PHASES.
        Use APPROVE_TERMINAL_PHASES for approve commands.
    heartbeat_silence_seconds:
        If a task has received heartbeats, only consider watchdog triggered
        if the latest heartbeat is older than this many seconds.  Provides
        a bounded failure path so tasks with stale heartbeats still fail.
    waiter_command:
        Label stored in the waiter record to identify the source command.
    _clock:
        Optional injectable clock for testing.  Must support ``time()``
        and ``sleep(n)``.  Defaults to the real ``time`` module.
    _utcnow:
        Optional callable returning current UTC datetime for testing.
        Defaults to ``datetime.now(UTC)``.

    Returns
    -------
    WaitResult
        The final phase and whether the wait timed out.
    """
    from datetime import UTC, datetime

    clock = _clock or time
    utcnow_fn = _utcnow or (lambda: datetime.now(UTC))
    tasks = TaskRepository(db_path)
    waiters = WaiterRepository(db_path)
    deadline = clock.time() + timeout_seconds  # type: ignore[union-attr]

    # --- Register waiter ---------------------------------------------------
    waiter = None
    try:
        waiter = waiters.start_waiter(task_id=task_id, command=waiter_command)
    except sqlite3.IntegrityError:
        # Another active waiter exists — we still poll, just don't persist ours
        pass

    try:
        while True:
            task = tasks.get_task(task_id)
            current_phase = task.phase if task else "unknown"

            if current_phase in terminal_phases:
                if waiter:
                    waiters.finalize(waiter.waiter_id, outcome=f"phase:{current_phase}")
                return WaitResult(phase=current_phase, timed_out=False)

            # Watchdog early-exit: phase is still acked but daemon flagged
            # retry_recommended — fail fast instead of blind-waiting until
            # the hard timeout promotes to stuck.
            #
            # BUT: if we have recent heartbeats OR recent workspace activity,
            # the task is alive — do NOT trigger watchdog. Only trigger if
            # all liveness signals have gone silent.
            if (
                task is not None
                and current_phase == "acked"
                and task.retry_recommended
            ):
                has_fresh_heartbeat = False
                if task.last_heartbeat_at:
                    try:
                        hb_dt = datetime.fromisoformat(task.last_heartbeat_at.replace("Z", "+00:00"))
                        now_dt = utcnow_fn()  # type: ignore[operator]
                        silence = (now_dt - hb_dt).total_seconds()
                        has_fresh_heartbeat = silence < heartbeat_silence_seconds
                    except (ValueError, TypeError):
                        pass

                has_fresh_workspace = False
                if task.last_workspace_activity_at:
                    try:
                        ws_dt = datetime.fromisoformat(task.last_workspace_activity_at.replace("Z", "+00:00"))
                        now_dt = utcnow_fn()  # type: ignore[operator]
                        ws_silence = (now_dt - ws_dt).total_seconds()
                        has_fresh_workspace = ws_silence < heartbeat_silence_seconds
                    except (ValueError, TypeError):
                        pass

                if not has_fresh_heartbeat and not has_fresh_workspace:
                    if waiter:
                        waiters.finalize(waiter.waiter_id, outcome="watchdog")
                    return WaitResult(
                        phase=current_phase,
                        timed_out=False,
                        watchdog_triggered=True,
                    )

            if clock.time() >= deadline:  # type: ignore[union-attr]
                if waiter:
                    waiters.finalize(waiter.waiter_id, outcome="timeout")
                return WaitResult(phase=current_phase, timed_out=True)

            # Update poll timestamp before sleeping
            if waiter:
                waiters.update_poll(waiter.waiter_id)

            clock.sleep(interval_seconds)  # type: ignore[union-attr]
    except BaseException:
        # On any unhandled exception, finalize to avoid orphan waiters
        if waiter:
            try:
                waiters.finalize(waiter.waiter_id, outcome="error")
            except Exception:
                pass
        raise


# ---------------------------------------------------------------------------
# Exit-code helpers
# ---------------------------------------------------------------------------


def exit_code_for_dispatch(result: WaitResult) -> int:
    """Return 0 for success, 1 for failure/timeout/watchdog (dispatch commands)."""
    if result.timed_out or result.watchdog_triggered:
        return 1
    return 0 if result.phase in DISPATCH_SUCCESS_PHASES else 1


def exit_code_for_approve(result: WaitResult) -> int:
    """Return 0 for success, 1 for failure/timeout/watchdog (approve command)."""
    if result.timed_out or result.watchdog_triggered:
        return 1
    return 0 if result.phase in APPROVE_SUCCESS_PHASES else 1


# ---------------------------------------------------------------------------
# Auto-wait helper used by task commands
# ---------------------------------------------------------------------------


def maybe_auto_wait(
    db_path: Path,
    task_id: str,
    *,
    wait: bool,
    success_phases: frozenset[str],
    terminal_phases: frozenset[str] = TERMINAL_PHASES,
    interval_seconds: float = DEFAULT_INTERVAL_SECONDS,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    waiter_command: str = "auto_wait",
) -> None:
    """If *wait* is True, poll until terminal phase and exit accordingly.

    This is the shared entry point that dispatching commands call after
    their dispatch succeeds.  When ``--no-wait`` is passed, *wait* is
    False and this function returns immediately (the command exits 0 as
    it does today).
    """
    if not wait:
        return

    typer.echo(f"Waiting for task {task_id} to reach a terminal phase …")
    result = wait_for_terminal_phase(
        db_path,
        task_id,
        interval_seconds=interval_seconds,
        timeout_seconds=timeout_seconds,
        terminal_phases=terminal_phases,
        waiter_command=waiter_command,
    )

    if result.watchdog_triggered:
        typer.echo(
            f"Watchdog: task {task_id} is still acked but the daemon watchdog "
            f"threshold was reached — retry is recommended.\n"
            f"Run: agpair task retry {task_id}",
            err=True,
        )
        raise typer.Exit(code=1)

    if result.timed_out:
        typer.echo(
            f"Timed out after {timeout_seconds}s — current phase: {result.phase}",
            err=True,
        )
        raise typer.Exit(code=1)

    if result.phase in success_phases:
        typer.echo(f"Task {task_id} reached phase: {result.phase}")
    else:
        typer.echo(
            f"Task {task_id} reached failure phase: {result.phase}",
            err=True,
        )
        raise typer.Exit(code=1)
