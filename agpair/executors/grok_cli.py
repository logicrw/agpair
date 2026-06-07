from __future__ import annotations

import os
import pathlib

from agpair.executors.local_cli import LocalCLIExecutor
from agpair.executors.registry import executor_safety_metadata
from agpair.models import ContinuationCapability


def _output_format() -> str:
    value = os.environ.get("AGPAIR_GROK_OUTPUT_FORMAT", "json").strip() or "json"
    if value not in {"json", "streaming-json"}:
        raise ValueError("Unsupported AGPAIR_GROK_OUTPUT_FORMAT; use json or streaming-json")
    return value


def _max_turn_args() -> list[str]:
    value = os.environ.get("AGPAIR_GROK_MAX_TURNS", "24").strip()
    if not value:
        return []
    if not value.isdigit() or int(value) <= 0:
        raise ValueError("Unsupported AGPAIR_GROK_MAX_TURNS; use a positive integer or unset it")
    return ["--max-turns", value]


def _environment_mode() -> str:
    value = os.environ.get("AGPAIR_GROK_ENVIRONMENT_MODE", "managed-natural").strip() or "managed-natural"
    if value not in {"managed-natural", "managed-restricted"}:
        raise ValueError("Unsupported AGPAIR_GROK_ENVIRONMENT_MODE; use managed-natural or managed-restricted")
    return value


def _restricted_args() -> list[str]:
    if _environment_mode() != "managed-restricted":
        return []
    return ["--no-memory", "--no-subagents", "--disable-web-search"]


class GrokCLIExecutor(LocalCLIExecutor):
    def __init__(self, grok_bin: str | None = None) -> None:
        super().__init__(
            bin_path=grok_bin or os.environ.get("AGPAIR_GROK_CLI_BIN") or os.environ.get("AGPAIR_GROK_CLI", "grok"),
            backend_id="grok-cli",
            build_cmd=self._build_grok_cmd,
            safety_metadata=executor_safety_metadata("grok-cli"),
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
            _output_format(),
            "--always-approve",
            *_max_turn_args(),
            *_restricted_args(),
            "--single",
            body,
        ]

    @property
    def continuation_capability(self) -> ContinuationCapability:
        return ContinuationCapability.FRESH_RESUME_FIRST
