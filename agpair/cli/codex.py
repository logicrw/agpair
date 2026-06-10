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
from agpair.cli.skill_sync import bundled_skill_path, plan_skill_sync

app = typer.Typer(no_args_is_help=True)
hook_app = typer.Typer(no_args_is_help=True)
app.add_typer(hook_app, name="hook")

EXTERNAL_FIRST_CONTEXT = (
    "AGPair external-first routing is available in this repository. For non-trivial "
    "implementation, refactor, test-fix, research, or review work, prefer dispatching "
    "through AGPair external CLI executors before using Codex native subagents. For code "
    "changes, prefer a bounded `--completion-policy evidence --isolated-worktree` slice, "
    "then inspect status JSON protocol/adoption results and accept/adopt after verification. "
    "Codex main remains the controller and verifier. Use native subagents only when AGPair "
    "is unavailable, an external executor is unsuitable, or external results are not good enough."
)

SUBAGENT_ADVISORY_CONTEXT = (
    "AGPair external-first routing is active in the parent repo. If this native subagent "
    "was started for implementation work only because external agents were unavailable "
    "or insufficient, stay within the assigned fallback scope and report why external "
    "execution was bypassed."
)


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
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return None
    return Path(output).resolve() if output else None


def _resolve_repo_from_hook(payload: dict[str, Any]) -> Path | None:
    candidates: list[Path] = []
    cwd = payload.get("cwd")
    if isinstance(cwd, str) and cwd.strip():
        candidates.append(Path(cwd).expanduser())
    workspace = payload.get("workspace")
    if isinstance(workspace, dict):
        for key in ("current_dir", "project_dir"):
            value = workspace.get(key)
            if isinstance(value, str) and value.strip():
                candidates.append(Path(value).expanduser())
    for candidate in candidates:
        try:
            if not candidate.exists():
                continue
        except OSError:
            continue
        repo = _git_toplevel(candidate)
        if repo is not None:
            return repo
        return candidate.resolve()
    return None


def _managed_hook_entry(command: str, *, timeout: int | None = None) -> dict[str, Any]:
    hook: dict[str, Any] = {"type": "command", "command": command}
    if timeout is not None:
        hook["timeout"] = timeout
    return {"hooks": [hook]}


def _managed_config_payload() -> dict[str, Any]:
    return {
        "hooks": {
            "UserPromptSubmit": [_managed_hook_entry("agpair codex hook user-prompt-submit")],
            "Stop": [_managed_hook_entry("agpair codex hook stop", timeout=30)],
            "SubagentStart": [_managed_hook_entry("agpair codex hook subagent-start")],
        }
    }


def _managed_command_for_event(event_name: str) -> str | None:
    mapping = {
        "UserPromptSubmit": "agpair codex hook user-prompt-submit",
        "Stop": "agpair codex hook stop",
        "SubagentStart": "agpair codex hook subagent-start",
    }
    return mapping.get(event_name)


def _entry_has_command(entry: Any, command: str) -> bool:
    if not isinstance(entry, dict):
        return False
    hooks = entry.get("hooks")
    if not isinstance(hooks, list):
        return False
    return any(
        isinstance(hook, dict) and hook.get("command") == command
        for hook in hooks
    )


def _settings_path(*, scope: str, paths: AppPaths, repo_path: str | None) -> Path:
    if scope == "user":
        return Path.home() / ".codex" / "hooks.json"
    resolved = resolve_repo_path(repo_path, None, paths)
    base = Path(resolved).expanduser().resolve() if resolved else Path.cwd().resolve()
    return base / ".codex" / "hooks.json"


def _load_settings(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"Expected JSON object at {path}")
    return payload


def _render_settings(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


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


def _merge_managed_config(current: dict[str, Any]) -> dict[str, Any]:
    updated = json.loads(json.dumps(current))
    hooks = updated.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise RuntimeError("Existing hooks value is not a JSON object; manual merge required.")
    for event_name, desired_entries in _managed_config_payload()["hooks"].items():
        command = _managed_command_for_event(event_name)
        assert command is not None
        existing_entries = hooks.setdefault(event_name, [])
        if not isinstance(existing_entries, list):
            raise RuntimeError(f"Existing hooks.{event_name} is not a list; manual merge required.")
        if not any(_entry_has_command(entry, command) for entry in existing_entries):
            existing_entries.extend(desired_entries)
    return updated


def _uninstall_managed_config(current: dict[str, Any]) -> dict[str, Any]:
    updated = json.loads(json.dumps(current))
    hooks = updated.get("hooks")
    if not isinstance(hooks, dict):
        return updated
    for event_name in ("UserPromptSubmit", "Stop", "SubagentStart"):
        command = _managed_command_for_event(event_name)
        if command is None:
            continue
        existing = hooks.get(event_name)
        if not isinstance(existing, list):
            continue
        remaining = [entry for entry in existing if not _entry_has_command(entry, command)]
        if remaining:
            hooks[event_name] = remaining
        else:
            hooks.pop(event_name, None)
    if not hooks:
        updated.pop("hooks", None)
    return updated


def _emit_json(payload: dict[str, Any]) -> None:
    typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))


@app.command("config")
def config(
    install: bool = typer.Option(False, "--install", help="Install AGPair-managed Codex hooks."),
    uninstall: bool = typer.Option(False, "--uninstall", help="Remove AGPair-managed Codex hooks."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print a unified diff instead of writing changes."),
    sync_skill: bool = typer.Option(True, "--sync-skill/--no-sync-skill", help="Sync the AGPair Codex skill into the selected scope."),
    scope: str = typer.Option("project", "--scope", help="project or user"),
    repo_path: str | None = typer.Option(None, "--repo-path"),
) -> None:
    if scope not in {"project", "user"}:
        raise typer.BadParameter("--scope must be project or user")
    if install and uninstall:
        raise typer.BadParameter("choose only one of --install or --uninstall")
    if not install and not uninstall:
        _emit_json(_managed_config_payload())
        return

    paths = _paths()
    path = _settings_path(scope=scope, paths=paths, repo_path=repo_path)
    current = _load_settings(path)
    before = _render_settings(current) if current else ""
    updated = _uninstall_managed_config(current) if uninstall else _merge_managed_config(current)
    after = _render_settings(updated) if updated else ""
    skill_plan = None
    if sync_skill:
        try:
            skill_plan = plan_skill_sync(
                source_path=bundled_skill_path("Codex"),
                target_path=path.parent / "skills" / "agpair-codex" / "SKILL.md",
                uninstall=uninstall,
            )
        except RuntimeError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=1)
    if dry_run:
        _emit_diff(path, before, after)
        if skill_plan is not None:
            typer.echo(skill_plan.diff(), nl=False)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(after, encoding="utf-8")
    if skill_plan is not None:
        skill_plan.apply()
        typer.echo(str(skill_plan.target_path))
    typer.echo(str(path))


def _hook_specific_output(event_name: str, context: str) -> dict[str, Any]:
    return {
        "hookSpecificOutput": {
            "hookEventName": event_name,
            "additionalContext": context,
        }
    }


@hook_app.command("user-prompt-submit")
def hook_user_prompt_submit() -> None:
    payload = _read_stdin_json()
    repo_path = _resolve_repo_from_hook(payload)
    if repo_path is None:
        return
    try:
        _paths()
    except Exception:
        return
    _emit_json(_hook_specific_output("UserPromptSubmit", EXTERNAL_FIRST_CONTEXT))


def _latest_terminal_receipt(paths: AppPaths, task_id: str):
    journal = JournalRepository(paths.db_path)
    for row in journal.tail(task_id, limit=20):
        if row.event not in {"ready_for_review", "committed", "blocked", "evidence_ready"}:
            continue
        receipt = parse_structured_terminal_receipt(row.body, expected_task_id=task_id)
        if receipt is not None:
            return receipt
    return None


@hook_app.command("stop")
def hook_stop() -> None:
    payload = _read_stdin_json()
    repo_path = _resolve_repo_from_hook(payload)
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
    if task.is_approved:
        return
    if task.phase in {"ready_for_review", "committed", "evidence_ready"}:
        reason = (
            f"AGPair task {task.task_id} reached ready_for_review. Inspect git status, "
            "optional commit/diff, receipt, raw log paths, and required evidence before finalizing."
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
    payload = _read_stdin_json()
    repo_path = _resolve_repo_from_hook(payload)
    if repo_path is None:
        return
    try:
        _paths()
    except Exception:
        return
    _emit_json(_hook_specific_output("SubagentStart", SUBAGENT_ADVISORY_CONTEXT))
