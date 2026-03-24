from __future__ import annotations

from uuid import uuid4
import subprocess

import typer

from agpair.cli.wait import (
    APPROVE_SUCCESS_PHASES,
    APPROVE_TERMINAL_PHASES,
    DEFAULT_INTERVAL_SECONDS,
    DEFAULT_TIMEOUT_SECONDS,
    DISPATCH_SUCCESS_PHASES,
    TERMINAL_PHASES,
    exit_code_for_approve,
    exit_code_for_dispatch,
    maybe_auto_wait,
    wait_for_terminal_phase,
)
from agpair.config import AppPaths
from agpair.runtime_liveness import LivenessState, classify_liveness, is_task_live
from agpair.storage.db import ensure_database
from agpair.storage.journal import JournalRepository
from agpair.storage.tasks import TaskNotFoundError, TaskRepository
from agpair.storage.waiters import WaiterRepository
from agpair.transport.bus import AgentBusClient

app = typer.Typer(no_args_is_help=True)


def _paths() -> AppPaths:
    paths = AppPaths.default()
    ensure_database(paths.db_path)
    return paths


# ---------------------------------------------------------------------------
# Common option factories
# ---------------------------------------------------------------------------

_WAIT_OPTION = typer.Option(True, "--wait/--no-wait", help="Wait for a terminal phase after dispatch (default: on).")
_INTERVAL_OPTION = typer.Option(DEFAULT_INTERVAL_SECONDS, "--interval-seconds", help="Seconds between status polls during wait.")
_TIMEOUT_OPTION = typer.Option(DEFAULT_TIMEOUT_SECONDS, "--timeout-seconds", help="Maximum seconds to wait before timing out.")


# ---------------------------------------------------------------------------
# task start
# ---------------------------------------------------------------------------


@app.command("start")
def start_task(
    repo_path: str = typer.Option(..., "--repo-path"),
    body: str = typer.Option(..., "--body"),
    task_id: str | None = typer.Option(None, "--task-id"),
    wait: bool = _WAIT_OPTION,
    interval_seconds: float = _INTERVAL_OPTION,
    timeout_seconds: float = _TIMEOUT_OPTION,
) -> None:
    paths = _paths()
    bus = AgentBusClient(paths.agent_bus_bin)
    tasks = TaskRepository(paths.db_path)
    journal = JournalRepository(paths.db_path)
    final_task_id = task_id or f"TASK-{uuid4().hex[:12].upper()}"

    tasks.create_task(task_id=final_task_id, repo_path=repo_path)
    journal.append(final_task_id, "cli", "created", body)
    try:
        message_id = bus.send_task(task_id=final_task_id, body=body, repo_path=repo_path)
    except (subprocess.SubprocessError, FileNotFoundError) as exc:
        reason = f"dispatch failed: {exc}"
        journal.append(final_task_id, "cli", "dispatch_failed", reason)
        tasks.mark_blocked(task_id=final_task_id, reason=reason)
        typer.echo(reason, err=True)
        raise typer.Exit(code=1)
    journal.append(final_task_id, "cli", "dispatched", f"sent TASK to agent-bus id={message_id}")
    typer.echo(final_task_id)

    maybe_auto_wait(
        paths.db_path,
        final_task_id,
        wait=wait,
        success_phases=DISPATCH_SUCCESS_PHASES,
        interval_seconds=interval_seconds,
        timeout_seconds=timeout_seconds,
        waiter_command="task_start_auto_wait",
    )


# ---------------------------------------------------------------------------
# task status  (extended with waiter info)
# ---------------------------------------------------------------------------


@app.command("status")
def task_status(task_id: str) -> None:
    paths = _paths()
    tasks = TaskRepository(paths.db_path)
    task = tasks.get_task(task_id)
    if task is None:
        raise typer.Exit(code=1)
    liveness = classify_liveness(task) if task.phase == "acked" else None
    typer.echo(f"task_id: {task.task_id}")
    typer.echo(f"phase: {task.phase}")
    typer.echo(f"repo_path: {task.repo_path}")
    typer.echo(f"session_id: {task.antigravity_session_id}")
    typer.echo(f"attempt_no: {task.attempt_no}")
    typer.echo(f"retry_count: {task.retry_count}")
    typer.echo(f"retry_recommended: {task.retry_recommended}")
    typer.echo(f"stuck_reason: {task.stuck_reason}")
    typer.echo(f"last_heartbeat_at: {task.last_heartbeat_at}")
    typer.echo(f"last_workspace_activity_at: {task.last_workspace_activity_at}")
    if liveness is not None:
        typer.echo(f"liveness_state: {liveness.value}")
    # Waiter state
    waiters = WaiterRepository(paths.db_path)
    waiter = waiters.get_active_waiter(task_id)
    if waiter:
        typer.echo(f"waiter_state: {waiter.state}")
        typer.echo(f"waiter_command: {waiter.command}")
        typer.echo(f"waiter_started_at: {waiter.started_at}")
        typer.echo(f"waiter_last_poll_at: {waiter.last_poll_at}")
    else:
        typer.echo("waiter_state: none")


@app.command("list")
def task_list(
    phase: str | None = typer.Option(None, "--phase", help="Only show tasks in this phase."),
    limit: int = typer.Option(20, "--limit", min=1, help="Maximum number of tasks to print."),
) -> None:
    paths = _paths()
    tasks = TaskRepository(paths.db_path)
    rows = tasks.list_tasks(phase=phase, limit=limit)
    if not rows:
        typer.echo("no tasks found")
        return
    for task in rows:
        typer.echo(
            f"{task.task_id} {task.phase} attempt={task.attempt_no} "
            f"retry={task.retry_count} recommended={task.retry_recommended} repo={task.repo_path}"
        )


# ---------------------------------------------------------------------------
# task active-waits
# ---------------------------------------------------------------------------


@app.command("active-waits")
def active_waits() -> None:
    """List tasks that currently have an active blocking wait."""
    paths = _paths()
    waiters = WaiterRepository(paths.db_path)
    active = waiters.list_active_waiters()
    if not active:
        typer.echo("no active waits")
        return
    for w in active:
        typer.echo(
            f"{w.task_id} waiter={w.waiter_id} command={w.command} "
            f"started_at={w.started_at} last_poll_at={w.last_poll_at}"
        )


# ---------------------------------------------------------------------------
# task logs  (unchanged)
# ---------------------------------------------------------------------------


@app.command("logs")
def task_logs(task_id: str, limit: int = typer.Option(20, "--limit")) -> None:
    paths = _paths()
    tasks = TaskRepository(paths.db_path)
    journal = JournalRepository(paths.db_path)
    if tasks.get_task(task_id) is None:
        raise typer.Exit(code=1)
    for row in journal.tail(task_id, limit=limit):
        typer.echo(f"{row.created_at} [{row.source}] {row.event}: {row.body}")


@app.command("abandon")
def abandon_task(
    task_id: str,
    reason: str = typer.Option("abandoned locally", "--reason"),
    force: bool = typer.Option(False, "--force", help="Bypass liveness and waiter guards."),
) -> None:
    paths = _paths()
    tasks = TaskRepository(paths.db_path)
    journal = JournalRepository(paths.db_path)
    task = tasks.get_task(task_id)
    if task is None:
        typer.echo(f"task not found: {task_id}", err=True)
        raise typer.Exit(code=1)
    _guard_active_waiter(paths, task_id, force=force, command="abandon")
    _guard_live_task(task, force=force, command="abandon")
    tasks.mark_abandoned(task_id=task_id, reason=reason)
    journal.append(task_id, "cli", "abandoned", reason)
    typer.echo(task_id)


# ---------------------------------------------------------------------------
# task wait
# ---------------------------------------------------------------------------


@app.command("wait")
def wait_task(
    task_id: str,
    interval_seconds: float = _INTERVAL_OPTION,
    timeout_seconds: float = _TIMEOUT_OPTION,
) -> None:
    """Wait for a task to reach a terminal phase.

    Terminal phases: evidence_ready, blocked, committed, stuck, abandoned.
    Also exits early if the daemon watchdog flags the task (acked + retry_recommended).

    Exit code 0 for evidence_ready / committed.
    Exit code 1 for blocked / stuck / abandoned / timeout / watchdog.
    """
    paths = _paths()
    tasks = TaskRepository(paths.db_path)
    if tasks.get_task(task_id) is None:
        typer.echo(f"task not found: {task_id}", err=True)
        raise typer.Exit(code=1)

    typer.echo(f"Waiting for task {task_id} to reach a terminal phase …")
    result = wait_for_terminal_phase(
        paths.db_path,
        task_id,
        interval_seconds=interval_seconds,
        timeout_seconds=timeout_seconds,
        waiter_command="task_wait",
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

    typer.echo(f"Task {task_id} reached phase: {result.phase}")
    code = exit_code_for_dispatch(result)
    if code != 0:
        raise typer.Exit(code=code)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _require_task_with_session(tasks: TaskRepository, task_id: str):
    task = tasks.get_task(task_id)
    if task is None or not task.antigravity_session_id:
        raise typer.Exit(code=1)
    return task


def _guard_live_task(task, *, force: bool, command: str) -> None:
    """Block intervention on a live acked task unless --force is passed.

    Only guards acked tasks. Non-acked tasks (terminal phases, new, etc.)
    are allowed through unconditionally.
    """
    if task.phase != "acked":
        return
    if force:
        return
    if is_task_live(task):
        liveness = classify_liveness(task)
        source = liveness.value.replace("active_via_", "").replace("_", " / ")
        typer.echo(
            f"Refused: task {task.task_id} still appears active due to recent {source}. "
            f"Wait longer or pass --force to override.",
            err=True,
        )
        raise typer.Exit(code=1)


def _guard_active_waiter(paths: AppPaths, task_id: str, *, force: bool, command: str) -> None:
    """Block intervention while another wait is in progress unless --force."""
    if force:
        return
    waiters = WaiterRepository(paths.db_path)
    waiter = waiters.get_active_waiter(task_id)
    if waiter:
        typer.echo(
            f"Refused: task {task_id} has an active wait in progress "
            f"(waiter={waiter.waiter_id}, command={waiter.command}, "
            f"started_at={waiter.started_at}). "
            f"Another process is still waiting on this task. "
            f"Pass --force to override.",
            err=True,
        )
        raise typer.Exit(code=1)


def _send_semantic_or_exit(
    *,
    send_fn,
    journal: JournalRepository,
    task_id: str,
    event_ok: str,
    event_fail: str,
    body: str,
) -> int:
    try:
        message_id = send_fn()
    except (subprocess.SubprocessError, FileNotFoundError) as exc:
        reason = f"dispatch failed: {exc}"
        journal.append(task_id, "cli", event_fail, reason)
        typer.echo(reason, err=True)
        raise typer.Exit(code=1)
    journal.append(task_id, "cli", event_ok, f"id={message_id} {body}")
    return message_id


# ---------------------------------------------------------------------------
# task continue
# ---------------------------------------------------------------------------


@app.command("continue")
def continue_task(
    task_id: str,
    body: str = typer.Option(..., "--body"),
    force: bool = typer.Option(False, "--force", help="Bypass liveness and waiter guards."),
    wait: bool = _WAIT_OPTION,
    interval_seconds: float = _INTERVAL_OPTION,
    timeout_seconds: float = _TIMEOUT_OPTION,
) -> None:
    paths = _paths()
    bus = AgentBusClient(paths.agent_bus_bin)
    tasks = TaskRepository(paths.db_path)
    journal = JournalRepository(paths.db_path)
    task = _require_task_with_session(tasks, task_id)
    _guard_active_waiter(paths, task_id, force=force, command="continue")
    _guard_live_task(task, force=force, command="continue")
    _send_semantic_or_exit(
        send_fn=lambda: bus.send_review(task_id=task.task_id, body=body),
        journal=journal,
        task_id=task.task_id,
        event_ok="continued",
        event_fail="continue_failed",
        body=body,
    )
    typer.echo(task.task_id)

    maybe_auto_wait(
        paths.db_path,
        task.task_id,
        wait=wait,
        success_phases=DISPATCH_SUCCESS_PHASES,
        interval_seconds=interval_seconds,
        timeout_seconds=timeout_seconds,
        waiter_command="task_continue_auto_wait",
    )


# ---------------------------------------------------------------------------
# task approve
# ---------------------------------------------------------------------------


@app.command("approve")
def approve_task(
    task_id: str,
    body: str = typer.Option("Approved", "--body"),
    force: bool = typer.Option(False, "--force", help="Bypass waiter guard."),
    wait: bool = _WAIT_OPTION,
    interval_seconds: float = _INTERVAL_OPTION,
    timeout_seconds: float = _TIMEOUT_OPTION,
) -> None:
    paths = _paths()
    bus = AgentBusClient(paths.agent_bus_bin)
    tasks = TaskRepository(paths.db_path)
    journal = JournalRepository(paths.db_path)
    task = _require_task_with_session(tasks, task_id)
    _guard_active_waiter(paths, task_id, force=force, command="approve")
    _send_semantic_or_exit(
        send_fn=lambda: bus.send_approved(task_id=task.task_id, body=body),
        journal=journal,
        task_id=task.task_id,
        event_ok="approved",
        event_fail="approve_failed",
        body=body,
    )
    typer.echo(task.task_id)

    maybe_auto_wait(
        paths.db_path,
        task.task_id,
        wait=wait,
        success_phases=APPROVE_SUCCESS_PHASES,
        terminal_phases=APPROVE_TERMINAL_PHASES,
        interval_seconds=interval_seconds,
        timeout_seconds=timeout_seconds,
        waiter_command="task_approve_auto_wait",
    )


# ---------------------------------------------------------------------------
# task reject
# ---------------------------------------------------------------------------


@app.command("reject")
def reject_task(
    task_id: str,
    body: str = typer.Option(..., "--body"),
    force: bool = typer.Option(False, "--force", help="Bypass waiter guard."),
    wait: bool = _WAIT_OPTION,
    interval_seconds: float = _INTERVAL_OPTION,
    timeout_seconds: float = _TIMEOUT_OPTION,
) -> None:
    paths = _paths()
    bus = AgentBusClient(paths.agent_bus_bin)
    tasks = TaskRepository(paths.db_path)
    journal = JournalRepository(paths.db_path)
    task = _require_task_with_session(tasks, task_id)
    _guard_active_waiter(paths, task_id, force=force, command="reject")
    _send_semantic_or_exit(
        send_fn=lambda: bus.send_review(task_id=task.task_id, body=body),
        journal=journal,
        task_id=task.task_id,
        event_ok="rejected",
        event_fail="reject_failed",
        body=body,
    )
    typer.echo(task.task_id)

    maybe_auto_wait(
        paths.db_path,
        task.task_id,
        wait=wait,
        success_phases=DISPATCH_SUCCESS_PHASES,
        interval_seconds=interval_seconds,
        timeout_seconds=timeout_seconds,
        waiter_command="task_reject_auto_wait",
    )


# ---------------------------------------------------------------------------
# task retry
# ---------------------------------------------------------------------------


@app.command("retry")
def retry_task(
    task_id: str,
    body: str | None = typer.Option(None, "--body"),
    force: bool = typer.Option(False, "--force", help="Bypass liveness and waiter guards."),
    wait: bool = _WAIT_OPTION,
    interval_seconds: float = _INTERVAL_OPTION,
    timeout_seconds: float = _TIMEOUT_OPTION,
) -> None:
    paths = _paths()
    bus = AgentBusClient(paths.agent_bus_bin)
    tasks = TaskRepository(paths.db_path)
    journal = JournalRepository(paths.db_path)
    try:
        task = tasks.prepare_retry(task_id=task_id)
    except TaskNotFoundError:
        raise typer.Exit(code=1)
    _guard_active_waiter(paths, task_id, force=force, command="retry")
    _guard_live_task(task, force=force, command="retry")
    next_attempt = task.attempt_no + 1
    retry_body = body or f"Fresh retry requested for {task.task_id} attempt {next_attempt}"
    try:
        message_id = bus.send_task(task_id=task.task_id, body=retry_body, repo_path=task.repo_path)
    except (subprocess.SubprocessError, FileNotFoundError) as exc:
        reason = f"dispatch failed: {exc}"
        journal.append(task.task_id, "cli", "retry_failed", reason)
        typer.echo(reason, err=True)
        raise typer.Exit(code=1)
    updated = tasks.apply_retry_dispatch(task_id=task.task_id)
    journal.append(updated.task_id, "cli", "retried", f"id={message_id} attempt={updated.attempt_no}")
    typer.echo(task.task_id)

    maybe_auto_wait(
        paths.db_path,
        task.task_id,
        wait=wait,
        success_phases=DISPATCH_SUCCESS_PHASES,
        interval_seconds=interval_seconds,
        timeout_seconds=timeout_seconds,
        waiter_command="task_retry_auto_wait",
    )
