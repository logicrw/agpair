from __future__ import annotations

import typing


class ExecutorAdapter(typing.Protocol):
    """Minimal abstraction for task execution."""

    def dispatch(self, *, task_id: str, body: str, repo_path: str) -> int | str:
        """
        Dispatch a task payload to the underlying executor.

        Returns a message ID or an internal tracking reference for the dispatched task.
        """
        ...
