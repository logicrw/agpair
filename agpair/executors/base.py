from __future__ import annotations

import typing

from agpair.models import ContinuationCapability


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

    def dispatch(self, *, task_id: str, body: str, repo_path: str) -> typing.Any:
        """
        Dispatch a task payload to the underlying executor.

        Returns a message ID or an internal tracking reference for the dispatched task.
        """
        ...

    def poll(self, task_ref: typing.Any) -> typing.Any:
        """
        Poll the status of an ongoing task.
        """
        ...

    def cancel(self, task_ref: typing.Any) -> None:
        """
        Cancel an ongoing task, best-effort.
        """
        ...
