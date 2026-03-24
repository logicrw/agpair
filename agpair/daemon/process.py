from __future__ import annotations

from pathlib import Path
import json
import os
import signal
import subprocess
import sys

from agpair.config import AppPaths
from agpair.daemon.loop import read_daemon_status


def start_background_daemon(paths: AppPaths, *, interval_ms: int = 1000, timeout_seconds: int = 1800) -> int:
    paths.root.mkdir(parents=True, exist_ok=True)
    if paths.pid_path.exists():
        existing = _read_pid(paths.pid_path)
        if existing and _is_process_alive(existing):
            return existing
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "agpair.cli.app",
            "daemon",
            "run",
            "--interval-ms",
            str(interval_ms),
            "--timeout-seconds",
            str(timeout_seconds),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        cwd=str(Path.cwd()),
    )
    paths.pid_path.write_text(str(proc.pid), encoding="utf-8")
    return proc.pid


def stop_background_daemon(paths: AppPaths) -> None:
    pid = _read_pid(paths.pid_path)
    if not pid:
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    paths.pid_path.unlink(missing_ok=True)


def daemon_status(paths: AppPaths) -> dict:
    status = read_daemon_status(paths)
    pid = _read_pid(paths.pid_path)
    running = bool(pid and _is_process_alive(pid))
    return {**status, "pid": pid, "running": running}


def _read_pid(path: Path) -> int | None:
    if not path.exists():
        return None
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except ValueError:
        return None


def _is_process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False
