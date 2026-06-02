from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Mapping, Sequence


@dataclass(frozen=True)
class ExecutorHealth:
    executor_id: str
    available: bool
    binary_path: str | None = None
    last_checked_at: datetime | None = None
    last_success_at: datetime | None = None
    recent_failure_count: int = 0
    consecutive_stuck_count: int = 0
    malformed_receipt_count: int = 0
    last_error_excerpt: str | None = None


def executor_is_eligible(health: ExecutorHealth) -> bool:
    if not health.available:
        return False
    if health.malformed_receipt_count >= 3:
        return False
    if health.consecutive_stuck_count >= 2:
        return False
    if health.recent_failure_count >= 5:
        return False
    return True


def choose_healthy_executor(
    candidates: Sequence[str],
    health_by_executor: Mapping[str, ExecutorHealth],
    *,
    explicit_executor: str | None = None,
) -> str | None:
    if explicit_executor is not None:
        health = health_by_executor.get(explicit_executor)
        return explicit_executor if health and executor_is_eligible(health) else None

    for executor_id in candidates:
        health = health_by_executor.get(executor_id)
        if health and executor_is_eligible(health):
            return executor_id
    return None
