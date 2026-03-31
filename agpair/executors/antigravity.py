from __future__ import annotations

from agpair.executors.base import ExecutorAdapter
from agpair.transport.bus import AgentBusClient


class AntigravityExecutor(ExecutorAdapter):
    """Default executor wrapper that uses the existing agent-bus mechanism."""

    def __init__(self, agent_bus_bin: str = "agent-bus") -> None:
        self.bus = AgentBusClient(executable=agent_bus_bin)

    def dispatch(self, *, task_id: str, body: str, repo_path: str) -> int:
        """Dispatch via the existing AgentBusClient semantics."""
        return self.bus.send_task(task_id=task_id, body=body, repo_path=repo_path)

    def poll(self, task_ref: int) -> None:
        """Not yet needed or implemented for Antigravity (handled by external daemon poll)."""
        pass

    def cancel(self, task_ref: int) -> None:
        """Best-effort cancellation not supported yet via agent-bus directly."""
        pass
