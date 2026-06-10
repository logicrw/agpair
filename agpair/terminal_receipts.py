from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from typing import Any, Literal, Mapping


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


@dataclass(frozen=True)
class ReceiptValidationResult:
    ok: bool
    required_missing: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReceiptProtocolResult:
    receipt: StructuredTerminalReceipt | None
    ok: bool
    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    raw_body: str = ""

    @property
    def has_usable_receipt(self) -> bool:
        return self.receipt is not None and not self.errors

    @property
    def status(self) -> TerminalReceiptStatus | None:
        return self.receipt.status if self.receipt is not None else None

    @property
    def payload(self) -> dict[str, Any]:
        return self.receipt.payload if self.receipt is not None else {}

    @property
    def summary(self) -> str | None:
        return self.receipt.summary if self.receipt is not None else None


_VALID_STATUSES = frozenset({"EVIDENCE_PACK", "BLOCKED", "COMMITTED"})
_STATUS_ALIASES = {
    "evidence_pack": "EVIDENCE_PACK",
    "evidence-ready": "EVIDENCE_PACK",
    "evidence_ready": "EVIDENCE_PACK",
    "ready-for-review": "EVIDENCE_PACK",
    "ready_for_review": "EVIDENCE_PACK",
    "report": "EVIDENCE_PACK",
    "report_only": "EVIDENCE_PACK",
    "success": "EVIDENCE_PACK",
    "succeeded": "EVIDENCE_PACK",
    "complete": "EVIDENCE_PACK",
    "completed": "EVIDENCE_PACK",
    "done": "EVIDENCE_PACK",
    "blocked": "BLOCKED",
    "block": "BLOCKED",
    "failed": "BLOCKED",
    "failure": "BLOCKED",
    "committed": "COMMITTED",
    "commit": "COMMITTED",
}
_LISTISH_COMMITTED_FIELDS = frozenset({"changed_files", "validation", "residual_risks"})


def _normalize_status(value: Any) -> TerminalReceiptStatus | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    if stripped in _VALID_STATUSES:
        return stripped  # type: ignore[return-value]
    alias = _STATUS_ALIASES.get(stripped.lower())
    if alias in _VALID_STATUSES:
        return alias  # type: ignore[return-value]
    return None


def _status_is_alias(value: Any) -> bool:
    return isinstance(value, str) and value.strip() not in _VALID_STATUSES and _normalize_status(value) is not None


def _schema_is_alias(value: Any) -> bool:
    return str(value or "").strip() in {"1.0", "1.0.0"}


def _receipt_payload_has_missing_artifact_path(payload: Mapping[str, Any]) -> bool:
    for key in ("raw_log_path", "receipt_path"):
        value = payload.get(key)
        if not isinstance(value, str) or not value.strip():
            return True
    return False


def validate_terminal_receipt_payload(
    kind: str,
    payload: Mapping[str, Any],
) -> ReceiptValidationResult:
    if kind == "BLOCKED" and payload.get("blocker_type") == "approval_required":
        required = (
            "requested_authorization_profile",
            "requested_actions",
            "authorization_delta",
            "request_reason",
            "risk_assessment",
            "safe_to_retry",
            "raw_log_path",
        )
    elif kind in {"COMMITTED", "EVIDENCE_PACK"} or payload.get("claimed_state") == "ready_for_review":
        required = (
            "changed_files",
            "scope_violations",
            "raw_log_path",
            "receipt_path",
        )
        missing = tuple(field for field in required if field not in payload)
        has_validation = bool(payload.get("validation")) or bool(payload.get("validation_not_run"))
        if not has_validation:
            missing = (*missing, "validation")
        return ReceiptValidationResult(ok=not missing, required_missing=missing)
    else:
        required = ()

    missing = tuple(field for field in required if field not in payload)
    return ReceiptValidationResult(ok=not missing, required_missing=missing)


def validate_structured_receipt_dict(
    parsed: Any,
    raw_body: str = "",
    *,
    expected_status: str | None = None,
    expected_task_id: str | None = None,
) -> StructuredTerminalReceipt | None:
    if not isinstance(parsed, dict):
        return None
    schema_version = str(parsed.get("schema_version", "")).strip()
    if schema_version not in {"1", "1.0", "1.0.0"}:
        return None

    status = _normalize_status(parsed.get("status"))
    task_id = parsed.get("task_id")
    attempt_no = parsed.get("attempt_no")
    review_round = parsed.get("review_round")
    summary = parsed.get("summary")
    payload = parsed.get("payload")

    if status is None:
        return None
    normalized_expected_status = _normalize_status(expected_status) if expected_status is not None else None
    if expected_status is not None and status != normalized_expected_status:
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
        raw_body=raw_body,
    )


_WRAPPED_TEXT_KEYS = (
    "result",
    "output",
    "text",
    "content",
    "message",
    "response",
)


def _parse_direct_receipt(
    body: str,
    *,
    expected_status: str | None = None,
    expected_task_id: str | None = None,
) -> StructuredTerminalReceipt | None:
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return None
    return validate_structured_receipt_dict(
        parsed,
        raw_body=body,
        expected_status=expected_status,
        expected_task_id=expected_task_id,
    )


def _protocol_for_receipt(
    receipt: StructuredTerminalReceipt | None,
    *,
    raw_body: str,
    parsed: Mapping[str, Any] | None = None,
    source_warning: str | None = None,
    errors: tuple[str, ...] = (),
) -> ReceiptProtocolResult:
    warnings: list[str] = []
    if source_warning:
        warnings.append(source_warning)
    if parsed is not None:
        if _schema_is_alias(parsed.get("schema_version")):
            warnings.append("schema_version_alias")
        if _status_is_alias(parsed.get("status")):
            warnings.append("status_alias")
        payload = parsed.get("payload")
        if isinstance(payload, Mapping) and _receipt_payload_has_missing_artifact_path(payload):
            warnings.append("artifact_path_missing")
    return ReceiptProtocolResult(
        receipt=receipt,
        ok=receipt is not None and not errors,
        warnings=tuple(dict.fromkeys(warnings)),
        errors=errors,
        raw_body=raw_body,
    )


def _try_parse_json(value: str) -> Any | None:
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return None


def _balanced_json_object_candidates(body: str) -> list[str]:
    candidates: list[str] = []
    in_string = False
    escape = False
    start: int | None = None
    depth = 0
    for index, char in enumerate(body):
        if start is None:
            if char == "{":
                start = index
                depth = 1
                in_string = False
                escape = False
            continue
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                candidates.append(body[start : index + 1])
                start = None
    return candidates


def _receipt_from_json_candidates(
    candidates: list[str],
    *,
    expected_status: str | None = None,
    expected_task_id: str | None = None,
) -> StructuredTerminalReceipt | None:
    for candidate in reversed(candidates):
        parsed = _parse_direct_receipt(
            candidate,
            expected_status=expected_status,
            expected_task_id=expected_task_id,
        )
        if parsed is not None:
            return parsed
    return None


def _wrapped_text_candidates(value: Any) -> list[str]:
    candidates: list[str] = []
    if isinstance(value, str):
        candidates.append(value)
        return candidates
    if isinstance(value, list):
        for item in value:
            candidates.extend(_wrapped_text_candidates(item))
        return candidates
    if isinstance(value, dict):
        visited_keys: set[str] = set()
        for key in _WRAPPED_TEXT_KEYS:
            if key in value:
                visited_keys.add(key)
                candidates.extend(_wrapped_text_candidates(value[key]))
        for key, item in value.items():
            if key in visited_keys:
                continue
            candidates.extend(_wrapped_text_candidates(item))
        return candidates
    return candidates


def parse_structured_terminal_receipt(
    body: str,
    *,
    expected_status: str | None = None,
    expected_task_id: str | None = None,
) -> StructuredTerminalReceipt | None:
    return normalize_terminal_receipt(
        body,
        expected_status=expected_status,
        expected_task_id=expected_task_id,
    ).receipt


def normalize_terminal_receipt(
    body: str,
    *,
    expected_status: str | None = None,
    expected_task_id: str | None = None,
) -> ReceiptProtocolResult:
    if not body:
        return ReceiptProtocolResult(receipt=None, ok=False, errors=("empty_body",), raw_body=body)
    parsed_body = _try_parse_json(body)
    direct = validate_structured_receipt_dict(
        parsed_body,
        raw_body=body,
        expected_status=expected_status,
        expected_task_id=expected_task_id,
    ) if isinstance(parsed_body, Mapping) else None
    if direct is not None:
        return _protocol_for_receipt(direct, raw_body=body, parsed=parsed_body)

    raw_candidates = _balanced_json_object_candidates(body)
    raw_candidate = _receipt_from_json_candidates(
        raw_candidates,
        expected_status=expected_status,
        expected_task_id=expected_task_id,
    )
    if raw_candidate is not None:
        parsed_candidate = _try_parse_json(raw_candidate.raw_body)
        return _protocol_for_receipt(
            raw_candidate,
            raw_body=body,
            parsed=parsed_candidate if isinstance(parsed_candidate, Mapping) else None,
            source_warning="mixed_text_json",
        )
    parsed = parsed_body
    if parsed is None:
        return ReceiptProtocolResult(receipt=None, ok=False, errors=("malformed_json",), raw_body=body)
    for candidate in _wrapped_text_candidates(parsed):
        for line in reversed(candidate.splitlines()):
            stripped = line.strip()
            if not stripped.startswith("{"):
                continue
            nested_json = _try_parse_json(stripped)
            nested = validate_structured_receipt_dict(
                nested_json,
                raw_body=stripped,
                expected_status=expected_status,
                expected_task_id=expected_task_id,
            ) if isinstance(nested_json, Mapping) else None
            if nested is not None:
                return _protocol_for_receipt(
                    nested,
                    raw_body=body,
                    parsed=nested_json,
                    source_warning="wrapped_text_json",
                )
    return ReceiptProtocolResult(receipt=None, ok=False, errors=("receipt_not_found",), raw_body=body)


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
    recommended_next_action = payload.get("recommended_next_action") or payload.get("suggested_action")
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
