"""Unified liveness classification for acked tasks.

Provides:
  - ``LivenessState`` enum for classification
  - ``classify_liveness()`` to classify a task's current liveness
  - ``detect_workspace_activity()`` to probe a repo for fresh local file changes
  - ``is_task_live()`` convenience predicate used by intervention guards
"""
from __future__ import annotations

import enum
import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

from agpair.models import TaskRecord

# Default freshness window — same spirit as heartbeat silence window.
DEFAULT_FRESHNESS_SECONDS: float = 300.0  # 5 minutes


class LivenessState(str, enum.Enum):
    """Classification of an acked task's liveness."""

    silent = "silent"
    active_via_heartbeat = "active_via_heartbeat"
    active_via_workspace = "active_via_workspace"
    active_via_both = "active_via_both"


def _is_fresh(iso_timestamp: str | None, cutoff: datetime) -> bool:
    """Return True if *iso_timestamp* is non-null and newer than *cutoff*."""
    if not iso_timestamp:
        return False
    try:
        dt = datetime.fromisoformat(iso_timestamp.replace("Z", "+00:00"))
        return dt > cutoff
    except (ValueError, TypeError):
        return False


def classify_liveness(
    task: TaskRecord,
    *,
    now: datetime | None = None,
    freshness_seconds: float = DEFAULT_FRESHNESS_SECONDS,
) -> LivenessState:
    """Classify an acked task's liveness from its stored timestamps.

    Parameters
    ----------
    task:
        The task record to classify.
    now:
        Current UTC datetime.  Defaults to ``datetime.now(UTC)``.
    freshness_seconds:
        A timestamp within this many seconds of *now* is considered fresh.

    Returns
    -------
    LivenessState
    """
    current = now or datetime.now(UTC)
    cutoff = current - timedelta(seconds=freshness_seconds)

    hb_fresh = _is_fresh(task.last_heartbeat_at, cutoff)
    ws_fresh = _is_fresh(task.last_workspace_activity_at, cutoff)

    if hb_fresh and ws_fresh:
        return LivenessState.active_via_both
    if hb_fresh:
        return LivenessState.active_via_heartbeat
    if ws_fresh:
        return LivenessState.active_via_workspace
    return LivenessState.silent


def is_task_live(
    task: TaskRecord,
    *,
    now: datetime | None = None,
    freshness_seconds: float = DEFAULT_FRESHNESS_SECONDS,
) -> bool:
    """Return True if the task shows any recent liveness signal."""
    return classify_liveness(task, now=now, freshness_seconds=freshness_seconds) != LivenessState.silent


def detect_workspace_activity(repo_path: str, *, freshness_seconds: float = DEFAULT_FRESHNESS_SECONDS) -> str | None:
    """Detect fresh file-system activity in a repo working tree.

    Uses ``git status --porcelain --untracked-files=all`` to list dirty /
    untracked paths, then inspects their mtimes.

    Returns an ISO-8601 timestamp of the most-recent file change if any file
    was modified within the freshness window, or None if detection fails or
    no fresh activity is found.

    Degrades gracefully: returns None if the path is not a git checkout, if
    git is not installed, or if any other error occurs.
    """
    repo = Path(repo_path)
    if not repo.is_dir():
        return None

    try:
        result = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            capture_output=True,
            text=True,
            cwd=repo,
            timeout=10,
        )
        if result.returncode != 0:
            return None
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        return None

    now = datetime.now(UTC)
    cutoff = now - timedelta(seconds=freshness_seconds)
    latest_mtime: datetime | None = None

    for line in result.stdout.splitlines():
        # porcelain format: "XY filename" or "XY filename -> renamed"
        if len(line) < 4:
            continue
        filepath = line[3:].split(" -> ")[-1].strip()
        if not filepath:
            continue

        # Skip .git internals (should not appear, but guard anyway)
        if filepath.startswith(".git/") or filepath.startswith(".git\\"):
            continue

        full = repo / filepath
        try:
            st = full.stat()
        except (OSError, ValueError):
            continue

        mtime = datetime.fromtimestamp(st.st_mtime, tz=UTC)
        if mtime > cutoff:
            if latest_mtime is None or mtime > latest_mtime:
                latest_mtime = mtime

    # Also check .agpair/receipts directory if it exists
    receipts_dir = repo / ".agpair" / "receipts"
    if receipts_dir.is_dir():
        try:
            for entry in receipts_dir.iterdir():
                try:
                    st = entry.stat()
                    mtime = datetime.fromtimestamp(st.st_mtime, tz=UTC)
                    if mtime > cutoff:
                        if latest_mtime is None or mtime > latest_mtime:
                            latest_mtime = mtime
                except (OSError, ValueError):
                    continue
        except OSError:
            pass

    if latest_mtime is None:
        return None

    return latest_mtime.replace(microsecond=0).isoformat().replace("+00:00", "Z")
