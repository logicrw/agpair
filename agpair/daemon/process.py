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
    existing = _read_pid(paths.pid_path)
    if existing and _is_process_alive(existing):
        return existing
    stdout_log = open(paths.daemon_stdout_path, "a", encoding="utf-8")  # noqa: SIM115
    stderr_log = open(paths.daemon_stderr_path, "a", encoding="utf-8")  # noqa: SIM115
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
        stdout=stdout_log,
        stderr=stderr_log,
        start_new_session=True,
        cwd=str(Path.cwd()),
    )
    # Close the parent-side file handles — the child has inherited them.
    stdout_log.close()
    stderr_log.close()
    # Atomic write: write to temp file, then rename to avoid partial reads
    import tempfile
    tmp = tempfile.NamedTemporaryFile(
        mode="w", dir=str(paths.root), prefix=".pid_", delete=False
    )
    try:
        tmp.write(str(proc.pid))
        tmp.close()
        Path(tmp.name).replace(paths.pid_path)
    except Exception:
        Path(tmp.name).unlink(missing_ok=True)
        raise
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
    return {
        **status,
        "pid": pid,
        "running": running,
        "log_stdout": str(paths.daemon_stdout_path),
        "log_stderr": str(paths.daemon_stderr_path),
    }


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
