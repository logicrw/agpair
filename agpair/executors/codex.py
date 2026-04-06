from __future__ import annotations
import os
import pathlib
from agpair.executors.local_cli import LocalCLIExecutor
from agpair.models import ContinuationCapability


def _approval_args() -> list[str]:
    mode = os.environ.get("AGPAIR_CODEX_APPROVAL_MODE", "bypass_all").strip().lower()
    if mode == "default":
        return []
    if mode == "full_auto":
        return ["--full-auto"]
    return ["--dangerously-bypass-approvals-and-sandbox"]


class CodexExecutor(LocalCLIExecutor):
    def __init__(self, codex_bin: str = "codex") -> None:
        super().__init__(
            bin_path=codex_bin,
            backend_id="codex_cli",
            build_cmd=self._build_codex_cmd,
        )
    def _build_codex_cmd(self, body: str, repo_path: str, temp_dir: pathlib.Path) -> list[str]:
        last_msg_file = temp_dir / "last_msg.txt"
        return [
            self.bin_path,
            "exec",
            *_approval_args(),
            "--ephemeral",
            "--json",
            "--skip-git-repo-check",
            "-C",
            repo_path,
            "-o",
            str(last_msg_file),
            body,
        ]
    @property
    def continuation_capability(self) -> ContinuationCapability:
        return ContinuationCapability.FRESH_RESUME_FIRST
