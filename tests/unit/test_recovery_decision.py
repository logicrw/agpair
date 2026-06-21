from agpair.recovery import RecoveryInput, choose_recovery_decision


def test_usable_report_recommends_use_result() -> None:
    decision = choose_recovery_decision(
        RecoveryInput(
            task_id="TASK-1",
            controller="codex",
            current_executor="grok-cli",
            requested_executor=None,
            agent_result={
                "state": "usable",
                "controller_action": "use_result",
                "hard_blockers": [],
                "soft_warnings": [],
            },
            liveness_state=None,
            wait_outcome=None,
            execution_budget_exhausted=False,
            next_eligible_executor="antigravity-cli",
        )
    )

    assert decision.action == "use_result"
    assert decision.next_executor is None
    assert decision.command is None


def test_no_signal_with_next_executor_recommends_switch() -> None:
    decision = choose_recovery_decision(
        RecoveryInput(
            task_id="TASK-2",
            controller="codex",
            current_executor="grok-cli",
            requested_executor=None,
            agent_result={
                "state": "blocked",
                "controller_action": "retry_or_switch_executor",
                "hard_blockers": ["no_useful_executor_signal", "execution_budget_exhausted"],
                "soft_warnings": ["bootstrap_noise_only"],
            },
            liveness_state="silent",
            wait_outcome="soft_no_progress",
            execution_budget_exhausted=True,
            next_eligible_executor="antigravity-cli",
        )
    )

    assert decision.action == "switch_executor"
    assert decision.next_executor == "antigravity-cli"
    assert decision.command == "agpair task retry TASK-2 --from-block --executor antigravity-cli"


def test_direct_executor_request_does_not_silently_switch() -> None:
    decision = choose_recovery_decision(
        RecoveryInput(
            task_id="TASK-3",
            controller="codex",
            current_executor="antigravity-cli",
            requested_executor="antigravity-cli",
            agent_result={
                "state": "blocked",
                "controller_action": "retry_or_switch_executor",
                "hard_blockers": ["executor_response_timeout"],
                "soft_warnings": [],
            },
            liveness_state="silent",
            wait_outcome="strict_timeout",
            execution_budget_exhausted=True,
            next_eligible_executor="grok-cli",
        )
    )

    assert decision.action == "retry_same_executor"
    assert decision.next_executor == "grok-cli"
    assert decision.command == "agpair task retry TASK-3 --from-block --executor antigravity-cli"
    assert decision.alternative_command == "agpair task retry TASK-3 --from-block --executor grok-cli"


def test_auth_failure_recommends_repair_executor() -> None:
    decision = choose_recovery_decision(
        RecoveryInput(
            task_id="TASK-4",
            controller="codex",
            current_executor="claude-code",
            requested_executor=None,
            agent_result={
                "state": "blocked",
                "controller_action": "retry_or_switch_executor",
                "hard_blockers": ["executor_auth_required"],
                "soft_warnings": [],
            },
            liveness_state=None,
            wait_outcome="terminal_failure",
            execution_budget_exhausted=False,
            next_eligible_executor="grok-cli",
        )
    )

    assert decision.action == "repair_executor"
    assert decision.command == "agpair doctor --fresh"
    assert decision.next_executor == "grok-cli"
    assert decision.alternative_command == "agpair task retry TASK-4 --from-block --executor grok-cli"


def test_executor_probe_failure_recommends_repair_executor() -> None:
    decision = choose_recovery_decision(
        RecoveryInput(
            task_id="TASK-4B",
            controller="codex",
            current_executor="claude-code",
            requested_executor=None,
            agent_result={
                "state": "blocked",
                "controller_action": "retry_or_switch_executor",
                "hard_blockers": ["executor_probe_failed"],
                "soft_warnings": [],
            },
            liveness_state=None,
            wait_outcome="terminal_failure",
            execution_budget_exhausted=False,
            next_eligible_executor="grok-cli",
        )
    )

    assert decision.action == "repair_executor"
    assert decision.command == "agpair doctor --fresh"
    assert decision.alternative_command == "agpair task retry TASK-4B --from-block --executor grok-cli"


def test_approval_required_does_not_silently_switch() -> None:
    decision = choose_recovery_decision(
        RecoveryInput(
            task_id="TASK-5",
            controller="codex",
            current_executor="grok-cli",
            requested_executor=None,
            agent_result={
                "state": "blocked",
                "controller_action": "retry_or_switch_executor",
                "hard_blockers": ["approval_required"],
                "soft_warnings": [],
            },
            liveness_state=None,
            wait_outcome="terminal_failure",
            execution_budget_exhausted=False,
            next_eligible_executor="antigravity-cli",
        )
    )

    assert decision.action == "retry_same_executor"
    assert decision.command == "agpair task retry TASK-5 --from-block --executor grok-cli"
    assert decision.next_executor == "antigravity-cli"


def test_scope_violation_requires_inspection() -> None:
    decision = choose_recovery_decision(
        RecoveryInput(
            task_id="TASK-6",
            controller="codex",
            current_executor="grok-cli",
            requested_executor=None,
            agent_result={
                "state": "blocked",
                "controller_action": "inspect_evidence",
                "hard_blockers": ["scope_violation"],
                "soft_warnings": [],
            },
            liveness_state=None,
            wait_outcome="terminal_failure",
            execution_budget_exhausted=False,
            next_eligible_executor="antigravity-cli",
        )
    )

    assert decision.action == "inspect_evidence"
    assert decision.alternative_command == "agpair task retry TASK-6 --from-block --executor antigravity-cli"


def test_controller_lease_expiry_recommends_background_wait() -> None:
    decision = choose_recovery_decision(
        RecoveryInput(
            task_id="TASK-7",
            controller="codex",
            current_executor="grok-cli",
            requested_executor=None,
            agent_result={
                "state": "needs_review",
                "controller_action": "inspect_evidence",
                "hard_blockers": [],
                "soft_warnings": ["process_still_alive"],
            },
            liveness_state="stdout_active",
            wait_outcome="controller_lease_expired",
            execution_budget_exhausted=False,
            next_eligible_executor="antigravity-cli",
        )
    )

    assert decision.action == "wait_background"
    assert decision.command == "agpair task wait TASK-7 --json"


def test_recovery_uses_result_when_stdout_salvage_is_available() -> None:
    decision = choose_recovery_decision(
        RecoveryInput(
            task_id="TASK-8",
            controller="codex",
            current_executor="grok-cli",
            requested_executor=None,
            agent_result={
                "state": "needs_review",
                "controller_action": "use_result",
                "hard_blockers": [],
                "soft_warnings": ["terminal_receipt_missing"],
            },
            artifact_result={
                "state": "needs_review",
                "primary_artifact": "stdout_salvage",
                "global_hard_blockers": [],
            },
            liveness_state=None,
            wait_outcome=None,
            execution_budget_exhausted=False,
            next_eligible_executor="claude-code",
        )
    )

    assert decision.action == "use_result"
    assert decision.next_executor is None


def test_recovery_inspects_evidence_for_global_authorization_blocker() -> None:
    decision = choose_recovery_decision(
        RecoveryInput(
            task_id="TASK-9",
            controller="codex",
            current_executor="grok-cli",
            requested_executor=None,
            agent_result={
                "state": "blocked",
                "controller_action": "inspect_evidence",
                "hard_blockers": ["authorization_violation"],
                "soft_warnings": [],
            },
            artifact_result={
                "state": "blocked",
                "primary_artifact": "report",
                "global_hard_blockers": ["authorization_violation"],
            },
            liveness_state=None,
            wait_outcome=None,
            execution_budget_exhausted=False,
            next_eligible_executor="claude-code",
        )
    )

    assert decision.action == "inspect_evidence"


def test_recovery_can_route_from_artifact_result_when_agent_action_is_missing() -> None:
    decision = choose_recovery_decision(
        RecoveryInput(
            task_id="TASK-10",
            controller="codex",
            current_executor="grok-cli",
            requested_executor=None,
            agent_result={"state": "needs_review", "hard_blockers": [], "soft_warnings": []},
            artifact_result={
                "state": "needs_review",
                "primary_artifact": "report",
                "global_hard_blockers": [],
            },
            liveness_state=None,
            wait_outcome=None,
            execution_budget_exhausted=False,
            next_eligible_executor="claude-code",
        )
    )

    assert decision.action == "use_result"
