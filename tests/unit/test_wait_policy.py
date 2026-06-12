from __future__ import annotations

import pytest

from agpair.wait_policy import DEFAULT_WAIT_BUDGETS, normalize_task_kind, normalize_wait_policy, resolve_wait_budget


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("quick-review", "quick_review"),
        ("deep_review", "deep_review"),
        ("implementation", "implementation"),
        ("test-fix", "test_fix"),
        ("research", "research"),
        ("smoke", "smoke"),
        (None, "generic"),
    ],
)
def test_normalize_task_kind_aliases(raw, expected):
    assert normalize_task_kind(raw) == expected


def test_resolve_implementation_budget_defaults_to_background_lease():
    budget = resolve_wait_budget(task_kind="implementation", wait_policy=None)

    assert budget.task_kind == "implementation"
    assert budget.wait_policy == "lease"
    assert budget.controller_wait_seconds == DEFAULT_WAIT_BUDGETS["implementation"].controller_wait_seconds
    assert budget.execution_budget_seconds == DEFAULT_WAIT_BUDGETS["implementation"].execution_budget_seconds
    assert budget.background_ok is True


def test_strict_wait_policy_disables_background_detach():
    budget = resolve_wait_budget(task_kind="implementation", wait_policy="strict")

    assert budget.wait_policy == "strict"
    assert budget.background_ok is False


def test_background_policy_can_be_selected_explicitly():
    budget = resolve_wait_budget(task_kind="research", wait_policy="background")

    assert budget.wait_policy == "background"
    assert budget.background_ok is True


def test_unknown_task_kind_rejected():
    with pytest.raises(ValueError, match="task kind"):
        normalize_task_kind("large-magic")


def test_unknown_wait_policy_rejected():
    with pytest.raises(ValueError, match="wait policy"):
        normalize_wait_policy("maybe", task_kind="generic")


def test_budget_overrides_are_validated():
    budget = resolve_wait_budget(
        task_kind="quick_review",
        controller_wait_seconds=5,
        execution_budget_seconds=10,
        background_ok=False,
    )

    assert budget.controller_wait_seconds == 5.0
    assert budget.execution_budget_seconds == 10.0
    assert budget.background_ok is False

    with pytest.raises(ValueError, match="execution budget seconds"):
        resolve_wait_budget(task_kind="quick_review", execution_budget_seconds=0)
