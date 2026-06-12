"""Unified liveness classification for acked tasks.

Provides:
  - ``LivenessState`` enum for classification
  - ``classify_liveness()`` to classify a task's current liveness
  - ``detect_workspace_activity()`` to probe a repo for fresh local file changes
  - ``is_task_live()`` convenience predicate used by intervention guards
"""
from __future__ import annotations

from dataclasses import dataclass
import enum
import json
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
    active_via_output = "active_via_output"
    active_via_both = "active_via_both"


@dataclass(frozen=True)
class SignalSummary:
    """Bounded observable signal state for controller decisions."""

    state: str
    last_signal_at: str | None
    last_signal_type: str | None
    stdout_bytes: int = 0
    stderr_bytes: int = 0
    bootstrap_noise_only: bool = False
    process_alive: bool | None = None
    controller_silence_seconds: float | None = None
    execution_budget_remaining_seconds: float | None = None

    def to_dict(self) -> dict[str, object | None]:
        return {
            "state": self.state,
            "last_signal_at": self.last_signal_at,
            "last_signal_type": self.last_signal_type,
            "stdout_bytes": self.stdout_bytes,
            "stderr_bytes": self.stderr_bytes,
            "bootstrap_noise_only": self.bootstrap_noise_only,
            "process_alive": self.process_alive,
            "controller_silence_seconds": self.controller_silence_seconds,
            "execution_budget_remaining_seconds": self.execution_budget_remaining_seconds,
        }


def _is_fresh(iso_timestamp: str | None, cutoff: datetime) -> bool:
    """Return True if *iso_timestamp* is non-null and newer than *cutoff*."""
    if not iso_timestamp:
        return False
    try:
        dt = datetime.fromisoformat(iso_timestamp.replace("Z", "+00:00"))
        return dt > cutoff
    except (ValueError, TypeError):
        return False


_BOOTSTRAP_STDERR_NOISE_MARKERS = (
    "plugin discovered",
    "plugin manifest",
    "skill name mismatch",
    "agent definition parse failure",
    "mcp-debugger",
    "broken pipe",
    "session registry sync",
    "grep timed out",
)


def is_bootstrap_noise(text: str) -> bool:
    """Return True when text contains only known startup/plugin noise."""
    lines = [line.strip().lower() for line in text.splitlines() if line.strip()]
    if not lines:
        return False
    return all(any(marker in line for marker in _BOOTSTRAP_STDERR_NOISE_MARKERS) for line in lines)


def _stderr_has_useful_signal(path: Path) -> bool:
    """Return True when stderr contains more than bootstrap/plugin noise."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")[-16384:]
    except OSError:
        return False
    return not is_bootstrap_noise(text)


def _parse_iso(iso_timestamp: str | None) -> datetime | None:
    if not iso_timestamp:
        return None
    try:
        dt = datetime.fromisoformat(str(iso_timestamp).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _session_dir(task: TaskRecord) -> Path | None:
    session_id = getattr(task, "executor_session_id", None) or getattr(task, "antigravity_session_id", None)
    return Path(str(session_id)) if session_id else None


def _read_log_tail(path: Path, *, max_chars: int = 8192) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[-max_chars:]
    except OSError:
        return ""


def _output_stat(task: TaskRecord, filename: str) -> tuple[int, datetime | None, bool]:
    session_dir = _session_dir(task)
    if session_dir is None:
        return 0, None, False
    path = session_dir / filename
    try:
        stat = path.stat()
    except OSError:
        return 0, None, False
    if not path.is_file() or stat.st_size <= 0:
        return 0, None, False
    modified = datetime.fromtimestamp(stat.st_mtime, UTC)
    if filename == "stderr.log":
        return int(stat.st_size), modified, is_bootstrap_noise(_read_log_tail(path))
    return int(stat.st_size), modified, False


def _process_alive_from_state(task: TaskRecord) -> bool | None:
    session_dir = _session_dir(task)
    if session_dir is None:
        return None
    state_path = session_dir / "state.json"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    value = state.get("is_process_alive")
    return value if isinstance(value, bool) else None


REPORT_ONLY_NO_PROGRESS_SECONDS: float = 180.0


def effective_no_progress_seconds(
    task: TaskRecord,
    configured_seconds: float = DEFAULT_FRESHNESS_SECONDS,
) -> float:
    """Return the no-progress freshness window for this task shape."""
    completion_policy = str(getattr(task, "completion_policy", "") or "").lower()
    authorization_profile = str(getattr(task, "authorization_profile", "") or "").lower()
    if completion_policy == "report" or authorization_profile == "local_readonly":
        return min(configured_seconds, REPORT_ONLY_NO_PROGRESS_SECONDS)
    return configured_seconds


def _latest_output_timestamp(task: TaskRecord) -> str | None:
    session_id = getattr(task, "executor_session_id", None) or getattr(task, "antigravity_session_id", None)
    if not session_id:
        return None
    attempt_dir = Path(str(session_id))
    latest: datetime | None = None
    for filename in ("stdout.log", "stderr.log"):
        path = attempt_dir / filename
        try:
            stat = path.stat()
        except OSError:
            continue
        if not path.is_file() or stat.st_size <= 0:
            continue
        if filename == "stderr.log" and not _stderr_has_useful_signal(path):
            continue
        modified = datetime.fromtimestamp(stat.st_mtime, UTC)
        if latest is None or modified > latest:
            latest = modified
    return latest.isoformat() if latest is not None else None


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
    output_fresh = _is_fresh(_latest_output_timestamp(task), cutoff)

    if hb_fresh and ws_fresh:
        return LivenessState.active_via_both
    if hb_fresh:
        return LivenessState.active_via_heartbeat
    if ws_fresh:
        return LivenessState.active_via_workspace
    if output_fresh:
        return LivenessState.active_via_output
    return LivenessState.silent


def is_task_live(
    task: TaskRecord,
    *,
    now: datetime | None = None,
    freshness_seconds: float = DEFAULT_FRESHNESS_SECONDS,
) -> bool:
    """Return True if the task shows any recent liveness signal."""
    return classify_liveness(task, now=now, freshness_seconds=freshness_seconds) != LivenessState.silent


def build_signal_summary(
    task: TaskRecord,
    *,
    now: datetime | None = None,
    freshness_seconds: float | None = None,
) -> SignalSummary:
    """Build a private-log-safe signal summary for status/watch consumers."""
    current = now or datetime.now(UTC)
    freshness = freshness_seconds or effective_no_progress_seconds(task)
    state = (
        classify_liveness(task, now=current, freshness_seconds=freshness).value
        if task.phase == "acked"
        else task.phase
    )
    heartbeat_at = _parse_iso(task.last_heartbeat_at)
    workspace_at = _parse_iso(task.last_workspace_activity_at)
    last_activity_at = _parse_iso(task.last_activity_at)
    stdout_bytes, stdout_at, _ = _output_stat(task, "stdout.log")
    stderr_bytes, stderr_at, stderr_bootstrap_only = _output_stat(task, "stderr.log")

    output_at = None
    output_type = None
    if stdout_at is not None:
        output_at = stdout_at
        output_type = "stdout"
    if stderr_at is not None and not stderr_bootstrap_only and (output_at is None or stderr_at > output_at):
        output_at = stderr_at
        output_type = "stderr"

    signal_candidates: list[tuple[str, datetime]] = []
    if heartbeat_at is not None:
        signal_candidates.append(("heartbeat", heartbeat_at))
    if workspace_at is not None:
        signal_candidates.append(("workspace", workspace_at))
    if output_at is not None and output_type is not None:
        signal_candidates.append((output_type, output_at))
    signal_candidates.sort(key=lambda item: item[1], reverse=True)
    last_signal_type = signal_candidates[0][0] if signal_candidates else None
    last_signal_at_dt = signal_candidates[0][1] if signal_candidates else last_activity_at

    controller_silence_seconds = (
        max(0.0, (current - last_signal_at_dt).total_seconds())
        if last_signal_at_dt is not None
        else None
    )
    execution_budget_remaining_seconds = None
    created_at = _parse_iso(task.created_at)
    if task.execution_budget_seconds is not None and created_at is not None:
        budget_deadline = created_at + timedelta(seconds=float(task.execution_budget_seconds))
        execution_budget_remaining_seconds = max(0.0, (budget_deadline - current).total_seconds())

    return SignalSummary(
        state=state,
        last_signal_at=last_signal_at_dt.isoformat() if last_signal_at_dt is not None else None,
        last_signal_type=last_signal_type,
        stdout_bytes=stdout_bytes,
        stderr_bytes=stderr_bytes,
        bootstrap_noise_only=bool(stderr_bytes and stderr_bootstrap_only and not stdout_bytes),
        process_alive=_process_alive_from_state(task),
        controller_silence_seconds=controller_silence_seconds,
        execution_budget_remaining_seconds=execution_budget_remaining_seconds,
    )


def recommend_controller_action(task: TaskRecord, signal: SignalSummary) -> str | None:
    """Suggest the controller's next action without mutating task state."""
    if task.phase in {"ready_for_review", "evidence_ready"} and not task.is_approved:
        return "verify_then_accept"
    if task.phase == "committed" or (
        task.phase in {"ready_for_review", "evidence_ready"} and task.is_approved
    ):
        return "inspect_and_accept"
    if task.phase in {"blocked", "stuck", "abandoned"}:
        return "retry_switch_or_native_fallback"
    if task.phase != "acked":
        return None
    if (
        task.background_ok
        and task.controller_wait_seconds is not None
        and signal.controller_silence_seconds is not None
        and signal.controller_silence_seconds >= float(task.controller_wait_seconds)
        and signal.state != LivenessState.silent.value
    ):
        return "detach_and_continue"
    if task.background_ok and signal.state == LivenessState.silent.value:
        return "inspect_logs_or_continue_background"
    if signal.state == LivenessState.silent.value:
        return "retry_or_switch_executor"
    return "continue_waiting"


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
