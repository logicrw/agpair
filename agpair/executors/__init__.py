from __future__ import annotations

from agpair.executors.antigravity import AntigravityExecutor
from agpair.executors.antigravity_cli import AntigravityCLIExecutor
from agpair.executors.base import DispatchResult, ExecutorAdapter, TaskState
from agpair.executors.claude_code import ClaudeCodeExecutor
from agpair.executors.codex import CodexExecutor
from agpair.executors.gemini import GeminiExecutor
from agpair.executors.grok_cli import GrokCLIExecutor
from agpair.executors.registry import active_executor_ids
from agpair.executors.routing import validate_supported_executor

_LOCAL_CLI_LEGACY_BACKENDS = frozenset({"codex_cli"})
LOCAL_CLI_BACKENDS = frozenset((*active_executor_ids(), *_LOCAL_CLI_LEGACY_BACKENDS))

_EXECUTOR_FACTORIES = {
    "antigravity-cli": lambda **kwargs: AntigravityCLIExecutor(antigravity_bin=kwargs.get("antigravity_bin")),
    "grok-cli": lambda **kwargs: GrokCLIExecutor(grok_bin=kwargs.get("grok_bin")),
    "claude-code": lambda **kwargs: ClaudeCodeExecutor(claude_bin=kwargs.get("claude_bin")),
    "codex": lambda **kwargs: CodexExecutor(codex_bin=kwargs.get("codex_bin")),
}

_LEGACY_FACTORIES = {
    "antigravity": lambda **kwargs: (
        AntigravityExecutor(kwargs["agent_bus_bin"]) if kwargs.get("agent_bus_bin") is not None else AntigravityExecutor(**kwargs)
    ),
    "codex_cli": lambda **kwargs: CodexExecutor(codex_bin=kwargs.get("codex_bin")),
    "gemini_cli": lambda **kwargs: GeminiExecutor(gemini_bin=kwargs.get("gemini_bin")),
}

__all__ = [
    "AntigravityExecutor",
    "AntigravityCLIExecutor",
    "ClaudeCodeExecutor",
    "CodexExecutor",
    "DispatchResult",
    "ExecutorAdapter",
    "GeminiExecutor",
    "GrokCLIExecutor",
    "LOCAL_CLI_BACKENDS",
    "TaskState",
    "get_executor",
    "is_local_cli_backend",
]


def is_local_cli_backend(backend_id: str | None) -> bool:
    if backend_id is None:
        return False
    if backend_id in _LOCAL_CLI_LEGACY_BACKENDS:
        return True
    if backend_id not in _EXECUTOR_FACTORIES:
        return False
    try:
        normalized = validate_supported_executor(backend_id)
    except ValueError:
        return False
    return normalized == backend_id


def get_executor(backend_id: str | None, **kwargs) -> ExecutorAdapter | None:
    if backend_id is None:
        return None
    legacy_key = backend_id.strip().lower()
    legacy_factory = _LEGACY_FACTORIES.get(legacy_key)
    if legacy_factory is not None:
        return legacy_factory(**kwargs)
    try:
        normalized = validate_supported_executor(backend_id)
    except ValueError:
        return None
    factory = _EXECUTOR_FACTORIES.get(normalized)
    if factory is not None:
        return factory(**kwargs)
    return None
