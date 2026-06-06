import json
import pathlib
import subprocess
import time
from datetime import UTC, datetime

from typer.testing import CliRunner

from agpair.cli.app import app
from agpair.config import AppPaths
from agpair.daemon.loop import run_once
from agpair.storage.db import ensure_database
from agpair.storage.journal import JournalRepository
from agpair.storage.tasks import TaskRepository

VALID_BRIEF = "Goal: fake\nScope: fake\nRequired changes: fake\nExit criteria: fake"


def init_git_repo(repo_path: pathlib.Path) -> None:
    repo_path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init"], cwd=repo_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "tests@example.com"], cwd=repo_path, check=True)
    subprocess.run(["git", "config", "user.name", "AGPair Tests"], cwd=repo_path, check=True)
    (repo_path / "README.md").write_text("baseline\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=repo_path, check=True)
    subprocess.run(["git", "commit", "-m", "baseline"], cwd=repo_path, check=True, capture_output=True)


def write_fake_executor(tmp_path: pathlib.Path) -> pathlib.Path:
    bin_path = tmp_path / "fake-antigravity"
    bin_path.write_text(
        """#!/bin/bash
set -euo pipefail
if [ "${1:-}" = "--help" ]; then
  echo "fake antigravity help"
  exit 0
fi
PROMPT="$*"
TASK_ID=$(printf '%s\n' "$PROMPT" | grep -o 'TASK-[A-Za-z0-9-]*' | head -n1)
MODE="${AGPAIR_FAKE_EXECUTOR_MODE:-success}"
if [ -z "$TASK_ID" ]; then
  TASK_ID="TASK-UNKNOWN"
fi

case "$MODE" in
  success)
    echo "fake success for $TASK_ID" > fake_success.txt
    git add fake_success.txt
    git commit -m "feat: fake executor success $TASK_ID" >/dev/null 2>&1
    printf '{"schema_version":"1","task_id":"%s","attempt_no":1,"review_round":0,"status":"COMMITTED","summary":"Ready for review","payload":{"claimed_state":"ready_for_review","changed_files":["fake_success.txt"],"validation_not_run":"fake executor smoke","scope_violations":[],"raw_log_path":"stdout.log","receipt_path":"receipt.json"}}\n' "$TASK_ID"
    ;;
  approval_required)
    printf '{"schema_version":"1","task_id":"%s","attempt_no":1,"review_round":0,"status":"BLOCKED","summary":"Need expanded authorization","payload":{"blocker_type":"approval_required","recoverable":true,"suggested_action":"retry_with_expanded_authorization","authorization_profile":"local_readonly","requested_authorization_profile":"local_mutating","requested_actions":["edit files"],"authorization_delta":{"allow_file_edits":true},"request_reason":"Readonly profile cannot edit files.","risk_assessment":"Repo-local edits only.","safe_to_retry":true,"raw_log_path":"stderr.log"}}\n' "$TASK_ID"
    ;;
  malformed_json)
    printf '{"schema_version":"1","task_id":"%s","status":"COMMITTED","payload":' "$TASK_ID"
    ;;
  scope_violation)
    printf '{"schema_version":"1","task_id":"%s","attempt_no":1,"review_round":0,"status":"COMMITTED","summary":"Scope violated","payload":{"claimed_state":"ready_for_review","changed_files":["../outside.txt"],"validation_not_run":"fake executor smoke","scope_violations":["../outside.txt"],"raw_log_path":"stdout.log","receipt_path":"receipt.json"}}\n' "$TASK_ID"
    ;;
esac
exit 0
""",
        encoding="utf-8",
    )
    bin_path.chmod(0o755)
    return bin_path


def setup_fake_run(tmp_path: pathlib.Path, monkeypatch):
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    fake_executor = write_fake_executor(tmp_path)
    monkeypatch.setenv("AGPAIR_ANTIGRAVITY_CLI", str(fake_executor))
    paths = AppPaths.default()
    ensure_database(paths.db_path)
    repo_path = tmp_path / "repo"
    init_git_repo(repo_path)
    return paths, repo_path


def wait_for_executor_exit(task) -> None:
    assert task.antigravity_session_id is not None
    rc_file = pathlib.Path(task.antigravity_session_id) / "rc.txt"
    for _ in range(50):
        if rc_file.exists():
            return
        time.sleep(0.1)
    raise AssertionError("fake executor did not exit")


def latest_terminal_event(paths: AppPaths, task_id: str):
    journal = JournalRepository(paths.db_path)
    for row in journal.tail(task_id, limit=20):
        if row.event in {"ready_for_review", "committed", "blocked"}:
            return row
    raise AssertionError("terminal event not found")


def start_and_settle(paths: AppPaths, repo_path: pathlib.Path, task_id: str):
    result = CliRunner().invoke(
        app,
        [
            "task",
            "start",
            "--repo-path",
            str(repo_path),
            "--task-id",
            task_id,
            "--body",
            VALID_BRIEF,
            "--no-wait",
        ],
    )
    assert result.exit_code == 0
    tasks = TaskRepository(paths.db_path)
    task = tasks.get_task(task_id)
    assert task is not None
    wait_for_executor_exit(task)
    run_once(paths, now=datetime.now(UTC))
    settled = tasks.get_task(task_id)
    assert settled is not None
    return settled


def test_fake_executor_success_reaches_ready_for_review(tmp_path: pathlib.Path, monkeypatch) -> None:
    paths, repo_path = setup_fake_run(tmp_path, monkeypatch)
    monkeypatch.setenv("AGPAIR_FAKE_EXECUTOR_MODE", "success")

    task = start_and_settle(paths, repo_path, "TASK-FAKE-SUCCESS")

    assert task.phase == "ready_for_review"
    receipt = json.loads(latest_terminal_event(paths, "TASK-FAKE-SUCCESS").body)
    assert receipt["payload"]["claimed_state"] == "ready_for_review"
    assert receipt["payload"]["raw_log_path"].endswith("stdout.log")


def test_fake_executor_approval_required_reaches_blocked(tmp_path: pathlib.Path, monkeypatch) -> None:
    paths, repo_path = setup_fake_run(tmp_path, monkeypatch)
    monkeypatch.setenv("AGPAIR_FAKE_EXECUTOR_MODE", "approval_required")

    task = start_and_settle(paths, repo_path, "TASK-FAKE-APPROVAL")

    assert task.phase == "blocked"
    receipt = json.loads(latest_terminal_event(paths, "TASK-FAKE-APPROVAL").body)
    assert receipt["payload"]["blocker_type"] == "approval_required"
    assert receipt["payload"]["authorization_delta"] == {"allow_file_edits": True}


def test_fake_executor_malformed_receipt_does_not_reach_ready_for_review(tmp_path: pathlib.Path, monkeypatch) -> None:
    paths, repo_path = setup_fake_run(tmp_path, monkeypatch)
    monkeypatch.setenv("AGPAIR_FAKE_EXECUTOR_MODE", "malformed_json")

    task = start_and_settle(paths, repo_path, "TASK-FAKE-MALFORMED")

    assert task.phase == "blocked"
    receipt = json.loads(latest_terminal_event(paths, "TASK-FAKE-MALFORMED").body)
    assert receipt["payload"]["blocker_type"] in {"missing_commit", "validation_failure"}


def test_fake_executor_scope_violation_is_not_silently_accepted(tmp_path: pathlib.Path, monkeypatch) -> None:
    paths, repo_path = setup_fake_run(tmp_path, monkeypatch)
    monkeypatch.setenv("AGPAIR_FAKE_EXECUTOR_MODE", "scope_violation")

    task = start_and_settle(paths, repo_path, "TASK-FAKE-SCOPE")

    assert task.phase == "blocked"
    receipt = json.loads(latest_terminal_event(paths, "TASK-FAKE-SCOPE").body)
    assert receipt["payload"]["blocker_type"] == "validation_failure"
    assert receipt["payload"]["scope_violations"] == ["../outside.txt"]
