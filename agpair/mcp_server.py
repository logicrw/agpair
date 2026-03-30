from __future__ import annotations

import json
import subprocess
import sys
from typing import Any

from mcp.server.fastmcp import FastMCP


mcp = FastMCP("agpair", json_response=True)


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


def _extract_task_id(stdout: str) -> str:
    lines = [line.strip() for line in stdout.splitlines() if line.strip()]
    if len(lines) != 1:
        raise RuntimeError(f"expected single-line task id output, got: {stdout!r}")
    return lines[0]


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
    """Get the current task state as structured JSON."""
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
    repo_path: str,
    body: str,
    task_id: str | None = None,
    idempotency_key: str | None = None,
    wait: bool = False,
    interval_seconds: float = 5.0,
    timeout_seconds: float = 3600.0,
) -> dict[str, Any]:
    """Dispatch a new task via agpair and optionally wait for a terminal phase."""
    args = ["task", "start", "--repo-path", repo_path, "--body", body]
    if task_id:
        args.extend(["--task-id", task_id])
    if idempotency_key:
        args.extend(["--idempotency-key", idempotency_key])
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


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
