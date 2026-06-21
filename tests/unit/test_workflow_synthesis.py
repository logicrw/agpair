import pytest

from agpair.workflows.synthesis import (
    SynthesisValidationError,
    build_lane_card,
    derive_panel_result,
    validate_synthesis_result,
)


def test_build_lane_card_preserves_agent_result_and_artifacts() -> None:
    node_payload = {
        "node_id": "review-primary",
        "kind": "task",
        "phase": "ready_for_review",
        "task_id": "TASK-1",
        "role": "primary",
        "executor_backend": "grok-cli",
        "artifacts": [
            {"type": "report", "path": "/tmp/report.md", "size_bytes": 100, "sha256": "abc"},
            {"type": "stdout", "path": "/tmp/stdout.log", "size_bytes": 200, "sha256": "def"},
        ],
        "adoption_result": {
            "adoptable_result": "yes",
            "agent_result": {
                "state": "usable",
                "controller_action": "use_result",
                "summary": "Report can be used.",
                "hard_blockers": [],
                "soft_warnings": [],
            },
            "artifact_result": {
                "state": "usable",
                "primary_artifact": "report",
                "artifacts": [{"kind": "report", "state": "usable"}],
                "global_hard_blockers": [],
                "soft_warnings": [],
            },
        },
        "terminal_receipt": {
            "payload": {
                "report": "Useful report body",
                "changed_files": ["agpair/workflows/synthesis.py"],
                "scope_violations": [],
            }
        },
    }

    card = build_lane_card(node_payload)

    assert card["node_id"] == "review-primary"
    assert card["role"] == "primary"
    assert card["executor"] == "grok-cli"
    assert card["agent_result"]["state"] == "usable"
    assert card["artifact_result"]["primary_artifact"] == "report"
    assert card["artifacts"]["report"] == "/tmp/report.md"
    assert card["artifacts"]["stdout"] == "/tmp/stdout.log"
    assert card["summary_excerpt"] == "Useful report body"
    assert card["changed_files"] == ["agpair/workflows/synthesis.py"]
    assert card["scope_violations"] == []
    assert card["adoptable_result"] == "yes"


def test_build_lane_card_marks_stdout_salvage_as_partial_evidence() -> None:
    node_payload = {
        "node_id": "review-salvage",
        "kind": "task",
        "phase": "blocked",
        "task_id": "TASK-2",
        "role": "adversarial",
        "executor_backend": "grok-cli",
        "artifacts": [
            {"type": "stdout", "path": "/tmp/stdout.log", "size_bytes": 2048, "sha256": "stdout"},
        ],
        "terminal_receipt": None,
        "adoption_result": None,
        "error": "terminal receipt missing",
    }

    card = build_lane_card(node_payload)

    assert card["agent_result"]["state"] == "needs_review"
    assert card["agent_result"]["controller_action"] == "inspect_evidence"
    assert card["agent_result"]["hard_blockers"] == []
    assert "stdout_report_salvaged" in card["agent_result"]["soft_warnings"]
    assert "terminal_receipt_missing" in card["agent_result"]["soft_warnings"]
    assert card["adoptable_result"] == "partial"


def test_build_lane_card_preserves_report_when_diff_artifact_is_blocked() -> None:
    node_payload = {
        "node_id": "review-mixed",
        "kind": "task",
        "phase": "ready_for_review",
        "task_id": "TASK-3",
        "role": "implementation",
        "executor_backend": "grok-cli",
        "adoption_result": {
            "adoptable_result": "partial",
            "agent_result": {
                "state": "needs_review",
                "controller_action": "use_result",
                "summary": "Report is useful; diff is blocked.",
                "hard_blockers": ["apply_check_failed"],
                "soft_warnings": [],
            },
            "artifact_result": {
                "state": "needs_review",
                "primary_artifact": "report",
                "artifacts": [
                    {"kind": "report", "state": "usable", "hard_blockers": [], "soft_warnings": []},
                    {"kind": "diff", "state": "blocked", "hard_blockers": ["apply_check_failed"], "soft_warnings": []},
                ],
                "global_hard_blockers": [],
                "soft_warnings": [],
            },
        },
        "terminal_receipt": {"payload": {"report": "Useful report body"}},
    }

    card = build_lane_card(node_payload)

    assert card["agent_result"]["controller_action"] == "use_result"
    assert card["recovery_decision"]["action"] == "use_result"
    assert card["artifact_result"]["primary_artifact"] == "report"
    diff = next(item for item in card["artifact_result"]["artifacts"] if item["kind"] == "diff")
    assert diff["state"] == "blocked"


def test_validate_synthesis_result_requires_comparison_fields() -> None:
    with pytest.raises(SynthesisValidationError, match="consensus"):
        validate_synthesis_result(
            {
                "schema_version": "1",
                "workflow_id": "WF-1",
                "recommended_controller_action": "use_result",
                "contradictions": [],
                "unique_insights": [],
                "blind_spots": [],
            }
        )


def test_synthesis_accepts_legacy_fall_back_but_outputs_native_fallback() -> None:
    result = validate_synthesis_result(
        {
            "workflow_id": "WF-1",
            "consensus": [],
            "contradictions": [],
            "unique_insights": [],
            "blind_spots": [],
            "recommended_controller_action": "fall_back",
        }
    )

    assert result["recommended_controller_action"] == "native_fallback"


def test_derive_panel_result_degrades_for_partial_lane_and_scope_violation() -> None:
    synthesis = validate_synthesis_result(
        {
            "schema_version": "1",
            "workflow_id": "WF-1",
            "consensus": ["All lanes agree that the parser should preserve evidence."],
            "contradictions": [],
            "unique_insights": ["One lane found stdout salvage evidence."],
            "blind_spots": ["No live antigravity-cli lane was available."],
            "recommended_controller_action": "use_result",
        }
    )
    lanes = [
        {
            "node_id": "review-primary",
            "agent_result": {"state": "usable", "hard_blockers": [], "soft_warnings": []},
            "adoptable_result": "yes",
            "scope_violations": [],
        },
        {
            "node_id": "review-salvage",
            "agent_result": {
                "state": "needs_review",
                "hard_blockers": [],
                "soft_warnings": ["stdout_report_salvaged"],
            },
            "adoptable_result": "partial",
            "scope_violations": [{"path": "../outside.txt"}],
        },
    ]

    panel = derive_panel_result(workflow_id="WF-1", lane_cards=lanes, synthesis_result=synthesis)

    assert panel["state"] == "needs_review"
    assert panel["controller_action"] == "inspect_evidence"
    assert panel["lane_count"] == 2
    assert panel["usable_lane_count"] == 1
    assert panel["partial_lane_count"] == 1
    assert panel["blocked_lane_count"] == 0
    assert "scope_violation" in panel["hard_blockers"]
