from __future__ import annotations

from pathlib import Path
import json
import os


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
