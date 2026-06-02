from __future__ import annotations

import os
import pathlib

from agpair.executors.local_cli import LocalCLIExecutor
from agpair.models import ContinuationCapability


def _permission_args() -> list[str]:
    mode = os.environ.get("AGPAIR_CLAUDE_CODE_PERMISSION_MODE", "bypassPermissions").strip()
    if not mode or mode == "default":
        return []
    return ["--permission-mode", mode]


class ClaudeCodeExecutor(LocalCLIExecutor):
    def __init__(self, claude_bin: str | None = None) -> None:
        super().__init__(
            bin_path=claude_bin or os.environ.get("AGPAIR_CLAUDE_CODE_CLI", "claude"),
            backend_id="claude-code",
            build_cmd=self._build_claude_cmd,
        )

    def _build_claude_cmd(
        self,
        body: str,
        repo_path: str,
        temp_dir: pathlib.Path,
    ) -> list[str]:
        return [
            self.bin_path,
            *_permission_args(),
            "--output-format",
            "json",
            "--print",
            body,
        ]

    @property
    def continuation_capability(self) -> ContinuationCapability:
        return ContinuationCapability.FRESH_RESUME_FIRST
