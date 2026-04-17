from __future__ import annotations

import json
import subprocess
from pathlib import Path

from typer.testing import CliRunner

from agpair.cli.app import app
from agpair.config import AppPaths
from agpair.storage.db import ensure_database
from agpair.storage.tasks import TaskRepository


def make_paths(tmp_path: Path) -> AppPaths:
    return AppPaths.from_root(tmp_path / ".agpair")


def make_task_repo(tmp_path: Path) -> TaskRepository:
    paths = make_paths(tmp_path)
    ensure_database(paths.db_path)
    return TaskRepository(paths.db_path)


def init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)


def claude_statusline_input(
    cwd: Path,
    *,
    project_dir: Path | None = None,
    git_worktree: str | None = None,
) -> str:
    payload: dict[str, object] = {
        "cwd": str(cwd),
        "session_id": "session-test-1",
        "model": {"display_name": "Sonnet"},
        "workspace": {
            "current_dir": str(cwd),
            "project_dir": str(project_dir or cwd),
            "added_dirs": [],
        },
    }
    if git_worktree is not None:
        payload["workspace"]["git_worktree"] = git_worktree
    return json.dumps(payload)


def hook_input(cwd: Path, *, event: str, **extra: object) -> str:
    payload = {
        "session_id": "session-hook-1",
        "cwd": str(cwd),
        "hook_event_name": event,
        **extra,
    }
    return json.dumps(payload)


def test_cli_help_lists_claude_group() -> None:
    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "claude" in result.stdout


def test_claude_statusline_shows_idle_state_for_repo(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    init_git_repo(repo_path)

    result = CliRunner().invoke(
        app,
        ["claude", "statusline"],
        input=claude_statusline_input(repo_path),
    )

    assert result.exit_code == 0
    assert result.stdout.strip() == "agpair idle"


def test_claude_statusline_shows_active_task_and_worktree_name(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    init_git_repo(repo_path)
    src_path = repo_path / "src"
    src_path.mkdir()

    tasks = make_task_repo(tmp_path)
    tasks.create_task(task_id="TASK-CLAUDE-1", repo_path=str(repo_path))
    tasks.mark_acked(task_id="TASK-CLAUDE-1", session_id="session-123")

    result = CliRunner().invoke(
        app,
        ["claude", "statusline"],
        input=claude_statusline_input(src_path, project_dir=repo_path, git_worktree="feature-x"),
    )

    assert result.exit_code == 0
    assert result.stdout.strip() == "agpair acked TASK-CLAUDE-1 wt:feature-x"


def test_claude_config_emits_statusline_and_hook_commands() -> None:
    result = CliRunner().invoke(app, ["claude", "config"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["statusLine"]["command"] == "agpair claude statusline"
    assert payload["statusLine"]["refreshInterval"] == 5
    assert payload["hooks"]["SessionStart"][0]["hooks"][0]["command"] == "agpair claude hook session-start"
    assert payload["hooks"]["PreCompact"][0]["hooks"][0]["command"] == "agpair claude hook precompact"


def test_claude_config_install_writes_project_settings(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    monkeypatch.chdir(repo_path)

    result = CliRunner().invoke(app, ["claude", "config", "--install"])

    assert result.exit_code == 0
    settings_path = repo_path / ".claude" / "settings.json"
    payload = json.loads(settings_path.read_text())
    assert payload["statusLine"]["command"] == "agpair claude statusline"
    assert payload["hooks"]["SessionStart"][0]["hooks"][0]["command"] == "agpair claude hook session-start"


def test_claude_config_dry_run_prints_diff_without_writing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    monkeypatch.chdir(repo_path)

    result = CliRunner().invoke(app, ["claude", "config", "--install", "--dry-run"])

    assert result.exit_code == 0
    assert "---" in result.stdout
    assert "+++ " in result.stdout
    assert "agpair claude statusline" in result.stdout
    assert not (repo_path / ".claude" / "settings.json").exists()


def test_claude_config_install_refuses_foreign_statusline_without_force(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    monkeypatch.chdir(repo_path)
    settings_dir = repo_path / ".claude"
    settings_dir.mkdir()
    (settings_dir / "settings.json").write_text(
        json.dumps({"statusLine": {"type": "command", "command": "~/.claude/custom-statusline.sh"}}, indent=2),
        encoding="utf-8",
    )

    result = CliRunner().invoke(app, ["claude", "config", "--install"])

    assert result.exit_code == 1
    assert "statusLine" in result.output
    assert "manual merge" in result.output


def test_claude_config_install_user_scope_writes_home_settings(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    home_path = tmp_path / "home"
    home_path.mkdir()
    monkeypatch.setenv("HOME", str(home_path))

    result = CliRunner().invoke(app, ["claude", "config", "--install", "--scope", "user"])

    assert result.exit_code == 0
    settings_path = home_path / ".claude" / "settings.json"
    payload = json.loads(settings_path.read_text())
    assert payload["statusLine"]["command"] == "agpair claude statusline"


def test_claude_config_install_refuses_foreign_sessionstart_hook_without_force(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    monkeypatch.chdir(repo_path)
    settings_dir = repo_path / ".claude"
    settings_dir.mkdir()
    settings_payload = {
        "hooks": {
            "SessionStart": [
                {"hooks": [{"type": "command", "command": "/tmp/custom-session-start.sh"}]}
            ]
        }
    }
    (settings_dir / "settings.json").write_text(json.dumps(settings_payload, indent=2), encoding="utf-8")

    result = CliRunner().invoke(app, ["claude", "config", "--install"])

    assert result.exit_code == 1
    assert "hooks.SessionStart" in result.output
    assert "manual merge" in result.output


def test_claude_config_force_replaces_conflicting_managed_slots(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    monkeypatch.chdir(repo_path)
    settings_dir = repo_path / ".claude"
    settings_dir.mkdir()
    settings_payload = {
        "statusLine": {"type": "command", "command": "~/.claude/custom-statusline.sh"},
        "hooks": {
            "SessionStart": [
                {"hooks": [{"type": "command", "command": "/tmp/custom-session-start.sh"}]}
            ],
            "Notification": [
                {"hooks": [{"type": "command", "command": "/tmp/notify-me.sh"}]}
            ],
        },
    }
    (settings_dir / "settings.json").write_text(json.dumps(settings_payload, indent=2), encoding="utf-8")

    result = CliRunner().invoke(app, ["claude", "config", "--install", "--force"])

    assert result.exit_code == 0
    updated = json.loads((settings_dir / "settings.json").read_text())
    assert updated["statusLine"]["command"] == "agpair claude statusline"
    assert updated["hooks"]["SessionStart"][0]["hooks"][0]["command"] == "agpair claude hook session-start"
    assert updated["hooks"]["Notification"][0]["hooks"][0]["command"] == "/tmp/notify-me.sh"


def test_claude_config_uninstall_removes_only_managed_entries(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    monkeypatch.chdir(repo_path)
    settings_dir = repo_path / ".claude"
    settings_dir.mkdir()
    settings_payload = {
        "statusLine": {"type": "command", "command": "agpair claude statusline", "refreshInterval": 5},
        "hooks": {
            "SessionStart": [
                {"hooks": [{"type": "command", "command": "agpair claude hook session-start"}]}
            ],
            "Notification": [
                {"hooks": [{"type": "command", "command": "/tmp/notify-me.sh"}]}
            ],
        },
    }
    (settings_dir / "settings.json").write_text(json.dumps(settings_payload, indent=2), encoding="utf-8")

    result = CliRunner().invoke(app, ["claude", "config", "--uninstall"])

    assert result.exit_code == 0
    updated = json.loads((settings_dir / "settings.json").read_text())
    assert "statusLine" not in updated
    assert "SessionStart" not in updated["hooks"]
    assert updated["hooks"]["Notification"][0]["hooks"][0]["command"] == "/tmp/notify-me.sh"


def test_claude_session_start_hook_emits_agpair_context(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    init_git_repo(repo_path)

    tasks = make_task_repo(tmp_path)
    tasks.create_task(task_id="TASK-CLAUDE-CTX", repo_path=str(repo_path))
    tasks.mark_acked(task_id="TASK-CLAUDE-CTX", session_id="session-ctx")

    result = CliRunner().invoke(
        app,
        ["claude", "hook", "session-start"],
        input=hook_input(repo_path, event="SessionStart", source="startup"),
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert "TASK-CLAUDE-CTX" in payload["hookSpecificOutput"]["additionalContext"]
    assert "agpair task watch <TASK_ID> --json" in payload["hookSpecificOutput"]["additionalContext"]


def test_claude_precompact_hook_blocks_when_live_task_exists(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    init_git_repo(repo_path)

    tasks = make_task_repo(tmp_path)
    tasks.create_task(task_id="TASK-CLAUDE-LIVE", repo_path=str(repo_path))
    tasks.mark_acked(task_id="TASK-CLAUDE-LIVE", session_id="session-live")

    result = CliRunner().invoke(
        app,
        ["claude", "hook", "precompact"],
        input=hook_input(repo_path, event="PreCompact", trigger="auto", custom_instructions=""),
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["decision"] == "block"
    assert "TASK-CLAUDE-LIVE" in payload["reason"]


def test_claude_precompact_hook_allows_when_no_live_task_exists(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    init_git_repo(repo_path)

    tasks = make_task_repo(tmp_path)
    tasks.create_task(task_id="TASK-CLAUDE-DONE", repo_path=str(repo_path))
    tasks.mark_acked(task_id="TASK-CLAUDE-DONE", session_id="session-done")
    tasks.mark_committed(task_id="TASK-CLAUDE-DONE")

    result = CliRunner().invoke(
        app,
        ["claude", "hook", "precompact"],
        input=hook_input(repo_path, event="PreCompact", trigger="manual", custom_instructions=""),
    )

    assert result.exit_code == 0
    assert result.stdout.strip() == ""
