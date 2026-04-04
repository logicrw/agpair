from __future__ import annotations

import typing

from agpair.executors.base import DispatchResult, ExecutorAdapter, TaskState
from agpair.models import ContinuationCapability
from agpair.transport.bus import AgentBusClient


class AntigravityExecutor(ExecutorAdapter):
    """Default executor wrapper that uses the existing agent-bus mechanism."""

    def __init__(self, agent_bus_bin: str = "agent-bus") -> None:
        self.bus = AgentBusClient(executable=agent_bus_bin)

    @property
    def backend_id(self) -> str:
        return "antigravity"

    @property
    def continuation_capability(self) -> ContinuationCapability:
        return ContinuationCapability.SAME_SESSION

    def dispatch(self, *, task_id: str, body: str, repo_path: str) -> DispatchResult:
        """Dispatch via the existing AgentBusClient semantics."""
        msg_id = self.bus.send_task(task_id=task_id, body=body, repo_path=repo_path)
        return DispatchResult(message_id=str(msg_id))

    def poll(self, task_id: str, session_id: str, attempt_no: int = 1) -> TaskState | None:
        """Not yet needed or implemented for Antigravity (handled by external daemon poll)."""
        return None

    def cancel(self, task_id: str, session_id: str) -> None:
        """Best-effort cancellation not supported yet via agent-bus directly."""
        pass

    def cleanup(self, session_id: str) -> None:
        """Nothing to clean up for Antigravity here."""
        pass
