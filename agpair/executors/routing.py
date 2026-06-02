from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ExecutorRoute:
    executor_id: str
    enabled: bool = True


ROUTES: tuple[ExecutorRoute, ...] = (
    ExecutorRoute("antigravity-cli"),
    ExecutorRoute("grok-cli"),
    ExecutorRoute("claude-code"),
    ExecutorRoute("codex"),
)

LEGACY_EXECUTOR_IDS = frozenset({"antigravity", "codex_cli", "gemini_cli"})


def supported_executor_ids() -> tuple[str, ...]:
    return tuple(route.executor_id for route in ROUTES if route.enabled)


def default_executor_id() -> str:
    return "antigravity-cli"


def normalize_executor_id(executor_id: str | None) -> str | None:
    if executor_id is None:
        return None
    normalized = executor_id.strip().lower()
    return normalized or None


def is_supported_executor(executor_id: str | None) -> bool:
    normalized = normalize_executor_id(executor_id)
    return normalized in supported_executor_ids()


def is_legacy_executor(executor_id: str | None) -> bool:
    normalized = normalize_executor_id(executor_id)
    return normalized in LEGACY_EXECUTOR_IDS


def validate_supported_executor(executor_id: str | None) -> str:
    normalized = normalize_executor_id(executor_id)
    if normalized and is_supported_executor(normalized):
        return normalized
    allowed = ", ".join(supported_executor_ids())
    if normalized in {"gemini", "gemini_cli"}:
        raise ValueError(f"gemini is no longer supported for new tasks; choose one of: {allowed}")
    raise ValueError(f"executor must be one of: {allowed}")
