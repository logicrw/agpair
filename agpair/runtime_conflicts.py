from __future__ import annotations

from pathlib import Path
import json
import os
import subprocess


class DesktopReaderLockError(RuntimeError):
    """Raised when the shared desktop-reader lock cannot be acquired."""



def _safe_load_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _coerce_pid(value: object) -> int | None:
    try:
        pid = int(value)
    except (TypeError, ValueError):
        return None
    return pid if pid > 0 else None


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _get_process_start_time(pid: int) -> float | None:
    """Return the kernel-recorded start time of *pid* as epoch seconds.

    Mirrors the helper in agpair.executors.local_cli; duplicated here to
    avoid a circular import. Used to detect PID recycling so a stale lock
    file pointing at a re-issued PID can be cleaned up safely.
    """
    try:
        output = subprocess.check_output(
            ["ps", "-o", "lstart=", "-p", str(pid)],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        if not output:
            return None
        from datetime import datetime
        dt = datetime.strptime(output, "%a %b %d %H:%M:%S %Y")
        return dt.timestamp()
    except Exception:
        return None


def _coerce_float(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _is_lock_owner_alive(existing: dict) -> bool:
    """Return True only if the lock file's recorded owner is *still* the same
    process. Guards against PID recycling: if the recorded process_start_time
    no longer matches the actual start time of that PID, the original owner
    is dead and the PID has been re-issued.
    """
    owner_pid = _coerce_pid(existing.get("pid"))
    if not owner_pid or not _pid_alive(owner_pid):
        return False
    recorded_start = _coerce_float(existing.get("process_start_time"))
    if recorded_start is None:
        # Legacy lock file without start_time — fall back to PID-only check.
        return True
    actual_start = _get_process_start_time(owner_pid)
    if actual_start is None:
        # ps unavailable; conservatively trust the PID liveness check.
        return True
    if abs(actual_start - recorded_start) > 3:
        return False
    return True


def acquire_shared_desktop_reader_lock(lock_path: Path) -> dict:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    self_pid = os.getpid()
    payload = {
        "pid": self_pid,
        "started_at": _now_iso(),
        "process_start_time": _get_process_start_time(self_pid),
        "lock_path": str(lock_path),
        "owner": "agpair",
    }

    if _try_create_excl(lock_path, payload):
        return payload

    existing = _safe_load_json(lock_path)
    if existing and _is_lock_owner_alive(existing):
        raise DesktopReaderLockError(
            "another desktop-side receipt consumer already holds the shared lock "
            f"(pid={existing.get('pid')}, owner={existing.get('owner') or 'unknown'}, path={lock_path})"
        )

    try:
        lock_path.unlink()
    except FileNotFoundError:
        pass

    if _try_create_excl(lock_path, payload):
        return payload

    winner = _safe_load_json(lock_path) or {}
    raise DesktopReaderLockError(
        "failed to acquire shared desktop-reader lock after stale cleanup "
        f"(pid={winner.get('pid')}, owner={winner.get('owner')}, path={lock_path})"
    )


def release_shared_desktop_reader_lock(lock_path: Path) -> None:
    payload = _safe_load_json(lock_path)
    if payload and _coerce_pid(payload.get("pid")) == os.getpid():
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass


def _try_create_excl(lock_path: Path, payload: dict) -> bool:
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError:
        return False
    try:
        os.write(fd, json.dumps(payload, indent=2).encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    return True


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()
