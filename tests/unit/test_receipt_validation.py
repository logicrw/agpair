from agpair.terminal_receipts import validate_terminal_receipt_payload


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
