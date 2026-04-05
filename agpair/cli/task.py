from __future__ import annotations

from pathlib import Path
from uuid import uuid4
import json
import sqlite3
import subprocess
from urllib import error, request

import typer
from agpair.transport.bus import AgentBusClient, BusSendError

import dataclasses

from agpair.cli.wait import (
    DEFAULT_INTERVAL_SECONDS,
    DEFAULT_TIMEOUT_SECONDS,
    DISPATCH_SUCCESS_PHASES,
    TERMINAL_PHASES,
    WaitResult,
    exit_code_for_dispatch,
    is_watchdog_triggered,
    maybe_auto_wait,
    wait_for_terminal_phase,
)
from agpair.config import AppPaths
from agpair.models import a2a_state_hint_from_phase
from agpair.runtime_liveness import LivenessState, classify_liveness, is_task_live
from agpair.terminal_receipts import (
    blocked_failure_context_from_receipt,
    committed_result_from_receipt,
    parse_structured_terminal_receipt,
    structured_receipt_to_dict,
    validate_structured_receipt_dict,
)
from agpair.storage.db import ensure_database
from agpair.storage.journal import JournalRepository
from agpair.storage.tasks import TaskNotFoundError, TaskRepository
from agpair.storage.waiters import WaiterRepository

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


def _structured_receipt_payload(body: str) -> dict | None:
    receipt = parse_structured_terminal_receipt(body)
    if receipt is None:
        return None
    return structured_receipt_to_dict(receipt)


def _parsed_structured_receipt(receipt_payload: dict | None):
    if receipt_payload is None:
        return None
    return validate_structured_receipt_dict(receipt_payload)


def _committed_result_payload(receipt_payload: dict | None) -> dict | None:
    receipt = _parsed_structured_receipt(receipt_payload)
    if receipt is None:
        return None
    return committed_result_from_receipt(receipt)


def _failure_context_payload(task, terminal_receipt: dict | None) -> dict | None:
    receipt = _parsed_structured_receipt(terminal_receipt)
    if receipt is not None:
        failure_context = blocked_failure_context_from_receipt(receipt)
        if failure_context is not None:
            return failure_context
    if task.phase not in {"blocked", "stuck"}:
        return None
    reason = (task.stuck_reason or "").strip()
    if not reason:
        return None
    normalized = reason.lower()
    blocker_type = "unknown"
    recoverable = True
    recommended_next_action = "inspect_logs"
    if task.phase == "stuck" or "timeout" in normalized or "no progress" in normalized:
        blocker_type = "executor_runtime_failure"
        recommended_next_action = "retry"
    elif "dispatch failed" in normalized or "failed to start" in normalized or "failed to send" in normalized:
        blocker_type = "session_transport_failure"
        recommended_next_action = "retry"
    elif "bridge" in normalized:
        blocker_type = "bridge_unavailable"
        recommended_next_action = "fix_environment_then_retry"
    elif "workspace" in normalized or "open the target repo" in normalized:
        blocker_type = "workspace_conflict"
        recommended_next_action = "fix_environment_then_retry"
    elif "validation" in normalized or "lint" in normalized or "typecheck" in normalized or "test" in normalized:
        blocker_type = "validation_failure"
        recoverable = False
        recommended_next_action = "continue"
    elif "executor" in normalized or "session" in normalized:
        blocker_type = "executor_runtime_failure"
        recommended_next_action = "retry"
    return {
        "summary": reason,
        "blocker_type": blocker_type,
        "recoverable": recoverable,
        "recommended_next_action": recommended_next_action,
        "last_error_excerpt": reason,
        "details": {
            "phase": task.phase,
            "reason": reason,
        },
    }


def _latest_terminal_receipt(paths: AppPaths, task_id: str) -> dict | None:
    journal = JournalRepository(paths.db_path)
    for row in journal.tail(task_id, limit=20):
        if row.event not in {"evidence_ready", "blocked", "committed"}:
            continue
        parsed = _structured_receipt_payload(row.body)
        if parsed is not None:
            return parsed
    return None


def build_task_payload(paths: AppPaths, task) -> dict:
    liveness = classify_liveness(task) if task.phase == "acked" else None
    waiters = WaiterRepository(paths.db_path)
    waiter = waiters.get_active_waiter(task.task_id)
    terminal_receipt = _latest_terminal_receipt(paths, task.task_id) if task.phase in TERMINAL_PHASES else None
    committed_result = _committed_result_payload(terminal_receipt)
    failure_context = _failure_context_payload(task, terminal_receipt)
    blocker_type = failure_context["blocker_type"] if failure_context else None
    from agpair.executors import AntigravityExecutor, CodexExecutor, GeminiExecutor
    
    ag_exec = AntigravityExecutor("")
    cx_exec = CodexExecutor()
    gm_exec = GeminiExecutor()

    if task.executor_backend == cx_exec.backend_id:
        active_exec = cx_exec
    elif task.executor_backend == gm_exec.backend_id:
        active_exec = gm_exec
    else:
        active_exec = ag_exec

    return {
        "task_id": task.task_id,
        "active_executor_backend": active_exec.backend_id,
        "active_executor_continuation_capability": active_exec.continuation_capability.value,
        "active_executor_safety_metadata": dataclasses.asdict(active_exec.safety_metadata),
        "supported_backends": {
            ag_exec.backend_id: {
                "continuation_capability": ag_exec.continuation_capability.value,
                "safety_metadata": dataclasses.asdict(ag_exec.safety_metadata),
            },
            cx_exec.backend_id: {
                "continuation_capability": cx_exec.continuation_capability.value,
                "safety_metadata": dataclasses.asdict(cx_exec.safety_metadata),
            },
            gm_exec.backend_id: {
                "continuation_capability": gm_exec.continuation_capability.value,
                "safety_metadata": dataclasses.asdict(gm_exec.safety_metadata),
            },
        },
        "phase": task.phase,
        "a2a_state_hint": a2a_state_hint_from_phase(task.phase, blocker_type=blocker_type),
        "repo_path": task.repo_path,
        "session_id": task.antigravity_session_id,
        "attempt_no": task.attempt_no,
        "retry_count": task.retry_count,
        "retry_recommended": task.retry_recommended,
        "stuck_reason": task.stuck_reason,
        "last_heartbeat_at": task.last_heartbeat_at,
        "last_workspace_activity_at": task.last_workspace_activity_at,
        "depends_on": json.loads(task.depends_on) if task.depends_on else None,
        "isolated_worktree": task.isolated_worktree,
        "setup_commands": json.loads(task.setup_commands) if task.setup_commands else None,
        "teardown_commands": json.loads(task.teardown_commands) if task.teardown_commands else None,
        "env_vars": json.loads(task.env_vars) if task.env_vars else None,
        "worktree_boundary": task.worktree_boundary,
        "spotlight_testing": task.spotlight_testing,
        "completion_policy": task.completion_policy,
        "terminal_source": task.terminal_source,
        "is_approved": task.is_approved,
        "liveness_state": liveness.value if liveness is not None else None,
        "waiter": _waiter_payload(waiter),
        "terminal_receipt": terminal_receipt,
        "committed_result": committed_result,
        "failure_context": failure_context,
    }


def _journal_row_payload(row) -> dict:
    return {
        "created_at": row.created_at,
        "source": row.source,
        "event": row.event,
        "body": row.body,
        "structured_receipt": _structured_receipt_payload(row.body),
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

def _validate_task_body(body: str) -> None:
    trimmed = body.strip()
    if not trimmed:
        typer.echo("Refused: task body is empty.", err=True)
        raise typer.Exit(code=1)

    lower_body = trimmed.lower()
    placeholders = {"bar", "foo", "todo", "fix this", "test"}
    if trimmed.lower() in placeholders or len(trimmed) < 15:
        typer.echo("Refused: task body looks like a trivial placeholder.", err=True)
        raise typer.Exit(code=1)

    required_sections = ["goal", "scope", "required changes", "exit criteria"]
    missing = [s for s in required_sections if s not in lower_body]
    if missing:
        typer.echo(f"Refused: task body is missing key structural sections: {', '.join(missing)}", err=True)
        raise typer.Exit(code=1)



@app.command("start")
def start_task(
    repo_path: str | None = typer.Option(None, "--repo-path"),
    target: str | None = typer.Option(None, "--target", help="Target alias (alternative to --repo-path)."),
    body: str = typer.Option(..., "--body"),
    task_id: str | None = typer.Option(None, "--task-id"),
    idempotency_key: str | None = typer.Option(None, "--idempotency-key"),
    executor: str | None = typer.Option(None, "--executor", help="Executor backend to run the task (antigravity, codex, or gemini)."),
    depends_on: str | None = typer.Option(None, "--depends-on", help="JSON array of task IDs this task depends on."),
    isolated_worktree: bool = typer.Option(False, "--isolated-worktree", help="Whether the task requires a parallel-safe isolated worktree."),
    setup_commands: str | None = typer.Option(None, "--setup-commands", help="JSON array of setup commands to run before the task."),
    teardown_commands: str | None = typer.Option(None, "--teardown-commands", help="JSON array of teardown commands to run after the task."),
    env_vars: str | None = typer.Option(None, "--env-vars", help="JSON object of environment overrides (e.g. PORT) for this task's worktree."),
    worktree_boundary: str | None = typer.Option(None, "--worktree-boundary", help="Declared worktree boundary path/label for this task."),
    spotlight_testing: bool = typer.Option(False, "--spotlight-testing", help="Declare intent to prefer localized/spotlight tests over full-suite runs."),
    completion_policy: str = typer.Option("direct_commit", "--completion-policy", help="Completion policy: direct_commit or review_then_commit."),
    wait: bool = _WAIT_OPTION,
    interval_seconds: float = _INTERVAL_OPTION,
    timeout_seconds: float = _TIMEOUT_OPTION,
) -> None:
    from agpair.executors import AntigravityExecutor, CodexExecutor, GeminiExecutor
    from agpair.targets import resolve_repo_path

    _validate_task_body(body)

    paths = _paths()
    resolved_repo_path = resolve_repo_path(repo_path, target, paths)
    if not resolved_repo_path:
        raise typer.BadParameter("Either --repo-path or --target must be provided.")

    if executor == "codex":
        exec_instance = CodexExecutor()
        backend_to_store = exec_instance.backend_id
    elif executor == "gemini":
        exec_instance = GeminiExecutor()
        backend_to_store = exec_instance.backend_id
    elif executor == "antigravity":
        exec_instance = AntigravityExecutor(paths.agent_bus_bin)
        backend_to_store = exec_instance.backend_id
    elif executor is None:
        exec_instance = AntigravityExecutor(paths.agent_bus_bin)
        backend_to_store = None
    else:
        raise typer.BadParameter("Invalid --executor. Allowed values are 'antigravity', 'codex', or 'gemini'.")

    tasks = TaskRepository(paths.db_path)
    journal = JournalRepository(paths.db_path)
    final_task_id = task_id or f"TASK-{uuid4().hex[:12].upper()}"

    if idempotency_key:
        existing_task = tasks.get_task_by_idempotency_key(
            repo_path=resolved_repo_path,
            client_idempotency_key=idempotency_key,
        )
        if existing_task is not None:
            typer.echo(existing_task.task_id)
            maybe_auto_wait(
                paths.db_path,
                existing_task.task_id,
                wait=wait,
                success_phases=DISPATCH_SUCCESS_PHASES,
                interval_seconds=interval_seconds,
                timeout_seconds=timeout_seconds,
                waiter_command="task_start_auto_wait",
            )
            return
    try:
        tasks.create_task(
            task_id=final_task_id,
            repo_path=resolved_repo_path,
            client_idempotency_key=idempotency_key,
            executor_backend=backend_to_store,
            depends_on=depends_on,
            isolated_worktree=isolated_worktree,
            setup_commands=setup_commands,
            teardown_commands=teardown_commands,
            env_vars=env_vars,
            worktree_boundary=worktree_boundary,
            spotlight_testing=spotlight_testing,
            completion_policy=completion_policy,
        )
    except sqlite3.IntegrityError:
        if not idempotency_key:
            raise
        existing_task = tasks.get_task_by_idempotency_key(
            repo_path=resolved_repo_path,
            client_idempotency_key=idempotency_key,
        )
        if existing_task is None:
            raise
        typer.echo(existing_task.task_id)
        maybe_auto_wait(
            paths.db_path,
            existing_task.task_id,
            wait=wait,
            success_phases=DISPATCH_SUCCESS_PHASES,
            interval_seconds=interval_seconds,
            timeout_seconds=timeout_seconds,
            waiter_command="task_start_auto_wait",
        )
        return
    journal.append(final_task_id, "cli", "created", body)
    try:
        dispatch_result = exec_instance.dispatch(task_id=final_task_id, body=body, repo_path=resolved_repo_path)
    except (subprocess.SubprocessError, FileNotFoundError, BusSendError) as exc:
        reason = f"dispatch failed: {exc}"
        journal.append(final_task_id, "cli", "dispatch_failed", reason)
        tasks.mark_blocked(task_id=final_task_id, reason=reason)
        typer.echo(reason, err=True)
        raise typer.Exit(code=1)

    if dispatch_result.session_id:
        tasks.mark_acked(task_id=final_task_id, session_id=dispatch_result.session_id)
        journal.append(final_task_id, "cli", "dispatched", f"started {executor} exec in {dispatch_result.session_id}")
    elif dispatch_result.message_id:
        journal.append(final_task_id, "cli", "dispatched", f"sent TASK to provider msg={dispatch_result.message_id}")
    else:
        journal.append(final_task_id, "cli", "dispatched", f"dispatched {executor} task")

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
    payload = build_task_payload(paths, task)
    if json_output:
        _emit_json({"ok": True, **payload})
        return
    typer.echo(f"task_id: {payload['task_id']}")
    typer.echo(f"active_executor_backend: {payload['active_executor_backend']}")
    typer.echo(f"active_executor_continuation_capability: {payload['active_executor_continuation_capability']}")
    typer.echo(f"active_executor_safety_metadata: {json.dumps(payload['active_executor_safety_metadata'])}")
    typer.echo(f"supported_backends: {json.dumps(payload['supported_backends'])}")
    typer.echo(f"phase: {payload['phase']}")
    typer.echo(f"a2a_state_hint: {payload['a2a_state_hint']}")
    typer.echo(f"repo_path: {payload['repo_path']}")
    typer.echo(f"session_id: {payload['session_id']}")
    typer.echo(f"attempt_no: {payload['attempt_no']}")
    typer.echo(f"retry_count: {payload['retry_count']}")
    typer.echo(f"retry_recommended: {payload['retry_recommended']}")
    typer.echo(f"stuck_reason: {payload['stuck_reason']}")
    typer.echo(f"last_heartbeat_at: {payload['last_heartbeat_at']}")
    typer.echo(f"last_workspace_activity_at: {payload['last_workspace_activity_at']}")
    typer.echo(f"depends_on: {json.dumps(payload['depends_on'])}")
    typer.echo(f"isolated_worktree: {payload['isolated_worktree']}")
    typer.echo(f"setup_commands: {json.dumps(payload['setup_commands'])}")
    typer.echo(f"teardown_commands: {json.dumps(payload['teardown_commands'])}")
    typer.echo(f"env_vars: {json.dumps(payload['env_vars'])}")
    typer.echo(f"worktree_boundary: {payload['worktree_boundary']}")
    typer.echo(f"spotlight_testing: {payload['spotlight_testing']}")
    typer.echo(f"completion_policy: {payload['completion_policy']}")
    typer.echo(f"terminal_source: {payload['terminal_source']}")
    typer.echo(f"is_approved: {payload['is_approved']}")
    if payload["liveness_state"] is not None:
        typer.echo(f"liveness_state: {payload['liveness_state']}")
    terminal_receipt = payload["terminal_receipt"]
    if terminal_receipt is not None:
        typer.echo(f"terminal_receipt_schema_version: {terminal_receipt['schema_version']}")
        typer.echo(f"terminal_receipt_status: {terminal_receipt['status']}")
        typer.echo(f"terminal_receipt_summary: {terminal_receipt['summary']}")
        typer.echo(
            "terminal_receipt_payload: "
            + json.dumps(terminal_receipt["payload"], ensure_ascii=False, sort_keys=True)
        )
    committed_result = payload["committed_result"]
    if committed_result is not None:
        typer.echo(
            "committed_result: "
            + json.dumps(committed_result, ensure_ascii=False, sort_keys=True)
        )
    failure_context = payload["failure_context"]
    if failure_context is not None:
        typer.echo(
            "failure_context: "
            + json.dumps(failure_context, ensure_ascii=False, sort_keys=True)
        )
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
    all_events: bool = typer.Option(False, "--all", help="Include transient operational noise."),
    json_output: bool = _JSON_OPTION,
) -> None:
    paths = _paths()
    tasks = TaskRepository(paths.db_path)
    journal = JournalRepository(paths.db_path)
    if tasks.get_task(task_id) is None:
        if json_output:
            _emit_json(_not_found_payload(task_id))
        raise typer.Exit(code=1)
    rows = journal.tail(task_id, limit=limit, exclude_noise=not all_events)
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
        structured_receipt = _structured_receipt_payload(row.body)
        if structured_receipt is None:
            typer.echo(f"{row.created_at} [{row.source}] {row.event}: {row.body}")
            continue
        typer.echo(f"{row.created_at} [{row.source}] {row.event}: {structured_receipt['summary']}")
        typer.echo(
            "  payload: "
            + json.dumps(structured_receipt["payload"], ensure_ascii=False, sort_keys=True)
        )


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
        from agpair.executors import get_executor
        exec_instance = get_executor(task.executor_backend)
        if exec_instance and task.executor_backend in {"codex_cli", "gemini_cli"}:
            exec_instance.cancel(task_id=task.task_id, session_id=task.antigravity_session_id)
            journal.append(task_id, "cli", "executor_cancelled", f"{task.executor_backend} cancelled locally")
        else:
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
    if not bridge_cancel_attempted and task.executor_backend not in {"codex_cli", "gemini_cli"}:
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
        task_payload = build_task_payload(paths, current_task) if current_task is not None else None
        failure_context = task_payload["failure_context"] if task_payload is not None else None
        blocker_type = failure_context["blocker_type"] if failure_context else None
        _emit_json(
            {
                "ok": code == 0,
                "task_id": task_id,
                "phase": result.phase,
                "a2a_state_hint": a2a_state_hint_from_phase(result.phase, blocker_type=blocker_type),
                "timed_out": result.timed_out,
                "watchdog_triggered": result.watchdog_triggered,
                "exit_code": code,
                "task": task_payload,
                "committed_result": task_payload["committed_result"] if task_payload is not None else None,
                "failure_context": failure_context,
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
# task watch
# ---------------------------------------------------------------------------


@app.command("watch")
def watch_task(
    task_id: str,
    interval_seconds: float = _INTERVAL_OPTION,
    timeout_seconds: float = _TIMEOUT_OPTION,
    json_output: bool = _JSON_OPTION,
) -> None:
    """Stream task progress until it reaches a terminal phase or times out.

    This command periodically outputs task updates, avoiding spam by only
    emitting when the phase, heartbeat, workspace activity, or terminal receipt changes.
    """
    import time
    from datetime import UTC, datetime

    paths = _paths()
    tasks = TaskRepository(paths.db_path)

    if tasks.get_task(task_id) is None:
        if json_output:
            _emit_json(_not_found_payload(task_id))
        else:
            typer.echo(f"task not found: {task_id}", err=True)
        raise typer.Exit(code=1)

    start_time = time.time()
    deadline = start_time + timeout_seconds
    last_emitted_state = None

    if not json_output:
        typer.echo(f"Watching task {task_id} ...")

    while True:
        task = tasks.get_task(task_id)
        if task is None:
            # Should not happen if it existed above, but handle gracefully
            raise typer.Exit(code=1)

        current_phase = task.phase
        watchdog = is_watchdog_triggered(task)
        timed_out = time.time() >= deadline
        is_terminal = current_phase in TERMINAL_PHASES

        payload = build_task_payload(paths, task)

        # state tuple for deduplication:
        # (phase, heartbeat, workspace_activity, stringified_terminal_receipt)
        # Note: terminal_receipt might not be json-serializable if not a dict,
        # but build_task_payload ensures it is parsed dict or None.
        current_state = (
            payload["phase"],
            payload["last_heartbeat_at"],
            payload["last_workspace_activity_at"],
            json.dumps(payload.get("terminal_receipt"), sort_keys=True) if payload.get("terminal_receipt") else None,
        )

        changed = current_state != last_emitted_state

        if changed or watchdog or timed_out or is_terminal:
            event_type = "status_update"
            if watchdog:
                event_type = "watchdog"
            elif timed_out:
                event_type = "timeout"
            elif is_terminal:
                event_type = "terminal"

            if json_output:
                typer.echo(json.dumps({
                    "event_type": event_type,
                    "task_id": task_id,
                    "phase": current_phase,
                    "timestamp": datetime.now(UTC).isoformat(),
                    "payload": payload,
                }, ensure_ascii=False))
            else:
                if changed:
                    ts = datetime.now().strftime("%H:%M:%S")
                    prev_phase = last_emitted_state[0] if last_emitted_state else None
                    if current_phase != prev_phase:
                        typer.echo(f"[{ts}] Task {task_id} phase: {current_phase}")

                    prev_hb = last_emitted_state[1] if last_emitted_state else None
                    if payload["last_heartbeat_at"] != prev_hb and payload["last_heartbeat_at"]:
                        typer.echo(f"  -> Heartbeat: {payload['last_heartbeat_at']}")

                    prev_ws = last_emitted_state[2] if last_emitted_state else None
                    if payload["last_workspace_activity_at"] != prev_ws and payload["last_workspace_activity_at"]:
                        typer.echo(f"  -> Workspace activity: {payload['last_workspace_activity_at']}")

                    prev_rec = last_emitted_state[3] if last_emitted_state else None
                    if current_state[3] != prev_rec and payload.get("terminal_receipt"):
                        summary = payload["terminal_receipt"].get("summary", "No summary")
                        typer.echo(f"  -> Terminal receipt received: {summary}")

                if watchdog:
                    typer.echo(
                        f"Watchdog: task {task_id} is flagged for retry and silent.\n"
                        f"Run: agpair task retry {task_id}",
                        err=True,
                    )
                elif timed_out:
                    typer.echo(f"Timed out after {timeout_seconds}s.", err=True)

            last_emitted_state = current_state

            if is_terminal:
                code = exit_code_for_dispatch(WaitResult(phase=current_phase, timed_out=False))
                raise typer.Exit(code=code)

            if watchdog or timed_out:
                raise typer.Exit(code=1)

        time.sleep(interval_seconds)


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
    except (subprocess.SubprocessError, FileNotFoundError, BusSendError) as exc:
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
