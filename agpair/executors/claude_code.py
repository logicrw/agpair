from __future__ import annotations

import os
import pathlib

from agpair.executors.claude_auth import (
    ClaudeAuthResolution,
    FALSE_ENV_VALUES,
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


def _retry_env_args() -> list[str]:
    retries = claude_retry_env()["CLAUDE_CODE_MAX_RETRIES"]
    return ["env", f"CLAUDE_CODE_MAX_RETRIES={retries}"]


def _environment_mode() -> str:
    value = os.environ.get("AGPAIR_CLAUDE_CODE_ENVIRONMENT_MODE", "").strip().lower()
    if not value:
        legacy_bare = os.environ.get("AGPAIR_CLAUDE_CODE_BARE", "").strip().lower()
        value = "isolated-bare" if legacy_bare and legacy_bare not in FALSE_ENV_VALUES else "managed-natural"
    if value not in {"managed-natural", "isolated-bare", "diagnostic-natural"}:
        raise ValueError(
            "Unsupported AGPAIR_CLAUDE_CODE_ENVIRONMENT_MODE; use managed-natural, isolated-bare, or diagnostic-natural"
        )
    return value


def _bare_args() -> list[str]:
    if _environment_mode() != "isolated-bare":
        return []
    return [
        "--bare",
        "--strict-mcp-config",
        "--mcp-config",
        '{"mcpServers":{}}',
        "--disable-slash-commands",
        "--no-chrome",
    ]


def _oauth_profile_args(auth_mode: str) -> list[str]:
    if _environment_mode() != "managed-natural":
        return []
    if auth_mode != "oauth":
        return []
    profile = os.environ.get("AGPAIR_CLAUDE_CODE_OAUTH_PROFILE", "natural").strip().lower()
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
        self._auth_resolution: ClaudeAuthResolution | None = None
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

    def _resolve_auth(self) -> ClaudeAuthResolution:
        if self._auth_resolution is None:
            self._auth_resolution = resolve_claude_auth(
                self.bin_path,
                live_probe=explicit_claude_auth_mode() is None,
            )
        return self._auth_resolution

    def _build_claude_cmd(
        self,
        body: str,
        repo_path: str,
        temp_dir: pathlib.Path,
    ) -> list[str]:
        del repo_path, temp_dir
        resolution = self._resolve_auth()
        auth_mode = resolution.mode if resolution.error is None else "oauth"
        return [
            *_retry_env_args(),
            self.bin_path,
            *_bare_args(),
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
        resolution = self._resolve_auth()
        if resolution.error is None and resolution.mode == "ccswitch":
            return resolution.env_overrides or ccswitch_env_overrides(load_current_ccswitch_provider())
        return claude_retry_env()

    @property
    def continuation_capability(self) -> ContinuationCapability:
        return ContinuationCapability.FRESH_RESUME_FIRST
