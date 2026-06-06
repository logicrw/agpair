from pathlib import Path

from agpair.completion import ExecutionEvidence, evaluate_completion, resolve_effective_task_policy
from agpair.transport import messages


def test_auto_readonly_resolves_to_report() -> None:
    policy = resolve_effective_task_policy(
        requested_completion_policy="auto",
        authorization_profile="local_readonly",
        body="Goal: audit. Required changes: none.",
        controller="codex",
    )
    assert policy.effective_completion_policy == "report"
    assert policy.report_only is True
    assert policy.requires_commit is False


def test_auto_commit_hint_requires_commit() -> None:
    policy = resolve_effective_task_policy(
        requested_completion_policy="auto",
        authorization_profile="local_mutating",
        body="Fix the bug and commit required.",
    )
    assert policy.effective_completion_policy == "commit"
    assert policy.requires_commit is True


def test_report_policy_finishes_without_commit_when_report_artifact_exists(tmp_path: Path) -> None:
    report = tmp_path / "report.md"
    report.write_text("done", encoding="utf-8")
    policy = resolve_effective_task_policy(
        requested_completion_policy="report",
        authorization_profile="local_readonly",
        body="Required changes: none",
    )
    decision = evaluate_completion(
        effective_policy=policy,
        receipt={"status": messages.EVIDENCE_PACK, "summary": "report complete", "payload": {}},
        evidence=ExecutionEvidence(report_path=str(report), receipt_valid=True),
        process_returncode=0,
    )
    assert decision.ok is True
    assert decision.phase == "ready_for_review"


def test_report_policy_blocks_when_report_artifact_missing(tmp_path: Path) -> None:
    stdout = tmp_path / "stdout.log"
    stdout.write_text("executor produced output but no report artifact", encoding="utf-8")
    policy = resolve_effective_task_policy(
        requested_completion_policy="report",
        authorization_profile="local_readonly",
        body="Required changes: none",
    )
    decision = evaluate_completion(
        effective_policy=policy,
        receipt={"status": messages.EVIDENCE_PACK, "summary": "no report", "payload": {}},
        evidence=ExecutionEvidence(stdout_path=str(stdout), receipt_valid=True),
        process_returncode=0,
    )
    assert decision.ok is False
    assert decision.phase == "blocked"
    assert decision.reason_code == "report_missing"
    assert decision.blocker_type == "report_missing"


def test_commit_policy_blocks_without_commit(tmp_path: Path) -> None:
    stdout = tmp_path / "stdout.log"
    stdout.write_text("done", encoding="utf-8")
    policy = resolve_effective_task_policy(
        requested_completion_policy="commit",
        authorization_profile="local_mutating",
        body="must commit",
    )
    decision = evaluate_completion(
        effective_policy=policy,
        receipt={"status": messages.EVIDENCE_PACK, "summary": "done", "payload": {}},
        evidence=ExecutionEvidence(stdout_path=str(stdout), receipt_valid=True),
        process_returncode=0,
    )
    assert decision.ok is False
    assert decision.phase == "blocked"
    assert decision.reason_code == "missing_commit_for_commit_policy"
