from __future__ import annotations

from dataclasses import dataclass

ACTIVE = "active"
DISABLED = "disabled"
DEPRECATED = "deprecated"
REMOVED = "removed"

VALID_LIFECYCLE_STATUSES = frozenset({ACTIVE, DISABLED, DEPRECATED, REMOVED})


@dataclass(frozen=True)
class ExecutorLifecycleDecision:
    executor_id: str
    lifecycle_status: str
    allowed_for_new_tasks: bool
    blocker_type: str | None = None
    reason: str | None = None
    replacement_executor: str | None = None

    def to_dict(self) -> dict[str, object | None]:
        return {
            "executor_id": self.executor_id,
            "lifecycle_status": self.lifecycle_status,
            "allowed_for_new_tasks": self.allowed_for_new_tasks,
            "blocker_type": self.blocker_type,
            "reason": self.reason,
            "replacement_executor": self.replacement_executor,
        }


def validate_lifecycle_status(status: str | None) -> str:
    normalized = (status or ACTIVE).strip().lower()
    if normalized not in VALID_LIFECYCLE_STATUSES:
        allowed = ", ".join(sorted(VALID_LIFECYCLE_STATUSES))
        raise ValueError(f"executor lifecycle status must be one of: {allowed}")
    return normalized


def lifecycle_decision(
    *,
    executor_id: str,
    lifecycle_status: str | None,
    replacement_executor: str | None = None,
) -> ExecutorLifecycleDecision:
    status = validate_lifecycle_status(lifecycle_status)
    if status == ACTIVE:
        return ExecutorLifecycleDecision(
            executor_id=executor_id,
            lifecycle_status=status,
            allowed_for_new_tasks=True,
            replacement_executor=replacement_executor,
        )
    reason = f"executor {executor_id} is {status} and is not eligible for new task dispatch"
    if replacement_executor:
        reason += f"; use {replacement_executor} instead"
    return ExecutorLifecycleDecision(
        executor_id=executor_id,
        lifecycle_status=status,
        allowed_for_new_tasks=False,
        blocker_type=f"executor_{status}",
        reason=reason,
        replacement_executor=replacement_executor,
    )
