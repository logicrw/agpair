from __future__ import annotations
import os
import pathlib
from agpair.executors.local_cli import LocalCLIExecutor
from agpair.executors.registry import executor_safety_metadata
from agpair.models import ContinuationCapability


def _approval_args() -> list[str]:
    mode = os.environ.get("AGPAIR_CODEX_APPROVAL_MODE", "bypass_all").strip().lower()
    if mode == "default":
        return []
    if mode == "full_auto":
        return ["--full-auto"]
    return ["--dangerously-bypass-approvals-and-sandbox"]


def _config_isolation_args() -> list[str]:
    value = os.environ.get("AGPAIR_CODEX_IGNORE_USER_CONFIG", "1").strip().lower()
    if value in {"0", "false", "no", "off"}:
        return []
    return ["--ignore-user-config", "--ignore-rules"]


class CodexExecutor(LocalCLIExecutor):
    def __init__(self, codex_bin: str | None = None) -> None:
        super().__init__(
            bin_path=codex_bin or os.environ.get("AGPAIR_CODEX_BIN") or os.environ.get("AGPAIR_CODEX_CLI", "codex"),
            backend_id="codex",
            build_cmd=self._build_codex_cmd,
            safety_metadata=executor_safety_metadata("codex"),
        )
    def _build_codex_cmd(self, body: str, repo_path: str, temp_dir: pathlib.Path) -> list[str]:
        last_msg_file = temp_dir / "last_msg.txt"
        return [
            self.bin_path,
            "exec",
            *_approval_args(),
            *_config_isolation_args(),
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
