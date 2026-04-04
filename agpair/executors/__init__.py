from __future__ import annotations

from agpair.executors.antigravity import AntigravityExecutor
from agpair.executors.base import DispatchResult, ExecutorAdapter, TaskState
from agpair.executors.codex import CodexExecutor
from agpair.executors.gemini import GeminiExecutor

__all__ = ["AntigravityExecutor", "CodexExecutor", "DispatchResult", "ExecutorAdapter", "GeminiExecutor", "TaskState", "get_executor"]

def get_executor(backend_id: str, **kwargs) -> ExecutorAdapter | None:
    if backend_id == "codex_cli":
        return CodexExecutor(**kwargs)
    elif backend_id == "gemini_cli":
        return GeminiExecutor(**kwargs)
    elif backend_id == "antigravity":
        return AntigravityExecutor(**kwargs)
    return None
