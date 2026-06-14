from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Mapping

RecoveryAction = Literal[
    "use_result",
    "review_then_apply",
    "inspect_evidence",
    "wait_background",
    "retry_same_executor",
    "switch_executor",
    "native_fallback",
    "repair_executor",
]


@dataclass(frozen=True, slots=True)
class RecoveryInput:
    task_id: str
    controller: str | None
    current_executor: str | None
    requested_executor: str | None
    agent_result: Mapping[str, Any] | None
    liveness_state: str | None
    wait_outcome: str | None
    execution_budget_exhausted: bool
    next_eligible_executor: str | None = None


@dataclass(frozen=True, slots=True)
class RecoveryDecision:
    action: RecoveryAction
    reason: str
    next_executor: str | None = None
    command: str | None = None
    alternative_command: str | None = None

    def to_dict(self) -> dict[str, str | None]:
        return {
            "action": self.action,
            "reason": self.reason,
            "next_executor": self.next_executor,
            "command": self.command,
            "alternative_command": self.alternative_command,
        }


def _strings(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(str(item) for item in value if item)


def _retry_command(task_id: str, executor: str | None) -> str:
    if executor:
        return f"agpair task retry {task_id} --from-block --executor {executor}"
    return f"agpair task retry {task_id} --from-block"


def _has_any_blocker(blockers: set[str], names: set[str]) -> bool:
    return bool(blockers.intersection(names))


def choose_recovery_decision(data: RecoveryInput) -> RecoveryDecision:
    agent = data.agent_result or {}
    state = str(agent.get("state") or "")
    controller_action = str(agent.get("controller_action") or "")
    blockers = set(_strings(agent.get("hard_blockers")))

    match controller_action:
        case "use_result":
            return RecoveryDecision(
                action="use_result",
                reason="External executor produced usable controller evidence.",
            )
        case "review_then_apply":
            return RecoveryDecision(
                action="review_then_apply",
                reason="External executor produced code or diff evidence that must be reviewed before applying.",
            )
        case "native_fallback":
            return RecoveryDecision(
                action="native_fallback",
                reason="External workflow recommends controller-native fallback.",
            )
        case _:
            pass

    if data.wait_outcome == "controller_lease_expired" and not data.execution_budget_exhausted:
        return RecoveryDecision(
            action="wait_background",
            reason="Controller wait lease expired, but execution budget is still available.",
            command=f"agpair task wait {data.task_id} --json",
        )

    if _has_any_blocker(
        blockers,
        {
            "executor_auth_required",
            "executor_auth_failed",
            "executor_probe_failed",
            "executor_unavailable",
            "executor_quota_exhausted",
        },
    ):
        return RecoveryDecision(
            action="repair_executor",
            reason="Executor setup, authentication, quota, or provider health is unhealthy.",
            next_executor=data.next_eligible_executor,
            command="agpair doctor --fresh",
            alternative_command=_retry_command(data.task_id, data.next_eligible_executor),
        )

    if _has_any_blocker(blockers, {"approval_required", "authorization_profile_insufficient"}):
        return RecoveryDecision(
            action="retry_same_executor",
            reason="The task exceeded its dispatch-time authorization and needs a fresh attempt.",
            next_executor=data.next_eligible_executor,
            command=_retry_command(data.task_id, data.current_executor),
            alternative_command=_retry_command(data.task_id, data.next_eligible_executor),
        )

    if _has_any_blocker(blockers, {"scope_violation", "authorization_violation"}):
        return RecoveryDecision(
            action="inspect_evidence",
            reason="Executor crossed a task boundary; inspect evidence before reusing any output.",
            next_executor=data.next_eligible_executor,
            alternative_command=_retry_command(data.task_id, data.next_eligible_executor),
        )

    no_useful_signal = _has_any_blocker(
        blockers,
        {
            "no_useful_executor_signal",
            "terminal_receipt_missing",
            "report_missing",
            "execution_budget_exhausted",
            "executor_response_timeout",
            "no_progress_budget_exceeded",
        },
    )

    if state == "blocked" and no_useful_signal:
        if data.requested_executor:
            return RecoveryDecision(
                action="retry_same_executor",
                reason="The explicitly requested executor did not produce useful evidence; AGPair must not silently switch it.",
                next_executor=data.next_eligible_executor,
                command=_retry_command(data.task_id, data.current_executor),
                alternative_command=_retry_command(data.task_id, data.next_eligible_executor),
            )
        if data.next_eligible_executor:
            return RecoveryDecision(
                action="switch_executor",
                reason="The current executor produced no useful evidence within its budget.",
                next_executor=data.next_eligible_executor,
                command=_retry_command(data.task_id, data.next_eligible_executor),
            )
        return RecoveryDecision(
            action="native_fallback",
            reason="No eligible external executor remains; use the controller's native helper or handle directly.",
        )

    if controller_action == "inspect_evidence":
        return RecoveryDecision(
            action="inspect_evidence",
            reason="External executor produced evidence that needs controller inspection.",
            next_executor=data.next_eligible_executor,
        )

    if state == "blocked":
        return RecoveryDecision(
            action="retry_same_executor",
            reason="Executor is blocked but may recover with a fresh attempt.",
            command=_retry_command(data.task_id, data.current_executor),
            alternative_command=_retry_command(data.task_id, data.next_eligible_executor),
            next_executor=data.next_eligible_executor,
        )

    return RecoveryDecision(
        action="inspect_evidence",
        reason="AGPair could not classify the result confidently; inspect artifacts before deciding.",
        next_executor=data.next_eligible_executor,
    )
