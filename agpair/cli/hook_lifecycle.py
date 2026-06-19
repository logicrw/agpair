from __future__ import annotations

from agpair.config import AppPaths
from agpair.models import TaskRecord
from agpair.storage.journal import JournalRepository
from agpair.terminal_receipts import StructuredTerminalReceipt, parse_structured_terminal_receipt

REVIEW_TERMINAL_PHASES = frozenset({"ready_for_review", "committed", "evidence_ready"})
RECEIPT_EVENTS = REVIEW_TERMINAL_PHASES | frozenset({"blocked"})


def latest_terminal_receipt(paths: AppPaths, task_id: str) -> StructuredTerminalReceipt | None:
    journal = JournalRepository(paths.db_path)
    for row in journal.tail(task_id, limit=20):
        if row.event not in RECEIPT_EVENTS:
            continue
        receipt = parse_structured_terminal_receipt(row.body, expected_task_id=task_id)
        if receipt is not None:
            return receipt
    return None


def terminal_receipt_for_task(paths: AppPaths, task: TaskRecord) -> StructuredTerminalReceipt | None:
    receipt = latest_terminal_receipt(paths, task.task_id)
    if receipt is not None:
        return receipt
    if not task.terminal_receipt_json:
        return None
    return parse_structured_terminal_receipt(
        task.terminal_receipt_json,
        expected_task_id=task.task_id,
    )


def is_readonly_report_only_without_effective_changes(
    task: TaskRecord,
    receipt: StructuredTerminalReceipt | None,
) -> bool:
    if task.phase not in REVIEW_TERMINAL_PHASES:
        return False
    if task.authorization_profile != "local_readonly":
        return False
    if receipt is None:
        return False

    effective_task_policy = receipt.payload.get("effective_task_policy")
    is_report_only = task.completion_policy == "report"
    if isinstance(effective_task_policy, dict):
        report_only = effective_task_policy.get("report_only")
        is_report_only = bool(
            report_only is True
            or (report_only is None and effective_task_policy.get("effective_completion_policy") == "report")
        )
    elif task.completion_policy == "auto":
        is_report_only = True
    if not is_report_only:
        return False

    scope_validation = receipt.payload.get("scope_validation")
    if isinstance(scope_validation, dict):
        effective_changed_files = scope_validation.get("effective_changed_files")
        if isinstance(effective_changed_files, list):
            return scope_validation.get("ok") is not False and not effective_changed_files
    return False
