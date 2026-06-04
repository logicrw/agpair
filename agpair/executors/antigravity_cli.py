from __future__ import annotations

import os
import pathlib
import re

from agpair.executors.local_cli import LocalCLIExecutor
from agpair.models import ContinuationCapability

_GO_DURATION_RE = re.compile(r"^\d+(?:ns|us|ms|s|m|h)(?:\d+(?:ns|us|ms|s|m|h))*$")


def _approval_args() -> list[str]:
    mode = os.environ.get("AGPAIR_ANTIGRAVITY_APPROVAL_MODE", "yolo").strip().lower()
    if mode == "default":
        return []
    if mode in {"yolo", "dangerous", "dangerously-skip-permissions"}:
        return ["--dangerously-skip-permissions"]
    if mode == "auto_edit":
        raise ValueError(
            "agy does not support AGPAIR_ANTIGRAVITY_APPROVAL_MODE=auto_edit; "
            "use default or yolo"
        )
    raise ValueError(
        "Unsupported AGPAIR_ANTIGRAVITY_APPROVAL_MODE; use default or yolo"
    )


def _print_timeout() -> str:
    timeout = os.environ.get("AGPAIR_ANTIGRAVITY_PRINT_TIMEOUT", "30m0s").strip() or "30m0s"
    if not _GO_DURATION_RE.fullmatch(timeout):
        raise ValueError(
            "Unsupported AGPAIR_ANTIGRAVITY_PRINT_TIMEOUT; use a Go-style duration such as 30m0s"
        )
    return timeout


class AntigravityCLIExecutor(LocalCLIExecutor):
    def __init__(self, antigravity_bin: str | None = None) -> None:
        super().__init__(
            bin_path=antigravity_bin or os.environ.get("AGPAIR_ANTIGRAVITY_CLI_BIN") or os.environ.get("AGPAIR_ANTIGRAVITY_CLI", "agy"),
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
            "--add-dir",
            repo_path,
            "--print-timeout",
            _print_timeout(),
            "--print",
            body,
        ]

    @property
    def continuation_capability(self) -> ContinuationCapability:
        return ContinuationCapability.FRESH_RESUME_FIRST
