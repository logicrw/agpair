from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from agpair.executors.routing import validate_supported_executor


class ProtectedFastMCP(FastMCP):
    """An MCP server that protects its built-in tools from being silently overridden."""
    
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._builtin_tools: set[str] = set()
        self._sealed: bool = False

    def add_tool(self, fn: Any, name: str | None = None, **kwargs: Any) -> None:
        tool_name = name or getattr(fn, "__name__", str(fn))
        if self._sealed:
            if tool_name in self._builtin_tools:
                raise ValueError(f"Cannot override built-in MCP tool: {tool_name}")
        else:
            self._builtin_tools.add(tool_name)
        return super().add_tool(fn, name=name, **kwargs)

    def seal_builtins(self) -> None:
        """Lock the built-in tool registry. Subsequent registrations cannot shadow these."""
        self._sealed = True


mcp = ProtectedFastMCP("agpair", json_response=True)


def _base_command() -> list[str]:
    return [sys.executable, "-m", "agpair.cli.app"]


def _run_cli(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [*_base_command(), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _run_cli_text(args: list[str]) -> str:
    proc = _run_cli(args)
    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip() or f"command exited {proc.returncode}"
        raise RuntimeError(detail)
    return proc.stdout.strip()


def _run_cli_json(args: list[str], *, allow_nonzero: bool = False) -> dict[str, Any]:
    proc = _run_cli(args)
    try:
        payload = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError as exc:
        detail = proc.stderr.strip() or proc.stdout.strip() or f"command exited {proc.returncode}"
        raise RuntimeError(f"invalid JSON response: {detail}") from exc
    if proc.returncode != 0 and not allow_nonzero:
        detail = payload.get("error") if isinstance(payload, dict) else None
        if not isinstance(detail, str) or not detail:
            detail = proc.stderr.strip() or f"command exited {proc.returncode}"
        raise RuntimeError(detail)
    if not isinstance(payload, dict):
        raise RuntimeError("CLI returned a non-object JSON payload")
    return payload


def _validate_repo_path(repo_path: str) -> None:
    path = Path(repo_path)
    if not path.is_absolute():
        raise RuntimeError(f"repo_path must be an absolute path: {repo_path}")
    if not path.is_dir():
        raise RuntimeError(f"repo_path must be an existing directory: {repo_path}")


def _append_repo_locator_args(
    args: list[str],
    *,
    repo_path: str | None,
    target: str | None,
    require_locator: bool,
) -> None:
    if repo_path and target:
        raise RuntimeError("Specify either repo_path or target, not both")
    if repo_path is not None:
        _validate_repo_path(repo_path)
        args.extend(["--repo-path", repo_path])
        return
    if target is not None:
        args.extend(["--target", target])
        return
    if require_locator:
        raise RuntimeError("Either repo_path or target must be provided")


def _append_start_metadata_args(
    args: list[str],
    *,
    executor: str | None,
    controller: str | None,
    completion_policy: str | None,
    allow_self_executor: bool,
    depends_on: list[str] | None,
    isolated_worktree: bool,
    setup_commands: list[str] | None,
    teardown_commands: list[str] | None,
    env_vars: dict[str, str] | None,
    worktree_boundary: str | None,
    spotlight_testing: bool,
) -> None:
    if executor is not None:
        try:
            normalized_executor = validate_supported_executor(executor)
        except ValueError as exc:
            raise RuntimeError(str(exc)) from exc
        args.extend(["--executor", normalized_executor])
    if controller:
        args.extend(["--controller", controller])
    if completion_policy:
        args.extend(["--completion-policy", completion_policy])
    if allow_self_executor:
        args.append("--allow-self-executor")
    if depends_on:
        args.extend(["--depends-on", json.dumps(depends_on, ensure_ascii=False)])
    if isolated_worktree:
        args.append("--isolated-worktree")
    if setup_commands:
        args.extend(["--setup-commands", json.dumps(setup_commands, ensure_ascii=False)])
    if teardown_commands:
        args.extend(["--teardown-commands", json.dumps(teardown_commands, ensure_ascii=False)])
    if env_vars:
        args.extend(["--env-vars", json.dumps(env_vars, ensure_ascii=False, sort_keys=True)])
    if worktree_boundary:
        args.extend(["--worktree-boundary", worktree_boundary])
    if spotlight_testing:
        args.append("--spotlight-testing")



def _workflow_paths():
    from agpair.config import AppPaths
    from agpair.storage.db import ensure_database

    paths = AppPaths.default()
    ensure_database(paths.db_path)
    return paths


def _resolve_workflow_repo_for_mcp(repo_path: str | None) -> str:
    if repo_path:
        _validate_repo_path(repo_path)
        return str(Path(repo_path).expanduser().resolve())
    from agpair.targets import detect_git_root

    detected = detect_git_root()
    if detected:
        return detected
    raise RuntimeError("repo_path is required when no current git root is detectable")

def _extract_task_id(stdout: str) -> str:
    lines = [line.strip() for line in stdout.splitlines() if line.strip()]
    if not lines:
        raise RuntimeError(f"expected single-line task id output, got: {stdout!r}")

    if len(lines) == 1:
        return lines[0]

    task_id = lines[-1]
    # Keep the parser conservative: do not silently accept ambiguous multi-line output.
    if " " in task_id or any(" " not in line for line in lines[:-1]):
        raise RuntimeError(f"expected single-line task id output, got: {stdout!r}")

    return task_id


def _dispatch_then_maybe_wait(
    dispatch_args: list[str],
    *,
    wait: bool,
    interval_seconds: float,
    timeout_seconds: float,
) -> dict[str, Any]:
    task_id = _extract_task_id(_run_cli_text([*dispatch_args, "--no-wait"]))
    if not wait:
        return {
            "ok": True,
            "task_id": task_id,
            "waited": False,
        }
    wait_payload = _run_cli_json(
        [
            "task",
            "wait",
            task_id,
            "--json",
            "--interval-seconds",
            str(interval_seconds),
            "--timeout-seconds",
            str(timeout_seconds),
        ],
        allow_nonzero=True,
    )
    return {
        "ok": bool(wait_payload.get("ok")),
        "task_id": task_id,
        "waited": True,
        "result": wait_payload,
    }


@mcp.tool()
def agpair_get_task(task_id: str) -> dict[str, Any]:
    """Get one task by id. Prefer this over agpair_inspect_repo when you already know the task_id."""
    return _run_cli_json(["task", "status", task_id, "--json"], allow_nonzero=True)


@mcp.tool()
def agpair_wait_task(
    task_id: str,
    interval_seconds: float = 5.0,
    timeout_seconds: float = 3600.0,
) -> dict[str, Any]:
    """Wait for a task to reach a terminal phase and return the JSON result."""
    return _run_cli_json(
        [
            "task",
            "wait",
            task_id,
            "--json",
            "--interval-seconds",
            str(interval_seconds),
            "--timeout-seconds",
            str(timeout_seconds),
        ],
        allow_nonzero=True,
    )


@mcp.tool()
def agpair_get_logs(task_id: str, limit: int = 20, include_executor_output: bool = False) -> dict[str, Any]:
    """Fetch structured task logs, optionally with safe durable executor-output excerpts."""
    args = ["task", "logs", task_id, "--json", "--limit", str(limit)]
    if include_executor_output:
        args.append("--include-executor-output")
    return _run_cli_json(args, allow_nonzero=True)


@mcp.tool()
def agpair_start_task(
    body: str,
    repo_path: str | None = None,
    target: str | None = None,
    task_id: str | None = None,
    idempotency_key: str | None = None,
    executor: str | None = None,
    controller: str | None = None,
    completion_policy: str | None = None,
    allow_self_executor: bool = False,
    depends_on: list[str] | None = None,
    isolated_worktree: bool = False,
    setup_commands: list[str] | None = None,
    teardown_commands: list[str] | None = None,
    env_vars: dict[str, str] | None = None,
    worktree_boundary: str | None = None,
    spotlight_testing: bool = False,
    wait: bool = False,
    interval_seconds: float = 5.0,
    timeout_seconds: float = 3600.0,
) -> dict[str, Any]:
    """Dispatch a new task via agpair and optionally wait for a terminal phase."""
    args = ["task", "start"]
    _append_repo_locator_args(args, repo_path=repo_path, target=target, require_locator=False)
    args.extend(["--body", body])
    if task_id:
        args.extend(["--task-id", task_id])
    if idempotency_key:
        args.extend(["--idempotency-key", idempotency_key])
    _append_start_metadata_args(
        args,
        executor=executor,
        controller=controller,
        completion_policy=completion_policy,
        allow_self_executor=allow_self_executor,
        depends_on=depends_on,
        isolated_worktree=isolated_worktree,
        setup_commands=setup_commands,
        teardown_commands=teardown_commands,
        env_vars=env_vars,
        worktree_boundary=worktree_boundary,
        spotlight_testing=spotlight_testing,
    )
    return _dispatch_then_maybe_wait(
        args,
        wait=wait,
        interval_seconds=interval_seconds,
        timeout_seconds=timeout_seconds,
    )


@mcp.tool()
def agpair_continue_task(
    task_id: str,
    body: str,
    force: bool = False,
    wait: bool = False,
    interval_seconds: float = 5.0,
    timeout_seconds: float = 3600.0,
) -> dict[str, Any]:
    """Send review feedback into an existing task session."""
    args = ["task", "continue", task_id, "--body", body]
    if force:
        args.append("--force")
    return _dispatch_then_maybe_wait(
        args,
        wait=wait,
        interval_seconds=interval_seconds,
        timeout_seconds=timeout_seconds,
    )


@mcp.tool()
def agpair_approve_task(
    task_id: str,
    body: str = "Approved",
    force: bool = False,
    wait: bool = False,
    interval_seconds: float = 5.0,
    timeout_seconds: float = 3600.0,
) -> dict[str, Any]:
    """Approve a task and optionally wait for commit/block."""
    args = ["task", "approve", task_id, "--body", body]
    if force:
        args.append("--force")
    return _dispatch_then_maybe_wait(
        args,
        wait=wait,
        interval_seconds=interval_seconds,
        timeout_seconds=timeout_seconds,
    )


@mcp.tool()
def agpair_retry_task(
    task_id: str,
    body: str | None = None,
    force: bool = False,
    wait: bool = False,
    interval_seconds: float = 5.0,
    timeout_seconds: float = 3600.0,
) -> dict[str, Any]:
    """Request a fresh retry for a task."""
    args = ["task", "retry", task_id]
    if body:
        args.extend(["--body", body])
    if force:
        args.append("--force")
    return _dispatch_then_maybe_wait(
        args,
        wait=wait,
        interval_seconds=interval_seconds,
        timeout_seconds=timeout_seconds,
    )


@mcp.tool()
def agpair_list_tasks(
    repo_path: str | None = None,
    target: str | None = None,
    phase: str | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    """List tasks as structured JSON, optionally filtered by repo or phase."""
    args = ["task", "list", "--json", "--limit", str(limit)]
    _append_repo_locator_args(args, repo_path=repo_path, target=target, require_locator=False)
    if phase:
        args.extend(["--phase", phase])
    return _run_cli_json(args)


@mcp.tool()
def agpair_inspect_repo(
    repo_path: str | None = None,
    target: str | None = None,
    task_id: str | None = None,
) -> dict[str, Any]:
    """Inspect repo-level bridge health plus the most relevant task. Use this when you need repo readiness/context, not just a known task_id."""
    args = ["inspect", "--json"]
    _append_repo_locator_args(args, repo_path=repo_path, target=target, require_locator=False)
    if task_id:
        args.extend(["--task-id", task_id])
    return _run_cli_json(args)


@mcp.tool()
def agpair_doctor(
    repo_path: str | None = None,
    target: str | None = None,
    fresh: bool = False,
) -> dict[str, Any]:
    """Run agpair doctor and return the health report JSON."""
    args = ["doctor"]
    _append_repo_locator_args(args, repo_path=repo_path, target=target, require_locator=False)
    if fresh:
        args.append("--fresh")
    return _run_cli_json(args)


@mcp.tool()
def agpair_start_workflow(
    manifest: dict[str, Any],
    repo_path: str | None = None,
    controller: str | None = None,
    wait: bool = False,
    interval_seconds: float = 2.0,
    timeout_seconds: float = 3600.0,
) -> dict[str, Any]:
    """Start a validated declarative workflow manifest. Script fields are rejected by the shared validator."""
    from time import monotonic, sleep

    from agpair.workflows.schema import validate_manifest
    from agpair.workflows.scheduler import TERMINAL_WORKFLOW_PHASES, WorkflowScheduler
    from agpair.workflows.store import WorkflowRepository
    from agpair.workflows.watch import workflow_event_payload, workflow_status_payload

    paths = _workflow_paths()
    manifest_payload = dict(manifest)
    if controller:
        manifest_payload["controller"] = controller
    effective_repo_path = _resolve_workflow_repo_for_mcp(repo_path or manifest_payload.get("repo_path"))
    parsed = validate_manifest(manifest_payload, require_repo_path=True, repo_path=effective_repo_path)
    workflow_id = WorkflowRepository(paths.db_path).create_workflow(parsed, repo_path=effective_repo_path)
    scheduler = WorkflowScheduler(paths)
    tick_payload = scheduler.tick(workflow_id, repo_path=effective_repo_path)
    status = workflow_status_payload(paths, workflow_id)
    last_watch_event: dict[str, Any] | None = None
    if wait:
        last_watch_event = workflow_event_payload(paths, workflow_id)
        cursor = str(last_watch_event.get("cursor") or "")
        deadline = monotonic() + timeout_seconds
        while last_watch_event.get("phase") not in TERMINAL_WORKFLOW_PHASES and monotonic() < deadline:
            scheduler.tick(workflow_id, repo_path=effective_repo_path)
            last_watch_event = workflow_event_payload(paths, workflow_id, previous_cursor=cursor)
            cursor = str(last_watch_event.get("cursor") or cursor)
            if last_watch_event.get("phase") in TERMINAL_WORKFLOW_PHASES:
                break
            sleep(interval_seconds)
        status = workflow_status_payload(paths, workflow_id)
    status["waited"] = bool(wait)
    if last_watch_event is not None:
        status["last_watch_event"] = last_watch_event
    status["tick"] = tick_payload
    status["status_command"] = f"agpair workflow status {workflow_id} --json"
    status["watch_command"] = f"agpair workflow watch {workflow_id} --json"
    return status


@mcp.tool()
def agpair_get_workflow(workflow_id: str) -> dict[str, Any]:
    """Get workflow status with node states and durable artifact paths."""
    from agpair.workflows.watch import workflow_status_payload

    return workflow_status_payload(_workflow_paths(), workflow_id)


@mcp.tool()
def agpair_watch_workflow(workflow_id: str, cursor: str | None = None) -> dict[str, Any]:
    """Return one low-noise workflow watch event or an unchanged cursor response."""
    from agpair.workflows.watch import workflow_event_payload

    return workflow_event_payload(_workflow_paths(), workflow_id, previous_cursor=cursor)


@mcp.tool()
def agpair_cancel_workflow(workflow_id: str, reason: str = "cancelled") -> dict[str, Any]:
    """Abandon a workflow and active child tasks without deleting task rows or artifacts."""
    from agpair.workflows.control import cancel_workflow

    paths = _workflow_paths()
    return cancel_workflow(paths, workflow_id, reason=reason)


@mcp.tool()
def agpair_retry_workflow_node(
    workflow_id: str,
    node_id: str,
    authorization_profile: str | None = None,
    executor: str | None = None,
    repo_path: str | None = None,
) -> dict[str, Any]:
    """Reset one workflow node for a new child task attempt and run one scheduler tick."""
    from agpair.models import validate_authorization_profile
    from agpair.executors.routing import validate_supported_executor
    from agpair.workflows.scheduler import WorkflowScheduler
    from agpair.workflows.store import WorkflowRepository
    from agpair.workflows.watch import workflow_status_payload

    paths = _workflow_paths()
    repo = WorkflowRepository(paths.db_path)
    workflow = repo.require_workflow(workflow_id)
    if authorization_profile:
        authorization_profile = validate_authorization_profile(authorization_profile)
    if executor:
        executor = validate_supported_executor(executor)
    repo.reset_node_for_retry(
        workflow_id,
        node_id,
        authorization_profile=authorization_profile,
        executor_backend=executor,
        reason="mcp retry workflow node",
    )
    effective_repo_path = _resolve_workflow_repo_for_mcp(repo_path or workflow.repo_path)
    tick_payload = WorkflowScheduler(paths).tick(workflow_id, repo_path=effective_repo_path)
    status = workflow_status_payload(paths, workflow_id)
    status["tick"] = tick_payload
    return status


# Seal the built-in registry so any external extensions or dynamic loads
# cannot shadow these tools.
mcp.seal_builtins()


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
