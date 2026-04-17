from __future__ import annotations

import json
import subprocess
import sys
from difflib import unified_diff
from pathlib import Path
from typing import Any

import typer

from agpair.config import AppPaths
from agpair.storage.db import ensure_database
from agpair.storage.tasks import TaskRepository
from agpair.targets import resolve_repo_path

app = typer.Typer(no_args_is_help=True)
hook_app = typer.Typer(no_args_is_help=True)
app.add_typer(hook_app, name="hook")


def _paths() -> AppPaths:
    paths = AppPaths.default()
    ensure_database(paths.db_path)
    return paths


def _read_stdin_json() -> dict[str, Any]:
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _git_toplevel(path: Path) -> Path | None:
    try:
        output = subprocess.check_output(
            ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    if not output:
        return None
    return Path(output).resolve()


def _candidate_dirs(payload: dict[str, Any]) -> list[Path]:
    workspace = payload.get("workspace")
    dirs: list[Path] = []
    if isinstance(workspace, dict):
        current_dir = workspace.get("current_dir")
        project_dir = workspace.get("project_dir")
        if isinstance(current_dir, str) and current_dir.strip():
            dirs.append(Path(current_dir).expanduser())
        if isinstance(project_dir, str) and project_dir.strip():
            dirs.append(Path(project_dir).expanduser())
    cwd = payload.get("cwd")
    if isinstance(cwd, str) and cwd.strip():
        dirs.append(Path(cwd).expanduser())
    deduped: list[Path] = []
    seen: set[str] = set()
    for path in dirs:
        key = str(path)
        if key not in seen:
            seen.add(key)
            deduped.append(path)
    return deduped


def _resolve_repo_path(payload: dict[str, Any]) -> Path | None:
    for candidate in _candidate_dirs(payload):
        try:
            if not candidate.exists():
                continue
        except OSError:
            continue
        repo_root = _git_toplevel(candidate)
        if repo_root is not None:
            return repo_root
        return candidate.resolve()
    return None


def _most_relevant_claude_task(paths: AppPaths, repo_path: Path | None):
    if repo_path is None:
        return None
    task = TaskRepository(paths.db_path).get_most_relevant_active_task(str(repo_path))
    if task is None or task.phase in {"committed", "abandoned"}:
        return None
    return task


def _git_worktree_name(payload: dict[str, Any]) -> str | None:
    workspace = payload.get("workspace")
    if not isinstance(workspace, dict):
        return None
    git_worktree = workspace.get("git_worktree")
    if not isinstance(git_worktree, str):
        return None
    name = git_worktree.strip()
    return name or None


def _emit_json(payload: dict[str, Any]) -> None:
    typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))


def _managed_statusline() -> dict[str, Any]:
    return {
        "type": "command",
        "command": "agpair claude statusline",
        "refreshInterval": 5,
    }


def _managed_hook_entry(command: str) -> dict[str, Any]:
    return {
        "hooks": [
            {
                "type": "command",
                "command": command,
            }
        ]
    }


def _managed_config_payload() -> dict[str, Any]:
    return {
        "statusLine": _managed_statusline(),
        "hooks": {
            "SessionStart": [_managed_hook_entry("agpair claude hook session-start")],
            "PreCompact": [_managed_hook_entry("agpair claude hook precompact")],
        },
    }


def _is_managed_statusline(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and isinstance(value.get("command"), str)
        and value["command"].startswith("agpair claude statusline")
    )


def _managed_hook_command_for_event(event_name: str) -> str | None:
    if event_name == "SessionStart":
        return "agpair claude hook session-start"
    if event_name == "PreCompact":
        return "agpair claude hook precompact"
    return None


def _is_managed_hook_entry(event_name: str, entry: Any) -> bool:
    expected = _managed_hook_command_for_event(event_name)
    if expected is None or not isinstance(entry, dict):
        return False
    hooks = entry.get("hooks")
    if not isinstance(hooks, list):
        return False
    commands = [
        hook.get("command")
        for hook in hooks
        if isinstance(hook, dict) and isinstance(hook.get("command"), str)
    ]
    return expected in commands


def _project_settings_path(paths: AppPaths, repo_path: str | None, target: str | None) -> Path:
    resolved = resolve_repo_path(repo_path, target, paths)
    if resolved:
        base = Path(resolved).expanduser().resolve()
    else:
        base = _git_toplevel(Path.cwd()) or Path.cwd().resolve()
    return base / ".claude" / "settings.json"


def _settings_path(*, scope: str, paths: AppPaths, repo_path: str | None, target: str | None) -> Path:
    if scope == "user":
        return Path.home() / ".claude" / "settings.json"
    return _project_settings_path(paths, repo_path, target)


def _load_settings(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Failed to parse existing settings JSON at {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"Expected settings JSON object at {path}")
    return payload


def _render_settings(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def _merge_managed_config(current: dict[str, Any], *, force: bool) -> dict[str, Any]:
    updated = json.loads(json.dumps(current))
    managed = _managed_config_payload()

    existing_statusline = updated.get("statusLine")
    if existing_statusline is None or _is_managed_statusline(existing_statusline):
        updated["statusLine"] = managed["statusLine"]
    elif force:
        updated["statusLine"] = managed["statusLine"]
    else:
        raise RuntimeError(
            "Existing statusLine is not managed by AGPair. Refusing to overwrite; manual merge or --force required."
        )

    hooks = updated.get("hooks")
    if hooks is None:
        hooks = {}
        updated["hooks"] = hooks
    elif not isinstance(hooks, dict):
        raise RuntimeError("Existing hooks value is not a JSON object; manual merge required.")

    managed_hooks = managed["hooks"]
    for event_name, desired_entries in managed_hooks.items():
        existing_entries = hooks.get(event_name)
        if existing_entries is None:
            hooks[event_name] = desired_entries
            continue
        if not isinstance(existing_entries, list):
            raise RuntimeError(f"Existing hooks.{event_name} is not a list; manual merge required.")
        foreign_entries = [
            entry for entry in existing_entries if not _is_managed_hook_entry(event_name, entry)
        ]
        if foreign_entries and not force:
            raise RuntimeError(
                f"Existing hooks.{event_name} contains non-AGPair entries. Refusing to merge; manual merge or --force required."
            )
        hooks[event_name] = desired_entries

    return updated


def _uninstall_managed_config(current: dict[str, Any]) -> dict[str, Any]:
    updated = json.loads(json.dumps(current))

    if _is_managed_statusline(updated.get("statusLine")):
        updated.pop("statusLine", None)

    hooks = updated.get("hooks")
    if isinstance(hooks, dict):
        for event_name in ("SessionStart", "PreCompact"):
            existing_entries = hooks.get(event_name)
            if not isinstance(existing_entries, list):
                continue
            remaining = [
                entry for entry in existing_entries if not _is_managed_hook_entry(event_name, entry)
            ]
            if remaining:
                hooks[event_name] = remaining
            else:
                hooks.pop(event_name, None)
        if not hooks:
            updated.pop("hooks", None)

    return updated


def _emit_diff(path: Path, before: str, after: str) -> None:
    before_lines = before.splitlines(keepends=True)
    after_lines = after.splitlines(keepends=True)
    diff = "".join(
        unified_diff(
            before_lines,
            after_lines,
            fromfile=str(path),
            tofile=str(path),
        )
    )
    if diff:
        typer.echo(diff, nl=False)


@app.command("statusline")
def statusline() -> None:
    """Read Claude Code statusline JSON on stdin and print a compact AGPair summary."""
    payload = _read_stdin_json()
    repo_path = _resolve_repo_path(payload)
    task = _most_relevant_claude_task(_paths(), repo_path)
    parts = ["agpair"]
    if task is None:
        parts.append("idle")
    else:
        parts.extend([task.phase, task.task_id])
    worktree_name = _git_worktree_name(payload)
    if worktree_name:
        parts.append(f"wt:{worktree_name}")
    typer.echo(" ".join(parts))


@app.command("config")
def config(
    install: bool = typer.Option(False, "--install", help="Write or update the AGPair-managed Claude Code config fragment."),
    merge: bool = typer.Option(False, "--merge", help="Alias of --install for explicit merge/update flows."),
    uninstall: bool = typer.Option(False, "--uninstall", help="Remove the AGPair-managed Claude Code config fragment."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print a unified diff instead of writing changes."),
    force: bool = typer.Option(False, "--force", help="Replace conflicting AGPair-managed keys under statusLine/SessionStart/PreCompact."),
    scope: str = typer.Option("project", "--scope", help="Where to manage Claude Code settings: project or user."),
    repo_path: str | None = typer.Option(None, "--repo-path", help="Project repo path for --scope project."),
    target: str | None = typer.Option(None, "--target", help="Target alias for --scope project."),
) -> None:
    """Emit or manage a Claude Code settings snippet for AGPair statusline and lightweight hooks."""
    if scope not in {"project", "user"}:
        raise typer.BadParameter("--scope must be 'project' or 'user'")
    if uninstall and (install or merge):
        raise typer.BadParameter("Cannot combine --uninstall with --install/--merge")

    write_mode = install or merge or uninstall
    if not write_mode:
        _emit_json(_managed_config_payload())
        return

    paths = _paths()
    settings_path = _settings_path(scope=scope, paths=paths, repo_path=repo_path, target=target)
    try:
        current = _load_settings(settings_path)
        before = _render_settings(current) if current else ""
        if uninstall:
            updated = _uninstall_managed_config(current)
        else:
            updated = _merge_managed_config(current, force=force)
    except RuntimeError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1)
    after = _render_settings(updated) if updated else ""

    if dry_run:
        _emit_diff(settings_path, before, after)
        return

    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(after, encoding="utf-8")
    typer.echo(f"Updated {settings_path}")


@hook_app.command("session-start")
def hook_session_start() -> None:
    """Emit SessionStart hook context that nudges Claude Code toward AGPair for durable task orchestration."""
    payload = _read_stdin_json()
    paths = _paths()
    repo_path = _resolve_repo_path(payload)
    if repo_path is None:
        return
    task = _most_relevant_claude_task(paths, repo_path)
    context = (
        "AGPair is available for durable task orchestration in this repo. "
        "Prefer the agpair MCP tools or skill for long-running delegated coding tasks. "
        "After dispatch, start Monitor with `agpair task watch <TASK_ID> --json`."
    )
    if task is not None:
        context += f" Current AGPair task: {task.task_id} ({task.phase})."
    _emit_json(
        {
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": context,
            }
        }
    )


@hook_app.command("precompact")
def hook_precompact() -> None:
    """Block compaction only for repo tasks in acked/evidence_ready; other visible states may still show in statusline without blocking."""
    payload = _read_stdin_json()
    repo_path = _resolve_repo_path(payload)
    task = _most_relevant_claude_task(_paths(), repo_path)
    if task is None or task.phase not in {"acked", "evidence_ready"}:
        return
    _emit_json(
        {
            "decision": "block",
            "reason": (
                f"AGPair task {task.task_id} is still {task.phase}. "
                f"Check `agpair task status {task.task_id}` or wait for a terminal state before compacting."
            ),
        }
    )
