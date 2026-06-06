from __future__ import annotations

from dataclasses import asdict
from typing import Any

from agpair.executors.lifecycle import lifecycle_decision
from agpair.executors.policy import CANONICAL_EXECUTOR_IDS, EXECUTOR_SPECS, ExecutorSpec, executor_health_snapshot
from agpair.models import ExecutorSafetyMetadata


def registered_executor_ids() -> tuple[str, ...]:
    return CANONICAL_EXECUTOR_IDS


def registered_executor_specs() -> tuple[ExecutorSpec, ...]:
    return tuple(sorted(EXECUTOR_SPECS.values(), key=lambda item: item.default_priority))


def executor_spec(executor_id: str) -> ExecutorSpec:
    try:
        return EXECUTOR_SPECS[executor_id]
    except KeyError as exc:
        allowed = ", ".join(registered_executor_ids())
        raise ValueError(f"executor must be one of: {allowed}") from exc


def executor_safety_metadata(executor_id: str) -> ExecutorSafetyMetadata:
    spec = executor_spec(executor_id)
    return ExecutorSafetyMetadata(
        is_mutating=spec.is_mutating,
        is_concurrency_safe=spec.is_concurrency_safe,
        requires_human_interaction=spec.requires_human_interaction,
    )


def executor_profile(executor_id: str) -> dict[str, Any]:
    return executor_spec(executor_id).to_dict()


def executor_profiles() -> dict[str, dict[str, Any]]:
    return {spec.executor_id: spec.to_dict() for spec in registered_executor_specs()}


def executor_lifecycle_status(executor_id: str) -> str:
    spec = executor_spec(executor_id)
    return lifecycle_decision(
        executor_id=executor_id,
        lifecycle_status=spec.lifecycle_status,
        replacement_executor=spec.replacement_executor,
    ).lifecycle_status


def executor_allows_new_tasks(executor_id: str) -> bool:
    spec = executor_spec(executor_id)
    decision = lifecycle_decision(
        executor_id=executor_id,
        lifecycle_status=spec.lifecycle_status,
        replacement_executor=spec.replacement_executor,
    )
    return spec.enabled_by_default and decision.allowed_for_new_tasks


def active_executor_ids() -> tuple[str, ...]:
    return tuple(spec.executor_id for spec in registered_executor_specs() if executor_allows_new_tasks(spec.executor_id))


def executor_start_blocker(executor_id: str, *, require_available: bool = False) -> dict[str, Any] | None:
    spec = executor_spec(executor_id)
    lifecycle = lifecycle_decision(
        executor_id=executor_id,
        lifecycle_status=spec.lifecycle_status,
        replacement_executor=spec.replacement_executor,
    )
    if not lifecycle.allowed_for_new_tasks:
        return lifecycle.to_dict()
    if not spec.enabled_by_default:
        return {
            "executor_id": executor_id,
            "blocker_type": "executor_disabled",
            "reason": f"executor {executor_id} is disabled for new task dispatch",
            "replacement_executor": spec.replacement_executor,
        }
    if require_available:
        health = executor_health_snapshot(run_launch_probe=True)[executor_id]
        if not health["available"]:
            reason = health.get("last_error_excerpt")
            if not reason:
                reason = (
                    f"executor {executor_id} is unavailable; install {health['binary_name']} "
                    f"or set {health['env_var']}"
                )
            return {
                "executor_id": executor_id,
                "blocker_type": health.get("last_failure_type") or "executor_unavailable",
                "reason": str(reason),
                "replacement_executor": spec.replacement_executor,
            }
    return None


def executor_diagnostic_rows(*, run_launch_probe: bool = False) -> dict[str, dict[str, Any]]:
    health = executor_health_snapshot(run_launch_probe=run_launch_probe)
    return {
        executor_id: {
            **executor_profile(executor_id),
            "health": health[executor_id],
            "safety_metadata": asdict(executor_safety_metadata(executor_id)),
        }
        for executor_id in registered_executor_ids()
    }
