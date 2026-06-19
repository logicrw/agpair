from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from difflib import unified_diff
from pathlib import Path
from typing import Any

import typer

from agpair.config import AppPaths
from agpair.internal_context import client_hooks_suppressed
from agpair.storage.db import ensure_database
from agpair.targets import resolve_repo_path
from agpair.cli.skill_sync import bundled_skill_path, plan_skill_sync

app = typer.Typer(no_args_is_help=True)
hook_app = typer.Typer(no_args_is_help=True)
app.add_typer(hook_app, name="hook")

HERMES_PRE_LLM_HOOK_COMMAND_FALLBACK = "agpair hermes hook pre-llm-call"
HERMES_PRE_LLM_HOOK_COMMAND_PATTERN = re.compile(
    r"""^\s*-\s+command:\s*["']?.*?\bagpair\s+hermes\s+hook\s+pre-llm-call["']?\s*$"""
)

EXTERNAL_FIRST_CONTEXT = (
    "AGPair external-first routing is available in this repository. For non-trivial "
    "implementation, refactor, test-fix, research, review, or multi-file work, prefer "
    "AGPair external CLI executors before Hermes native delegation. For code changes, "
    "prefer a bounded `--completion-policy evidence --isolated-worktree` slice, then "
    "inspect status JSON protocol/adoption results and accept/adopt after verification. "
    "Hermes remains the controller and verifier. Use Hermes native `delegate_task` only "
    "when AGPair is unavailable, unsuitable, not good enough, or hidden Hermes context is required."
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
    extra = payload.get("extra")
    if isinstance(extra, dict):
        for key in ("cwd", "project_dir", "current_dir"):
            value = extra.get(key)
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


def _emit_json(payload: dict[str, Any]) -> None:
    typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))


def _emit_noop_hook() -> None:
    _emit_json({})


def _should_noop_client_hook() -> bool:
    return client_hooks_suppressed()


def _settings_path(config_path: str | None) -> Path:
    if config_path:
        return Path(config_path).expanduser()
    return Path.home() / ".hermes" / "config.yaml"


def _managed_config_payload() -> dict[str, Any]:
    return {
        "hooks": {
            "pre_llm_call": [
                {
                    "command": _managed_hook_command(),
                    "timeout": 10,
                }
            ]
        }
    }


def _managed_hook_command() -> str:
    override = os.environ.get("AGPAIR_HERMES_HOOK_COMMAND")
    if override:
        return override
    executable = shutil.which("agpair")
    if executable:
        return f"{shlex.quote(executable)} hermes hook pre-llm-call"
    return HERMES_PRE_LLM_HOOK_COMMAND_FALLBACK


def _render_settings_text(payload: str) -> str:
    return payload if payload.endswith("\n") else payload + "\n"


def _emit_diff(path: Path, before: str, after: str) -> None:
    diff = "".join(
        unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=str(path),
            tofile=str(path),
        )
    )
    if diff:
        typer.echo(diff, nl=False)


def _top_level_key(line: str) -> str | None:
    if line.startswith((" ", "\t", "#")):
        return None
    match = re.match(r"^([A-Za-z_][A-Za-z0-9_-]*):", line)
    return match.group(1) if match else None


def _top_level_block(lines: list[str], key: str) -> tuple[int, int] | None:
    start = next((i for i, line in enumerate(lines) if _top_level_key(line) == key), None)
    if start is None:
        return None
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if _top_level_key(lines[i]) is not None:
            end = i
            break
    return start, end


def _managed_hook_lines() -> list[str]:
    return [
        f'    - command: "{_managed_hook_command()}"',
        "      timeout: 10",
    ]


def _managed_hooks_block() -> list[str]:
    return [
        "hooks:",
        "  pre_llm_call:",
        *_managed_hook_lines(),
    ]


def _event_line_index(lines: list[str], start: int, end: int, event_name: str) -> int | None:
    pattern = re.compile(rf"^  {re.escape(event_name)}:\s*(.*)$")
    for i in range(start + 1, end):
        if pattern.match(lines[i]):
            return i
    return None


def _event_block_end(lines: list[str], event_start: int, hooks_end: int) -> int:
    for i in range(event_start + 1, hooks_end):
        if re.match(r"^  [A-Za-z_][A-Za-z0-9_-]*:", lines[i]):
            return i
    return hooks_end


def _merge_managed_config(current: str) -> str:
    current = _remove_managed_config(current)
    text = _render_settings_text(current) if current else ""
    lines = text.splitlines()
    block = _top_level_block(lines, "hooks")
    if block is None:
        if lines and lines[-1].strip():
            lines.append("")
        lines.extend(_managed_hooks_block())
        return "\n".join(lines) + "\n"

    start, end = block
    hooks_header = lines[start].strip()
    if hooks_header in {"hooks: {}", "hooks: null", "hooks:"}:
        if hooks_header != "hooks:":
            lines[start : start + 1] = _managed_hooks_block()
            return "\n".join(lines) + "\n"
    else:
        raise RuntimeError(
            "Existing Hermes hooks config is inline or unsupported; edit manually to avoid clobbering it."
        )

    event_start = _event_line_index(lines, start, end, "pre_llm_call")
    if event_start is None:
        lines[end:end] = ["  pre_llm_call:", *_managed_hook_lines()]
        return "\n".join(lines) + "\n"

    event_line = lines[event_start].strip()
    if event_line in {"pre_llm_call: []", "pre_llm_call: null"}:
        lines[event_start : event_start + 1] = ["  pre_llm_call:", *_managed_hook_lines()]
        return "\n".join(lines) + "\n"

    insert_at = _event_block_end(lines, event_start, end)
    lines[insert_at:insert_at] = _managed_hook_lines()
    return "\n".join(lines) + "\n"


def _remove_managed_config(current: str) -> str:
    text = _render_settings_text(current) if current else ""
    if "agpair hermes hook pre-llm-call" not in text:
        return text
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        if not HERMES_PRE_LLM_HOOK_COMMAND_PATTERN.match(lines[i]):
            i += 1
            continue
        item_indent = len(lines[i]) - len(lines[i].lstrip())
        end = i + 1
        while end < len(lines):
            stripped = lines[end].strip()
            next_indent = len(lines[end]) - len(lines[end].lstrip())
            if stripped and next_indent <= item_indent:
                break
            end += 1
        del lines[i:end]
        continue
    return "\n".join(lines) + "\n"


@app.command("config")
def config(
    install: bool = typer.Option(False, "--install", help="Install AGPair-managed Hermes shell hooks."),
    uninstall: bool = typer.Option(False, "--uninstall", help="Remove AGPair-managed Hermes shell hooks."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print a unified diff instead of writing changes."),
    sync_skill: bool = typer.Option(True, "--sync-skill/--no-sync-skill", help="Sync the AGPair Hermes skill."),
    scope: str = typer.Option("user", "--scope", help="Only user scope is supported for Hermes."),
    config_path: str | None = typer.Option(None, "--config-path", help="Override Hermes config path for tests or profiles."),
) -> None:
    if scope != "user":
        raise typer.BadParameter("--scope must be 'user' for Hermes")
    if install and uninstall:
        raise typer.BadParameter("choose only one of --install or --uninstall")
    if not install and not uninstall:
        _emit_json(_managed_config_payload())
        return

    path = _settings_path(config_path)
    current = path.read_text(encoding="utf-8") if path.exists() else ""
    before = _render_settings_text(current) if current else ""
    try:
        updated = _remove_managed_config(current) if uninstall else _merge_managed_config(current)
        skill_plan = None
        if sync_skill:
            skill_plan = plan_skill_sync(
                source_path=bundled_skill_path("Hermes"),
                target_path=path.parent / "skills" / "autonomous-ai-agents" / "agpair" / "SKILL.md",
                uninstall=uninstall,
            )
    except RuntimeError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1)
    after = _render_settings_text(updated) if updated else ""

    if dry_run:
        _emit_diff(path, before, after)
        if skill_plan is not None:
            typer.echo(skill_plan.diff(), nl=False)
        return

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(after, encoding="utf-8")
    if skill_plan is not None:
        skill_plan.apply()
        typer.echo(f"Updated {skill_plan.target_path}")
    typer.echo(f"Updated {path}")


@hook_app.command("pre-llm-call")
def hook_pre_llm_call() -> None:
    if _should_noop_client_hook():
        _emit_noop_hook()
        return
    payload = _read_stdin_json()
    repo_path = _resolve_repo_from_hook(payload)
    if repo_path is None:
        return
    try:
        _paths()
    except Exception:
        return
    _emit_json({"context": EXTERNAL_FIRST_CONTEXT})
