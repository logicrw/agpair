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


def blocked_reason_from_receipt(receipt: StructuredTerminalReceipt, fallback: str) -> str:
    summary = receipt.summary.strip()
    if summary:
        return summary
    message = receipt.payload.get("message")
    if isinstance(message, str) and message.strip():
        return message.strip()
    return fallback
