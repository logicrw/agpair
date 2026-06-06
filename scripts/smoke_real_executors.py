#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import signal
import shutil
import subprocess
import sys
import time
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agpair.executors.policy import executor_health_snapshot, resolve_controller_policy
from agpair.executors.registry import registered_executor_ids


TERMINAL_OK_PHASES = {"ready_for_review", "evidence_ready", "committed"}
TERMINAL_FAILURE_PHASES = {"blocked", "stuck", "abandoned"}
TERMINAL_PHASES = TERMINAL_OK_PHASES | TERMINAL_FAILURE_PHASES


def _run(cmd: list[str], *, cwd: str | Path | None = None, timeout: float | None = None) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    existing_pythonpath = env.get("PYTHONPATH")
    env["PYTHONPATH"] = str(PROJECT_ROOT) + (os.pathsep + existing_pythonpath if existing_pythonpath else "")
    return subprocess.run(
        cmd,
        cwd=str(cwd) if cwd is not None else None,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )


def _parse_json_output(text: str) -> dict[str, Any] | None:
    stripped = text.strip()
    if not stripped:
        return None
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(stripped[start : end + 1])
            except json.JSONDecodeError:
                return None
    return None


def _timestamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _slug(value: str) -> str:
    return "".join(ch if ch.isalnum() else "-" for ch in value).strip("-").lower()


def _ensure_git_repo(repo_path: Path) -> None:
    proc = _run(["git", "-C", str(repo_path), "rev-parse", "--show-toplevel"])
    if proc.returncode != 0:
        raise SystemExit(f"repo path is not a git repository: {repo_path}\n{proc.stderr}")


def _make_worktree(repo_path: Path, worktree_path: Path) -> tuple[bool, str | None]:
    worktree_path.parent.mkdir(parents=True, exist_ok=True)
    proc = _run(["git", "-C", str(repo_path), "worktree", "add", "--detach", "--", str(worktree_path), "HEAD"])
    if proc.returncode == 0:
        return True, None
    return False, (proc.stderr or proc.stdout).strip()


def _cleanup_worktree(repo_path: Path, worktree_path: Path) -> dict[str, Any]:
    if not worktree_path.exists():
        return {"removed": True, "detail": "already_absent"}
    proc = _run(["git", "-C", str(repo_path), "worktree", "remove", "--force", str(worktree_path)], timeout=30)
    if proc.returncode == 0:
        return {"removed": True, "detail": "git_worktree_remove"}
    shutil.rmtree(worktree_path, ignore_errors=True)
    prune = _run(["git", "-C", str(repo_path), "worktree", "prune"], timeout=30)
    return {
        "removed": not worktree_path.exists(),
        "detail": "rmtree_fallback",
        "git_error": (proc.stderr or proc.stdout).strip(),
        "prune_returncode": prune.returncode,
    }


def _body_for_executor(executor_id: str, controller: str, task_id: str, worktree_path: Path) -> str:
    target_file = f"tests/fixtures/external_executor_smoke/{_slug(executor_id)}.txt"
    receipt = {
        "schema_version": "1",
        "task_id": task_id,
        "attempt_no": 1,
        "review_round": 0,
        "status": "EVIDENCE_PACK",
        "summary": f"Smoke changed {target_file}",
        "payload": {
            "claimed_state": "ready_for_review",
            "changed_files": [target_file],
            "validation_not_run": "real executor smoke only requires the tiny file change",
            "scope_violations": [],
            "report": f"{executor_id} real executor smoke completed for {task_id}",
            "raw_log_path": "stdout.log",
            "receipt_path": "terminal",
        },
    }
    return (
        "Goal: Verify that this AGPair executor can make a tiny repo-local change and return AGPair-compatible evidence.\n"
        f"Scope: Work only inside this disposable AGPair git worktree: {worktree_path}\n"
        "Do not access user home, private logs, credentials, browser state, or unrelated paths.\n"
        "Do not inspect the repository, run the full test suite, start subagents, browse the web, or search outside the target file path.\n"
        "Do not create any file except the required target file; do not create receipt.json or report files in the worktree.\n"
        f"Required changes: Create or update {target_file} with exactly one line: {executor_id} {task_id}\n"
        f"Recommended shell command: mkdir -p tests/fixtures/external_executor_smoke && printf '%s %s\\n' '{executor_id}' '{task_id}' > {target_file}\n"
        "Do not push or touch any remote repository. A git commit is optional for this evidence-policy smoke.\n"
        "Exit criteria: After the file is written, print this exact one-line AGPair terminal receipt JSON as the final output line:\n"
        f"{json.dumps(receipt, ensure_ascii=False, separators=(',', ':'))}\n\n"
        f"Task id: {task_id}\n"
        f"Executor under test: {executor_id}\n"
        f"Controller under test: {controller}\n"
    )


def _artifact_summary(status_payload: dict[str, Any] | None) -> dict[str, Any]:
    if not status_payload:
        return {}
    return {
        "stdout_path": status_payload.get("stdout_path"),
        "stderr_path": status_payload.get("stderr_path"),
        "receipt_path": status_payload.get("receipt_path"),
        "report_path": status_payload.get("report_path"),
        "artifact_paths": status_payload.get("artifact_paths"),
        "terminal_receipt": status_payload.get("terminal_receipt"),
        "executor_output_excerpt": status_payload.get("executor_output_excerpt"),
    }


def _artifact_progress_signature(status_payload: dict[str, Any] | None, worktree_path: Path) -> tuple[Any, ...]:
    if not status_payload:
        return ()
    artifact_items: list[tuple[str, int | None, int | None]] = []
    artifacts = status_payload.get("active_attempt_artifacts")
    if isinstance(artifacts, dict):
        for key in sorted(artifacts):
            item = artifacts.get(key)
            if isinstance(item, dict):
                artifact_items.append((key, item.get("size_bytes"), item.get("mtime_ns")))
    for key in ("stdout_path", "stderr_path", "receipt_path", "report_path", "evidence_path"):
        path = status_payload.get(key)
        if not isinstance(path, str) or not path:
            continue
        artifact_path = Path(path)
        if artifact_path.exists():
            stat = artifact_path.stat()
            artifact_items.append((key, stat.st_size, stat.st_mtime_ns))
    git_status = ""
    if worktree_path.exists():
        git_status = _run(
            ["git", "-C", str(worktree_path), "status", "--short", "--untracked-files=all"],
            timeout=30,
        ).stdout
    return (
        tuple(artifact_items),
        git_status,
        bool(status_payload.get("terminal_receipt")),
        status_payload.get("phase"),
    )


def _signature_has_progress(signature: tuple[Any, ...]) -> bool:
    if not signature:
        return False
    artifact_items = signature[0] if len(signature) > 0 else ()
    git_status = signature[1] if len(signature) > 1 else ""
    has_terminal_receipt = bool(signature[2]) if len(signature) > 2 else False
    for key, size, _ in artifact_items:
        if key in {"stdout", "stderr", "stdout_path", "stderr_path"}:
            continue
        if isinstance(size, int) and size > 0:
            return True
    return bool(git_status) or has_terminal_receipt


def _process_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _terminate_executor_session(status_payload: dict[str, Any] | None) -> dict[str, Any]:
    if not status_payload:
        return {"terminated": False, "reason": "missing_status"}
    session_id = status_payload.get("executor_session_id") or status_payload.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        return {"terminated": False, "reason": "missing_session_id"}
    session_path = Path(session_id)
    pid_path = session_path / "pid.txt"
    if not pid_path.exists():
        return {"terminated": False, "session_id": session_id, "reason": "missing_pid_file"}
    try:
        pid = int(pid_path.read_text(encoding="utf-8").strip())
    except ValueError:
        return {"terminated": False, "session_id": session_id, "reason": "invalid_pid_file"}
    detail: dict[str, Any] = {"terminated": False, "session_id": session_id, "pid": pid}
    try:
        os.killpg(pid, signal.SIGTERM)
        detail["sigterm_sent"] = True
    except ProcessLookupError:
        detail["terminated"] = True
        detail["reason"] = "already_exited"
        return detail
    except OSError as exc:
        detail["sigterm_error"] = str(exc)
        try:
            os.kill(pid, signal.SIGTERM)
            detail["sigterm_pid_sent"] = True
        except OSError as pid_exc:
            detail["sigterm_pid_error"] = str(pid_exc)
    time.sleep(1)
    if _process_exists(pid):
        try:
            os.killpg(pid, signal.SIGKILL)
            detail["sigkill_sent"] = True
        except OSError as exc:
            detail["sigkill_error"] = str(exc)
            try:
                os.kill(pid, signal.SIGKILL)
                detail["sigkill_pid_sent"] = True
            except OSError as pid_exc:
                detail["sigkill_pid_error"] = str(pid_exc)
    detail["terminated"] = not _process_exists(pid)
    return detail


def _abandon_task(task_id: str, reason: str) -> subprocess.CompletedProcess[str]:
    return _run(
        [
            sys.executable,
            "-m",
            "agpair.cli.app",
            "task",
            "abandon",
            task_id,
            "--reason",
            reason,
            "--force",
        ],
        cwd=PROJECT_ROOT,
        timeout=30,
    )


def _wait_for_status(
    *,
    task_id: str,
    wait_cmd_base: list[str],
    status_cmd: list[str],
    worktree_path: Path,
    timeout_seconds: float,
    interval_seconds: float,
    no_progress_seconds: float,
) -> tuple[int, dict[str, Any], dict[str, Any] | None]:
    started_at = time.monotonic()
    deadline = started_at + timeout_seconds
    last_progress_at = started_at
    last_signature: tuple[Any, ...] | None = None
    last_status_payload: dict[str, Any] | None = None
    while True:
        now = time.monotonic()
        wait_window = max(0.1, min(interval_seconds, max(0.1, deadline - now)))
        wait_proc = _run(
            [
                *wait_cmd_base,
                "--interval-seconds",
                str(min(interval_seconds, wait_window)),
                "--timeout-seconds",
                str(wait_window),
            ],
            cwd=PROJECT_ROOT,
            timeout=wait_window + 10,
        )
        wait_payload = _parse_json_output(wait_proc.stdout)
        status = _run(status_cmd, cwd=PROJECT_ROOT, timeout=30)
        status_payload = _parse_json_output(status.stdout)
        if status_payload is not None:
            last_status_payload = status_payload
        phase = status_payload.get("phase") if status_payload else None
        if phase is None and wait_payload:
            phase = wait_payload.get("phase")
        signature = _artifact_progress_signature(status_payload, worktree_path)
        if signature != last_signature:
            last_signature = signature
            if _signature_has_progress(signature):
                last_progress_at = time.monotonic()
        if phase in TERMINAL_PHASES:
            return (0 if phase in TERMINAL_OK_PHASES else 1), {
                "ok": phase in TERMINAL_OK_PHASES,
                "phase": phase,
                "timed_out": False,
                "watchdog_triggered": False,
                "last_wait_returncode": wait_proc.returncode,
                "last_wait_payload": wait_payload,
            }, status_payload
        now = time.monotonic()
        if no_progress_seconds > 0 and now - last_progress_at >= no_progress_seconds:
            terminate_detail = _terminate_executor_session(last_status_payload)
            reason = f"smoke no-progress watchdog after {no_progress_seconds:g}s"
            abandon = _abandon_task(task_id, reason)
            status = _run(status_cmd, cwd=PROJECT_ROOT, timeout=30)
            status_payload = _parse_json_output(status.stdout) or last_status_payload
            return 1, {
                "ok": False,
                "phase": status_payload.get("phase") if status_payload else None,
                "timed_out": False,
                "watchdog_triggered": True,
                "blocker_type": "no_progress_timeout",
                "reason": reason,
                "terminate_detail": terminate_detail,
                "abandon_returncode": abandon.returncode,
                "abandon_output": (abandon.stdout or abandon.stderr).strip(),
            }, status_payload
        if now >= deadline:
            return 1, {
                "ok": False,
                "phase": phase,
                "timed_out": True,
                "watchdog_triggered": False,
                "last_wait_returncode": wait_proc.returncode,
                "last_wait_payload": wait_payload,
            }, status_payload
        time.sleep(max(0.1, min(interval_seconds, deadline - now)))


def _executor_result(
    *,
    repo_path: Path,
    worktree_root: Path,
    controller: str,
    executor_id: str,
    allow_self_executor: bool,
    timeout_seconds: float,
    interval_seconds: float,
    no_progress_seconds: float,
    cleanup: bool,
    run_id: str,
) -> dict[str, Any]:
    policy_controller = "generic" if controller == "diagnostic" else controller
    try:
        decision = resolve_controller_policy(
            controller=policy_controller,
            requested_executor=executor_id,
            allow_self_executor=allow_self_executor,
            require_available=True,
        )
    except ValueError as exc:
        return {
            "executor_id": executor_id,
            "outcome": "blocked",
            "blocker_type": "invalid_executor",
            "reason": str(exc),
            "attempted": False,
        }
    if decision.rejected_executor:
        health = executor_health_snapshot(run_launch_probe=True).get(executor_id, {})
        return {
            "executor_id": executor_id,
            "outcome": "blocked",
            "blocker_type": health.get("last_failure_type") or "executor_suppressed",
            "reason": decision.reasons[-1] if decision.reasons else "executor rejected by controller policy",
            "controller_policy": decision.to_dict(),
            "health": health,
            "attempted": False,
        }

    task_id = f"TASK-SMOKE-{_slug(controller)[:8].upper()}-{_slug(executor_id).replace('-', '')[:10].upper()}-{run_id[-6:]}"
    worktree_path = worktree_root / f"{task_id}-{_slug(executor_id)}"
    created, error = _make_worktree(repo_path, worktree_path)
    if not created:
        return {
            "executor_id": executor_id,
            "task_id": task_id,
            "outcome": "blocked",
            "blocker_type": "worktree_create_failed",
            "reason": error,
            "attempted": False,
            "worktree_path": str(worktree_path),
        }

    start_cmd = [
        sys.executable,
        "-m",
        "agpair.cli.app",
        "task",
        "start",
        "--repo-path",
        str(worktree_path),
        "--controller",
        policy_controller,
        "--executor",
        executor_id,
        "--completion-policy",
        "evidence",
        "--authorization-profile",
        "local_mutating",
        "--task-id",
        task_id,
        "--body",
        _body_for_executor(executor_id, policy_controller, task_id, worktree_path),
        "--no-wait",
    ]
    if allow_self_executor:
        start_cmd.append("--allow-self-executor")
    wait_cmd_base = [
        sys.executable,
        "-m",
        "agpair.cli.app",
        "task",
        "wait",
        task_id,
        "--json",
    ]
    status_cmd = [
        sys.executable,
        "-m",
        "agpair.cli.app",
        "task",
        "status",
        task_id,
        "--json",
    ]

    result_payload: dict[str, Any] = {}
    try:
        start = _run(start_cmd, cwd=repo_path, timeout=60)
        if start.returncode != 0:
            result_payload = {
                "executor_id": executor_id,
                "task_id": task_id,
                "outcome": "blocked",
                "blocker_type": "task_start_failed",
                "reason": (start.stderr or start.stdout).strip(),
                "attempted": True,
                "worktree_path": str(worktree_path),
                "start_returncode": start.returncode,
            }
            return result_payload
        wait_returncode, wait_payload, status_payload = _wait_for_status(
            task_id=task_id,
            wait_cmd_base=wait_cmd_base,
            status_cmd=status_cmd,
            worktree_path=worktree_path,
            timeout_seconds=timeout_seconds,
            interval_seconds=interval_seconds,
            no_progress_seconds=no_progress_seconds,
        )
        status = _run(status_cmd, cwd=repo_path, timeout=30)
        status_payload = _parse_json_output(status.stdout) or status_payload
        phase = status_payload.get("phase") if status_payload else None
        failure_context = status_payload.get("failure_context") if status_payload else None
        blocker_type = wait_payload.get("blocker_type")
        if isinstance(failure_context, dict):
            blocker_type = blocker_type or failure_context.get("blocker_type")
        outcome = "ready_for_review" if phase in TERMINAL_OK_PHASES else "blocked"
        if wait_returncode != 0 and phase not in TERMINAL_OK_PHASES:
            outcome = "blocked"
        result_payload = {
            "executor_id": executor_id,
            "task_id": task_id,
            "outcome": outcome,
            "blocker_type": blocker_type,
            "phase": phase,
            "attempted": True,
            "worktree_path": str(worktree_path),
            "git_status_short": _run(
                ["git", "-C", str(worktree_path), "status", "--short", "--untracked-files=all"],
                timeout=30,
            ).stdout,
            "git_diff_name_status": _run(["git", "-C", str(worktree_path), "diff", "--name-status"], timeout=30).stdout,
            "controller_policy": decision.to_dict(),
            "health": executor_health_snapshot().get(executor_id, {}),
            "start_returncode": start.returncode,
            "wait_returncode": wait_returncode,
            "status_returncode": status.returncode,
            "wait_payload": wait_payload,
            "status": status_payload,
            "artifacts": _artifact_summary(status_payload),
            "stdout_excerpt": (start.stdout or "")[-2000:],
            "stderr_excerpt": (start.stderr or "")[-2000:],
        }
        return result_payload
    finally:
        if cleanup:
            result_payload["cleanup"] = _cleanup_worktree(repo_path, worktree_path)


def _write_report(report: dict[str, Any], repo_path: Path) -> Path:
    reports_dir = repo_path / ".agpair" / "smoke" / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / f"{report['run_id']}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run AGPair external executor smoke checks.")
    parser.add_argument("--repo-path", required=True)
    parser.add_argument("--controller", default="generic")
    parser.add_argument("--executors", help="Comma-separated executor ids.")
    parser.add_argument("--all-registered", action="store_true")
    parser.add_argument("--allow-self-executor", action="store_true")
    parser.add_argument("--timeout-seconds", type=float, default=300)
    parser.add_argument("--interval-seconds", type=float, default=2)
    parser.add_argument("--no-progress-seconds", type=float, default=120)
    parser.add_argument("--keep-worktrees", action="store_true")
    args = parser.parse_args(argv)

    repo_path = Path(args.repo_path).expanduser().resolve()
    _ensure_git_repo(repo_path)
    run_id = f"smoke-{_slug(args.controller)}-{_timestamp()}"
    worktree_root = repo_path / ".agpair" / "smoke" / "worktrees" / run_id
    if args.all_registered:
        executor_ids = list(registered_executor_ids())
    elif args.executors:
        executor_ids = [item.strip() for item in args.executors.split(",") if item.strip()]
    else:
        raise SystemExit("pass --executors or --all-registered")
    if not executor_ids:
        raise SystemExit("no executors selected")

    results = [
        _executor_result(
            repo_path=repo_path,
            worktree_root=worktree_root,
            controller=args.controller,
            executor_id=executor_id,
            allow_self_executor=args.allow_self_executor,
            timeout_seconds=args.timeout_seconds,
            interval_seconds=args.interval_seconds,
            no_progress_seconds=args.no_progress_seconds,
            cleanup=not args.keep_worktrees,
            run_id=run_id,
        )
        for executor_id in executor_ids
    ]
    report = {
        "schema_version": "1",
        "run_id": run_id,
        "repo_path": str(repo_path),
        "controller": args.controller,
        "executors": executor_ids,
        "allow_self_executor": args.allow_self_executor,
        "all_registered": args.all_registered,
        "timeout_seconds": args.timeout_seconds,
        "no_progress_seconds": args.no_progress_seconds,
        "results": results,
    }
    report["harness_completed"] = True
    report["all_success"] = all(result.get("outcome") == "ready_for_review" for result in results)
    report_path = repo_path / ".agpair" / "smoke" / "reports" / f"{run_id}.json"
    report["report_path"] = str(report_path)
    _write_report(report, repo_path)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
