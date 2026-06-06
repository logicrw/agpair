import json

from agpair.terminal_receipts import (
    blocked_failure_context_from_receipt,
    parse_structured_terminal_receipt,
    validate_terminal_receipt_payload,
)


def test_ready_for_review_requires_machine_checkable_evidence() -> None:
    payload = {
        "changed_files": ["agpair/cli/task.py"],
        "validation": [{"command": "pytest tests/unit -q", "exit_code": 0, "log_path": "/tmp/pytest.log"}],
        "scope_violations": [],
        "raw_log_path": "/tmp/stdout.log",
        "receipt_path": ".agpair/tasks/TASK-123/attempt-1/receipt.json",
        "claimed_state": "ready_for_review",
    }

    assert validate_terminal_receipt_payload("COMMITTED", payload).ok


def test_commit_ref_is_optional_for_ready_for_review() -> None:
    payload = {
        "changed_files": ["agpair/cli/task.py"],
        "validation": [{"command": "pytest tests/unit -q", "exit_code": 0, "log_path": "/tmp/pytest.log"}],
        "scope_violations": [],
        "raw_log_path": "/tmp/stdout.log",
        "receipt_path": ".agpair/tasks/TASK-123/attempt-1/receipt.json",
        "claimed_state": "ready_for_review",
    }

    result = validate_terminal_receipt_payload("COMMITTED", payload)

    assert result.ok
    assert "commit_ref" not in result.required_missing


def test_malformed_success_receipt_is_rejected() -> None:
    payload = {
        "summary": "Done",
        "confidence": "high",
        "claimed_state": "ready_for_review",
    }

    result = validate_terminal_receipt_payload("COMMITTED", payload)

    assert not result.ok
    assert "changed_files" in result.required_missing
    assert "raw_log_path" in result.required_missing


def test_approval_required_requires_authorization_delta() -> None:
    payload = {
        "blocker_type": "approval_required",
        "recoverable": True,
        "suggested_action": "retry_with_expanded_authorization",
        "authorization_profile": "local_readonly",
        "requested_authorization_profile": "local_mutating",
        "requested_actions": ["edit files"],
        "authorization_delta": {"allow_file_edits": True},
        "request_reason": "Readonly profile cannot edit files.",
        "risk_assessment": "Repo-local edits only.",
        "safe_to_retry": True,
        "raw_log_path": "/tmp/stderr.log",
    }

    assert validate_terminal_receipt_payload("BLOCKED", payload).ok


def test_parse_receipt_from_wrapped_result_text() -> None:
    receipt = {
        "schema_version": "1",
        "task_id": "TASK-WRAPPED",
        "attempt_no": 1,
        "review_round": 0,
        "status": "EVIDENCE_PACK",
        "summary": "Smoke complete",
        "payload": {
            "claimed_state": "ready_for_review",
            "changed_files": ["tests/fixtures/external_executor_smoke/grok-cli.txt"],
            "validation_not_run": "smoke",
            "scope_violations": [],
            "raw_log_path": "stdout.log",
            "receipt_path": "receipt.json",
        },
    }
    wrapper = {
        "type": "result",
        "status": "success",
        "result": "human summary\n" + json.dumps(receipt, sort_keys=True),
    }

    parsed = parse_structured_terminal_receipt(json.dumps(wrapper), expected_task_id="TASK-WRAPPED")

    assert parsed is not None
    assert parsed.status == "EVIDENCE_PACK"
    assert parsed.payload["changed_files"] == ["tests/fixtures/external_executor_smoke/grok-cli.txt"]


def test_parse_receipt_from_nested_content_text() -> None:
    receipt = {
        "schema_version": "1",
        "task_id": "TASK-CONTENT",
        "attempt_no": 1,
        "review_round": 0,
        "status": "COMMITTED",
        "summary": "Committed",
        "payload": {
            "claimed_state": "ready_for_review",
            "changed_files": ["tests/fixtures/external_executor_smoke/claude-code.txt"],
            "validation": "git status --short",
            "scope_violations": [],
            "raw_log_path": "stdout.log",
            "receipt_path": "receipt.json",
        },
    }
    wrapper = {
        "type": "message",
        "message": {
            "content": [
                {"type": "text", "text": "done\n" + json.dumps(receipt, sort_keys=True)}
            ]
        },
    }

    parsed = parse_structured_terminal_receipt(json.dumps(wrapper), expected_task_id="TASK-CONTENT")

    assert parsed is not None
    assert parsed.status == "COMMITTED"
    assert parsed.payload["validation"] == "git status --short"


def test_parse_receipt_from_codex_jsonl_item_text() -> None:
    receipt = {
        "schema_version": "1",
        "task_id": "TASK-CODEX-JSONL",
        "attempt_no": 1,
        "review_round": 0,
        "status": "EVIDENCE_PACK",
        "summary": "Codex worker complete",
        "payload": {
            "claimed_state": "ready_for_review",
            "changed_files": ["tests/fixtures/external_executor_smoke/codex.txt"],
            "validation_not_run": "smoke",
            "scope_violations": [],
            "raw_log_path": "stdout.log",
            "receipt_path": "receipt.json",
        },
    }
    wrapper = {
        "type": "item.completed",
        "item": {
            "id": "item_2",
            "type": "agent_message",
            "text": json.dumps(receipt, sort_keys=True),
        },
    }

    parsed = parse_structured_terminal_receipt(json.dumps(wrapper), expected_task_id="TASK-CODEX-JSONL")

    assert parsed is not None
    assert parsed.status == "EVIDENCE_PACK"
    assert parsed.payload["changed_files"] == ["tests/fixtures/external_executor_smoke/codex.txt"]


def test_parse_receipt_normalizes_ready_for_review_status_alias() -> None:
    receipt = {
        "schema_version": "1",
        "task_id": "TASK-REPORT-ALIAS",
        "attempt_no": 1,
        "review_round": 0,
        "status": "ready_for_review",
        "summary": "Report complete",
        "payload": {
            "report": "Read-only review completed.",
            "changed_files": [],
            "validation_not_run": "report-only task",
            "scope_violations": [],
            "raw_log_path": None,
            "receipt_path": None,
        },
    }

    parsed = parse_structured_terminal_receipt(
        json.dumps(receipt),
        expected_status="ready_for_review",
        expected_task_id="TASK-REPORT-ALIAS",
    )

    assert parsed is not None
    assert parsed.status == "EVIDENCE_PACK"
    assert parsed.payload["report"] == "Read-only review completed."


def test_parse_receipt_normalizes_blocked_and_committed_status_aliases() -> None:
    blocked = {
        "schema_version": "1",
        "task_id": "TASK-BLOCKED-ALIAS",
        "attempt_no": 1,
        "review_round": 0,
        "status": "blocked",
        "summary": "Need auth",
        "payload": {"blocker_type": "auth", "recoverable": False},
    }
    committed = {
        "schema_version": "1",
        "task_id": "TASK-COMMITTED-ALIAS",
        "attempt_no": 1,
        "review_round": 0,
        "status": "committed",
        "summary": "Committed",
        "payload": {
            "changed_files": ["README.md"],
            "validation_not_run": "not needed",
            "scope_violations": [],
            "raw_log_path": "stdout.log",
            "receipt_path": "receipt.json",
        },
    }

    parsed_blocked = parse_structured_terminal_receipt(json.dumps(blocked), expected_status="blocked")
    parsed_committed = parse_structured_terminal_receipt(json.dumps(committed), expected_status="committed")

    assert parsed_blocked is not None
    assert parsed_blocked.status == "BLOCKED"
    assert parsed_committed is not None
    assert parsed_committed.status == "COMMITTED"


def test_blocked_failure_context_prefers_recommended_next_action() -> None:
    receipt = {
        "schema_version": "1",
        "task_id": "TASK-BLOCKED-ACTION",
        "attempt_no": 1,
        "review_round": 0,
        "status": "BLOCKED",
        "summary": "quota exhausted",
        "payload": {
            "blocker_type": "executor_quota_exhausted",
            "recoverable": True,
            "recommended_next_action": "wait_or_switch_executor",
            "message": "usage limit",
        },
    }

    parsed = parse_structured_terminal_receipt(json.dumps(receipt), expected_task_id="TASK-BLOCKED-ACTION")

    assert parsed is not None
    context = blocked_failure_context_from_receipt(parsed)
    assert context is not None
    assert context["recommended_next_action"] == "wait_or_switch_executor"
