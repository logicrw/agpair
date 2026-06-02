from __future__ import annotations

import os
import pathlib

from agpair.executors.local_cli import LocalCLIExecutor
from agpair.models import ContinuationCapability


def _approval_args() -> list[str]:
    mode = os.environ.get("AGPAIR_ANTIGRAVITY_APPROVAL_MODE", "yolo").strip().lower()
    if mode == "default":
        return []
    if mode == "auto_edit":
        return ["--approval-mode", "auto_edit"]
    return ["-y"]


class AntigravityCLIExecutor(LocalCLIExecutor):
    def __init__(self, antigravity_bin: str | None = None) -> None:
        super().__init__(
            bin_path=antigravity_bin or os.environ.get("AGPAIR_ANTIGRAVITY_CLI", "antigravity"),
            backend_id="antigravity-cli",
            build_cmd=self._build_antigravity_cmd,
        )

    def _build_antigravity_cmd(
        self,
        body: str,
        repo_path: str,
        temp_dir: pathlib.Path,
    ) -> list[str]:
        return [
            self.bin_path,
            *_approval_args(),
            "--output-format",
            "json",
            "-p",
            body,
        ]

    @property
    def continuation_capability(self) -> ContinuationCapability:
        return ContinuationCapability.FRESH_RESUME_FIRST
