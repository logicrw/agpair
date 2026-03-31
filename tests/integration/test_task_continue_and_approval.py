from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from agpair.cli.app import app
from agpair.storage.db import ensure_database
from agpair.storage.journal import JournalRepository
from agpair.storage.tasks import TaskRepository
from tests.fixtures.fake_agent_bus import read_calls, write_fake_agent_bus


def seed_acked_task(tmp_path: Path, task_id: str = "TASK-1") -> TaskRepository:
    db_path = tmp_path / ".agpair" / "agpair.db"
    ensure_database(db_path)
    repo = TaskRepository(db_path)
    repo.create_task(task_id=task_id, repo_path="/tmp/repo")
    repo.mark_acked(task_id=task_id, session_id="session-123")
    return repo


def seed_evidence_ready_task(tmp_path: Path, task_id: str = "TASK-1") -> TaskRepository:
    repo = seed_acked_task(tmp_path, task_id)
    repo.mark_evidence_ready(task_id=task_id, last_receipt_id="101")
    return repo


def append_confirmation(
    tmp_path: Path,
    *,
    task_id: str,
    event: str,
    body: str,
    reply_to_message_id: int = 101,
) -> None:
    journal = JournalRepository(tmp_path / ".agpair" / "agpair.db")
    journal.append(task_id, "daemon", event, f"reply_to_message_id={reply_to_message_id}\n{body}")


def test_task_continue_sends_review_for_existing_session(tmp_path: Path, monkeypatch) -> None:
    binary, calls_path, _pull_path = write_fake_agent_bus(tmp_path)
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    monkeypatch.setenv("AGPAIR_AGENT_BUS_BIN", binary)
    monkeypatch.setenv("FAKE_AGENT_BUS_CALLS", str(calls_path))
    monkeypatch.setenv("FAKE_AGENT_BUS_PULL", str(_pull_path))
    seed_acked_task(tmp_path)
    append_confirmation(tmp_path, task_id="TASK-1", event="review_ack", body="OK")

    result = CliRunner().invoke(app, ["task", "continue", "TASK-1", "--body", "Please fix edge case", "--no-wait"])

    assert result.exit_code == 0
    recorded = read_calls(calls_path)
    sent_calls = [c for c in recorded if c["argv"][1] == "send"]
    pull_calls = [c for c in recorded if c["argv"][1] == "pull"]
    assert pull_calls == []
    assert sent_calls[-1]["argv"][:8] == [
        "agent-bus",
        "send",
        "--sender",
        "desktop",
        "--task-id",
        "TASK-1",
        "--status",
        "REVIEW",
    ]


def test_task_continue_sends_review_for_evidence_ready_task(tmp_path: Path, monkeypatch) -> None:
    binary, calls_path, _pull_path = write_fake_agent_bus(tmp_path)
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    monkeypatch.setenv("AGPAIR_AGENT_BUS_BIN", binary)
    monkeypatch.setenv("FAKE_AGENT_BUS_CALLS", str(calls_path))
    monkeypatch.setenv("FAKE_AGENT_BUS_PULL", str(_pull_path))
    seed_evidence_ready_task(tmp_path)
    append_confirmation(tmp_path, task_id="TASK-1", event="review_ack", body="OK")

    result = CliRunner().invoke(app, ["task", "continue", "TASK-1", "--body", "Please fix edge case", "--no-wait"])

    assert result.exit_code == 0
    recorded = read_calls(calls_path)
    sent_calls = [c for c in recorded if c["argv"][1] == "send"]
    pull_calls = [c for c in recorded if c["argv"][1] == "pull"]
    assert pull_calls == []
    assert sent_calls[-1]["argv"][:8] == [
        "agent-bus",
        "send",
        "--sender",
        "desktop",
        "--task-id",
        "TASK-1",
        "--status",
        "REVIEW",
    ]


def test_task_continue_fails_on_nack(tmp_path: Path, monkeypatch) -> None:
    binary, calls_path, _pull_path = write_fake_agent_bus(tmp_path)
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    monkeypatch.setenv("AGPAIR_AGENT_BUS_BIN", binary)
    monkeypatch.setenv("FAKE_AGENT_BUS_CALLS", str(calls_path))
    monkeypatch.setenv("FAKE_AGENT_BUS_PULL", str(_pull_path))
    seed_acked_task(tmp_path)
    append_confirmation(tmp_path, task_id="TASK-1", event="review_nack", body="Session lost")

    result = CliRunner().invoke(app, ["task", "continue", "TASK-1", "--body", "Please fix", "--no-wait"])
    assert result.exit_code == 1
    assert "Session lost" in result.stderr


def test_task_continue_fails_on_nack_for_evidence_ready_task(tmp_path: Path, monkeypatch) -> None:
    binary, calls_path, _pull_path = write_fake_agent_bus(tmp_path)
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    monkeypatch.setenv("AGPAIR_AGENT_BUS_BIN", binary)
    monkeypatch.setenv("FAKE_AGENT_BUS_CALLS", str(calls_path))
    monkeypatch.setenv("FAKE_AGENT_BUS_PULL", str(_pull_path))
    seed_evidence_ready_task(tmp_path)
    append_confirmation(tmp_path, task_id="TASK-1", event="review_nack", body="Session lost")

    result = CliRunner().invoke(app, ["task", "continue", "TASK-1", "--body", "Please fix", "--no-wait"])
    assert result.exit_code == 1
    assert "Session lost" in result.stderr


def test_task_continue_fails_on_timeout(tmp_path: Path, monkeypatch) -> None:
    # We patch time.time locally to simulate a timeout without actually waiting 15s.
    import time
    original_time = time.time

    class FakeTime:
        def __init__(self):
            self.current = original_time()
            self.calls = 0

        def __call__(self):
            self.calls += 1
            if self.calls > 3:  # third time.time() check
                self.current += 30.0
            return self.current

        def sleep(self, seconds):
            self.current += seconds

    fake_time = FakeTime()
    monkeypatch.setattr("time.time", fake_time)
    monkeypatch.setattr("time.sleep", fake_time.sleep)

    binary, calls_path, _pull_path = write_fake_agent_bus(tmp_path)
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    monkeypatch.setenv("AGPAIR_AGENT_BUS_BIN", binary)
    monkeypatch.setenv("FAKE_AGENT_BUS_CALLS", str(calls_path))
    monkeypatch.setenv("FAKE_AGENT_BUS_PULL", str(_pull_path))
    seed_acked_task(tmp_path)

    result = CliRunner().invoke(app, ["task", "continue", "TASK-1", "--body", "Please fix", "--no-wait"])
    assert result.exit_code == 1
    assert "timeout waiting for extension confirmation" in result.stderr


def test_task_continue_fails_on_timeout_for_evidence_ready_task(tmp_path: Path, monkeypatch) -> None:
    # We patch time.time locally to simulate a timeout without actually waiting 15s.
    import time
    original_time = time.time

    class FakeTime:
        def __init__(self):
            self.current = original_time()
            self.calls = 0

        def __call__(self):
            self.calls += 1
            if self.calls > 3:  # third time.time() check
                self.current += 30.0
            return self.current

        def sleep(self, seconds):
            self.current += seconds

    fake_time = FakeTime()
    monkeypatch.setattr("time.time", fake_time)
    monkeypatch.setattr("time.sleep", fake_time.sleep)

    binary, calls_path, _pull_path = write_fake_agent_bus(tmp_path)
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    monkeypatch.setenv("AGPAIR_AGENT_BUS_BIN", binary)
    monkeypatch.setenv("FAKE_AGENT_BUS_CALLS", str(calls_path))
    monkeypatch.setenv("FAKE_AGENT_BUS_PULL", str(_pull_path))
    seed_evidence_ready_task(tmp_path)

    result = CliRunner().invoke(app, ["task", "continue", "TASK-1", "--body", "Please fix", "--no-wait"])
    assert result.exit_code == 1
    assert "timeout waiting for extension confirmation" in result.stderr


def test_task_continue_ignores_ack_for_previous_message_id(tmp_path: Path, monkeypatch) -> None:
    import time
    original_time = time.time

    class FakeTime:
        def __init__(self):
            self.current = original_time()
            self.calls = 0

        def __call__(self):
            self.calls += 1
            if self.calls > 3:
                self.current += 30.0
            return self.current

        def sleep(self, seconds):
            self.current += seconds

    fake_time = FakeTime()
    monkeypatch.setattr("time.time", fake_time)
    monkeypatch.setattr("time.sleep", fake_time.sleep)

    binary, calls_path, _pull_path = write_fake_agent_bus(tmp_path)
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    monkeypatch.setenv("AGPAIR_AGENT_BUS_BIN", binary)
    monkeypatch.setenv("FAKE_AGENT_BUS_CALLS", str(calls_path))
    monkeypatch.setenv("FAKE_AGENT_BUS_PULL", str(_pull_path))
    seed_evidence_ready_task(tmp_path)
    append_confirmation(
        tmp_path,
        task_id="TASK-1",
        event="review_ack",
        body="Old ack that should be ignored",
        reply_to_message_id=100,
    )

    result = CliRunner().invoke(app, ["task", "continue", "TASK-1", "--body", "Please fix", "--no-wait"])

    assert result.exit_code == 1
    assert "timeout waiting for extension confirmation" in result.stderr


def test_task_approve_sends_approved(tmp_path: Path, monkeypatch) -> None:
    binary, calls_path, _pull_path = write_fake_agent_bus(tmp_path)
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    monkeypatch.setenv("AGPAIR_AGENT_BUS_BIN", binary)
    monkeypatch.setenv("FAKE_AGENT_BUS_CALLS", str(calls_path))
    monkeypatch.setenv("FAKE_AGENT_BUS_PULL", str(_pull_path))
    seed_acked_task(tmp_path)
    append_confirmation(tmp_path, task_id="TASK-1", event="approve_ack", body="OK")

    result = CliRunner().invoke(app, ["task", "approve", "TASK-1", "--body", "Looks good", "--no-wait"])

    assert result.exit_code == 0
    recorded = read_calls(calls_path)
    sent_calls = [c for c in recorded if c["argv"][1] == "send"]
    assert sent_calls[-1]["argv"][:8] == [
        "agent-bus",
        "send",
        "--sender",
        "desktop",
        "--task-id",
        "TASK-1",
        "--status",
        "APPROVED",
    ]


def test_task_approve_sends_approved_for_evidence_ready_task(tmp_path: Path, monkeypatch) -> None:
    binary, calls_path, _pull_path = write_fake_agent_bus(tmp_path)
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    monkeypatch.setenv("AGPAIR_AGENT_BUS_BIN", binary)
    monkeypatch.setenv("FAKE_AGENT_BUS_CALLS", str(calls_path))
    monkeypatch.setenv("FAKE_AGENT_BUS_PULL", str(_pull_path))
    seed_evidence_ready_task(tmp_path)
    append_confirmation(tmp_path, task_id="TASK-1", event="approve_ack", body="OK")

    result = CliRunner().invoke(app, ["task", "approve", "TASK-1", "--body", "Looks good", "--no-wait"])

    assert result.exit_code == 0
    recorded = read_calls(calls_path)
    sent_calls = [c for c in recorded if c["argv"][1] == "send"]
    assert sent_calls[-1]["argv"][:8] == [
        "agent-bus",
        "send",
        "--sender",
        "desktop",
        "--task-id",
        "TASK-1",
        "--status",
        "APPROVED",
    ]


def test_task_approve_fails_on_nack(tmp_path: Path, monkeypatch) -> None:
    binary, calls_path, _pull_path = write_fake_agent_bus(tmp_path)
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    monkeypatch.setenv("AGPAIR_AGENT_BUS_BIN", binary)
    monkeypatch.setenv("FAKE_AGENT_BUS_CALLS", str(calls_path))
    monkeypatch.setenv("FAKE_AGENT_BUS_PULL", str(_pull_path))
    seed_acked_task(tmp_path)
    append_confirmation(tmp_path, task_id="TASK-1", event="approve_nack", body="Session deleted")

    result = CliRunner().invoke(app, ["task", "approve", "TASK-1", "--body", "Looks good", "--no-wait"])

    assert result.exit_code == 1
    assert "Session deleted" in result.stderr


def test_task_approve_fails_on_nack_for_evidence_ready_task(tmp_path: Path, monkeypatch) -> None:
    binary, calls_path, _pull_path = write_fake_agent_bus(tmp_path)
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    monkeypatch.setenv("AGPAIR_AGENT_BUS_BIN", binary)
    monkeypatch.setenv("FAKE_AGENT_BUS_CALLS", str(calls_path))
    monkeypatch.setenv("FAKE_AGENT_BUS_PULL", str(_pull_path))
    seed_evidence_ready_task(tmp_path)
    append_confirmation(tmp_path, task_id="TASK-1", event="approve_nack", body="Session deleted")

    result = CliRunner().invoke(app, ["task", "approve", "TASK-1", "--body", "Looks good", "--no-wait"])

    assert result.exit_code == 1
    assert "Session deleted" in result.stderr


def test_task_reject_routes_back_as_review_feedback(tmp_path: Path, monkeypatch) -> None:
    binary, calls_path, _pull_path = write_fake_agent_bus(tmp_path)
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    monkeypatch.setenv("AGPAIR_AGENT_BUS_BIN", binary)
    monkeypatch.setenv("FAKE_AGENT_BUS_CALLS", str(calls_path))
    monkeypatch.setenv("FAKE_AGENT_BUS_PULL", str(_pull_path))
    seed_acked_task(tmp_path)
    append_confirmation(tmp_path, task_id="TASK-1", event="review_ack", body="OK")

    result = CliRunner().invoke(app, ["task", "reject", "TASK-1", "--body", "Still failing", "--no-wait"])

    assert result.exit_code == 0
    recorded = read_calls(calls_path)
    sent_calls = [c for c in recorded if c["argv"][1] == "send"]
    assert sent_calls[-1]["argv"][:8] == [
        "agent-bus",
        "send",
        "--sender",
        "desktop",
        "--task-id",
        "TASK-1",
        "--status",
        "REVIEW",
    ]


def test_task_retry_creates_fresh_attempt_and_new_task_message(tmp_path: Path, monkeypatch) -> None:
    binary, calls_path, _pull_path = write_fake_agent_bus(tmp_path)
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    monkeypatch.setenv("AGPAIR_AGENT_BUS_BIN", binary)
    monkeypatch.setenv("FAKE_AGENT_BUS_CALLS", str(calls_path))
    monkeypatch.setenv("FAKE_AGENT_BUS_PULL", str(_pull_path))
    repo = seed_acked_task(tmp_path)

    result = CliRunner().invoke(app, ["task", "retry", "TASK-1", "--body", "Retry with a fresh session", "--no-wait"])

    assert result.exit_code == 0
    task = repo.get_task("TASK-1")
    assert task is not None
    assert task.attempt_no == 2
    assert task.retry_count == 1
    assert task.antigravity_session_id is None
    recorded = read_calls(calls_path)
    sent_calls = [c for c in recorded if c["argv"][1] == "send"]
    assert sent_calls[-1]["argv"][:8] == [
        "agent-bus",
        "send",
        "--sender",
        "desktop",
        "--task-id",
        "TASK-1",
        "--status",
        "TASK",
    ]


def test_task_continue_requires_known_session_mapping(tmp_path: Path, monkeypatch) -> None:
    binary, calls_path, _pull_path = write_fake_agent_bus(tmp_path)
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    monkeypatch.setenv("AGPAIR_AGENT_BUS_BIN", binary)
    monkeypatch.setenv("FAKE_AGENT_BUS_CALLS", str(calls_path))
    monkeypatch.setenv("FAKE_AGENT_BUS_PULL", str(_pull_path))
    db_path = tmp_path / ".agpair" / "agpair.db"
    ensure_database(db_path)
    repo = TaskRepository(db_path)
    repo.create_task(task_id="TASK-1", repo_path="/tmp/repo")

    result = CliRunner().invoke(app, ["task", "continue", "TASK-1", "--body", "Please fix edge case", "--no-wait"])

    assert result.exit_code == 1
    assert read_calls(calls_path) == []


def test_task_retry_does_not_mutate_state_when_dispatch_fails(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    monkeypatch.setenv("AGPAIR_AGENT_BUS_BIN", str(tmp_path / "missing-agent-bus"))
    repo = seed_acked_task(tmp_path)

    result = CliRunner().invoke(app, ["task", "retry", "TASK-1", "--body", "Retry with a fresh session", "--no-wait"])

    assert result.exit_code == 1
    task = repo.get_task("TASK-1")
    assert task is not None
    assert task.phase == "acked"
    assert task.attempt_no == 1
    assert task.retry_count == 0
    assert task.antigravity_session_id == "session-123"


def test_task_approve_fails_cleanly_when_dispatch_fails(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    monkeypatch.setenv("AGPAIR_AGENT_BUS_BIN", str(tmp_path / "missing-agent-bus"))
    seed_acked_task(tmp_path)

    result = CliRunner().invoke(app, ["task", "approve", "TASK-1", "--body", "Looks good"])

    assert result.exit_code == 1
    assert "dispatch failed:" in result.stderr


def test_task_continue_fresh_resume_preserves_context(tmp_path: Path, monkeypatch) -> None:
    import json
    from agpair.storage.journal import JournalRepository

    binary, calls_path, _pull_path = write_fake_agent_bus(tmp_path)
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    monkeypatch.setenv("AGPAIR_AGENT_BUS_BIN", binary)
    monkeypatch.setenv("FAKE_AGENT_BUS_CALLS", str(calls_path))
    monkeypatch.setenv("FAKE_AGENT_BUS_PULL", str(_pull_path))
    repo = seed_acked_task(tmp_path)

    journal = JournalRepository(tmp_path / ".agpair" / "agpair.db")
    journal.append("TASK-1", "cli", "created", "Original Task Body")

    receipt = json.dumps({
        "schema_version": "1",
        "task_id": "TASK-1",
        "attempt_no": 1,
        "review_round": 0,
        "status": "EVIDENCE_PACK",
        "summary": "Evidence achieved!",
        "payload": {}
    })
    journal.append("TASK-1", "daemon", "evidence_ready", receipt)

    result = CliRunner().invoke(app, ["task", "continue", "TASK-1", "--body", "Fix stuff", "--fresh-resume", "--no-wait"])

    assert result.exit_code == 0
    task = repo.get_task("TASK-1")
    assert task is not None
    assert task.attempt_no == 2
    assert task.retry_count == 1

    recorded = read_calls(calls_path)
    sent_calls = [c for c in recorded if c["argv"][1] == "send"]
    assert sent_calls[-1]["argv"][:8] == [
        "agent-bus",
        "send",
        "--sender",
        "desktop",
        "--task-id",
        "TASK-1",
        "--status",
        "TASK",
    ]
    body = sent_calls[-1]["body"]
    assert "Original Task Body" in body
    assert "Evidence achieved!" in body
    assert "Fix stuff" in body


def test_task_approve_fresh_resume_preserves_context(tmp_path: Path, monkeypatch) -> None:
    import json
    from agpair.storage.journal import JournalRepository

    binary, calls_path, _pull_path = write_fake_agent_bus(tmp_path)
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    monkeypatch.setenv("AGPAIR_AGENT_BUS_BIN", binary)
    monkeypatch.setenv("FAKE_AGENT_BUS_CALLS", str(calls_path))
    monkeypatch.setenv("FAKE_AGENT_BUS_PULL", str(_pull_path))
    repo = seed_acked_task(tmp_path)

    journal = JournalRepository(tmp_path / ".agpair" / "agpair.db")
    journal.append("TASK-1", "cli", "created", "Original Task Body")

    receipt = json.dumps({
        "schema_version": "1",
        "task_id": "TASK-1",
        "attempt_no": 1,
        "review_round": 0,
        "status": "EVIDENCE_PACK",
        "summary": "Evidence achieved!",
        "payload": {}
    })
    journal.append("TASK-1", "daemon", "evidence_ready", receipt)

    result = CliRunner().invoke(app, ["task", "approve", "TASK-1", "--body", "Approved it", "--fresh-resume", "--no-wait"])

    assert result.exit_code == 0
    task = repo.get_task("TASK-1")
    assert task is not None
    assert task.attempt_no == 2
    assert task.retry_count == 1
    recorded = read_calls(calls_path)
    sent_calls = [c for c in recorded if c["argv"][1] == "send"]
    assert sent_calls[-1]["argv"][:8] == [
        "agent-bus",
        "send",
        "--sender",
        "desktop",
        "--task-id",
        "TASK-1",
        "--status",
        "TASK",
    ]
    body = sent_calls[-1]["body"]
    assert "Original Task Body" in body
    assert "Evidence achieved!" in body
    assert "Approved it" in body

def test_task_continue_codex_forces_fresh_resume(tmp_path: Path, monkeypatch) -> None:
    import json
    from agpair.storage.journal import JournalRepository

    binary, calls_path, _pull_path = write_fake_agent_bus(tmp_path)
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    monkeypatch.setenv("AGPAIR_AGENT_BUS_BIN", binary)

    db_path = tmp_path / ".agpair" / "agpair.db"
    ensure_database(db_path)
    repo = TaskRepository(db_path)
    repo.create_task(task_id="TASK-1", repo_path="/tmp/repo", executor_backend="codex_cli")
    repo.mark_acked(task_id="TASK-1", session_id="session-123")

    class DummyDispatchResult:
        temp_dir = tmp_path / "dummy_codex_session"

    dummy_called = []
    
    # We must patch CodexExecutor's dispatch to avoid actually running codex.
    from agpair.executors.codex import CodexExecutor
    def dummy_dispatch(self, task_id, body, repo_path):
        dummy_called.append({"task_id": task_id, "body": body})
        return DummyDispatchResult()

    monkeypatch.setattr(CodexExecutor, "dispatch", dummy_dispatch)

    journal = JournalRepository(tmp_path / ".agpair" / "agpair.db")
    journal.append("TASK-1", "cli", "created", "Original Task Body")

    result = CliRunner().invoke(app, ["task", "continue", "TASK-1", "--body", "Fix stuff", "--no-wait"])

    assert result.exit_code == 0
    task = repo.get_task("TASK-1")
    assert task is not None
    assert task.attempt_no == 2
    assert task.retry_count == 1
    assert "dummy_codex_session" in task.antigravity_session_id

    assert len(dummy_called) == 1
    assert dummy_called[0]["task_id"] == "TASK-1"
    assert "Original Task Body" in dummy_called[0]["body"]
    assert "Fix stuff" in dummy_called[0]["body"]


def test_task_approve_codex_forces_fresh_resume(tmp_path: Path, monkeypatch) -> None:
    import json
    from agpair.storage.journal import JournalRepository

    binary, calls_path, _pull_path = write_fake_agent_bus(tmp_path)
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    monkeypatch.setenv("AGPAIR_AGENT_BUS_BIN", binary)

    db_path = tmp_path / ".agpair" / "agpair.db"
    ensure_database(db_path)
    repo = TaskRepository(db_path)
    repo.create_task(task_id="TASK-1", repo_path="/tmp/repo", executor_backend="codex_cli")
    repo.mark_acked(task_id="TASK-1", session_id="session-123")

    class DummyDispatchResult:
        temp_dir = tmp_path / "dummy_codex_session_"

    dummy_called = []
    
    from agpair.executors.codex import CodexExecutor
    def dummy_dispatch(self, task_id, body, repo_path):
        dummy_called.append({"task_id": task_id, "body": body})
        return DummyDispatchResult()

    monkeypatch.setattr(CodexExecutor, "dispatch", dummy_dispatch)

    journal = JournalRepository(tmp_path / ".agpair" / "agpair.db")
    journal.append("TASK-1", "cli", "created", "Original Task Body")

    result = CliRunner().invoke(app, ["task", "approve", "TASK-1", "--body", "Looks good", "--no-wait"])

    assert result.exit_code == 0
    task = repo.get_task("TASK-1")
    assert task is not None
    assert task.attempt_no == 2
    assert task.retry_count == 1
    assert "dummy_codex_session_" in task.antigravity_session_id

    assert len(dummy_called) == 1
    assert dummy_called[0]["task_id"] == "TASK-1"
    assert "Original Task Body" in dummy_called[0]["body"]
    assert "Looks good" in dummy_called[0]["body"]

def test_task_reject_codex_forces_fresh_resume(tmp_path: Path, monkeypatch) -> None:
    import json
    from agpair.storage.journal import JournalRepository

    binary, calls_path, _pull_path = write_fake_agent_bus(tmp_path)
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    monkeypatch.setenv("AGPAIR_AGENT_BUS_BIN", binary)

    db_path = tmp_path / ".agpair" / "agpair.db"
    ensure_database(db_path)
    repo = TaskRepository(db_path)
    repo.create_task(task_id="TASK-1", repo_path="/tmp/repo", executor_backend="codex_cli")
    repo.mark_acked(task_id="TASK-1", session_id="session-123")

    class DummyDispatchResult:
        temp_dir = tmp_path / "dummy_codex_session_reject"

    dummy_called = []
    
    from agpair.executors.codex import CodexExecutor
    def dummy_dispatch(self, task_id, body, repo_path):
        dummy_called.append({"task_id": task_id, "body": body})
        return DummyDispatchResult()

    monkeypatch.setattr(CodexExecutor, "dispatch", dummy_dispatch)

    journal = JournalRepository(tmp_path / ".agpair" / "agpair.db")
    journal.append("TASK-1", "cli", "created", "Original Task Body")

    result = CliRunner().invoke(app, ["task", "reject", "TASK-1", "--body", "Fix your errors", "--no-wait"])

    assert result.exit_code == 0
    task = repo.get_task("TASK-1")
    assert task is not None
    assert task.attempt_no == 2
    assert task.retry_count == 1

    assert len(dummy_called) == 1
    assert "Fix your errors" in dummy_called[0]["body"]

def test_task_continue_auto_resumes_on_synthetic_nack(tmp_path: Path, monkeypatch) -> None:
    import json
    from agpair.storage.journal import JournalRepository
    
    binary, calls_path, _pull_path = write_fake_agent_bus(tmp_path)
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    monkeypatch.setenv("AGPAIR_AGENT_BUS_BIN", binary)
    monkeypatch.setenv("FAKE_AGENT_BUS_CALLS", str(calls_path))
    monkeypatch.setenv("FAKE_AGENT_BUS_PULL", str(_pull_path))
    repo = seed_acked_task(tmp_path)
    
    journal = JournalRepository(tmp_path / ".agpair" / "agpair.db")
    journal.append("TASK-1", "cli", "created", "Original Task Body")
    
    append_confirmation(tmp_path, task_id="TASK-1", event="review_nack", body="Cannot continue synthetic session ag-cmd-1234. Please use --fresh-resume instead.")

    result = CliRunner().invoke(app, ["task", "continue", "TASK-1", "--body", "Please fix", "--no-wait"])
    assert result.exit_code == 0
    assert "Auto-converting to fresh resume: same-session continuation is impossible for synthetic ids." in result.stdout
    
    task = repo.get_task("TASK-1")
    assert task is not None
    assert task.attempt_no == 2
    
    recorded = read_calls(calls_path)
    sent_calls = [c for c in recorded if c["argv"][1] == "send"]
    # The first send is `review`, the second one is `task` (fresh resume)
    # Actually wait: The `continue_task` calls `_send_semantic_or_exit`. The `_send_semantic_or_exit` calls `bus.send_review` which executes `agent-bus send ... --status REVIEW`.
    # Then `agent-bus` returns a message_id. Then we read the journal and see `review_nack` and it raises `SyntheticSessionNackError`.
    # Then `continue_task` catches it and calls `_prepare_fresh_resume_dispatch`. 
    # `_prepare_fresh_resume_dispatch` then calls `active_exec.dispatch()` which does `bus.send_task(...)` (`agent-bus send ... --status TASK`).
    assert sent_calls[-1]["argv"][:8] == [
        "agent-bus",
        "send",
        "--sender",
        "desktop",
        "--task-id",
        "TASK-1",
        "--status",
        "TASK",
    ]
    body = sent_calls[-1]["body"]
    assert "Original Task Body" in body
    assert "Please fix" in body

def test_task_approve_auto_resumes_on_synthetic_nack(tmp_path: Path, monkeypatch) -> None:
    import json
    from agpair.storage.journal import JournalRepository
    
    binary, calls_path, _pull_path = write_fake_agent_bus(tmp_path)
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    monkeypatch.setenv("AGPAIR_AGENT_BUS_BIN", binary)
    monkeypatch.setenv("FAKE_AGENT_BUS_CALLS", str(calls_path))
    monkeypatch.setenv("FAKE_AGENT_BUS_PULL", str(_pull_path))
    repo = seed_acked_task(tmp_path)
    
    journal = JournalRepository(tmp_path / ".agpair" / "agpair.db")
    journal.append("TASK-1", "cli", "created", "Original Task Body")
    
    append_confirmation(tmp_path, task_id="TASK-1", event="approve_nack", body="Cannot commit synthetic session ag-cmd-1234. Please use --fresh-resume instead.")

    result = CliRunner().invoke(app, ["task", "approve", "TASK-1", "--body", "Looks good", "--no-wait"])
    assert result.exit_code == 0
    assert "Auto-converting to fresh resume: same-session continuation is impossible for synthetic ids." in result.stdout
    
    task = repo.get_task("TASK-1")
    assert task is not None
    assert task.attempt_no == 2
    
    recorded = read_calls(calls_path)
    sent_calls = [c for c in recorded if c["argv"][1] == "send"]
    assert sent_calls[-1]["argv"][:8] == [
        "agent-bus",
        "send",
        "--sender",
        "desktop",
        "--task-id",
        "TASK-1",
        "--status",
        "TASK",
    ]
    body = sent_calls[-1]["body"]
    assert "Original Task Body" in body
    assert "Looks good" in body
