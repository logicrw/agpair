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
from agpair.storage.journal import JournalRepository
from agpair.storage.tasks import TaskRepository
from agpair.targets import resolve_repo_path
from agpair.terminal_receipts import parse_structured_terminal_receipt

app = typer.Typer(no_args_is_help=True)
hook_app = typer.Typer(no_args_is_help=True)
app.add_typer(hook_app, name="hook")

EXTERNAL_FIRST_CONTEXT = (
    "AGPair external-first routing is available in this repository. For non-trivial "
    "implementation, refactor, test-fix, research, or review work, prefer AGPair "
    "external CLI executors before Claude Code native subagents or background tasks. "
    "Claude Code remains controller and verifier. Use native subagents only when AGPair "
    "is unavailable, unsuitable, or not good enough."
)

SUBAGENT_ADVISORY_CONTEXT = (
    "AGPair external-first routing is active in the parent repo. If this Claude Code "
    "native subagent was started for implementation work only because AGPair external "
    "executors were unavailable or insufficient, stay within the assigned fallback scope "
    "and report why external execution was bypassed."
)

MANAGED_HOOK_COMMANDS = {
    "SessionStart": "agpair claude hook session-start",
    "PreCompact": "agpair claude hook precompact",
    "UserPromptSubmit": "agpair claude hook user-prompt-submit",
    "Stop": "agpair claude hook stop",
    "SubagentStart": "agpair claude hook subagent-start",
    "SubagentStop": "agpair claude hook subagent-stop",
    "TaskCreated": "agpair claude hook task-created",
    "TaskCompleted": "agpair claude hook task-completed",
}


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


def _managed_hook_entry(command: str, *, timeout: int | None = None) -> dict[str, Any]:
    hook: dict[str, Any] = {
        "type": "command",
        "command": command,
    }
    if timeout is not None:
        hook["timeout"] = timeout
    return {"hooks": [hook]}


def _managed_config_payload() -> dict[str, Any]:
    return {
        "statusLine": _managed_statusline(),
        "hooks": {
            "SessionStart": [_managed_hook_entry(MANAGED_HOOK_COMMANDS["SessionStart"])],
            "PreCompact": [_managed_hook_entry(MANAGED_HOOK_COMMANDS["PreCompact"])],
            "UserPromptSubmit": [_managed_hook_entry(MANAGED_HOOK_COMMANDS["UserPromptSubmit"])],
            "Stop": [_managed_hook_entry(MANAGED_HOOK_COMMANDS["Stop"], timeout=30)],
            "SubagentStart": [_managed_hook_entry(MANAGED_HOOK_COMMANDS["SubagentStart"])],
            "SubagentStop": [_managed_hook_entry(MANAGED_HOOK_COMMANDS["SubagentStop"])],
            "TaskCreated": [_managed_hook_entry(MANAGED_HOOK_COMMANDS["TaskCreated"])],
            "TaskCompleted": [_managed_hook_entry(MANAGED_HOOK_COMMANDS["TaskCompleted"])],
        },
    }


def _is_managed_statusline(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and isinstance(value.get("command"), str)
        and value["command"].startswith("agpair claude statusline")
    )


def _managed_hook_command_for_event(event_name: str) -> str | None:
    return MANAGED_HOOK_COMMANDS.get(event_name)


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
        hooks[event_name] = [*foreign_entries, *desired_entries]

    return updated


def _uninstall_managed_config(current: dict[str, Any]) -> dict[str, Any]:
    updated = json.loads(json.dumps(current))

    if _is_managed_statusline(updated.get("statusLine")):
        updated.pop("statusLine", None)

    hooks = updated.get("hooks")
    if isinstance(hooks, dict):
        for event_name in MANAGED_HOOK_COMMANDS:
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


def _hook_specific_output(event_name: str, context: str) -> dict[str, Any]:
    return {
        "hookSpecificOutput": {
            "hookEventName": event_name,
            "additionalContext": context,
        }
    }


def _latest_terminal_receipt(paths: AppPaths, task_id: str):
    journal = JournalRepository(paths.db_path)
    for row in journal.tail(task_id, limit=20):
        if row.event not in {"ready_for_review", "committed", "blocked", "evidence_ready"}:
            continue
        receipt = parse_structured_terminal_receipt(row.body, expected_task_id=task_id)
        if receipt is not None:
            return receipt
    return None


@app.command("statusline")
def statusline() -> None:
    """Read Claude Code statusline JSON on stdin and print a compact AGPair summary."""
    payload = _read_stdin_json()
    repo_path = _resolve_repo_path(payload)
    try:
        task = _most_relevant_claude_task(_paths(), repo_path)
    except Exception:
        task = None
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
    force: bool = typer.Option(False, "--force", help="Replace conflicting AGPair-managed statusLine while preserving non-AGPair hooks."),
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
    try:
        paths = _paths()
        repo_path = _resolve_repo_path(payload)
        if repo_path is None:
            return
        task = _most_relevant_claude_task(paths, repo_path)
    except Exception:
        return
    context = (
        "AGPair external-first routing is available in this repo. "
        "Prefer AGPair external CLI executors for long-running delegated coding tasks. "
        "After dispatch, observe with `agpair task watch <TASK_ID> --json`."
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
    try:
        repo_path = _resolve_repo_path(payload)
        task = _most_relevant_claude_task(_paths(), repo_path)
    except Exception:
        return
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


@hook_app.command("user-prompt-submit")
def hook_user_prompt_submit() -> None:
    """Inject external-first routing context into Claude Code prompts."""
    payload = _read_stdin_json()
    repo_path = _resolve_repo_path(payload)
    if repo_path is None:
        return
    try:
        _paths()
    except Exception:
        return
    _emit_json(_hook_specific_output("UserPromptSubmit", EXTERNAL_FIRST_CONTEXT))


@hook_app.command("stop")
def hook_stop() -> None:
    """Block Claude Code completion only when AGPair has an actionable terminal state."""
    payload = _read_stdin_json()
    repo_path = _resolve_repo_path(payload)
    if repo_path is None:
        return
    try:
        paths = _paths()
        task = TaskRepository(paths.db_path).get_most_relevant_active_task(str(repo_path))
    except Exception:
        return
    if task is None:
        return

    receipt = _latest_terminal_receipt(paths, task.task_id)
    if task.phase in {"ready_for_review", "committed", "evidence_ready"}:
        reason = (
            f"AGPair task {task.task_id} reached ready_for_review. Inspect git status, "
            "diff/commits, receipt, raw log paths, and validation evidence before finalizing."
        )
        _emit_json({"decision": "block", "reason": reason})
        return

    if task.phase == "blocked":
        blocker_type = None
        if receipt is not None:
            raw_blocker_type = receipt.payload.get("blocker_type")
            if isinstance(raw_blocker_type, str):
                blocker_type = raw_blocker_type
        if blocker_type == "approval_required":
            reason = (
                f"AGPair task {task.task_id} is blocked with blocker_type=approval_required. "
                "Do not keep polling. Decide whether to retry with "
                f"`agpair task retry {task.task_id} --from-block --authorization-profile ...` "
                "or report the blocker."
            )
            _emit_json({"decision": "block", "reason": reason})


@hook_app.command("subagent-start")
def hook_subagent_start() -> None:
    """Advise Claude Code native subagents to stay in fallback scope."""
    payload = _read_stdin_json()
    repo_path = _resolve_repo_path(payload)
    if repo_path is None:
        return
    try:
        _paths()
    except Exception:
        return
    _emit_json(_hook_specific_output("SubagentStart", SUBAGENT_ADVISORY_CONTEXT))


@hook_app.command("subagent-stop")
def hook_subagent_stop() -> None:
    """Observability-only hook reserved for future AGPair telemetry."""
    _read_stdin_json()
    try:
        _paths()
    except Exception:
        return


@hook_app.command("task-created")
def hook_task_created() -> None:
    """Observability-only hook reserved for future AGPair telemetry."""
    _read_stdin_json()
    try:
        _paths()
    except Exception:
        return


@hook_app.command("task-completed")
def hook_task_completed() -> None:
    """Observability-only hook reserved for future AGPair telemetry."""
    _read_stdin_json()
    try:
        _paths()
    except Exception:
        return
