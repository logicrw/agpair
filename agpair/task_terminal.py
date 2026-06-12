from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from agpair.adoption import derive_adoption_decision
from agpair.artifacts import (
    artifact_metadata,
    copy_artifact,
    ensure_attempt_dir,
    read_excerpt,
    write_json,
    write_text,
)
from agpair.completion import (
    CompletionDecision,
    evidence_from_receipt_and_paths,
    evaluate_completion,
    resolve_effective_task_policy,
)
from agpair.storage.journal import JournalRepository
from agpair.storage.tasks import TaskRepository
from agpair.scope_validation import (
    changed_files_from_git_diff,
    changed_files_from_git_status,
    normalize_changed_files,
    validate_changed_files,
)
from agpair.terminal_receipts import (
    parse_structured_terminal_receipt,
    validate_structured_receipt_dict,
    validate_terminal_receipt_payload,
)
from agpair.terminal_arbitration import completed_report_text
from agpair.transport import messages
from agpair.worktree_adoption import (
    WorktreeAdoptionError,
    build_worktree_diff,
    check_apply_to_controller_repo,
)


def finalize_executor_receipt(
    *,
    state_root: Path,
    tasks: TaskRepository,
    journal: JournalRepository,
    task,
    raw_receipt: Mapping[str, Any] | None,
    source: str,
    message_id: str | None = None,
    original_body: str | None = None,
) -> CompletionDecision:
    """Persist attempt artifacts, validate the terminal claim, and update task phase."""
    receipt: dict[str, Any] = dict(raw_receipt or {})
    payload = receipt.get("payload")
    if not isinstance(payload, dict):
        payload = {}
    receipt["payload"] = payload
    receipt.setdefault("schema_version", "1")
    receipt.setdefault("task_id", task.task_id)
    receipt.setdefault("attempt_no", task.attempt_no)
    receipt.setdefault("review_round", 0)
    receipt.setdefault("status", messages.BLOCKED)
    receipt.setdefault("summary", payload.get("message") or "terminal receipt")

    attempt_dir = ensure_attempt_dir(state_root, task.task_id, task.attempt_no)
    stdout_path = copy_artifact(payload.get("raw_log_path"), attempt_dir / "stdout.log") or str(attempt_dir / "stdout.log")
    stderr_path = copy_artifact(payload.get("stderr_log_path"), attempt_dir / "stderr.log") or None
    if not Path(stdout_path).exists():
        Path(stdout_path).write_text("", encoding="utf-8")
    if stderr_path is None:
        stderr_candidate = attempt_dir / "stderr.log"
        if not stderr_candidate.exists():
            stderr_candidate.write_text("", encoding="utf-8")
        stderr_path = str(stderr_candidate)

    stdout_excerpt = read_excerpt(stdout_path, max_chars=2000)
    stderr_excerpt = read_excerpt(stderr_path, max_chars=2000)
    output_excerpt = stdout_excerpt or stderr_excerpt
    report_text = _report_text_from_receipt_or_output(receipt, stdout_excerpt)
    report_path = write_text(attempt_dir / "report.md", report_text) if report_text else None

    payload["raw_log_path"] = stdout_path
    payload["stderr_log_path"] = stderr_path
    if report_path:
        payload["report_path"] = report_path
    receipt_path = str(attempt_dir / "receipt.json")
    payload["receipt_path"] = receipt_path
    requested_policy = getattr(task, "completion_policy", None) or "auto"
    effective_policy = resolve_effective_task_policy(
        requested_completion_policy=requested_policy,
        authorization_profile=task.authorization_profile,
        body=original_body,
    )
    _merge_worktree_evidence(task, payload, effective_policy.effective_completion_policy)
    scope_validation = _scope_validation_payload(task, payload)
    if scope_validation is not None:
        payload["scope_validation"] = scope_validation
    structured = validate_structured_receipt_dict(receipt, expected_task_id=task.task_id)
    structured_ok = structured is not None
    validation = validate_terminal_receipt_payload(
        str(receipt.get("status")),
        payload,
        report_only=effective_policy.report_only,
    )
    if not validation.ok:
        worktree_diff = payload.get("worktree_diff")
        validation_only_missing = set(validation.required_missing) == {"validation"}
        apply_check_ok = isinstance(worktree_diff, Mapping) and worktree_diff.get("apply_check_ok") is True
        if validation_only_missing and apply_check_ok:
            warnings = payload.get("protocol_warnings") if isinstance(payload.get("protocol_warnings"), list) else []
            payload["protocol_warnings"] = [*warnings, "validation_missing_softened_by_apply_check"]
        else:
            structured_ok = False
            payload["required_missing"] = list(validation.required_missing)
    write_json(attempt_dir / "receipt.json", receipt)

    evidence_path = str(attempt_dir / "evidence.json")
    evidence_payload = {
        "task_id": task.task_id,
        "attempt_no": task.attempt_no,
        "effective_task_policy": effective_policy.to_dict(),
        "artifacts": [
            item
            for item in (
                artifact_metadata(stdout_path, artifact_type="stdout"),
                artifact_metadata(stderr_path, artifact_type="stderr"),
                artifact_metadata(receipt_path, artifact_type="receipt"),
                artifact_metadata(report_path, artifact_type="report"),
            )
            if item is not None
        ],
    }
    write_json(attempt_dir / "evidence.json", evidence_payload)
    metadata_path = write_json(
        attempt_dir / "metadata.json",
        {
            "task_id": task.task_id,
            "attempt_no": task.attempt_no,
            "executor_backend": task.executor_backend,
            "executor_session_id": task.executor_session_id or task.antigravity_session_id,
            "source": source,
            "message_id": message_id,
            "authorization_profile": task.authorization_profile,
        },
    )

    _record_artifacts(
        tasks,
        task_id=task.task_id,
        attempt_no=task.attempt_no,
        artifacts={
            "stdout": stdout_path,
            "stderr": stderr_path,
            "receipt": receipt_path,
            "report": report_path,
            "evidence": evidence_path,
            "metadata": metadata_path,
        },
    )
    evidence = evidence_from_receipt_and_paths(
        receipt,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
        receipt_path=receipt_path,
        report_path=report_path,
        evidence_path=evidence_path,
        output_excerpt=output_excerpt,
        receipt_valid=structured_ok,
    )
    decision = evaluate_completion(
        effective_policy=effective_policy,
        receipt=receipt,
        evidence=evidence,
        process_returncode=_int_value(payload.get("returncode") or payload.get("exit_code")),
        structured_receipt_ok=structured_ok,
    )
    protocol_warnings = tuple(str(item) for item in payload.get("protocol_warnings", ()) if isinstance(item, str))
    protocol_errors = tuple(str(item) for item in payload.get("protocol_errors", ()) if isinstance(item, str))
    adoption = derive_adoption_decision(
        effective_policy=effective_policy,
        receipt=decision.receipt or receipt,
        report_path=report_path,
        stdout_path=stdout_path,
        receipt_path=receipt_path,
        scope_validation=scope_validation,
        protocol_warnings=protocol_warnings,
        protocol_errors=protocol_errors,
    )
    terminal_receipt = dict(decision.receipt or receipt)
    terminal_payload = terminal_receipt.get("payload")
    if not isinstance(terminal_payload, dict):
        terminal_payload = {}
    terminal_receipt["payload"] = terminal_payload
    if not decision.ok:
        terminal_receipt["status"] = messages.BLOCKED
        terminal_receipt["summary"] = decision.summary
        terminal_payload.setdefault("blocker_type", decision.blocker_type or decision.reason_code or "unknown")
        if decision.reason_code:
            terminal_payload.setdefault("reason_code", decision.reason_code)
        if decision.terminal_status:
            terminal_payload.setdefault("claimed_status", decision.terminal_status)
        if decision.effective_policy is not None:
            terminal_payload.setdefault("effective_task_policy", decision.effective_policy.to_dict())
        write_json(attempt_dir / "receipt.json", terminal_receipt)
    terminal_body = json.dumps(terminal_receipt, ensure_ascii=False)
    if decision.ok:
        tasks.mark_ready_for_review(
            task_id=task.task_id,
            last_receipt_id=message_id,
            terminal_source=source,
            terminal_receipt_json=terminal_body,
        )
        journal.append(task.task_id, source, "ready_for_review", terminal_body)
    else:
        tasks.mark_blocked(
            task_id=task.task_id,
            reason=decision.summary,
            last_receipt_id=message_id,
            terminal_source=source,
            terminal_receipt_json=terminal_body,
        )
        journal.append(task.task_id, source, "blocked", terminal_body)
    tasks.record_attempt_terminal(
        task_id=task.task_id,
        attempt_no=task.attempt_no,
        phase=decision.phase,
        terminal_receipt_json=terminal_body,
        terminal_source=source,
        effective_policy_json=json.dumps(effective_policy.to_dict(), ensure_ascii=False, sort_keys=True),
    )
    tasks.update_attempt_adoption(
        task_id=task.task_id,
        attempt_no=task.attempt_no,
        protocol_warnings_json=json.dumps(list(protocol_warnings), ensure_ascii=False, sort_keys=True),
        protocol_errors_json=json.dumps(list(protocol_errors), ensure_ascii=False, sort_keys=True),
        adoptable_result=adoption.adoptable_result,
        adoption_evidence_json=json.dumps(adoption.to_dict(), ensure_ascii=False, sort_keys=True),
    )
    return decision


def _record_artifacts(tasks: TaskRepository, *, task_id: str, attempt_no: int, artifacts: dict[str, str | None]) -> None:
    for kind, path in artifacts.items():
        if not path:
            continue
        tasks.record_artifact(task_id=task_id, attempt_no=attempt_no, artifact_type=kind, path=path)


def _merge_worktree_evidence(task, payload: dict[str, Any], completion_policy: str) -> None:
    if not getattr(task, "isolated_worktree", False):
        return
    if completion_policy not in {"evidence", "commit"}:
        return
    try:
        diff = build_worktree_diff(task=task, session_state=_executor_state(task))
        apply_check = check_apply_to_controller_repo(repo_path=task.repo_path, patch=diff.patch)
    except WorktreeAdoptionError as exc:
        payload["worktree_diff"] = {
            "has_patch": False,
            "changed_files": [],
            "apply_check_ok": False,
            "apply_check_reason": exc.reason,
        }
        return
    payload.setdefault("changed_files", list(diff.changed_files))
    payload["worktree_diff"] = {
        "has_patch": bool(diff.patch.strip()),
        "changed_files": list(diff.changed_files),
        "apply_check_ok": apply_check.ok,
        "apply_check_reason": apply_check.reason,
        "stat": diff.stat,
    }


def _report_text_from_receipt_or_output(receipt: Mapping[str, Any], output_excerpt: str | None) -> str | None:
    payload = receipt.get("payload") if isinstance(receipt.get("payload"), Mapping) else {}
    report = payload.get("report") if isinstance(payload, Mapping) else None
    summary = receipt.get("summary")
    stdout_report = _stdout_report_text(output_excerpt)
    sections: list[str] = []
    if (isinstance(report, str) and report.strip() or stdout_report) and isinstance(summary, str) and summary.strip():
        sections.append("# Executor Report\n\n" + summary.strip())
    if isinstance(report, str) and report.strip():
        sections.append(report.strip())
    if stdout_report:
        sections.append("## Executor Stdout Report\n\n```text\n" + stdout_report + "\n```")
    return "\n\n".join(sections) if sections else None


def _stdout_report_text(output_excerpt: str | None) -> str | None:
    text = (output_excerpt or "").strip()
    if not text:
        return None
    if parse_structured_terminal_receipt(text) is not None and text.startswith("{"):
        return None
    lines = text.splitlines()
    while lines and parse_structured_terminal_receipt(lines[-1].strip()) is not None:
        lines.pop()
    return completed_report_text("\n".join(lines))


def _int_value(value: Any) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _scope_validation_payload(task, payload: Mapping[str, Any]) -> dict[str, Any] | None:
    execution_repo_path = getattr(task, "execution_repo_path", None) or getattr(task, "repo_path", None)
    if not execution_repo_path:
        return None

    state = _executor_state(task)
    commit_ref = _string_value(payload.get("commit_ref") or payload.get("commit") or payload.get("commit_sha"))
    start_ref = _string_value(state.get("start_head")) if state else None
    current_ref = _string_value(state.get("current_head")) if state else None
    if commit_ref is None and current_ref and current_ref != start_ref:
        commit_ref = current_ref

    committed_actual: tuple[str, ...] = ()
    if commit_ref:
        committed_actual = changed_files_from_git_diff(
            execution_repo_path,
            start_ref=start_ref,
            end_ref=commit_ref,
        )
    dirty_actual = changed_files_from_git_status(execution_repo_path)
    actual = normalize_changed_files((*committed_actual, *dirty_actual))
    baseline = state.get("start_dirty_files") if isinstance(state, dict) else ()
    result = validate_changed_files(
        declared_changed_files=payload.get("changed_files"),
        actual_changed_files=actual,
        baseline_changed_files=baseline,
    )
    return result.to_dict()


def _executor_state(task) -> dict[str, Any]:
    session_id = getattr(task, "executor_session_id", None) or getattr(task, "antigravity_session_id", None)
    if not session_id:
        return {}
    state_path = Path(str(session_id)) / "state.json"
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _string_value(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None
