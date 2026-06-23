from __future__ import annotations

import os
import pathlib
import re

from agpair.executor_errors import prioritize_error_lines
from agpair.executors.antigravity_project_state import cleanup_target_from_session, remove_antigravity_project_state
from agpair.executors.local_cli import LocalCLIExecutor
from agpair.executors.registry import executor_safety_metadata
from agpair.models import ContinuationCapability

_GO_DURATION_RE = re.compile(r"^\d+(?:ns|us|ms|s|m|h)(?:\d+(?:ns|us|ms|s|m|h))*$")
_VENDOR_LOG_NAME = "antigravity-cli.log"


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


def _model_args() -> list[str]:
    model = (
        os.environ.get("AGPAIR_ANTIGRAVITY_MODEL", "").strip()
        or os.environ.get("AGPAIR_ANTIGRAVITY_CLI_MODEL", "").strip()
    )
    return ["--model", model] if model else []


class AntigravityCLIExecutor(LocalCLIExecutor):
    def __init__(self, antigravity_bin: str | None = None) -> None:
        super().__init__(
            bin_path=(
                antigravity_bin
                or os.environ.get("AGPAIR_ANTIGRAVITY_CLI_BIN")
                or os.environ.get("AGPAIR_ANTIGRAVITY_CLI", "agy")
            ),
            backend_id="antigravity-cli",
            build_cmd=self._build_antigravity_cmd,
            safety_metadata=executor_safety_metadata("antigravity-cli"),
        )

    def _build_antigravity_cmd(
        self,
        body: str,
        repo_path: str,
        temp_dir: pathlib.Path,
    ) -> list[str]:
        return [
            self.bin_path,
            *_model_args(),
            *_approval_args(),
            "--add-dir",
            repo_path,
            "--print-timeout",
            _print_timeout(),
            "--log-file",
            str(temp_dir / _VENDOR_LOG_NAME),
            "--print",
            body,
        ]

    def _raw_log_payload(self, temp_dir: pathlib.Path) -> dict:
        payload = super()._raw_log_payload(temp_dir)
        payload["vendor_log_path"] = str(temp_dir / _VENDOR_LOG_NAME)
        return payload

    def _extract_error_summary(self, temp_dir: pathlib.Path, max_chars: int = 500) -> str:
        summary = super()._extract_error_summary(temp_dir, max_chars=max_chars)
        if summary and summary != "No output captured":
            return summary
        vendor_log = temp_dir / _VENDOR_LOG_NAME
        if not vendor_log.exists():
            return summary
        lines = vendor_log.read_text(encoding="utf-8", errors="replace").splitlines()
        focused = prioritize_error_lines(lines, max_lines=12)
        return "\n".join(focused)[:max_chars] or summary

    def cleanup(self, session_id: str) -> None:
        target_path = cleanup_target_from_session(session_id)
        temp_dir = pathlib.Path(session_id) if session_id else None

        super().cleanup(session_id)

        if target_path is None or temp_dir is None or temp_dir.exists():
            return
        remove_antigravity_project_state(target_path)

    @property
    def continuation_capability(self) -> ContinuationCapability:
        return ContinuationCapability.FRESH_RESUME_FIRST
