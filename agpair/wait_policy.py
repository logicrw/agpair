from __future__ import annotations

from dataclasses import dataclass


VALID_TASK_KINDS = {
    "quick_review",
    "deep_review",
    "implementation",
    "test_fix",
    "research",
    "smoke",
    "generic",
}

VALID_WAIT_POLICIES = {"terminal", "lease", "background", "strict"}


@dataclass(frozen=True)
class WaitBudget:
    task_kind: str
    wait_policy: str
    controller_wait_seconds: float | None
    execution_budget_seconds: float | None
    background_ok: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "task_kind": self.task_kind,
            "wait_policy": self.wait_policy,
            "controller_wait_seconds": self.controller_wait_seconds,
            "execution_budget_seconds": self.execution_budget_seconds,
            "background_ok": self.background_ok,
        }


DEFAULT_WAIT_BUDGETS: dict[str, WaitBudget] = {
    "quick_review": WaitBudget("quick_review", "lease", 120.0, 900.0, True),
    "deep_review": WaitBudget("deep_review", "lease", 240.0, 1800.0, True),
    "implementation": WaitBudget("implementation", "lease", 300.0, 3600.0, True),
    "test_fix": WaitBudget("test_fix", "lease", 300.0, 3600.0, True),
    "research": WaitBudget("research", "lease", 300.0, 5400.0, True),
    "smoke": WaitBudget("smoke", "strict", 300.0, 600.0, False),
    "generic": WaitBudget("generic", "terminal", None, None, False),
}


def normalize_task_kind(value: str | None) -> str:
    normalized = (value or "generic").strip().lower().replace("-", "_")
    if normalized not in VALID_TASK_KINDS:
        allowed = ", ".join(sorted(VALID_TASK_KINDS))
        raise ValueError(f"task kind must be one of: {allowed}")
    return normalized


def normalize_wait_policy(value: str | None, *, task_kind: str = "generic") -> str:
    normalized_task_kind = normalize_task_kind(task_kind)
    if value is None or not str(value).strip():
        return DEFAULT_WAIT_BUDGETS[normalized_task_kind].wait_policy
    normalized = str(value).strip().lower().replace("-", "_")
    if normalized not in VALID_WAIT_POLICIES:
        allowed = ", ".join(sorted(VALID_WAIT_POLICIES))
        raise ValueError(f"wait policy must be one of: {allowed}")
    return normalized


def _normalize_seconds(value: float | int | None, *, field: str, minimum: float) -> float | None:
    if value is None:
        return None
    numeric = float(value)
    if numeric < minimum:
        raise ValueError(f"{field} must be >= {minimum:g}")
    return numeric


def resolve_wait_budget(
    *,
    task_kind: str | None = None,
    wait_policy: str | None = None,
    controller_wait_seconds: float | int | None = None,
    execution_budget_seconds: float | int | None = None,
    background_ok: bool | None = None,
) -> WaitBudget:
    normalized_task_kind = normalize_task_kind(task_kind)
    selected_wait_policy = normalize_wait_policy(wait_policy, task_kind=normalized_task_kind)
    defaults = DEFAULT_WAIT_BUDGETS[normalized_task_kind]
    selected_background_ok = defaults.background_ok if background_ok is None else bool(background_ok)
    if selected_wait_policy in {"terminal", "strict"}:
        selected_background_ok = False
    return WaitBudget(
        task_kind=normalized_task_kind,
        wait_policy=selected_wait_policy,
        controller_wait_seconds=(
            _normalize_seconds(controller_wait_seconds, field="controller wait seconds", minimum=0)
            if controller_wait_seconds is not None
            else defaults.controller_wait_seconds
        ),
        execution_budget_seconds=(
            _normalize_seconds(execution_budget_seconds, field="execution budget seconds", minimum=1)
            if execution_budget_seconds is not None
            else defaults.execution_budget_seconds
        ),
        background_ok=selected_background_ok,
    )
