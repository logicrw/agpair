from agpair.executors.task_contract import body_with_task_contract


def test_report_completion_policy_injects_report_only_contract() -> None:
    prompt = body_with_task_contract(
        "TASK-REPORT",
        "Goal: review\nScope: repo\nRequired changes: none\nExit criteria: report",
        authorization_profile="local_mutating",
        completion_policy="report",
    )

    assert "Report-only outcome requirements" in prompt
    assert "Choose the inspection strategy yourself" in prompt


def test_mutating_auto_task_omits_report_only_contract() -> None:
    prompt = body_with_task_contract(
        "TASK-MUTATE",
        "Goal: edit\nScope: repo\nRequired changes: patch\nExit criteria: tests",
        authorization_profile="local_mutating",
        completion_policy="auto",
    )

    assert "Report-only outcome requirements" not in prompt


def test_body_readonly_hint_injects_report_only_contract() -> None:
    prompt = body_with_task_contract(
        "TASK-HINT",
        "Goal: inspect\nScope: repo\nRequired changes: do not edit files\nExit criteria: report",
        authorization_profile="local_mutating",
        completion_policy="auto",
    )

    assert "Report-only outcome requirements" in prompt


def test_body_with_coordination_role_injects_advisory_role_hint() -> None:
    prompt = body_with_task_contract(
        "TASK-THINK",
        "Goal: inspect\nScope: repo\nRequired changes: none\nExit criteria: report",
        authorization_profile="local_readonly",
        completion_policy="report",
        coordination_role="thinker",
    )

    assert "Coordination role requirements" in prompt
    assert "Act as a thinker" in prompt
    assert "Structured terminal receipt JSON requirements" in prompt
