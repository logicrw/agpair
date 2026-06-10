from pathlib import Path

from agpair.adoption import derive_adoption_decision
from agpair.completion import resolve_effective_task_policy


def test_report_with_protocol_warning_is_partial_adoptable(tmp_path: Path) -> None:
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

    assert decision.adoptable_result == "partial"
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
