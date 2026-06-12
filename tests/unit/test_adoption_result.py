from pathlib import Path

from agpair.adoption import derive_adoption_decision
from agpair.completion import resolve_effective_task_policy


def test_report_with_protocol_warning_remains_adoptable(tmp_path: Path) -> None:
    report = tmp_path / "report.md"
    report.write_text("usable", encoding="utf-8")
    policy = resolve_effective_task_policy(
        requested_completion_policy="report",
        authorization_profile="local_readonly",
        body="Required changes: none",
    )

    decision = derive_adoption_decision(
        effective_policy=policy,
        receipt={"status": "EVIDENCE_PACK", "payload": {"report": "usable"}},
        report_path=str(report),
        protocol_warnings=("schema_version_alias",),
    )

    assert decision.adoptable_result == "yes"
    assert "schema_version_alias" in decision.warnings
    assert decision.evidence.has_report is True


def test_report_without_report_is_not_adoptable() -> None:
    policy = resolve_effective_task_policy(
        requested_completion_policy="report",
        authorization_profile="local_readonly",
        body="Required changes: none",
    )

    decision = derive_adoption_decision(
        effective_policy=policy,
        receipt={"status": "EVIDENCE_PACK", "payload": {}},
    )

    assert decision.adoptable_result == "no"
    assert decision.blockers == ("report_missing",)


def test_blocked_report_receipt_is_not_adoptable_even_with_report() -> None:
    policy = resolve_effective_task_policy(
        requested_completion_policy="report",
        authorization_profile="local_readonly",
        body="Required changes: none",
    )

    decision = derive_adoption_decision(
        effective_policy=policy,
        receipt={
            "status": "BLOCKED",
            "payload": {
                "blocker_type": "executor_waiting_for_input",
                "report": "partial notes before blocking",
            },
        },
    )

    assert decision.adoptable_result == "no"
    assert decision.blockers == ("executor_waiting_for_input",)
    assert decision.evidence.has_report is True


def test_evidence_with_changed_files_and_validation_is_adoptable() -> None:
    policy = resolve_effective_task_policy(
        requested_completion_policy="evidence",
        authorization_profile="local_mutating",
    )

    decision = derive_adoption_decision(
        effective_policy=policy,
        receipt={
            "status": "EVIDENCE_PACK",
            "payload": {
                "changed_files": ["agpair/terminal_receipts.py"],
                "validation_not_run": "fixture",
                "scope_violations": [],
            },
        },
        changed_files_present=True,
    )

    assert decision.adoptable_result == "yes"
    assert decision.blockers == ()


def test_low_risk_protocol_warning_does_not_demote_applyable_implementation() -> None:
    policy = resolve_effective_task_policy(
        requested_completion_policy="evidence",
        authorization_profile="local_mutating",
    )

    decision = derive_adoption_decision(
        effective_policy=policy,
        receipt={
            "status": "EVIDENCE_PACK",
            "payload": {
                "changed_files": ["agpair/example.py"],
                "validation_not_run": "fixture",
                "scope_violations": [],
            },
        },
        receipt_path="receipt.json",
        changed_files_present=True,
        git_status_summary=" M agpair/example.py",
        scope_validation={"ok": True},
        protocol_warnings=("wrapped_text_json",),
        protocol_errors=(),
        controller_rework="none",
    )

    assert decision.adoptable_result == "yes"
    assert decision.blockers == ()
    assert decision.warnings == ("wrapped_text_json",)
    assert decision.agent_result is not None
    assert decision.agent_result.state == "usable"
    assert decision.agent_result.controller_action == "review_then_apply"
    assert decision.to_dict()["agent_result"]["soft_warnings"] == ["wrapped_text_json"]


def test_evidence_missing_validation_is_partial() -> None:
    policy = resolve_effective_task_policy(
        requested_completion_policy="evidence",
        authorization_profile="local_mutating",
    )

    decision = derive_adoption_decision(
        effective_policy=policy,
        receipt={
            "status": "EVIDENCE_PACK",
            "payload": {
                "changed_files": ["agpair/terminal_receipts.py"],
                "scope_violations": [],
            },
        },
        changed_files_present=True,
    )

    assert decision.adoptable_result == "partial"
    assert "validation_missing" in decision.blockers


def test_apply_check_pass_with_missing_validation_needs_review() -> None:
    policy = resolve_effective_task_policy(
        requested_completion_policy="evidence",
        authorization_profile="local_mutating",
    )

    decision = derive_adoption_decision(
        effective_policy=policy,
        receipt={
            "status": "EVIDENCE_PACK",
            "payload": {
                "worktree_diff": {
                    "has_patch": True,
                    "changed_files": ["agpair/example.py"],
                    "apply_check_ok": True,
                    "apply_check_reason": None,
                },
                "scope_violations": [],
            },
        },
        changed_files_present=True,
        scope_validation={"ok": True},
    )

    assert decision.adoptable_result == "partial"
    assert decision.agent_result is not None
    assert decision.agent_result.state == "needs_review"
    assert decision.agent_result.controller_action == "review_then_apply"
    assert "validation_missing" in decision.blockers


def test_apply_check_failure_blocks_adoption() -> None:
    policy = resolve_effective_task_policy(
        requested_completion_policy="evidence",
        authorization_profile="local_mutating",
    )

    decision = derive_adoption_decision(
        effective_policy=policy,
        receipt={
            "status": "EVIDENCE_PACK",
            "payload": {
                "changed_files": ["agpair/example.py"],
                "validation_not_run": "fixture",
                "worktree_diff": {
                    "has_patch": True,
                    "changed_files": ["agpair/example.py"],
                    "apply_check_ok": False,
                    "apply_check_reason": "apply_conflict",
                },
                "scope_violations": [],
            },
        },
        changed_files_present=True,
        scope_validation={"ok": True},
    )

    assert decision.adoptable_result == "no"
    assert decision.agent_result is not None
    assert decision.agent_result.state == "blocked"
    assert "apply_conflict" in decision.blockers


def test_evidence_scope_validation_tuple_missing_declared_file_blocks_adoption() -> None:
    policy = resolve_effective_task_policy(
        requested_completion_policy="evidence",
        authorization_profile="local_mutating",
    )

    decision = derive_adoption_decision(
        effective_policy=policy,
        receipt={
            "status": "EVIDENCE_PACK",
            "payload": {
                "changed_files": ["tests/fixtures/external_executor_smoke/agent.smoke"],
                "validation_not_run": "fixture",
                "scope_violations": [],
            },
        },
        scope_validation={
            "ok": False,
            "missing_declared_files": ("tests/fixtures/external_executor_smoke/agent.smoke",),
            "undeclared_changed_files": (),
            "forbidden_changed_files": (),
        },
    )

    assert decision.adoptable_result == "partial"
    assert "changed_files_not_present" in decision.blockers
    assert "missing_declared_changes" in decision.blockers
    assert decision.evidence.has_missing_declared_changes is True
