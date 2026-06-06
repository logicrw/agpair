from __future__ import annotations

from agpair.executors.registry import active_executor_ids
from agpair.executors.policy import LEGACY_EXECUTOR_ALIASES, normalize_executor_id as _normalize_executor_id

LEGACY_EXECUTOR_IDS = frozenset({"antigravity", "codex_cli", "gemini_cli", "gemini", "gemini-cli"})


def supported_executor_ids() -> tuple[str, ...]:
    return active_executor_ids()


def default_executor_id() -> str:
    supported = supported_executor_ids()
    return supported[0] if supported else "antigravity-cli"


def normalize_executor_id(executor_id: str | None) -> str | None:
    if executor_id is None:
        return None
    normalized = executor_id.strip().lower().replace("_", "-")
    return LEGACY_EXECUTOR_ALIASES.get(normalized, normalized) or None


def is_supported_executor(executor_id: str | None) -> bool:
    try:
        normalized = _normalize_executor_id(executor_id)
    except ValueError:
        return False
    return normalized in supported_executor_ids()


def is_legacy_executor(executor_id: str | None) -> bool:
    if executor_id is None:
        return False
    normalized = executor_id.strip().lower()
    return normalized in LEGACY_EXECUTOR_IDS


def validate_supported_executor(executor_id: str | None) -> str:
    normalized = _normalize_executor_id(executor_id) or default_executor_id()
    if normalized not in supported_executor_ids():
        allowed = ", ".join(supported_executor_ids())
        raise ValueError(f"executor must be active for new AGPair work: {normalized}; allowed: {allowed}")
    return normalized
