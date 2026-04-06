from __future__ import annotations

from agpair.executors.antigravity import AntigravityExecutor
from agpair.executors.base import DispatchResult, ExecutorAdapter, TaskState
from agpair.executors.codex import CodexExecutor
from agpair.executors.gemini import GeminiExecutor

LOCAL_CLI_BACKENDS = frozenset({"codex_cli", "gemini_cli"})

__all__ = [
    "AntigravityExecutor",
    "CodexExecutor",
    "DispatchResult",
    "ExecutorAdapter",
    "GeminiExecutor",
    "LOCAL_CLI_BACKENDS",
    "TaskState",
    "get_executor",
    "is_local_cli_backend",
]


def is_local_cli_backend(backend_id: str | None) -> bool:
    return backend_id in LOCAL_CLI_BACKENDS

def get_executor(backend_id: str, **kwargs) -> ExecutorAdapter | None:
    if backend_id == "codex_cli":
        return CodexExecutor(**kwargs)
    elif backend_id == "gemini_cli":
        return GeminiExecutor(**kwargs)
    elif backend_id == "antigravity":
        return AntigravityExecutor(**kwargs)
    return None
