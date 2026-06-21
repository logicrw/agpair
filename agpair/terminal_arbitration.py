from __future__ import annotations

import json
import re
from typing import Any, Mapping

_REPORT_MARKERS = (
    "findings:",
    "conclusion:",
    "recommendation:",
    "recommendations:",
    "evidence:",
    "result:",
    "final answer",
    "requested review",
    "审查结论",
    "结论",
    "建议",
    "发现",
    "证据",
)
_INCOMPLETE_MARKERS = (
    "cancelled",
    "maxturns",
    "max_turns",
    "max turns",
    "tool_use",
    "tool call",
    "thinking",
)
_PROGRESS_PREFIXES = (
    "reviewing ",
    "reading ",
    "checking ",
    "i need to ",
    "i will ",
    "we need to ",
)


def completed_report_text(text: str | None) -> str | None:
    value = (text or "").strip()
    if not value:
        return None
    extracted = _extract_report_from_json(value)
    if extracted is not None:
        return extracted if looks_like_completed_report(extracted) else None
    stripped = _drop_trailing_json_receipt(value).strip()
    if not stripped:
        return None
    return stripped if looks_like_completed_report(stripped, parse_json=False) else None


def looks_like_completed_report(text: str | None, *, parse_json: bool = True) -> bool:
    value = (text or "").strip()
    if not value:
        return False
    if parse_json:
        extracted = _extract_report_from_json(value)
        if extracted is not None:
            return looks_like_completed_report(extracted, parse_json=False)
        parsed = _parse_json_object(value)
        if parsed is not None:
            return False
    lowered = value.lower()
    if any(marker in lowered for marker in _REPORT_MARKERS):
        return True
    if any(marker in lowered for marker in _INCOMPLETE_MARKERS):
        return False
    first_line = next((line.strip().lower() for line in value.splitlines() if line.strip()), "")
    if any(first_line.startswith(prefix) for prefix in _PROGRESS_PREFIXES):
        return False
    nonempty_lines = [line for line in value.splitlines() if line.strip()]
    if len(nonempty_lines) >= 3 and re.search(r"(^|\n)\s*(-|\d+\.)\s+", value):
        return True
    return False


def _extract_report_from_json(text: str) -> str | None:
    parsed = _parse_json_object(text)
    if parsed is None:
        return None
    if _json_object_is_failed_result(parsed):
        return ""
    payload = parsed.get("payload")
    if isinstance(payload, Mapping):
        report = payload.get("report")
        if isinstance(report, str) and report.strip():
            return report.strip()
    for key in ("report", "final", "answer", "content", "result", "text"):
        value = parsed.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    if _json_object_is_incomplete_event(parsed):
        return ""
    return None


def _json_object_is_failed_result(value: Mapping[str, Any]) -> bool:
    if value.get("is_error") is True:
        return True
    status = value.get("status")
    subtype = value.get("subtype")
    return str(status or subtype).strip().lower() in {
        "blocked",
        "cancelled",
        "canceled",
        "error",
        "failed",
        "failure",
        "interrupted",
    }


def _json_object_is_incomplete_event(value: Mapping[str, Any]) -> bool:
    lowered_keys = {str(key).lower() for key in value}
    if "thought" in lowered_keys:
        return True
    stop_reason = str(value.get("stopReason") or value.get("stop_reason") or "").strip().lower()
    return stop_reason in {"cancelled", "canceled", "maxturns", "max_turns", "max turns"}


def _drop_trailing_json_receipt(text: str) -> str:
    lines = text.splitlines()
    while lines:
        candidate = lines[-1].strip()
        if not candidate:
            lines.pop()
            continue
        parsed = _parse_json_object(candidate)
        if parsed is None:
            break
        if {"schema_version", "status", "payload"} & set(parsed):
            lines.pop()
            continue
        break
    return "\n".join(lines)


def _parse_json_object(text: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None
