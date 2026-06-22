from __future__ import annotations


from agpair.executors.base import DispatchResult, ExecutorAdapter, TaskState
from agpair.models import ContinuationCapability, ExecutorSafetyMetadata
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

    @property
    def safety_metadata(self) -> ExecutorSafetyMetadata:
        return ExecutorSafetyMetadata(
            is_mutating=True,
            is_concurrency_safe=False,
            requires_human_interaction=False,
        )

    def dispatch(
        self,
        *,
        task_id: str,
        body: str,
        repo_path: str,
        isolated_worktree: bool = False,
        worktree_boundary: str | None = None,
        authorization_profile: str = "local_mutating",
        authorization_summary: str | None = None,
        environment_mode: str | None = None,
        skill_policy: str | None = None,
        mcp_policy: str | None = None,
        dirty_snapshot_mode: str = "off",
        completion_policy: str = "auto",
    ) -> DispatchResult:
        """Dispatch via the existing AgentBusClient semantics."""
        del isolated_worktree, worktree_boundary, authorization_profile, authorization_summary
        del environment_mode, skill_policy, mcp_policy, dirty_snapshot_mode, completion_policy
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
