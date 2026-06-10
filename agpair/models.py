from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import enum


class ContinuationCapability(str, enum.Enum):
    SAME_SESSION = "same_session"
    FRESH_RESUME_FIRST = "fresh_resume_first"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True)
class ExecutorSafetyMetadata:
    is_mutating: bool
    is_concurrency_safe: bool
    requires_human_interaction: bool


VALID_AUTHORIZATION_PROFILES: tuple[str, ...] = (
    "local_readonly",
    "local_mutating",
    "local_test_heavy",
    "external_network",
)

_AUTHORIZATION_PROFILE_SUMMARIES: dict[str, str] = {
    "local_readonly": (
        "Allowed actions: inspect repository files, run read-only commands, and report findings. "
        "Denied actions: edit files, create commits, install dependencies, or access external network services."
    ),
    "local_mutating": (
        "Allowed actions: inspect and edit repository-local files, run focused tests, and prepare commits when requested. "
        "Denied actions: destructive cleanup, credential changes, production deploys, or broad external network access."
    ),
    "local_test_heavy": (
        "Allowed actions: inspect and edit repository-local files, run broad test/build/verification commands, and collect local logs. "
        "Denied actions: destructive cleanup, credential changes, production deploys, or unrelated external network access."
    ),
    "external_network": (
        "Allowed actions: inspect and edit repository-local files, run tests, and use external network access needed for the task. "
        "Denied actions: credential exfiltration, production deploys, destructive cleanup, or changes outside the authorized scope."
    ),
}


def validate_authorization_profile(profile: str | None) -> str:
    normalized = (profile or "local_mutating").strip().lower()
    if normalized in VALID_AUTHORIZATION_PROFILES:
        return normalized
    allowed = ", ".join(VALID_AUTHORIZATION_PROFILES)
    raise ValueError(f"authorization profile must be one of: {allowed}")


def authorization_profile_summary(profile: str | None) -> str:
    return _AUTHORIZATION_PROFILE_SUMMARIES[validate_authorization_profile(profile)]


@dataclass(frozen=True)
class TaskRecord:
    task_id: str
    repo_path: str
    execution_repo_path: str | None
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
    client_idempotency_key: str | None = None
    executor_backend: str | None = None
    depends_on: str | None = None
    isolated_worktree: bool = False
    setup_commands: str | None = None
    teardown_commands: str | None = None
    env_vars: str | None = None
    worktree_boundary: str | None = None
    spotlight_testing: bool = False
    broad_repo_path_override: bool = False
    completion_policy: str = "auto"
    terminal_source: str | None = None
    terminal_receipt_json: str | None = None
    is_approved: bool = False
    authorization_profile: str = "local_mutating"
    authorization_summary: str | None = None
    executor_session_id: str | None = None
    workflow_id: str | None = None
    workflow_node_id: str | None = None
    parent_task_id: str | None = None
    child_role: str | None = None


@dataclass(frozen=True)
class TaskAttemptRecord:
    task_id: str
    attempt_no: int
    executor_backend: str | None
    authorization_profile: str
    requested_completion_policy: str
    effective_policy_json: str | None
    environment_mode: str
    environment_mode_source: str
    skill_policy: str
    mcp_policy: str
    protocol_warnings_json: str
    protocol_errors_json: str
    adoptable_result: str
    adoption_evidence_json: str
    controller_rework_json: str
    dirty_snapshot_mode: str
    dirty_snapshot_json: str
    dirty_snapshot_applied: bool
    executor_session_id: str | None
    phase: str
    terminal_receipt_json: str | None
    terminal_source: str | None
    started_at: str
    finished_at: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class TaskArtifactRecord:
    task_id: str
    attempt_no: int
    artifact_type: str
    path: str
    size_bytes: int | None
    sha256: str | None
    created_at: str


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
    ("ready_for_review", "evidence_ready", "committed", "blocked", "stuck", "abandoned")
)

SUCCESS_REVIEW_PHASES: frozenset[str] = frozenset(
    ("ready_for_review", "evidence_ready", "committed")
)


def utcnow_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def a2a_state_hint_from_phase(phase: str, blocker_type: str | None = None) -> str:
    """Map an agpair phase to the closest A2A TaskState hint."""
    if phase == "blocked" and blocker_type == "auth":
        return "auth-required"
    mapping = {
        "new": "submitted",
        "queued_unclaimed": "submitted",
        "provider_consumed_no_ack": "working",
        "running_without_receipt": "working",
        "acked": "working",
        "ready_for_review": "input-required",
        "evidence_ready": "input-required",
        "committed": "input-required",
        "blocked": "failed",
        "stuck": "failed",
        "abandoned": "canceled",
    }
    return mapping.get(phase, "unknown")
