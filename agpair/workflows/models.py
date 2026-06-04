from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class WorkflowPhase(StrEnum):
    NEW = "new"
    RUNNING = "running"
    READY_FOR_REVIEW = "ready_for_review"
    BLOCKED = "blocked"
    STUCK = "stuck"
    ABANDONED = "abandoned"
    CANCELLED = "cancelled"


class NodePhase(StrEnum):
    PENDING = "pending"
    DISPATCHING = "dispatching"
    RUNNING = "running"
    READY_FOR_REVIEW = "ready_for_review"
    BLOCKED = "blocked"
    STUCK = "stuck"
    SKIPPED = "skipped"
    ABANDONED = "abandoned"
    CANCELLED = "cancelled"


WORKFLOW_PHASES = frozenset(item.value for item in WorkflowPhase) | {"created"}
NODE_KINDS = frozenset({"task", "synthesis", "verification", "gate"})
NODE_PHASES = frozenset(item.value for item in NodePhase)
SUCCESS_NODE_PHASES = frozenset({WorkflowPhase.READY_FOR_REVIEW.value})
FAILED_NODE_PHASES = frozenset({
    NodePhase.BLOCKED.value,
    NodePhase.STUCK.value,
    NodePhase.ABANDONED.value,
    NodePhase.CANCELLED.value,
})


@dataclass(frozen=True)
class WorkflowRecord:
    workflow_id: str
    repo_path: str
    controller: str
    name: str
    phase: str
    manifest_json: str
    limits_json: str
    result_json: str | None
    evidence_path: str | None
    created_at: str
    updated_at: str
    started_at: str | None
    finished_at: str | None
    cancelled_at: str | None
    stuck_reason: str | None
    error: str | None

    def limits(self) -> dict[str, Any]:
        try:
            value = json.loads(self.limits_json or "{}")
        except json.JSONDecodeError:
            return {}
        return value if isinstance(value, dict) else {}


@dataclass(frozen=True)
class WorkflowNodeRecord:
    workflow_id: str
    node_id: str
    kind: str
    role: str | None
    phase: str
    task_id: str | None
    depends_on_json: str
    authorization_profile: str
    requested_completion_policy: str
    effective_policy_json: str
    executor_backend: str | None
    attempt_no: int
    max_retries: int
    allow_partial: bool
    isolated_worktree: bool
    body: str | None
    result_json: str | None
    evidence_json: str | None
    last_error: str | None
    error: str | None
    created_at: str
    updated_at: str
    started_at: str | None
    finished_at: str | None

    @property
    def completion_policy(self) -> str:
        return self.requested_completion_policy

    @property
    def depends_on(self) -> str:
        return self.depends_on_json

    def depends_list(self) -> list[str]:
        try:
            value = json.loads(self.depends_on_json or "[]")
        except json.JSONDecodeError:
            return []
        return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []
