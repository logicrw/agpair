import json
from pathlib import Path

from typer.testing import CliRunner

from agpair.cli.app import app
from agpair.config import AppPaths
from agpair.executors.base import DispatchResult
from agpair.storage.db import ensure_database
from agpair.storage.journal import JournalRepository
from agpair.storage.tasks import TaskRepository


VALID_BRIEF = "Goal: test\nScope: retry\nRequired changes: retry\nExit criteria: verified"


class FakeExecutor:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def dispatch(self, **kwargs):
        self.calls.append(kwargs)
        return DispatchResult(session_id="retry-session")


def make_paths(tmp_path: Path, monkeypatch) -> AppPaths:
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    for env_var, filename in (
        ("AGPAIR_CODEX_BIN", "codex"),
        ("AGPAIR_CLAUDE_CODE_BIN", "claude"),
        ("AGPAIR_ANTIGRAVITY_CLI_BIN", "agy"),
        ("AGPAIR_GROK_CLI_BIN", "grok"),
    ):
        bin_path = bin_dir / filename
        bin_path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        bin_path.chmod(0o755)
        monkeypatch.setenv(env_var, str(bin_path))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-anthropic-api-key")
    paths = AppPaths.default()
    ensure_database(paths.db_path)
    return paths


def seed_blocked_task(
    paths: AppPaths,
    *,
    task_id: str = "TASK-RETRY-BLOCK",
    executor_backend: str = "codex",
    blocker_type: str = "approval_required",
    authorization_profile: str = "local_readonly",
) -> None:
    repo = TaskRepository(paths.db_path)
    journal = JournalRepository(paths.db_path)
    repo.create_task(
        task_id=task_id,
        repo_path=str(paths.root.parent),
        executor_backend=executor_backend,
        authorization_profile=authorization_profile,
    )
    journal.append(task_id, "cli", "created", VALID_BRIEF)
    payload = {
        "blocker_type": blocker_type,
        "recoverable": True,
        "suggested_action": "retry_with_expanded_authorization",
        "authorization_profile": authorization_profile,
        "requested_authorization_profile": "local_mutating",
        "requested_actions": ["edit files"],
        "authorization_delta": {"allow_file_edits": True},
        "request_reason": "Readonly profile cannot edit files.",
        "risk_assessment": "Repo-local edits only.",
        "safe_to_retry": True,
        "raw_log_path": "/tmp/stderr.log",
    }
    journal.append(
        task_id,
        "daemon",
        "blocked",
        json.dumps(
            {
                "schema_version": "1",
                "task_id": task_id,
                "attempt_no": 1,
                "review_round": 0,
                "status": "BLOCKED",
                "summary": "Need expanded authorization",
                "payload": payload,
            }
        ),
    )
    repo.mark_blocked(task_id=task_id, reason="Need expanded authorization")


def test_retry_from_block_generates_context_and_updates_authorization(tmp_path: Path, monkeypatch) -> None:
    paths = make_paths(tmp_path, monkeypatch)
    seed_blocked_task(paths)
    fake_executor = FakeExecutor()
    monkeypatch.setattr("agpair.executors.get_executor", lambda backend_id, **kwargs: fake_executor)

    result = CliRunner().invoke(
        app,
        [
            "task",
            "retry",
            "TASK-RETRY-BLOCK",
            "--from-block",
            "--authorization-profile",
            "local_mutating",
            "--no-wait",
        ],
    )

    assert result.exit_code == 0
    assert fake_executor.calls
    retry_body = fake_executor.calls[0]["body"]
    assert "Original brief:" in retry_body
    assert VALID_BRIEF in retry_body
    assert "Previous blocked reason: Need expanded authorization" in retry_body
    assert "approval_required" in retry_body
    assert "authorization_delta" in retry_body
    assert fake_executor.calls[0]["authorization_profile"] == "local_mutating"

    task = TaskRepository(paths.db_path).get_task("TASK-RETRY-BLOCK")
    assert task is not None
    assert task.attempt_no == 2
    assert task.phase == "acked"
    assert task.authorization_profile == "local_mutating"


def test_retry_from_block_with_executor_override_stores_supported_executor(tmp_path: Path, monkeypatch) -> None:
    paths = make_paths(tmp_path, monkeypatch)
    seed_blocked_task(paths)
    monkeypatch.setenv("AGPAIR_CLAUDE_CODE_AUTH_MODE", "api")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-anthropic-api-key")
    fake_executor = FakeExecutor()
    monkeypatch.setattr("agpair.executors.get_executor", lambda backend_id, **kwargs: fake_executor)

    result = CliRunner().invoke(
        app,
        [
            "task",
            "retry",
            "TASK-RETRY-BLOCK",
            "--from-block",
            "--executor",
            "claude-code",
            "--authorization-profile",
            "local_mutating",
            "--no-wait",
        ],
    )

    assert result.exit_code == 0
    task = TaskRepository(paths.db_path).get_task("TASK-RETRY-BLOCK")
    assert task is not None
    assert task.executor_backend == "claude-code"


def test_retry_from_block_can_use_recommended_next_executor(tmp_path: Path, monkeypatch) -> None:
    paths = make_paths(tmp_path, monkeypatch)
    seed_blocked_task(
        paths,
        task_id="TASK-SWITCH",
        executor_backend="grok-cli",
        blocker_type="no_progress_budget_exceeded",
        authorization_profile="local_readonly",
    )
    fake_executor = FakeExecutor()
    monkeypatch.setattr("agpair.executors.get_executor", lambda backend_id, **kwargs: fake_executor)

    result = CliRunner().invoke(
        app,
        [
            "task",
            "retry",
            "TASK-SWITCH",
            "--from-block",
            "--next-executor",
            "--authorization-profile",
            "local_mutating",
            "--no-wait",
        ],
    )

    assert result.exit_code == 0, result.output
    assert fake_executor.calls
    task = TaskRepository(paths.db_path).get_task("TASK-SWITCH")
    assert task is not None
    assert task.executor_backend != "grok-cli"


def test_retry_next_executor_requires_from_block_and_no_executor_override(tmp_path: Path, monkeypatch) -> None:
    paths = make_paths(tmp_path, monkeypatch)
    seed_blocked_task(paths, task_id="TASK-NEXT-CONFLICT", executor_backend="grok-cli")

    missing_from_block = CliRunner().invoke(app, ["task", "retry", "TASK-NEXT-CONFLICT", "--next-executor"])
    assert missing_from_block.exit_code != 0
    assert "--next-executor requires --from-block" in missing_from_block.output

    conflict = CliRunner().invoke(
        app,
        [
            "task",
            "retry",
            "TASK-NEXT-CONFLICT",
            "--from-block",
            "--next-executor",
            "--executor",
            "antigravity-cli",
        ],
    )
    assert conflict.exit_code != 0
    assert "--next-executor cannot be combined with --executor" in conflict.output


def test_retry_from_block_rejects_committed_task(tmp_path: Path, monkeypatch) -> None:
    paths = make_paths(tmp_path, monkeypatch)
    repo = TaskRepository(paths.db_path)
    repo.create_task(task_id="TASK-RETRY-DONE", repo_path=str(tmp_path), executor_backend="codex")
    repo.mark_acked(task_id="TASK-RETRY-DONE", session_id="session")
    repo.mark_committed(task_id="TASK-RETRY-DONE")

    result = CliRunner().invoke(app, ["task", "retry", "TASK-RETRY-DONE", "--from-block", "--no-wait"])

    assert result.exit_code != 0
    assert "--from-block requires a blocked or stuck task" in result.output


def test_legacy_gemini_retry_from_block_requires_executor_override(tmp_path: Path, monkeypatch) -> None:
    paths = make_paths(tmp_path, monkeypatch)
    seed_blocked_task(paths, task_id="TASK-RETRY-GEMINI", executor_backend="gemini_cli")

    result = CliRunner().invoke(app, ["task", "retry", "TASK-RETRY-GEMINI", "--from-block", "--no-wait"])

    assert result.exit_code != 0
    assert "legacy gemini_cli retry requires --executor" in result.output
