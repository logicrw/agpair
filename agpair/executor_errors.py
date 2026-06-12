from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Final

ExecutorErrorClassification = tuple[str, bool, str | None]

_WAITING_FOR_INPUT_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"\bpress\s+enter\s+to\s+continue\b", re.IGNORECASE),
    re.compile(r"\bdo\s+you\s+want\s+to\s+(proceed|continue)\b", re.IGNORECASE),
    re.compile(r"\b(approve|confirm|continue)\?\s*(\[[^\]]*y[^\]]*n[^\]]*\]|\([^\)]*y[^\)]*n[^\)]*\))?", re.IGNORECASE),
    re.compile(r"\bwaiting\s+for\s+(user\s+)?input\b", re.IGNORECASE),
    re.compile(r"\brequires\s+(human\s+)?confirmation\b", re.IGNORECASE),
    re.compile(r"\bapproval\s+required\b", re.IGNORECASE),
)

_SALIENT_ERROR_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"\b(error|fatal|failed|exception):", re.IGNORECASE),
    re.compile(r"\bmax[-_ ]?turns?\b", re.IGNORECASE),
    re.compile(r"\bturn budget\b", re.IGNORECASE),
    re.compile(r"\btimed out\b", re.IGNORECASE),
    re.compile(r"\binvalid[_ -]?grant\b", re.IGNORECASE),
    re.compile(r"\btokenrefreshfailed\b", re.IGNORECASE),
    re.compile(r"\bnot authenticated\b", re.IGNORECASE),
    re.compile(r"\bquota\b|\busage limit\b|\bpurchase more credits\b", re.IGNORECASE),
    *_WAITING_FOR_INPUT_PATTERNS,
)


def classify_executor_error(summary: str) -> ExecutorErrorClassification:
    normalized = summary.lower()
    if _looks_turn_budget_exhausted(normalized):
        return "executor_turn_budget_exhausted", True, "retry_same_executor_with_more_budget_or_switch_executor"
    if "timed out waiting for response" in normalized:
        return "executor_response_timeout", True, "retry_same_executor_or_switch_executor"
    if looks_waiting_for_input(normalized):
        return "executor_waiting_for_input", True, "switch_executor_or_retry_with_noninteractive_flags"
    if "usage limit" in normalized or "purchase more credits" in normalized or "quota" in normalized:
        return "executor_quota_exhausted", True, "wait_or_switch_executor"
    if (
        "invalid_grant" in normalized
        or "tokenrefreshfailed" in normalized
        or "auth(" in normalized
        or "not authenticated" in normalized
    ):
        return "executor_auth_failed", False, "repair_executor_auth"
    return "execution_error", False, "inspect_logs"


def looks_waiting_for_input(text: str) -> bool:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for line in lines[-40:]:
        if any(pattern.search(line) for pattern in _WAITING_FOR_INPUT_PATTERNS):
            return True
    return False


def prioritize_error_lines(lines: Sequence[str], *, max_lines: int) -> list[str]:
    cleaned = [line.strip() for line in lines if line.strip()]
    salient = [line for line in cleaned if any(pattern.search(line) for pattern in _SALIENT_ERROR_PATTERNS)]
    if salient:
        return salient[-max_lines:]
    return cleaned[-max_lines:]


def _looks_turn_budget_exhausted(text: str) -> bool:
    return (
        "max turns reached" in text
        or "max turns" in text
        or "maxturns" in text
        or "max_turns" in text
        or "maximum number of turns" in text
        or "turn budget" in text
    )
