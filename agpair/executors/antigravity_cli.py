from __future__ import annotations

import json
import logging
import os
import pathlib
import re
import tempfile
from urllib.parse import unquote, urlparse

from agpair.executor_errors import prioritize_error_lines
from agpair.executors.local_cli import LocalCLIExecutor
from agpair.executors.registry import executor_safety_metadata
from agpair.models import ContinuationCapability

logger = logging.getLogger(__name__)

_GO_DURATION_RE = re.compile(r"^\d+(?:ns|us|ms|s|m|h)(?:\d+(?:ns|us|ms|s|m|h))*$")
_VENDOR_LOG_NAME = "antigravity-cli.log"
_PROJECTS_DIR_ENV = "AGPAIR_ANTIGRAVITY_PROJECTS_DIR"


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


def _is_relative_to(path: pathlib.Path, base: pathlib.Path) -> bool:
    try:
        path.relative_to(base)
        return True
    except ValueError:
        return False


def _is_agpair_transient_execution_path(path: pathlib.Path) -> bool:
    parts = path.parts
    for index, part in enumerate(parts[:-1]):
        if part == ".agpair" and parts[index + 1] == "worktrees":
            return True

    temp_roots = {
        pathlib.Path(tempfile.gettempdir()).resolve(strict=False),
        pathlib.Path("/tmp").resolve(strict=False),
        pathlib.Path("/private/tmp").resolve(strict=False),
        pathlib.Path("/var/tmp").resolve(strict=False),
        pathlib.Path("/private/var/tmp").resolve(strict=False),
    }
    if not any(_is_relative_to(path, root) for root in temp_roots):
        return False
    return any(part.startswith(("agpair_", "agpair-")) for part in parts)


def _project_resource_path(raw: object) -> pathlib.Path | None:
    value = str(raw or "").strip()
    if not value:
        return None
    if value.startswith("file://"):
        parsed = urlparse(value)
        if parsed.scheme != "file" or not parsed.path:
            return None
        return pathlib.Path(unquote(parsed.path)).expanduser().resolve(strict=False)
    if value.startswith("/") or value.startswith("~"):
        return pathlib.Path(value).expanduser().resolve(strict=False)
    return None


def _project_resource_values(data: object) -> list[object]:
    if not isinstance(data, dict):
        return []
    project_resources = data.get("projectResources", {})
    if not isinstance(project_resources, dict):
        return []
    resources = project_resources.get("resources", [])
    if not isinstance(resources, list):
        return []
    values: list[object] = []
    for resource in resources:
        if not isinstance(resource, dict):
            continue
        for key in ("folderUri", "folderPath", "path", "uri"):
            if key in resource:
                values.append(resource[key])
    return values


def _project_config_matches_path(data: object, target_path: pathlib.Path) -> bool:
    target_uri = target_path.as_uri()
    for value in _project_resource_values(data):
        if str(value or "").strip() == target_uri:
            return True
        resource_path = _project_resource_path(value)
        if resource_path == target_path:
            return True
    return False


def _antigravity_projects_dir() -> pathlib.Path:
    raw = os.environ.get(_PROJECTS_DIR_ENV, "~/.gemini/config/projects")
    return pathlib.Path(raw).expanduser()


def _cleanup_target_from_session(session_id: str) -> pathlib.Path | None:
    if not session_id:
        return None
    temp_dir = pathlib.Path(session_id)
    if not temp_dir.exists() or not temp_dir.name.startswith("agpair_"):
        return None
    state_file = temp_dir / "state.json"
    if not state_file.exists():
        return None
    try:
        state = json.loads(state_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    repo_path = state.get("repo_path") if isinstance(state, dict) else None
    if not repo_path:
        return None
    target_path = pathlib.Path(str(repo_path)).expanduser().resolve(strict=False)
    if not _is_agpair_transient_execution_path(target_path):
        return None
    return target_path


def _remove_antigravity_project_configs(target_path: pathlib.Path) -> int:
    projects_dir = _antigravity_projects_dir()
    if not projects_dir.exists():
        return 0
    removed = 0
    for project_file in projects_dir.glob("*.json"):
        try:
            data = json.loads(project_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if not _project_config_matches_path(data, target_path):
            continue
        try:
            project_file.unlink()
        except OSError:
            logger.debug("Failed to remove Antigravity project config: %s", project_file, exc_info=True)
            continue
        removed += 1
        logger.info("Removed Antigravity project config for AGPair execution path: %s", target_path)
    return removed


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
        target_path = _cleanup_target_from_session(session_id)
        temp_dir = pathlib.Path(session_id) if session_id else None

        super().cleanup(session_id)

        if target_path is None or temp_dir is None or temp_dir.exists():
            return
        _remove_antigravity_project_configs(target_path)

    @property
    def continuation_capability(self) -> ContinuationCapability:
        return ContinuationCapability.FRESH_RESUME_FIRST
