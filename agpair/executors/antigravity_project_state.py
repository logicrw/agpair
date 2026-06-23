from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
import json
import logging
import pathlib
import tempfile
from typing import Callable, Protocol
from urllib.parse import unquote, urlparse

logger = logging.getLogger(__name__)

_PROJECTS_DIR_ENV = "AGPAIR_ANTIGRAVITY_PROJECTS_DIR"
_PROJECTS_CACHE_ENV = "AGPAIR_ANTIGRAVITY_PROJECTS_CACHE"

type JsonValue = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]


@dataclass(frozen=True, slots=True)
class AntigravityProjectStateCleanup:
    config_files: int = 0
    cache_entries: int = 0
    dry_run: bool = False

    @property
    def total(self) -> int:
        return self.config_files + self.cache_entries


class AntigravityTaskPath(Protocol):
    @property
    def phase(self) -> str: ...

    @property
    def executor_backend(self) -> str | None: ...

    @property
    def execution_repo_path(self) -> str | None: ...


def is_agpair_transient_execution_path(path: pathlib.Path) -> bool:
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


def cleanup_target_from_session(session_id: str) -> pathlib.Path | None:
    if not session_id:
        return None
    temp_dir = pathlib.Path(session_id)
    if not temp_dir.exists() or not temp_dir.name.startswith("agpair_"):
        return None
    state_file = temp_dir / "state.json"
    if not state_file.exists():
        return None
    data = _read_json_dict(state_file)
    if data is None:
        return None
    repo_path = data.get("repo_path")
    if not isinstance(repo_path, str) or not repo_path.strip():
        return None
    target_path = pathlib.Path(repo_path).expanduser().resolve(strict=False)
    if not is_agpair_transient_execution_path(target_path):
        return None
    return target_path


def remove_antigravity_project_state(
    target_path: pathlib.Path,
    *,
    dry_run: bool = False,
) -> AntigravityProjectStateCleanup:
    return AntigravityProjectStateCleanup(
        config_files=_remove_project_configs(lambda paths: target_path in paths, dry_run=dry_run),
        cache_entries=_remove_cache_entries(lambda path: path == target_path, dry_run=dry_run),
        dry_run=dry_run,
    )


def sweep_antigravity_transient_projects(
    *,
    active_paths: Iterable[pathlib.Path | str] = (),
    dry_run: bool = False,
) -> AntigravityProjectStateCleanup:
    protected_paths = _normalized_paths(active_paths)
    def should_remove(path: pathlib.Path) -> bool:
        return _should_remove_path(path, protected_paths=protected_paths)

    config_files = _remove_project_configs(lambda paths: any(should_remove(path) for path in paths), dry_run=dry_run)
    cache_entries = _remove_cache_entries(should_remove, dry_run=dry_run)
    return AntigravityProjectStateCleanup(config_files=config_files, cache_entries=cache_entries, dry_run=dry_run)


def active_antigravity_execution_paths(tasks: Iterable[AntigravityTaskPath]) -> frozenset[pathlib.Path]:
    paths: set[pathlib.Path] = set()
    for task in tasks:
        if task.phase != "acked" or task.executor_backend != "antigravity-cli" or task.execution_repo_path is None:
            continue
        path = pathlib.Path(task.execution_repo_path).expanduser().resolve(strict=False)
        if is_agpair_transient_execution_path(path):
            paths.add(path)
    return frozenset(paths)


def _antigravity_projects_dir() -> pathlib.Path:
    import os

    raw = os.environ.get(_PROJECTS_DIR_ENV, "~/.gemini/config/projects")
    return pathlib.Path(raw).expanduser()


def _antigravity_projects_cache() -> pathlib.Path:
    import os

    raw = os.environ.get(_PROJECTS_CACHE_ENV, "~/.gemini/antigravity-cli/cache/projects.json")
    return pathlib.Path(raw).expanduser()


def _is_relative_to(path: pathlib.Path, base: pathlib.Path) -> bool:
    try:
        path.relative_to(base)
    except ValueError:
        return False
    return True


def _normalized_paths(paths: Iterable[pathlib.Path | str]) -> frozenset[pathlib.Path]:
    return frozenset(pathlib.Path(path).expanduser().resolve(strict=False) for path in paths)


def _read_json_dict(path: pathlib.Path) -> dict[str, JsonValue] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def _write_json_dict(path: pathlib.Path, data: dict[str, JsonValue]) -> bool:
    try:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        logger.debug("Failed to update Antigravity CLI project cache: %s", path, exc_info=True)
        return False
    return True


def _project_resource_path(raw: JsonValue) -> pathlib.Path | None:
    if not isinstance(raw, str):
        return None
    value = raw.strip()
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


def _project_resource_values(data: dict[str, JsonValue]) -> list[JsonValue]:
    project_resources = data.get("projectResources")
    if not isinstance(project_resources, dict):
        return []
    resources = project_resources.get("resources")
    if not isinstance(resources, list):
        return []
    values: list[JsonValue] = []
    for resource in resources:
        if not isinstance(resource, dict):
            continue
        for key in ("folderUri", "folderPath", "path", "uri"):
            value = resource.get(key)
            if value is not None:
                values.append(value)
    return values


def _project_config_paths(data: dict[str, JsonValue]) -> list[pathlib.Path]:
    paths: list[pathlib.Path] = []
    for value in _project_resource_values(data):
        resource_path = _project_resource_path(value)
        if resource_path is not None:
            paths.append(resource_path)
    return paths


def _remove_project_configs(
    should_remove: Callable[[list[pathlib.Path]], bool],
    *,
    dry_run: bool,
) -> int:
    projects_dir = _antigravity_projects_dir()
    if not projects_dir.exists():
        return 0
    removed = 0
    for project_file in projects_dir.glob("*.json"):
        data = _read_json_dict(project_file)
        if data is None:
            continue
        if not should_remove(_project_config_paths(data)):
            continue
        if dry_run:
            removed += 1
            continue
        try:
            project_file.unlink()
        except OSError:
            logger.debug("Failed to remove Antigravity project config: %s", project_file, exc_info=True)
            continue
        removed += 1
    return removed


def _remove_cache_entries(should_remove: Callable[[pathlib.Path], bool], *, dry_run: bool) -> int:
    cache_file = _antigravity_projects_cache()
    data = _read_json_dict(cache_file)
    if data is None:
        return 0
    removed_keys = [raw_key for raw_key in data if _raw_key_should_remove(raw_key, should_remove)]
    if not removed_keys:
        return 0
    if dry_run:
        return len(removed_keys)
    for raw_key in removed_keys:
        data.pop(raw_key, None)
    if not _write_json_dict(cache_file, data):
        return 0
    return len(removed_keys)


def _raw_key_should_remove(raw_key: str, should_remove: Callable[[pathlib.Path], bool]) -> bool:
    path = _project_resource_path(raw_key)
    return path is not None and should_remove(path)


def _should_remove_path(path: pathlib.Path, *, protected_paths: frozenset[pathlib.Path]) -> bool:
    return is_agpair_transient_execution_path(path) and path not in protected_paths
