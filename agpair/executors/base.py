from __future__ import annotations

import dataclasses
import typing

from agpair.models import ContinuationCapability, ExecutorSafetyMetadata


@dataclasses.dataclass
class DispatchResult:
    session_id: str | None = None
    message_id: str | None = None
    execution_repo_path: str | None = None


@dataclasses.dataclass
class TaskState:
    is_done: bool
    receipt: dict[str, typing.Any] | None = None


class ExecutorAdapter(typing.Protocol):
    """Minimal abstraction for task execution."""

    @property
    def backend_id(self) -> str:
        """Return the stable identifier for this executor backend."""
        ...

    @property
    def continuation_capability(self) -> ContinuationCapability:
        """Indicate how this backend handles continuation (e.g. same-session, fresh-resume-first)."""
        ...

    @property
    def safety_metadata(self) -> ExecutorSafetyMetadata:
        """Return static safety properties of this executor backend."""
        ...

    def dispatch(self, *, task_id: str, body: str, repo_path: str, isolated_worktree: bool = False, worktree_boundary: str | None = None) -> DispatchResult:
        """
        Dispatch a task payload to the underlying executor.

        Returns a DispatchResult which may contain a session_id or tracking message_id.
        """
        ...

    def poll(self, task_id: str, session_id: str, attempt_no: int = 1) -> TaskState | None:
        """
        Poll the status of an ongoing task.
        Returns TaskState if polling is supported locally, otherwise None.
        """
        ...

    def cancel(self, task_id: str, session_id: str) -> None:
        """
        Cancel an ongoing task, best-effort.
        """
        ...

    def cleanup(self, session_id: str) -> None:
        """
        Clean up task artifacts given a session_id.
        """
        ...
