from __future__ import annotations

import os
import pathlib

from agpair.executors.claude_auth import (
    ClaudeAuthResolution,
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


def _settings_args() -> list[str]:
    value = os.environ.get("AGPAIR_CLAUDE_CODE_SETTINGS", "").strip()
    if not value:
        return []
    return ["--settings", value]


def _debug_args(temp_dir: pathlib.Path) -> list[str]:
    value = os.environ.get("AGPAIR_CLAUDE_CODE_DEBUG_FILE", "").strip()
    if value.lower() in {"0", "false", "no", "off"}:
        return []
    debug_path = pathlib.Path(value) if value else temp_dir / "claude-code-debug.log"
    return ["--debug-file", str(debug_path)]


def _chrome_args() -> list[str]:
    value = os.environ.get("AGPAIR_CLAUDE_CODE_CHROME", "").strip().lower()
    if value in {"1", "true", "yes", "on", "enable", "enabled"}:
        return []
    return ["--no-chrome"]


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
        del repo_path
        return [
            *_retry_env_args(),
            self.bin_path,
            *_settings_args(),
            *_debug_args(temp_dir),
            *_chrome_args(),
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

    def _raw_log_payload(self, temp_dir: pathlib.Path) -> dict:
        payload = super()._raw_log_payload(temp_dir)
        debug_args = _debug_args(temp_dir)
        if debug_args:
            payload["debug_log_path"] = debug_args[1]
        return payload

    @property
    def continuation_capability(self) -> ContinuationCapability:
        return ContinuationCapability.FRESH_RESUME_FIRST
