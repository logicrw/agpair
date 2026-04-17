from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP


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
    depends_on: list[str] | None,
    isolated_worktree: bool,
    setup_commands: list[str] | None,
    teardown_commands: list[str] | None,
    env_vars: dict[str, str] | None,
    worktree_boundary: str | None,
    spotlight_testing: bool,
) -> None:
    if executor is not None:
        allowed = {"antigravity", "codex", "gemini"}
        if executor not in allowed:
            raise RuntimeError(f"executor must be one of {sorted(allowed)}")
        args.extend(["--executor", executor])
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
def agpair_get_logs(task_id: str, limit: int = 20) -> dict[str, Any]:
    """Fetch structured task logs."""
    return _run_cli_json(["task", "logs", task_id, "--json", "--limit", str(limit)], allow_nonzero=True)


@mcp.tool()
def agpair_start_task(
    body: str,
    repo_path: str | None = None,
    target: str | None = None,
    task_id: str | None = None,
    idempotency_key: str | None = None,
    executor: str | None = None,
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
    _append_repo_locator_args(args, repo_path=repo_path, target=target, require_locator=True)
    args.extend(["--body", body])
    if task_id:
        args.extend(["--task-id", task_id])
    if idempotency_key:
        args.extend(["--idempotency-key", idempotency_key])
    _append_start_metadata_args(
        args,
        executor=executor,
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
    _append_repo_locator_args(args, repo_path=repo_path, target=target, require_locator=True)
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


# Seal the built-in registry so any external extensions or dynamic loads
# cannot shadow these tools.
mcp.seal_builtins()


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
