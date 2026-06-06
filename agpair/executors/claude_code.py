from __future__ import annotations

import os
import pathlib

from agpair.executors.claude_auth import (
    ccswitch_env_overrides,
    claude_retry_env,
    explicit_claude_auth_mode,
    load_current_ccswitch_provider,
    resolve_claude_auth,
)
from agpair.executors.local_cli import LocalCLIExecutor
from agpair.executors.registry import executor_safety_metadata
from agpair.models import ContinuationCapability


def _permission_args() -> list[str]:
    mode = os.environ.get("AGPAIR_CLAUDE_CODE_PERMISSION_MODE", "bypassPermissions").strip()
    if not mode or mode == "default":
        return []
    return ["--permission-mode", mode]


def _auth_mode(binary_path: str | None = None, *, live_probe: bool = False) -> str:
    explicit = explicit_claude_auth_mode()
    if explicit:
        return explicit
    resolution = resolve_claude_auth(binary_path, live_probe=live_probe)
    return resolution.mode if resolution.error is None else "oauth"


def _retry_env_args() -> list[str]:
    retries = claude_retry_env()["CLAUDE_CODE_MAX_RETRIES"]
    return ["env", f"CLAUDE_CODE_MAX_RETRIES={retries}"]


def _bare_args(auth_mode: str) -> list[str]:
    return ["--bare"] if auth_mode in {"api", "ccswitch"} else []


def _oauth_profile_args(auth_mode: str) -> list[str]:
    if auth_mode != "oauth":
        return []
    profile = os.environ.get("AGPAIR_CLAUDE_CODE_OAUTH_PROFILE", "quiet").strip().lower()
    if profile in {"natural", "full", "inherit"}:
        return []
    return [
        "--strict-mcp-config",
        "--mcp-config",
        '{"mcpServers":{}}',
        "--disable-slash-commands",
        "--no-chrome",
    ]


def _settings_args() -> list[str]:
    value = os.environ.get("AGPAIR_CLAUDE_CODE_SETTINGS", "").strip()
    if not value:
        return []
    return ["--settings", value]


class ClaudeCodeExecutor(LocalCLIExecutor):
    def __init__(self, claude_bin: str | None = None) -> None:
        super().__init__(
            bin_path=(
                claude_bin
                or os.environ.get("AGPAIR_CLAUDE_CODE_BIN")
                or os.environ.get("AGPAIR_CLAUDE_CODE_CLI", "claude")
            ),
            backend_id="claude-code",
            build_cmd=self._build_claude_cmd,
            build_env=self._build_claude_env,
            safety_metadata=executor_safety_metadata("claude-code"),
        )

    def _build_claude_cmd(
        self,
        body: str,
        repo_path: str,
        temp_dir: pathlib.Path,
    ) -> list[str]:
        del repo_path, temp_dir
        auth_mode = _auth_mode(self.bin_path, live_probe=True)
        return [
            *_retry_env_args(),
            self.bin_path,
            *_bare_args(auth_mode),
            *_oauth_profile_args(auth_mode),
            *_settings_args(),
            *_permission_args(),
            "--no-session-persistence",
            "--output-format",
            "json",
            "--print",
            body,
        ]

    def _build_claude_env(
        self,
        body: str,
        repo_path: str,
        temp_dir: pathlib.Path,
    ) -> dict[str, str]:
        del body, repo_path, temp_dir
        if _auth_mode(self.bin_path, live_probe=True) == "ccswitch":
            return ccswitch_env_overrides(load_current_ccswitch_provider())
        return claude_retry_env()

    @property
    def continuation_capability(self) -> ContinuationCapability:
        return ContinuationCapability.FRESH_RESUME_FIRST
