from __future__ import annotations

from pathlib import Path
import json
import os
import shutil
import sqlite3
import time
from urllib import error, request

from agpair.config import AppPaths
from agpair.daemon.process import daemon_status

# ---------------------------------------------------------------------------
# Doctor result cache  (disk-persisted, cross-process)
# ---------------------------------------------------------------------------
# Caches only *healthy* (no errors / no conflicts) doctor results for a
# bounded TTL so that repeated CLI invocations in a stable environment do
# not re-probe everything.
#
# Storage: a single JSON file at ``{AGPAIR_HOME}/doctor_cache.json``.
# Format:  ``{"key": "<config_root>::<repo_path>", "cached_at": <epoch>,
#             "ttl": 30, "report": { ... }}``
#
# Cache invalidation rules:
#   1. TTL expires            → stale, fresh probe runs.
#   2. ``fresh=True``         → cache bypassed unconditionally.
#   3. Cache key mismatch     → stale (different config_root / repo_path).
#   4. *Any* unhealthy report → never written to cache.
#   5. Corrupted cache file   → silently ignored (cache miss).
# ---------------------------------------------------------------------------

_DOCTOR_CACHE_TTL_SECONDS = 30.0
_DOCTOR_CACHE_FILENAME = "doctor_cache.json"


def _cache_path(paths: AppPaths) -> Path:
    return paths.root / _DOCTOR_CACHE_FILENAME


def _cache_key(paths: AppPaths, repo_path: str | None) -> str:
    return f"{paths.root}::{repo_path or ''}"


def _read_disk_cache(paths: AppPaths, key: str) -> tuple[dict, float] | None:
    """Try to read a valid cache entry from disk. Returns (report, cached_at)
    or None on miss / corruption / expiry / key-mismatch."""
    cp = _cache_path(paths)
    try:
        raw = cp.read_text(encoding="utf-8")
        envelope = json.loads(raw)
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    if not isinstance(envelope, dict):
        return None
    if envelope.get("key") != key:
        return None
    cached_at = envelope.get("cached_at")
    if not isinstance(cached_at, (int, float)):
        return None
    ttl = envelope.get("ttl", _DOCTOR_CACHE_TTL_SECONDS)
    if not isinstance(ttl, (int, float)):
        return None
    if (time.time() - cached_at) >= ttl:
        return None
    report = envelope.get("report")
    if not isinstance(report, dict):
        return None
    return report, cached_at


def _write_disk_cache(paths: AppPaths, key: str, report: dict) -> None:
    """Persist a healthy report to the cache file. Failures are silently
    ignored — caching is best-effort."""
    envelope = {
        "key": key,
        "cached_at": time.time(),
        "ttl": _DOCTOR_CACHE_TTL_SECONDS,
        "report": report,
    }
    try:
        paths.root.mkdir(parents=True, exist_ok=True)
        tmp = _cache_path(paths).with_suffix(".tmp")
        tmp.write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")
        tmp.replace(_cache_path(paths))
    except OSError:
        pass


def _is_healthy(report: dict) -> bool:
    """Return True when the report has no errors / conflicts worth re-probing."""
    if report.get("desktop_reader_conflict"):
        return False
    if report.get("db_error"):
        return False
    if report.get("repo_bridge_error"):
        return False
    if report.get("repo_bridge_warning"):
        return False
    return True


def build_doctor_report(
    paths: AppPaths,
    *,
    repo_path: str | None = None,
    fresh: bool = False,
) -> dict:
    key = _cache_key(paths, repo_path)

    # --- cache hit path (disk-persisted) ---
    if not fresh:
        cached = _read_disk_cache(paths, key)
        if cached is not None:
            cached_report, cached_at = cached
            age = round(time.time() - cached_at, 2)
            return {**cached_report, "doctor_cache_hit": True, "doctor_cache_age_s": age}

    # --- fresh probe ---
    db_exists = paths.db_path.exists()
    status = daemon_status(paths)
    latest_receipt_id, db_error = _safe_read_latest_receipt_id(paths.db_path) if db_exists else (None, None)

    # Build exclude-PIDs set from the daemon's own PID so that the agpair
    # daemon holding the shared lock is not reported as an external conflict.
    daemon_pid = status.get("pid")
    exclude_pids: set[int] | None = {daemon_pid} if isinstance(daemon_pid, int) and daemon_pid > 0 else None

    desktop_reader_conflict = None

    from agpair.executors import AntigravityExecutor, CodexExecutor

    report = {
        "config_root": str(paths.root),
        "db_path": str(paths.db_path),
        "db_exists": db_exists,
        "db_error": db_error,
        "agent_bus_bin": paths.agent_bus_bin,
        "agent_bus_available": _is_agent_bus_available(paths.agent_bus_bin),
        "status_path": str(paths.status_path),
        "status_path_exists": paths.status_path.exists(),
        "pid_path": str(paths.pid_path),
        "pid_path_exists": paths.pid_path.exists(),
        "daemon_running": status.get("running", False),
        "daemon_pid": status.get("pid"),
        "last_tick_at": status.get("last_tick_at"),
        "processed_receipts": status.get("processed_receipts", 0),
        "stuck_marked": status.get("stuck_marked", 0),
        "latest_receipt_id": latest_receipt_id,
        "active_executor_backend": AntigravityExecutor("").backend_id,
        "supported_executor_backends": [AntigravityExecutor("").backend_id, CodexExecutor("").backend_id],
        "desktop_reader_conflict": desktop_reader_conflict is not None,
        "desktop_reader_conflict_detail": desktop_reader_conflict,
        "doctor_cache_hit": False,
        "doctor_cache_age_s": None,
    }
    if repo_path:
        report.update(_build_repo_bridge_report(Path(repo_path)))

    # --- cache store (healthy results only, persisted to disk) ---
    if _is_healthy(report):
        _write_disk_cache(paths, key, report)

    return report


def emit_doctor_json(
    paths: AppPaths,
    *,
    repo_path: str | None = None,
    fresh: bool = False,
) -> str:
    return json.dumps(
        build_doctor_report(paths, repo_path=repo_path, fresh=fresh),
        ensure_ascii=False,
        indent=2,
    )


def _is_agent_bus_available(binary: str) -> bool:
    if "/" in binary:
        path = Path(binary)
        return path.exists() and os.access(path, os.X_OK)
    return shutil.which(binary) is not None


def _safe_read_latest_receipt_id(db_path: Path) -> tuple[str | None, str | None]:
    try:
        with sqlite3.connect(db_path) as conn:
            row = conn.execute("SELECT message_id FROM receipts ORDER BY rowid DESC LIMIT 1").fetchone()
    except sqlite3.DatabaseError as exc:
        return None, str(exc)
    if row is None:
        return None, None
    return str(row[0]), None


def _build_repo_bridge_report(repo_path: Path) -> dict:
    repo = repo_path.expanduser().resolve()
    expected_extension_path, expected_extension_version, expected_extension_id = _read_repo_companion_metadata(repo)
    repo_markers = [
        repo / ".agpair" / "bridge_port",
    ]
    global_markers = [
        Path.home() / ".agpair" / "bridge_port",
    ]
    chosen_marker = next((marker for marker in repo_markers if marker.exists()), None)
    if chosen_marker is None:
        chosen_marker = next((marker for marker in global_markers if marker.exists()), None)
    marker_source = "repo" if chosen_marker in repo_markers else "global" if chosen_marker in global_markers else None
    preferred_repo_marker = repo_markers[0]
    report = {
        "repo_path": str(repo),
        "repo_bridge_marker_path": str(preferred_repo_marker),
        "repo_bridge_marker_exists": any(marker.exists() for marker in repo_markers),
        "repo_bridge_marker_source": marker_source,
        "repo_bridge_marker_source_path": str(chosen_marker) if chosen_marker else None,
        "repo_bridge_port": None,
        "repo_bridge_health_url": None,
        "repo_bridge_reachable": False,
        "repo_bridge_error": None,
        "repo_bridge_sdk_initialized": None,
        "repo_bridge_ls_ready": None,
        "repo_bridge_monitor_running": None,
        "repo_bridge_workspace_paths": None,
        "repo_bridge_workspace_match": None,
        "repo_bridge_agent_bus_watch_running": None,
        "repo_bridge_agent_bus_delegation_enabled": None,
        "repo_bridge_receipt_watcher_running": None,
        "repo_bridge_pending_task_count": None,
        "repo_bridge_pending_task_ids": None,
        "repo_bridge_concurrency_policy": {
            "same_worktree_parallel_safe": False,
            "safe_isolation_boundary": "different repo or different git worktree",
        },
        "repo_bridge_session_ready": False,
        "repo_bridge_warning": None,
        "repo_bridge_version": None,
        "repo_bridge_extension_id": None,
        "repo_bridge_extension_path": None,
        "repo_bridge_expected_extension_id": expected_extension_id,
        "repo_bridge_expected_extension_path": str(expected_extension_path) if expected_extension_path else None,
        "repo_bridge_expected_version": expected_extension_version,
        "repo_bridge_running_from_repo": None,
        "repo_bridge_extension_id_match": None,
        "repo_bridge_version_match": None,
        "repo_bridge_auth_mode": None,
        "repo_bridge_mutating_auth_required": None,
    }
    if chosen_marker is None:
        report["repo_bridge_error"] = "bridge marker not found"
        report["repo_bridge_warning"] = "bridge marker missing"
        return report

    try:
        port = int(chosen_marker.read_text(encoding="utf-8").strip())
    except (OSError, ValueError) as exc:
        report["repo_bridge_error"] = f"invalid bridge marker: {exc}"
        report["repo_bridge_warning"] = "bridge marker unreadable"
        return report

    report["repo_bridge_port"] = port
    report["repo_bridge_health_url"] = f"http://127.0.0.1:{port}/health"
    payload, fetch_error = _fetch_bridge_health(report["repo_bridge_health_url"])
    if fetch_error is not None:
        report["repo_bridge_error"] = fetch_error
        report["repo_bridge_warning"] = fetch_error
        return report

    workspace_paths = payload.get("workspace_paths")
    if not isinstance(workspace_paths, list):
        workspace_paths = []
    workspace_paths = [str(value) for value in workspace_paths if isinstance(value, str)]
    delegation_status = payload.get("delegation_auto_return")
    receipt_watcher_running = None
    pending_task_count = None
    pending_task_ids = None

    if isinstance(delegation_status, dict):
        raw = delegation_status.get("receipt_watcher_running")
        receipt_watcher_running = bool(raw) if isinstance(raw, bool) else None

        summary = delegation_status.get("tracker_summary")
        if isinstance(summary, dict):
            raw_pending = summary.get("pending")
            if isinstance(raw_pending, int):
                pending_task_count = raw_pending

            tasks = summary.get("tasks")
            if isinstance(tasks, list):
                pending_task_ids = [
                    str(t.get("taskId"))
                    for t in tasks
                    if isinstance(t, dict) and t.get("terminalSentAt") is None and "taskId" in t
                ]

    sdk_initialized = bool(payload.get("sdk_initialized"))
    ls_ready = bool(payload.get("ls_bridge_ready"))
    monitor_running = bool(payload.get("monitor_running"))
    workspace_match = str(repo) in workspace_paths
    agent_bus_watch_running = payload.get("agent_bus_watch_running")
    if not isinstance(agent_bus_watch_running, bool):
        agent_bus_watch_running = None
    agent_bus_delegation_enabled = payload.get("agent_bus_delegation_enabled")
    if not isinstance(agent_bus_delegation_enabled, bool):
        agent_bus_delegation_enabled = None

    warning_reasons: list[str] = []
    if not sdk_initialized:
        warning_reasons.append("sdk_initialized=false")
    if not ls_ready:
        warning_reasons.append("ls_bridge_ready=false (likely stale Antigravity session / missing CSRF)")
    if not monitor_running:
        warning_reasons.append("monitor_running=false")
    if not workspace_match:
        warning_reasons.append("workspace_paths missing repo")
    if agent_bus_watch_running is False:
        warning_reasons.append("agent_bus_watch_running=false")
    if agent_bus_delegation_enabled is False:
        warning_reasons.append("agent_bus_delegation_enabled=false")
    if receipt_watcher_running is False:
        warning_reasons.append("receipt_watcher_running=false")

    running_extension_path = payload.get("extension_path")
    if not isinstance(running_extension_path, str):
        running_extension_path = None
    running_extension_id = payload.get("extension_id")
    if not isinstance(running_extension_id, str):
        running_extension_id = None
    running_extension_version = payload.get("version")
    if not isinstance(running_extension_version, str):
        running_extension_version = None

    extension_id_match = None
    if expected_extension_id is not None and running_extension_id is not None:
        extension_id_match = running_extension_id == expected_extension_id
        if not extension_id_match:
            warning_reasons.append(
                f"extension id mismatch (running={running_extension_id}, repo={expected_extension_id})"
            )

    version_match = None
    if expected_extension_version is not None and running_extension_version is not None:
        version_match = running_extension_version == expected_extension_version
        if not version_match:
            warning_reasons.append(
                f"extension version mismatch (running={running_extension_version}, repo={expected_extension_version})"
            )

    running_from_repo = None
    if expected_extension_path is not None and running_extension_path is not None:
        try:
            running_from_repo = Path(running_extension_path).expanduser().resolve() == expected_extension_path
        except OSError:
            running_from_repo = False
        if (
            not running_from_repo and
            (extension_id_match is False or version_match is False)
        ):
            warning_reasons.append(
                f"extension_path mismatch (running={running_extension_path}, repo={expected_extension_path})"
            )

    report.update(
        {
            "repo_bridge_reachable": True,
            "repo_bridge_sdk_initialized": sdk_initialized,
            "repo_bridge_ls_ready": ls_ready,
            "repo_bridge_monitor_running": monitor_running,
            "repo_bridge_workspace_paths": workspace_paths,
            "repo_bridge_workspace_match": workspace_match,
            "repo_bridge_agent_bus_watch_running": agent_bus_watch_running,
            "repo_bridge_agent_bus_delegation_enabled": agent_bus_delegation_enabled,
            "repo_bridge_receipt_watcher_running": receipt_watcher_running,
            "repo_bridge_pending_task_count": pending_task_count,
            "repo_bridge_pending_task_ids": pending_task_ids,
            "repo_bridge_session_ready": not warning_reasons,
            "repo_bridge_warning": "; ".join(warning_reasons) or None,
            "repo_bridge_version": running_extension_version,
            "repo_bridge_extension_id": running_extension_id,
            "repo_bridge_extension_path": running_extension_path,
            "repo_bridge_running_from_repo": running_from_repo,
            "repo_bridge_extension_id_match": extension_id_match,
            "repo_bridge_version_match": version_match,
            "repo_bridge_auth_mode": payload.get("bridge_auth_mode"),
            "repo_bridge_mutating_auth_required": payload.get("bridge_mutating_auth_required"),
        }
    )
    return report


def _fetch_bridge_health(url: str) -> tuple[dict, str | None]:
    try:
        with request.urlopen(url, timeout=1.5) as response:
            raw = response.read().decode("utf-8")
    except (OSError, error.URLError, TimeoutError) as exc:
        return {}, f"bridge health probe failed: {exc}"
    try:
        payload = json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        return {}, f"bridge health returned invalid JSON: {exc}"
    if not isinstance(payload, dict):
        return {}, "bridge health returned non-object payload"
    return payload, None


def _read_repo_companion_metadata(repo: Path) -> tuple[Path | None, str | None, str | None]:
    companion_root = repo / "companion-extension"
    package_json = companion_root / "package.json"
    if not package_json.exists():
        return None, None, None
    try:
        raw = json.loads(package_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return companion_root.resolve(), None, None
    version = raw.get("version") if isinstance(raw, dict) else None
    name = raw.get("name") if isinstance(raw, dict) else None
    extension_id = f"logicrw.{name}" if isinstance(name, str) and name else None
    return companion_root.resolve(), version if isinstance(version, str) else None, extension_id
