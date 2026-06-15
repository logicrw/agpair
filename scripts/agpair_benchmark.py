#!/usr/bin/env python3
from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agpair.executors.policy import resolve_controller_policy
from agpair.executors.registry import registered_executor_ids
from agpair.executors.routing import validate_supported_executor

SMOKE_SCRIPT = PROJECT_ROOT / "scripts" / "smoke_real_executors.py"
PEER_ORDER = ("antigravity-cli", "grok-cli")
CONTROLLER_NATIVE_SELF = {
    "codex": "codex",
    "claude": "claude-code",
    "claude-code": "claude-code",
    "claude_code": "claude-code",
}


@dataclass(frozen=True)
class Selection:
    executors: list[str]
    mode: str
    policy_expected_executors: list[str] | None
    policy_suppressed_executors: list[str]


def _timestamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _slug(value: str) -> str:
    return "".join(ch if ch.isalnum() else "-" for ch in value).strip("-").lower()


def _executor_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [validate_supported_executor(item.strip()) for item in value.split(",") if item.strip()]


def _peer_sorted(executors: list[str]) -> list[str]:
    order = {executor_id: index for index, executor_id in enumerate((*PEER_ORDER, "claude-code", "codex"))}
    return sorted(executors, key=lambda item: (order.get(item, 100), item))


def select_executors(
    *,
    controller: str,
    requested: str | None = None,
    all_registered: bool = False,
    allow_self_executor: bool = False,
) -> Selection:
    if requested:
        return Selection(
            executors=_peer_sorted(_executor_csv(requested)),
            mode="explicit",
            policy_expected_executors=None,
            policy_suppressed_executors=[],
        )
    if all_registered:
        selected = list(registered_executor_ids())
        if not allow_self_executor:
            self_executor = CONTROLLER_NATIVE_SELF.get(controller.strip().lower().replace("_", "-"))
            selected = [executor_id for executor_id in selected if executor_id != self_executor]
        return Selection(
            executors=_peer_sorted(selected),
            mode="all_registered",
            policy_expected_executors=None,
            policy_suppressed_executors=[],
        )
    decision = resolve_controller_policy(
        controller=controller,
        allow_self_executor=allow_self_executor,
        require_available=False,
    )
    return Selection(
        executors=_peer_sorted(list(decision.eligible_executors)),
        mode="controller_policy",
        policy_expected_executors=list(decision.eligible_executors),
        policy_suppressed_executors=list(decision.suppressed_executors),
    )


def _run(cmd: list[str], *, cwd: Path, env: dict[str, str], timeout: float | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=cwd,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )


def _run_checked(cmd: list[str], *, cwd: Path) -> None:
    proc = subprocess.run(cmd, cwd=cwd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout).strip() or f"command failed: {' '.join(cmd)}")


def _make_repo(parent: Path) -> Path:
    repo = parent / "repo"
    repo.mkdir(parents=True)
    (repo / "README.md").write_text("# AGPair benchmark fixture\n", encoding="utf-8")
    (repo / "src").mkdir()
    (repo / "src" / "hello.py").write_text("def hello():\n    return 'hello'\n", encoding="utf-8")
    _run_checked(["git", "init"], cwd=repo)
    _run_checked(["git", "config", "user.email", "benchmark@example.com"], cwd=repo)
    _run_checked(["git", "config", "user.name", "AGPair Benchmark"], cwd=repo)
    _run_checked(["git", "add", "."], cwd=repo)
    _run_checked(["git", "commit", "-m", "initial benchmark fixture"], cwd=repo)
    return repo


def _json_from_stdout(stdout: str) -> dict[str, Any] | None:
    stripped = stdout.strip()
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


def _benchmark_env(agpair_home: Path) -> dict[str, str]:
    env = os.environ.copy()
    existing_pythonpath = env.get("PYTHONPATH")
    env["PYTHONPATH"] = str(PROJECT_ROOT) + (os.pathsep + existing_pythonpath if existing_pythonpath else "")
    env["AGPAIR_HOME"] = str(agpair_home)
    return env


def run_executor_benchmark(
    *,
    repo_path: Path,
    agpair_home: Path,
    controller: str,
    executor_id: str,
    scenario: str,
    timeout_seconds: float,
    interval_seconds: float,
    no_progress_seconds: float,
    allow_self_executor: bool,
    dirty_snapshot: str,
    keep_worktrees: bool,
) -> dict[str, Any]:
    cmd = [
        sys.executable,
        str(SMOKE_SCRIPT),
        "--repo-path",
        str(repo_path),
        "--controller",
        controller,
        "--scenario",
        scenario,
        "--executors",
        executor_id,
        "--timeout-seconds",
        str(timeout_seconds),
        "--interval-seconds",
        str(interval_seconds),
        "--no-progress-seconds",
        str(no_progress_seconds),
        "--dirty-snapshot",
        dirty_snapshot,
    ]
    if allow_self_executor:
        cmd.append("--allow-self-executor")
    if keep_worktrees:
        cmd.append("--keep-worktrees")
    started_at = time.monotonic()
    proc = _run(
        cmd,
        cwd=PROJECT_ROOT,
        env=_benchmark_env(agpair_home),
        timeout=max(timeout_seconds + 120, 180),
    )
    duration = time.monotonic() - started_at
    payload = _json_from_stdout(proc.stdout)
    result = (payload.get("results") or [{}])[0] if isinstance(payload, dict) else {}
    success = (
        proc.returncode == 0
        and isinstance(result, dict)
        and bool(result.get("attempted"))
        and (bool(result.get("adoptable")) or result.get("outcome") == "ready_for_review")
    )
    return {
        "executor_id": executor_id,
        "duration_seconds": round(duration, 3),
        "returncode": proc.returncode,
        "success": success,
        "scenario": scenario,
        "task_id": result.get("task_id") if isinstance(result, dict) else None,
        "outcome": result.get("outcome") if isinstance(result, dict) else None,
        "phase": result.get("phase") if isinstance(result, dict) else None,
        "adoptable": result.get("adoptable") if isinstance(result, dict) else None,
        "controller_action": result.get("controller_action") if isinstance(result, dict) else None,
        "time_to_first_useful_signal_seconds": (
            result.get("time_to_first_useful_signal_seconds") if isinstance(result, dict) else None
        ),
        "blocker_type": result.get("blocker_type") if isinstance(result, dict) else None,
        "failure_class": result.get("failure_class") if isinstance(result, dict) else None,
        "smoke_report_path": payload.get("report_path") if isinstance(payload, dict) else None,
        "stdout_excerpt": (proc.stdout or "")[-2000:] if proc.returncode != 0 else "",
        "stderr_excerpt": (proc.stderr or "")[-2000:],
        "smoke_result": result if isinstance(result, dict) else {},
    }


def summarize_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for result in results:
        grouped[str(result.get("executor_id"))].append(result)
    per_executor = {}
    for executor_id, items in grouped.items():
        durations = [float(item["duration_seconds"]) for item in items if isinstance(item.get("duration_seconds"), (int, float))]
        signals = [
            float(item["time_to_first_useful_signal_seconds"])
            for item in items
            if isinstance(item.get("time_to_first_useful_signal_seconds"), (int, float))
        ]
        per_executor[executor_id] = {
            "runs": len(items),
            "successes": sum(1 for item in items if item.get("success") is True),
            "success_rate": round(sum(1 for item in items if item.get("success") is True) / len(items), 4),
            "avg_duration_seconds": round(sum(durations) / len(durations), 3) if durations else None,
            "avg_time_to_first_useful_signal_seconds": round(sum(signals) / len(signals), 3) if signals else None,
        }
    return {
        "runs": len(results),
        "successes": sum(1 for item in results if item.get("success") is True),
        "all_success": all(item.get("success") is True for item in results) if results else False,
        "per_executor": per_executor,
    }


def _report_path(report: dict[str, Any], output: Path | None) -> Path:
    return output or PROJECT_ROOT / ".agpair" / "benchmarks" / f"{report['run_id']}.json"


def _write_report(report: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def _print_table(report: dict[str, Any]) -> None:
    print(f"AGPair benchmark {report['run_id']}")
    print(f"controller={report['controller']} scenario={report['scenario']} report={report['report_path']}")
    print(f"{'executor':<18} {'ok':<5} {'duration':>10} {'signal':>10} {'outcome':<18} {'blocker':<24}")
    print("-" * 92)
    for result in report["results"]:
        signal = result.get("time_to_first_useful_signal_seconds")
        print(
            f"{result['executor_id']:<18} "
            f"{str(bool(result['success'])):<5} "
            f"{result['duration_seconds']:>10.3f} "
            f"{str(signal if signal is not None else ''):>10} "
            f"{str(result.get('outcome') or ''):<18} "
            f"{str(result.get('blocker_type') or result.get('failure_class') or ''):<24}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Benchmark AGPair external executor handoff.")
    parser.add_argument("--controller", choices=("codex", "claude-code", "generic"), default="codex")
    parser.add_argument("--scenario", choices=("implementation_smoke", "report_smoke"), default="implementation_smoke")
    parser.add_argument("--executors", help="Comma-separated executor ids. Defaults to controller policy matrix.")
    parser.add_argument("--all-registered", action="store_true")
    parser.add_argument("--allow-self-executor", action="store_true")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--timeout-seconds", type=float, default=300)
    parser.add_argument("--interval-seconds", type=float, default=5)
    parser.add_argument("--no-progress-seconds", type=float, default=120)
    parser.add_argument("--dirty-snapshot", choices=("off", "tracked", "default"), default="off")
    parser.add_argument("--keep-worktrees", action="store_true")
    parser.add_argument("--keep-temp-repo", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.repeat < 1:
        raise SystemExit("--repeat must be >= 1")
    selection = select_executors(
        controller=args.controller,
        requested=args.executors,
        all_registered=args.all_registered,
        allow_self_executor=args.allow_self_executor,
    )
    if not selection.executors:
        raise SystemExit("no executors selected")
    run_id = f"bench-{_slug(args.controller)}-{_timestamp()}"
    temp_root = Path(tempfile.mkdtemp(prefix=f"{run_id}-"))
    repo_path = _make_repo(temp_root)
    agpair_home = temp_root / ".agpair"
    results: list[dict[str, Any]] = []
    try:
        for repeat_index in range(1, args.repeat + 1):
            for executor_id in selection.executors:
                result = run_executor_benchmark(
                    repo_path=repo_path,
                    agpair_home=agpair_home,
                    controller=args.controller,
                    executor_id=executor_id,
                    scenario=args.scenario,
                    timeout_seconds=args.timeout_seconds,
                    interval_seconds=args.interval_seconds,
                    no_progress_seconds=args.no_progress_seconds,
                    allow_self_executor=args.allow_self_executor,
                    dirty_snapshot=args.dirty_snapshot,
                    keep_worktrees=args.keep_worktrees,
                )
                result["repeat_index"] = repeat_index
                results.append(result)
    finally:
        if not args.keep_temp_repo:
            shutil.rmtree(temp_root, ignore_errors=True)
    report = {
        "schema_version": "1",
        "run_id": run_id,
        "controller": args.controller,
        "scenario": args.scenario,
        "selection_mode": selection.mode,
        "executors": selection.executors,
        "policy_expected_executors": selection.policy_expected_executors,
        "policy_suppressed_executors": selection.policy_suppressed_executors,
        "allow_self_executor": args.allow_self_executor,
        "repeat": args.repeat,
        "timeout_seconds": args.timeout_seconds,
        "interval_seconds": args.interval_seconds,
        "adaptive_wait_expected": True,
        "agpair_home": str(agpair_home),
        "temp_repo_path": str(repo_path),
        "temp_repo_kept": args.keep_temp_repo,
        "results": results,
        "summary": summarize_results(results),
    }
    report_path = _report_path(report, args.output)
    report["report_path"] = str(report_path)
    _write_report(report, report_path)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        _print_table(report)
    return 0 if report["summary"]["successes"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
