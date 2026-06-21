from pathlib import Path

from agpair.artifact_classification import classify_artifacts


def test_report_artifact_is_usable(tmp_path: Path) -> None:
    report = tmp_path / "report.md"
    report.write_text("Recommendation: usable report.", encoding="utf-8")

    result = classify_artifacts(
        receipt={"status": "EVIDENCE_PACK", "payload": {"report": "Recommendation: usable report."}},
        report_path=str(report),
        stdout_path=None,
        receipt_path=None,
        git_status_summary=None,
        scope_validation=None,
        protocol_warnings=(),
        protocol_errors=(),
    )

    assert result.state == "usable"
    assert result.primary_artifact == "report"
    assert result.by_kind("report") is not None
    assert "controller_action" not in result.to_dict()


def test_stdout_completed_report_is_salvage_candidate(tmp_path: Path) -> None:
    stdout = tmp_path / "stdout.log"
    stdout.write_text("结论：这份报告可以作为第二意见使用。\n\n- 发现 A\n- 证据 B", encoding="utf-8")

    result = classify_artifacts(
        receipt={"status": "BLOCKED", "payload": {"blocker_type": "report_output_missing"}},
        report_path=None,
        stdout_path=str(stdout),
        receipt_path=None,
        git_status_summary=None,
        scope_validation=None,
        protocol_warnings=("wrapped_text_json",),
        protocol_errors=("report_output_missing",),
    )

    artifact = result.by_kind("stdout_salvage")
    assert result.state == "needs_review"
    assert result.primary_artifact == "stdout_salvage"
    assert artifact is not None
    assert artifact.state == "needs_review"
    assert "terminal_receipt_missing" in artifact.soft_warnings
    assert result.global_hard_blockers == ()


def test_stdout_json_text_report_is_salvage_candidate(tmp_path: Path) -> None:
    stdout = tmp_path / "stdout.log"
    stdout.write_text(
        (
            '{"text": "I inspected the implementation.\\n\\n'
            '## Implementation Report\\n\\n'
            '- Added artifact_result.\\n'
            '- Verified focused tests."}'
        ),
        encoding="utf-8",
    )

    result = classify_artifacts(
        receipt=None,
        report_path=None,
        stdout_path=str(stdout),
        receipt_path=None,
        git_status_summary=None,
        scope_validation=None,
        protocol_warnings=(),
        protocol_errors=(),
    )

    assert result.state == "needs_review"
    assert result.primary_artifact == "stdout_salvage"


def test_apply_check_failed_diff_is_blocked_artifact_not_global_blocker() -> None:
    result = classify_artifacts(
        receipt={
            "status": "EVIDENCE_PACK",
            "payload": {
                "worktree_diff": {
                    "has_patch": True,
                    "changed_files": ["agpair/example.py"],
                    "apply_check_ok": False,
                    "apply_check_reason": "apply_conflict",
                }
            },
        },
        report_path=None,
        stdout_path=None,
        receipt_path=None,
        git_status_summary=" M agpair/example.py",
        scope_validation={"ok": True},
        protocol_warnings=(),
        protocol_errors=(),
    )

    artifact = result.by_kind("diff")
    assert result.state == "blocked"
    assert result.primary_artifact == "nothing_useful"
    assert artifact is not None
    assert artifact.state == "blocked"
    assert artifact.hard_blockers == ("apply_conflict",)


def test_report_survives_blocked_diff_as_primary_artifact(tmp_path: Path) -> None:
    report = tmp_path / "report.md"
    report.write_text("Recommendation: inspect the failed implementation direction.", encoding="utf-8")

    result = classify_artifacts(
        receipt={
            "status": "EVIDENCE_PACK",
            "payload": {
                "report": "Recommendation: inspect the failed implementation direction.",
                "worktree_diff": {
                    "has_patch": True,
                    "changed_files": ["agpair/example.py"],
                    "apply_check_ok": False,
                    "apply_check_reason": "apply_check_failed",
                },
            },
        },
        report_path=str(report),
        stdout_path=None,
        receipt_path=None,
        git_status_summary=" M agpair/example.py",
        scope_validation={"ok": True},
        protocol_warnings=(),
        protocol_errors=(),
    )

    diff = result.by_kind("diff")
    assert result.state == "needs_review"
    assert result.primary_artifact == "report"
    assert diff is not None
    assert diff.state == "blocked"


def test_reviewable_diff_beats_report_for_mutating_artifact() -> None:
    result = classify_artifacts(
        receipt={
            "status": "EVIDENCE_PACK",
            "payload": {
                "report": "Recommendation: review the patch.",
                "worktree_diff": {
                    "has_patch": True,
                    "changed_files": ["agpair/example.py"],
                    "apply_check_ok": True,
                },
            },
        },
        report_path=None,
        stdout_path=None,
        receipt_path=None,
        git_status_summary=" M agpair/example.py",
        scope_validation={"ok": True},
        protocol_warnings=(),
        protocol_errors=(),
    )

    assert result.state == "usable"
    assert result.primary_artifact == "diff"


def test_authorization_violation_is_global_hard_blocker_even_with_report(tmp_path: Path) -> None:
    report = tmp_path / "report.md"
    report.write_text("Recommendation: useful text exists.", encoding="utf-8")

    result = classify_artifacts(
        receipt={
            "status": "BLOCKED",
            "payload": {
                "blocker_type": "authorization_violation",
                "report": "Recommendation: useful text exists.",
            },
        },
        report_path=str(report),
        stdout_path=None,
        receipt_path=None,
        git_status_summary=None,
        scope_validation={"ok": False},
        protocol_warnings=(),
        protocol_errors=(),
    )

    assert result.state == "blocked"
    assert result.primary_artifact == "report"
    assert result.global_hard_blockers == ("authorization_violation",)


def test_changed_files_without_apply_check_is_needs_review_diff() -> None:
    result = classify_artifacts(
        receipt={"status": "EVIDENCE_PACK", "payload": {"changed_files": ["agpair/example.py"]}},
        report_path=None,
        stdout_path=None,
        receipt_path=None,
        git_status_summary=None,
        scope_validation=None,
        protocol_warnings=(),
        protocol_errors=(),
    )

    diff = result.by_kind("diff")
    assert result.state == "needs_review"
    assert result.primary_artifact == "diff"
    assert diff is not None
    assert diff.soft_warnings == ("apply_check_missing",)


def test_empty_or_thought_only_output_is_blocked(tmp_path: Path) -> None:
    stdout = tmp_path / "stdout.log"
    stdout.write_text('{"thought": "I need to inspect files first"}', encoding="utf-8")

    result = classify_artifacts(
        receipt=None,
        report_path=None,
        stdout_path=str(stdout),
        receipt_path=None,
        git_status_summary=None,
        scope_validation=None,
        protocol_warnings=(),
        protocol_errors=(),
    )

    assert result.state == "blocked"
    assert result.primary_artifact == "nothing_useful"
    assert result.global_hard_blockers == ("thought_only_output",)
