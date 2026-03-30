from __future__ import annotations

from pathlib import Path
from uuid import uuid4
import json
import subprocess
from urllib import error, request

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

_BRIDGE_PORT_MARKER = "bridge_port"
_BRIDGE_AUTH_TOKEN_MARKER = "bridge_auth_token"


def _paths() -> AppPaths:
    paths = AppPaths.default()
    ensure_database(paths.db_path)
    return paths


def _emit_json(payload: dict) -> None:
    typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))


def _not_found_payload(task_id: str) -> dict:
    return {
        "ok": False,
        "error": "task_not_found",
        "task_id": task_id,
    }


def _waiter_payload(waiter) -> dict | None:
    if waiter is None:
        return None
    return {
        "state": waiter.state,
        "command": waiter.command,
        "started_at": waiter.started_at,
        "last_poll_at": waiter.last_poll_at,
    }


def _task_payload(paths: AppPaths, task) -> dict:
    liveness = classify_liveness(task) if task.phase == "acked" else None
    waiters = WaiterRepository(paths.db_path)
    waiter = waiters.get_active_waiter(task.task_id)
    return {
        "task_id": task.task_id,
        "phase": task.phase,
        "repo_path": task.repo_path,
        "session_id": task.antigravity_session_id,
        "attempt_no": task.attempt_no,
        "retry_count": task.retry_count,
        "retry_recommended": task.retry_recommended,
        "stuck_reason": task.stuck_reason,
        "last_heartbeat_at": task.last_heartbeat_at,
        "last_workspace_activity_at": task.last_workspace_activity_at,
        "liveness_state": liveness.value if liveness is not None else None,
        "waiter": _waiter_payload(waiter),
    }


def _journal_row_payload(row) -> dict:
    return {
        "created_at": row.created_at,
        "source": row.source,
        "event": row.event,
        "body": row.body,
        "classification": row.classification,
    }


def _bridge_marker_candidates(
    repo_path: str | None,
    marker_name: str,
    *,
    global_root: Path | None,
) -> list[Path]:
    candidates: list[Path] = []
    if repo_path:
        repo = Path(repo_path).expanduser().resolve()
        candidates.extend([
            repo / ".agpair" / marker_name,
            repo / ".supervisor" / marker_name,
        ])
    if global_root is not None:
        candidates.extend([
            global_root / marker_name,
            global_root.parent / ".supervisor" / marker_name,
        ])
        default_root = (Path.home() / ".agpair").resolve()
        if global_root.resolve() != default_root:
            return candidates
    home = Path.home()
    candidates.extend([
        home / ".agpair" / marker_name,
        home / ".supervisor" / marker_name,
    ])
    return candidates


def _read_bridge_marker(
    repo_path: str | None,
    marker_name: str,
    *,
    global_root: Path | None,
) -> tuple[str | None, Path | None]:
    for marker in _bridge_marker_candidates(repo_path, marker_name, global_root=global_root):
        try:
            raw = marker.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if raw:
            return raw, marker
    return None, None


def _resolve_bridge_port(repo_path: str | None, *, global_root: Path | None) -> tuple[int | None, Path | None, str | None]:
    raw, marker = _read_bridge_marker(repo_path, _BRIDGE_PORT_MARKER, global_root=global_root)
    if raw is None:
        return None, None, "bridge marker not found"
    try:
        return int(raw), marker, None
    except ValueError:
        return None, marker, f"invalid bridge marker value: {raw!r}"


def _fetch_bridge_health(port: int) -> tuple[dict, str | None]:
    url = f"http://127.0.0.1:{port}/health"
    try:
        with request.urlopen(url, timeout=1.5) as response:
            raw = response.read().decode("utf-8")
    except (OSError, error.URLError, TimeoutError) as exc:
        return {}, f"bridge health probe failed: {exc}"
    try:
        payload = json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        return {}, f"bridge health returned invalid JSON: {exc}"
    if not isinstance(payload, dict):
        return {}, "bridge health returned non-object payload"
    return payload, None


def _cancel_bridge_task(*, task_id: str, attempt_no: int, repo_path: str | None, global_root: Path | None) -> tuple[bool, str]:
    port, marker, port_error = _resolve_bridge_port(repo_path, global_root=global_root)
    if port is None:
        return False, port_error or "bridge marker not found"

    auth_required = True
    health_payload, health_error = _fetch_bridge_health(port)
    if health_error is None:
        raw = health_payload.get("bridge_mutating_auth_required")
        if isinstance(raw, bool):
            auth_required = raw

    headers = {"Content-Type": "application/json"}
    if auth_required:
        auth_token, auth_marker = _read_bridge_marker(
            repo_path,
            _BRIDGE_AUTH_TOKEN_MARKER,
            global_root=global_root,
        )
        if auth_token is None:
            marker_path = auth_marker or marker
            location = f" near {marker_path.parent}" if marker_path is not None else ""
            return False, f"bridge auth token marker not found{location}"
        headers["Authorization"] = f"Bearer {auth_token}"

    payload = json.dumps({"task_id": task_id, "attempt_no": attempt_no}).encode("utf-8")
    req = request.Request(
        f"http://127.0.0.1:{port}/cancel_task",
        data=payload,
        headers=headers,
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=1.5) as response:
            raw = response.read().decode("utf-8")
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        detail = body or exc.reason
        return False, f"bridge cancel failed: HTTP {exc.code} {detail}"
    except (OSError, error.URLError, TimeoutError) as exc:
        return False, f"bridge cancel failed: {exc}"
    try:
        result = json.loads(raw or "{}")
    except json.JSONDecodeError:
        result = {}
    if not isinstance(result, dict):
        return False, "bridge cancel returned non-object payload"
    if result.get("ok") is not True:
        return False, f"bridge cancel returned ok=false: {result}"
    return True, "bridge cancel acknowledged"


# ---------------------------------------------------------------------------
# Common option factories
# ---------------------------------------------------------------------------

_WAIT_OPTION = typer.Option(True, "--wait/--no-wait", help="Wait for a terminal phase after dispatch (default: on).")
_INTERVAL_OPTION = typer.Option(DEFAULT_INTERVAL_SECONDS, "--interval-seconds", help="Seconds between status polls during wait.")
_TIMEOUT_OPTION = typer.Option(DEFAULT_TIMEOUT_SECONDS, "--timeout-seconds", help="Maximum seconds to wait before timing out.")
_JSON_OPTION = typer.Option(False, "--json", help="Emit machine-readable JSON.")


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
def task_status(
    task_id: str,
    json_output: bool = _JSON_OPTION,
) -> None:
    paths = _paths()
    tasks = TaskRepository(paths.db_path)
    task = tasks.get_task(task_id)
    if task is None:
        if json_output:
            _emit_json(_not_found_payload(task_id))
        raise typer.Exit(code=1)
    payload = _task_payload(paths, task)
    if json_output:
        _emit_json({"ok": True, **payload})
        return
    typer.echo(f"task_id: {payload['task_id']}")
    typer.echo(f"phase: {payload['phase']}")
    typer.echo(f"repo_path: {payload['repo_path']}")
    typer.echo(f"session_id: {payload['session_id']}")
    typer.echo(f"attempt_no: {payload['attempt_no']}")
    typer.echo(f"retry_count: {payload['retry_count']}")
    typer.echo(f"retry_recommended: {payload['retry_recommended']}")
    typer.echo(f"stuck_reason: {payload['stuck_reason']}")
    typer.echo(f"last_heartbeat_at: {payload['last_heartbeat_at']}")
    typer.echo(f"last_workspace_activity_at: {payload['last_workspace_activity_at']}")
    if payload["liveness_state"] is not None:
        typer.echo(f"liveness_state: {payload['liveness_state']}")
    waiter = payload["waiter"]
    if waiter:
        typer.echo(f"waiter_state: {waiter['state']}")
        typer.echo(f"waiter_command: {waiter['command']}")
        typer.echo(f"waiter_started_at: {waiter['started_at']}")
        typer.echo(f"waiter_last_poll_at: {waiter['last_poll_at']}")
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
def task_logs(
    task_id: str,
    limit: int = typer.Option(20, "--limit"),
    json_output: bool = _JSON_OPTION,
) -> None:
    paths = _paths()
    tasks = TaskRepository(paths.db_path)
    journal = JournalRepository(paths.db_path)
    if tasks.get_task(task_id) is None:
        if json_output:
            _emit_json(_not_found_payload(task_id))
        raise typer.Exit(code=1)
    rows = journal.tail(task_id, limit=limit)
    if json_output:
        _emit_json(
            {
                "ok": True,
                "task_id": task_id,
                "logs": [_journal_row_payload(row) for row in rows],
            }
        )
        return
    for row in rows:
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
    bridge_cancel_attempted = False
    if task.phase == "acked" and task.antigravity_session_id:
        bridge_cancel_attempted = True
        bridge_cancelled, bridge_message = _cancel_bridge_task(
            task_id=task.task_id,
            attempt_no=task.attempt_no,
            repo_path=task.repo_path,
            global_root=paths.root,
        )
        if not bridge_cancelled and "bridge marker not found" not in bridge_message and not force:
            typer.echo(
                f"Refused: failed to release the Antigravity bridge task before abandon. "
                f"{bridge_message}. Pass --force to abandon locally anyway.",
                err=True,
            )
            raise typer.Exit(code=1)
        event = "bridge_cancelled" if bridge_cancelled else "bridge_cancel_skipped"
        journal.append(task_id, "cli", event, bridge_message)
        if not bridge_cancelled and force:
            typer.echo(
                f"warning: bridge task release failed; abandoning locally anyway ({bridge_message})",
                err=True,
            )
    tasks.mark_abandoned(task_id=task_id, reason=reason)
    journal.append(task_id, "cli", "abandoned", reason)
    if not bridge_cancel_attempted:
        journal.append(task_id, "cli", "bridge_cancel_skipped", "task had no live bridge session to release")
    typer.echo(task_id)


# ---------------------------------------------------------------------------
# task wait
# ---------------------------------------------------------------------------


@app.command("wait")
def wait_task(
    task_id: str,
    interval_seconds: float = _INTERVAL_OPTION,
    timeout_seconds: float = _TIMEOUT_OPTION,
    json_output: bool = _JSON_OPTION,
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
        if json_output:
            _emit_json(_not_found_payload(task_id))
            raise typer.Exit(code=1)
        typer.echo(f"task not found: {task_id}", err=True)
        raise typer.Exit(code=1)

    if not json_output:
        typer.echo(f"Waiting for task {task_id} to reach a terminal phase …")
    result = wait_for_terminal_phase(
        paths.db_path,
        task_id,
        interval_seconds=interval_seconds,
        timeout_seconds=timeout_seconds,
        waiter_command="task_wait",
    )

    code = exit_code_for_dispatch(result)
    current_task = tasks.get_task(task_id)
    if json_output:
        _emit_json(
            {
                "ok": code == 0,
                "task_id": task_id,
                "phase": result.phase,
                "timed_out": result.timed_out,
                "watchdog_triggered": result.watchdog_triggered,
                "exit_code": code,
                "task": _task_payload(paths, current_task) if current_task is not None else None,
            }
        )
        if code != 0:
            raise typer.Exit(code=code)
        return

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
    if code != 0:
        raise typer.Exit(code=code)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _require_task_with_session(tasks: TaskRepository, task_id: str):
    task = tasks.get_task(task_id)
    if task is None:
        typer.echo(f"Error: task {task_id!r} not found", err=True)
        raise typer.Exit(code=1)
    if not task.antigravity_session_id:
        typer.echo(f"Error: task {task_id!r} has no Antigravity session (phase={task.phase!r})", err=True)
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
