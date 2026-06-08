from __future__ import annotations

import json
import subprocess
from pathlib import Path

from typer.testing import CliRunner

from agpair.cli.app import app
from agpair.config import AppPaths
from agpair.storage.db import ensure_database
from agpair.storage.journal import JournalRepository
from agpair.storage.tasks import TaskRepository


def make_paths(tmp_path: Path) -> AppPaths:
    return AppPaths.from_root(tmp_path / ".agpair")


def init_git_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)


def hook_input(cwd: Path, *, event: str = "Stop") -> str:
    return json.dumps({"cwd": str(cwd), "hook_event_name": event})


def test_codex_config_emits_userprompt_and_stop_hooks() -> None:
    result = CliRunner().invoke(app, ["codex", "config"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"] == "agpair codex hook user-prompt-submit"
    assert payload["hooks"]["Stop"][0]["hooks"][0]["command"] == "agpair codex hook stop"
    assert payload["hooks"]["SubagentStart"][0]["hooks"][0]["command"] == "agpair codex hook subagent-start"


def test_codex_config_install_preserves_foreign_hooks(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    settings_path = tmp_path / ".codex" / "hooks.json"
    settings_path.parent.mkdir()
    settings_path.write_text(
        json.dumps(
            {
                "hooks": {
                    "Stop": [{"hooks": [{"type": "command", "command": "/tmp/foreign-stop.sh"}]}]
                }
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    result = CliRunner().invoke(app, ["codex", "config", "--install", "--repo-path", str(tmp_path)])

    assert result.exit_code == 0
    updated = json.loads(settings_path.read_text())
    stop_commands = [
        hook["command"]
        for entry in updated["hooks"]["Stop"]
        for hook in entry["hooks"]
    ]
    assert "/tmp/foreign-stop.sh" in stop_commands
    assert "agpair codex hook stop" in stop_commands


def test_codex_config_install_dry_run_prints_diff_without_writing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    repo_path = tmp_path / "repo"
    init_git_repo(repo_path)

    result = CliRunner().invoke(
        app,
        ["codex", "config", "--install", "--scope", "project", "--repo-path", str(repo_path), "--dry-run"],
    )

    assert result.exit_code == 0
    assert "---" in result.stdout
    assert "+++" in result.stdout
    assert "agpair codex hook user-prompt-submit" in result.stdout
    assert not (repo_path / ".codex" / "hooks.json").exists()


def test_codex_config_sync_skill_dry_run_and_install(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    repo_path = tmp_path / "repo"
    init_git_repo(repo_path)

    dry_run = CliRunner().invoke(
        app,
        [
            "codex",
            "config",
            "--install",
            "--scope",
            "project",
            "--repo-path",
            str(repo_path),
            "--sync-skill",
            "--dry-run",
        ],
    )

    skill_path = repo_path / ".codex" / "skills" / "agpair" / "SKILL.md"
    assert dry_run.exit_code == 0
    assert str(skill_path) in dry_run.stdout
    assert "agpair-codex" in dry_run.stdout
    assert not skill_path.exists()

    installed = CliRunner().invoke(
        app,
        [
            "codex",
            "config",
            "--install",
            "--scope",
            "project",
            "--repo-path",
            str(repo_path),
            "--sync-skill",
        ],
    )

    assert installed.exit_code == 0
    assert skill_path.exists()
    assert "agpair-codex" in skill_path.read_text(encoding="utf-8")


def test_codex_config_sync_skill_refuses_non_agpair_skill(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    repo_path = tmp_path / "repo"
    init_git_repo(repo_path)
    skill_path = repo_path / ".codex" / "skills" / "agpair" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("---\nname: custom\n---\n# Custom\n", encoding="utf-8")

    result = CliRunner().invoke(
        app,
        [
            "codex",
            "config",
            "--install",
            "--scope",
            "project",
            "--repo-path",
            str(repo_path),
            "--sync-skill",
        ],
    )

    assert result.exit_code == 1
    assert "Refusing to manage non-AGPair skill" in result.stderr
    assert "name: custom" in skill_path.read_text(encoding="utf-8")


def test_codex_hooks_fail_open_when_state_is_unreadable(monkeypatch) -> None:
    monkeypatch.setenv("AGPAIR_HOME", "/path/that/does/not/exist")

    result = CliRunner().invoke(app, ["codex", "hook", "stop"], input="{}")

    assert result.exit_code == 0
    assert result.stdout.strip() == ""


def test_codex_user_prompt_submit_emits_external_first_context(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    repo_path = tmp_path / "repo"
    init_git_repo(repo_path)

    result = CliRunner().invoke(
        app,
        ["codex", "hook", "user-prompt-submit"],
        input=hook_input(repo_path, event="UserPromptSubmit"),
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    context = payload["hookSpecificOutput"]["additionalContext"]
    assert "prefer dispatching through AGPair external CLI executors" in context
    assert "Codex native subagents" in context


def test_codex_stop_does_not_block_for_plain_acked_state(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    repo_path = tmp_path / "repo"
    init_git_repo(repo_path)
    paths = make_paths(tmp_path)
    ensure_database(paths.db_path)
    tasks = TaskRepository(paths.db_path)
    tasks.create_task(task_id="TASK-CODEX-ACKED", repo_path=str(repo_path), executor_backend="antigravity-cli")
    tasks.mark_acked(task_id="TASK-CODEX-ACKED", session_id="session-123")

    result = CliRunner().invoke(app, ["codex", "hook", "stop"], input=hook_input(repo_path))

    assert result.exit_code == 0
    assert result.stdout.strip() == ""


def test_codex_stop_blocks_for_ready_for_review_receipt(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    repo_path = tmp_path / "repo"
    init_git_repo(repo_path)
    paths = make_paths(tmp_path)
    ensure_database(paths.db_path)
    tasks = TaskRepository(paths.db_path)
    journal = JournalRepository(paths.db_path)
    tasks.create_task(task_id="TASK-CODEX-RFR", repo_path=str(repo_path), executor_backend="antigravity-cli")
    tasks.mark_acked(task_id="TASK-CODEX-RFR", session_id="session-123")
    receipt_body = json.dumps(
        {
            "schema_version": "1",
            "task_id": "TASK-CODEX-RFR",
            "attempt_no": 1,
            "review_round": 0,
            "status": "COMMITTED",
            "summary": "Ready for review",
            "payload": {
                "claimed_state": "ready_for_review",
                "changed_files": ["src/app.py"],
                "scope_violations": [],
                "raw_log_path": "/tmp/stdout.log",
                "receipt_path": "/tmp/receipt.json",
                "validation": ["pytest"],
            },
        }
    )
    tasks.mark_ready_for_review(
        task_id="TASK-CODEX-RFR",
        terminal_source="daemon",
        terminal_receipt_json=receipt_body,
    )
    journal.append(
        "TASK-CODEX-RFR",
        "daemon",
        "ready_for_review",
        receipt_body,
    )

    result = CliRunner().invoke(app, ["codex", "hook", "stop"], input=hook_input(repo_path))

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["decision"] == "block"
    assert "ready_for_review" in payload["reason"]


def test_codex_stop_does_not_block_approved_ready_for_review(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    repo_path = tmp_path / "repo"
    init_git_repo(repo_path)
    paths = make_paths(tmp_path)
    ensure_database(paths.db_path)
    tasks = TaskRepository(paths.db_path)
    tasks.create_task(task_id="TASK-CODEX-APPROVED", repo_path=str(repo_path), executor_backend="antigravity-cli")
    tasks.mark_acked(task_id="TASK-CODEX-APPROVED", session_id="session-123")
    tasks.mark_ready_for_review(
        task_id="TASK-CODEX-APPROVED",
        terminal_source="daemon",
        terminal_receipt_json=json.dumps(
            {
                "schema_version": "1",
                "task_id": "TASK-CODEX-APPROVED",
                "attempt_no": 1,
                "review_round": 0,
                "status": "COMMITTED",
                "summary": "Ready for review",
                "payload": {
                    "claimed_state": "ready_for_review",
                    "changed_files": [],
                    "scope_violations": [],
                    "raw_log_path": "/tmp/stdout.log",
                    "receipt_path": "/tmp/receipt.json",
                    "validation_not_run": "read-only task",
                },
            }
        ),
    )
    tasks.mark_approved(task_id="TASK-CODEX-APPROVED")

    result = CliRunner().invoke(app, ["codex", "hook", "stop"], input=hook_input(repo_path))

    assert result.exit_code == 0
    assert result.stdout.strip() == ""


def test_codex_stop_reports_unapproved_task_after_approved_task(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    repo_path = tmp_path / "repo"
    init_git_repo(repo_path)
    paths = make_paths(tmp_path)
    ensure_database(paths.db_path)
    tasks = TaskRepository(paths.db_path)

    tasks.create_task(task_id="TASK-CODEX-APPROVED", repo_path=str(repo_path), executor_backend="antigravity-cli")
    tasks.mark_acked(task_id="TASK-CODEX-APPROVED", session_id="session-approved")
    tasks.mark_ready_for_review(
        task_id="TASK-CODEX-APPROVED",
        terminal_source="daemon",
        terminal_receipt_json=json.dumps(
            {
                "schema_version": "1",
                "task_id": "TASK-CODEX-APPROVED",
                "attempt_no": 1,
                "review_round": 0,
                "status": "EVIDENCE_PACK",
                "summary": "Ready for review",
                "payload": {
                    "changed_files": [],
                    "scope_violations": [],
                    "raw_log_path": "/tmp/stdout-approved.log",
                    "receipt_path": "/tmp/receipt-approved.json",
                    "validation_not_run": "read-only task",
                },
            }
        ),
    )
    tasks.mark_approved(task_id="TASK-CODEX-APPROVED")

    tasks.create_task(task_id="TASK-CODEX-OPEN", repo_path=str(repo_path), executor_backend="antigravity-cli")
    tasks.mark_acked(task_id="TASK-CODEX-OPEN", session_id="session-open")
    tasks.mark_ready_for_review(
        task_id="TASK-CODEX-OPEN",
        terminal_source="daemon",
        terminal_receipt_json=json.dumps(
            {
                "schema_version": "1",
                "task_id": "TASK-CODEX-OPEN",
                "attempt_no": 1,
                "review_round": 0,
                "status": "EVIDENCE_PACK",
                "summary": "Ready for review",
                "payload": {
                    "changed_files": [],
                    "scope_violations": [],
                    "raw_log_path": "/tmp/stdout-open.log",
                    "receipt_path": "/tmp/receipt-open.json",
                    "validation_not_run": "read-only task",
                },
            }
        ),
    )

    result = CliRunner().invoke(app, ["codex", "hook", "stop"], input=hook_input(repo_path))

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["decision"] == "block"
    assert "TASK-CODEX-OPEN" in payload["reason"]
    assert "TASK-CODEX-APPROVED" not in payload["reason"]


def test_codex_stop_blocks_for_approval_required(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    repo_path = tmp_path / "repo"
    init_git_repo(repo_path)
    paths = make_paths(tmp_path)
    ensure_database(paths.db_path)
    tasks = TaskRepository(paths.db_path)
    journal = JournalRepository(paths.db_path)
    tasks.create_task(task_id="TASK-CODEX-BLOCK", repo_path=str(repo_path), executor_backend="antigravity-cli")
    tasks.mark_blocked(task_id="TASK-CODEX-BLOCK", reason="Need expanded authorization")
    journal.append(
        "TASK-CODEX-BLOCK",
        "daemon",
        "blocked",
        json.dumps(
            {
                "schema_version": "1",
                "task_id": "TASK-CODEX-BLOCK",
                "attempt_no": 1,
                "review_round": 0,
                "status": "BLOCKED",
                "summary": "Need expanded authorization",
                "payload": {"blocker_type": "approval_required", "recoverable": True},
            }
        ),
    )

    result = CliRunner().invoke(app, ["codex", "hook", "stop"], input=hook_input(repo_path))

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["decision"] == "block"
    assert "approval_required" in payload["reason"]


def test_codex_subagent_start_is_advisory(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    repo_path = tmp_path / "repo"
    init_git_repo(repo_path)

    result = CliRunner().invoke(
        app,
        ["codex", "hook", "subagent-start"],
        input=hook_input(repo_path, event="SubagentStart"),
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert "additionalContext" in payload["hookSpecificOutput"]
    assert "fallback scope" in payload["hookSpecificOutput"]["additionalContext"]
