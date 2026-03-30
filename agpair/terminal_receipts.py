from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from typing import Any, Literal


TerminalReceiptStatus = Literal["EVIDENCE_PACK", "BLOCKED", "COMMITTED"]


@dataclass(frozen=True)
class StructuredTerminalReceipt:
    schema_version: str
    task_id: str
    attempt_no: int
    review_round: int
    status: TerminalReceiptStatus
    summary: str
    payload: dict[str, Any]
    raw_body: str


_VALID_STATUSES = frozenset({"EVIDENCE_PACK", "BLOCKED", "COMMITTED"})
_LISTISH_COMMITTED_FIELDS = frozenset({"changed_files", "validation", "residual_risks"})


def parse_structured_terminal_receipt(
    body: str,
    *,
    expected_status: str | None = None,
    expected_task_id: str | None = None,
) -> StructuredTerminalReceipt | None:
    if not body:
        return None
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    if parsed.get("schema_version") != "1":
        return None

    status = parsed.get("status")
    task_id = parsed.get("task_id")
    attempt_no = parsed.get("attempt_no")
    review_round = parsed.get("review_round")
    summary = parsed.get("summary")
    payload = parsed.get("payload")

    if status not in _VALID_STATUSES:
        return None
    if expected_status is not None and status != expected_status:
        return None
    if not isinstance(task_id, str):
        return None
    if expected_task_id is not None and task_id != expected_task_id:
        return None
    if not isinstance(attempt_no, int) or isinstance(attempt_no, bool):
        return None
    if not isinstance(review_round, int) or isinstance(review_round, bool):
        return None
    if not isinstance(summary, str):
        return None
    if not isinstance(payload, dict):
        return None

    return StructuredTerminalReceipt(
        schema_version="1",
        task_id=task_id,
        attempt_no=attempt_no,
        review_round=review_round,
        status=status,
        summary=summary,
        payload=payload,
        raw_body=body,
    )


def structured_receipt_to_dict(receipt: StructuredTerminalReceipt) -> dict[str, Any]:
    payload = asdict(receipt)
    payload.pop("raw_body", None)
    return payload


def committed_result_from_receipt(receipt: StructuredTerminalReceipt) -> dict[str, Any] | None:
    if receipt.status != "COMMITTED":
        return None
    normalized_payload: dict[str, Any] = {}
    for key, value in receipt.payload.items():
        if key in _LISTISH_COMMITTED_FIELDS:
            normalized_payload[key] = _normalize_listish_field(value)
            continue
        normalized_payload[key] = value
    return {
        "schema_version": receipt.schema_version,
        "summary": receipt.summary,
        **normalized_payload,
    }


def blocked_failure_context_from_receipt(receipt: StructuredTerminalReceipt) -> dict[str, Any] | None:
    if receipt.status != "BLOCKED":
        return None
    payload = receipt.payload
    blocker_type = payload.get("blocker_type")
    if not isinstance(blocker_type, str) or not blocker_type.strip():
        blocker_type = "unknown"
    recoverable = payload.get("recoverable")
    if not isinstance(recoverable, bool):
        recoverable = False
    recommended_next_action = payload.get("suggested_action")
    if not isinstance(recommended_next_action, str) or not recommended_next_action.strip():
        recommended_next_action = "inspect_logs"
    last_error_excerpt = payload.get("last_error_excerpt")
    if not isinstance(last_error_excerpt, str) or not last_error_excerpt.strip():
        message = payload.get("message")
        if isinstance(message, str) and message.strip():
            last_error_excerpt = message.strip()
        else:
            last_error_excerpt = receipt.summary
    return {
        "summary": receipt.summary,
        "blocker_type": blocker_type.strip(),
        "recoverable": recoverable,
        "recommended_next_action": recommended_next_action.strip(),
        "last_error_excerpt": last_error_excerpt.strip(),
        "details": payload,
    }


def blocked_reason_from_receipt(receipt: StructuredTerminalReceipt, fallback: str) -> str:
    summary = receipt.summary.strip()
    if summary:
        return summary
    message = receipt.payload.get("message")
    if isinstance(message, str) and message.strip():
        return message.strip()
    return fallback


def _normalize_listish_field(value: Any) -> list[str]:
    if isinstance(value, str):
        stripped = value.strip()
        return [stripped] if stripped else []
    if isinstance(value, list):
        normalized: list[str] = []
        for item in value:
            if not isinstance(item, str):
                continue
            stripped = item.strip()
            if stripped:
                normalized.append(stripped)
        return normalized
    return []
