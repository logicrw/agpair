from __future__ import annotations

import os
import pathlib

from agpair.executors.local_cli import LocalCLIExecutor
from agpair.executors.registry import executor_safety_metadata
from agpair.models import ContinuationCapability


def _permission_args() -> list[str]:
    mode = os.environ.get("AGPAIR_CLAUDE_CODE_PERMISSION_MODE", "bypassPermissions").strip()
    if not mode or mode == "default":
        return []
    return ["--permission-mode", mode]


def _auth_mode() -> str:
    explicit = os.environ.get("AGPAIR_CLAUDE_CODE_AUTH_MODE", "").strip().lower()
    if explicit in {"api", "bare"}:
        return "api"
    if explicit in {"oauth", "subscription"}:
        return "oauth"
    legacy_bare = os.environ.get("AGPAIR_CLAUDE_CODE_BARE")
    if legacy_bare is not None and legacy_bare.strip().lower() not in {"0", "false", "no", "off"}:
        return "api"
    return "oauth"


def _retry_env_args() -> list[str]:
    retries = os.environ.get("AGPAIR_CLAUDE_CODE_MAX_RETRIES", "0").strip() or "0"
    return ["env", f"CLAUDE_CODE_MAX_RETRIES={retries}"]


def _bare_args() -> list[str]:
    return ["--bare"] if _auth_mode() == "api" else []


def _oauth_profile_args() -> list[str]:
    if _auth_mode() != "oauth":
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
            bin_path=claude_bin or os.environ.get("AGPAIR_CLAUDE_CODE_BIN") or os.environ.get("AGPAIR_CLAUDE_CODE_CLI", "claude"),
            backend_id="claude-code",
            build_cmd=self._build_claude_cmd,
            safety_metadata=executor_safety_metadata("claude-code"),
        )

    def _build_claude_cmd(
        self,
        body: str,
        repo_path: str,
        temp_dir: pathlib.Path,
    ) -> list[str]:
        return [
            *_retry_env_args(),
            self.bin_path,
            *_bare_args(),
            *_oauth_profile_args(),
            *_settings_args(),
            *_permission_args(),
            "--no-session-persistence",
            "--output-format",
            "json",
            "--print",
            body,
        ]

    @property
    def continuation_capability(self) -> ContinuationCapability:
        return ContinuationCapability.FRESH_RESUME_FIRST
