from __future__ import annotations

from pathlib import Path
import json
import os


class DesktopReaderLockError(RuntimeError):
    """Raised when the shared desktop-reader lock cannot be acquired."""


def detect_supervisor_desktop_reader_conflict(
    *,
    exclude_pids: set[int] | None = None,
) -> dict | None:
    """Detect an external desktop-reader holding the shared lock.

    When *exclude_pids* is provided, a lock whose PID is in the set **and**
    whose ``owner`` field is ``"agpair"`` is treated as self-owned and **not**
    reported as an external conflict.  This prevents the agpair daemon's own
    lock from being flagged as a conflict.

    Status-file conflicts (``desktop_agent_bus_watch.py`` running) are never
    excluded — those always indicate a real external watcher.
    """
    supervisor_root = Path.home() / ".supervisor"
    status_path = supervisor_root / "agent_bus_watch_desktop.status.json"
    lock_path = supervisor_root / "agent_bus_watch_desktop.lock"

    # --- status file: always a real external watcher ---
    status_payload = _safe_load_json(status_path)
    if status_payload:
        pid = _coerce_pid(status_payload.get("pid"))
        command = str(status_payload.get("command") or "")
        if pid and _pid_alive(pid) and "desktop_agent_bus_watch.py" in command:
            return {
                "source": "status",
                "pid": pid,
                "command": command,
                "status_path": str(status_path),
                "lock_path": str(lock_path),
                "updated_at": status_payload.get("updated_at"),
            }

    # --- lock file: may be self-owned by the agpair daemon ---
    lock_payload = _safe_load_json(lock_path)
    if lock_payload:
        pid = _coerce_pid(lock_payload.get("pid"))
        if pid and _pid_alive(pid):
            if _is_self_owned_agpair_lock(lock_payload, exclude_pids):
                return None
            return {
                "source": "lock",
                "pid": pid,
                "command": None,
                "status_path": str(status_path),
                "lock_path": str(lock_path),
                "updated_at": lock_payload.get("started_at"),
            }

    return None


def _is_self_owned_agpair_lock(
    lock_payload: dict,
    exclude_pids: set[int] | None,
) -> bool:
    """Return True when the lock is held by a known agpair process."""
    if not exclude_pids:
        return False
    pid = _coerce_pid(lock_payload.get("pid"))
    if pid is None or pid not in exclude_pids:
        return False
    owner = str(lock_payload.get("owner") or "")
    return owner == "agpair"


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


def acquire_shared_desktop_reader_lock(lock_path: Path) -> dict:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "pid": os.getpid(),
        "started_at": _now_iso(),
        "lock_path": str(lock_path),
        "owner": "agpair",
    }

    if _try_create_excl(lock_path, payload):
        return payload

    existing = _safe_load_json(lock_path)
    if existing:
        owner_pid = _coerce_pid(existing.get("pid"))
        if owner_pid and _pid_alive(owner_pid):
            raise DesktopReaderLockError(
                "another desktop-side receipt consumer already holds the shared lock "
                f"(pid={owner_pid}, owner={existing.get('owner') or 'unknown'}, path={lock_path})"
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
