from __future__ import annotations

import os
import pathlib

from agpair.executors.local_cli import LocalCLIExecutor
from agpair.models import ContinuationCapability


class GrokCLIExecutor(LocalCLIExecutor):
    def __init__(self, grok_bin: str | None = None) -> None:
        super().__init__(
            bin_path=grok_bin or os.environ.get("AGPAIR_GROK_CLI_BIN") or os.environ.get("AGPAIR_GROK_CLI", "grok"),
            backend_id="grok-cli",
            build_cmd=self._build_grok_cmd,
        )

    def _build_grok_cmd(
        self,
        body: str,
        repo_path: str,
        temp_dir: pathlib.Path,
    ) -> list[str]:
        return [
            self.bin_path,
            "--cwd",
            repo_path,
            "--output-format",
            "json",
            "--always-approve",
            "--single",
            body,
        ]

    @property
    def continuation_capability(self) -> ContinuationCapability:
        return ContinuationCapability.FRESH_RESUME_FIRST
