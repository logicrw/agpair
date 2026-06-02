from __future__ import annotations

from agpair.executors.antigravity import AntigravityExecutor
from agpair.executors.antigravity_cli import AntigravityCLIExecutor
from agpair.executors.base import DispatchResult, ExecutorAdapter, TaskState
from agpair.executors.claude_code import ClaudeCodeExecutor
from agpair.executors.codex import CodexExecutor
from agpair.executors.gemini import GeminiExecutor
from agpair.executors.grok_cli import GrokCLIExecutor

LOCAL_CLI_BACKENDS = frozenset({"antigravity-cli", "grok-cli", "claude-code", "codex", "codex_cli"})

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
    return backend_id in LOCAL_CLI_BACKENDS

def get_executor(backend_id: str | None, **kwargs) -> ExecutorAdapter | None:
    if backend_id == "antigravity-cli":
        return AntigravityCLIExecutor(antigravity_bin=kwargs.get("antigravity_bin"))
    if backend_id == "grok-cli":
        return GrokCLIExecutor(grok_bin=kwargs.get("grok_bin"))
    if backend_id == "claude-code":
        return ClaudeCodeExecutor(claude_bin=kwargs.get("claude_bin"))
    if backend_id in {"codex", "codex_cli"}:
        codex_bin = kwargs.get("codex_bin", "codex")
        return CodexExecutor(codex_bin=codex_bin)
    if backend_id == "gemini_cli":
        gemini_bin = kwargs.get("gemini_bin", "gemini")
        return GeminiExecutor(gemini_bin=gemini_bin)
    if backend_id == "antigravity":
        agent_bus_bin = kwargs.get("agent_bus_bin")
        return AntigravityExecutor(agent_bus_bin) if agent_bus_bin is not None else AntigravityExecutor(**kwargs)
    return None
