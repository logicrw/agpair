from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass(frozen=True)
class TaskRecord:
    task_id: str
    repo_path: str
    phase: str
    antigravity_session_id: str | None
    attempt_no: int
    retry_count: int
    last_receipt_id: str | None
    stuck_reason: str | None
    retry_recommended: bool
    last_activity_at: str
    created_at: str
    updated_at: str
    last_heartbeat_at: str | None = None
    last_workspace_activity_at: str | None = None


@dataclass(frozen=True)
class JournalRecord:
    task_id: str
    source: str
    event: str
    body: str
    created_at: str
    classification: str = "normal"


@dataclass(frozen=True)
class WaiterRecord:
    waiter_id: str
    task_id: str
    command: str
    state: str  # 'waiting' | 'terminal'
    started_at: str
    last_poll_at: str
    finished_at: str | None = None
    outcome: str | None = None


TERMINAL_PHASES: frozenset[str] = frozenset(
    ("evidence_ready", "committed", "blocked", "stuck", "abandoned")
)


def utcnow_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
