from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import typer

from agpair.config import AppPaths
from agpair.storage.db import ensure_database
from agpair.storage.tasks import TaskRepository

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
def config() -> None:
    """Emit a Claude Code settings snippet for AGPair statusline and lightweight hooks."""
    _emit_json(
        {
            "statusLine": {
                "type": "command",
                "command": "agpair claude statusline",
                "refreshInterval": 5,
            },
            "hooks": {
                "SessionStart": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": "agpair claude hook session-start",
                            }
                        ]
                    }
                ],
                "PreCompact": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": "agpair claude hook precompact",
                            }
                        ]
                    }
                ],
            },
        }
    )


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
    """Block compaction while an AGPair task is still live in the current repo."""
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
