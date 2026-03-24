from pathlib import Path
from typer.testing import CliRunner

from agpair.cli.app import app
from agpair.config import AppPaths
from agpair.storage.db import ensure_database
from agpair.storage.journal import JournalRepository
from agpair.storage.receipts import ReceiptRepository
from agpair.storage.tasks import TaskNotFoundError, TaskRepository
from tests.fixtures.fake_agent_bus import read_calls, write_fake_agent_bus


def make_paths(tmp_path: Path) -> AppPaths:
    return AppPaths.from_root(tmp_path / ".agpair")


def make_task_repo(tmp_path: Path) -> TaskRepository:
    paths = make_paths(tmp_path)
    ensure_database(paths.db_path)
    return TaskRepository(paths.db_path)


def make_receipt_repo(tmp_path: Path) -> ReceiptRepository:
    paths = make_paths(tmp_path)
    ensure_database(paths.db_path)
    return ReceiptRepository(paths.db_path)


def make_journal_repo(tmp_path: Path) -> JournalRepository:
    paths = make_paths(tmp_path)
    ensure_database(paths.db_path)
    return JournalRepository(paths.db_path)


def test_ensure_database_creates_sqlite_schema(tmp_path: Path) -> None:
    paths = make_paths(tmp_path)
    ensure_database(paths.db_path)
    assert paths.db_path.exists()


def test_task_repository_persists_session_mapping(tmp_path: Path) -> None:
    repo = make_task_repo(tmp_path)
    repo.create_task(task_id="TASK-1", repo_path="/tmp/repo")
    repo.mark_acked(task_id="TASK-1", session_id="session-123")

    task = repo.get_task("TASK-1")
    assert task is not None
    assert task.phase == "acked"
    assert task.antigravity_session_id == "session-123"


def test_receipt_repository_deduplicates_by_message_id(tmp_path: Path) -> None:
    receipts = make_receipt_repo(tmp_path)
    assert receipts.record("msg-1", "TASK-1", "ACK") is True
    assert receipts.record("msg-1", "TASK-1", "ACK") is False


def test_journal_repository_appends_and_reads_tail(tmp_path: Path) -> None:
    journal = make_journal_repo(tmp_path)
    journal.append("TASK-1", "cli", "created", "Goal: test")
    journal.append("TASK-1", "daemon", "acked", "session-123")

    rows = journal.tail("TASK-1", limit=2)
    assert len(rows) == 2
    assert rows[0].event == "acked"
    assert rows[0].source == "daemon"
    assert rows[1].event == "created"
    assert rows[1].source == "cli"


def test_task_repository_raises_when_task_is_missing(tmp_path: Path) -> None:
    repo = make_task_repo(tmp_path)
    try:
        repo.mark_acked(task_id="TASK-404", session_id="session-123")
    except TaskNotFoundError as exc:
        assert "TASK-404" in str(exc)
    else:
        raise AssertionError("expected TaskNotFoundError")


def test_task_start_creates_local_record_and_sends_task(tmp_path: Path, monkeypatch) -> None:
    binary, calls_path, pull_path = write_fake_agent_bus(tmp_path)
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    monkeypatch.setenv("AGPAIR_AGENT_BUS_BIN", binary)
    monkeypatch.setenv("FAKE_AGENT_BUS_CALLS", str(calls_path))
    monkeypatch.setenv("FAKE_AGENT_BUS_PULL", str(pull_path))

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["task", "start", "--repo-path", "/tmp/repo", "--body", "Goal: fix it", "--task-id", "TASK-CLI-1", "--no-wait"],
    )

    assert result.exit_code == 0
    assert "TASK-CLI-1" in result.stdout

    task = make_task_repo(tmp_path).get_task("TASK-CLI-1")
    assert task is not None
    assert task.phase == "new"
    recorded = read_calls(calls_path)
    assert recorded[-1]["argv"][:4] == ["agent-bus", "send", "--sender", "desktop"]


def test_task_status_shows_phase_and_session(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    repo = make_task_repo(tmp_path)
    repo.create_task(task_id="TASK-1", repo_path="/tmp/repo")
    repo.mark_acked(task_id="TASK-1", session_id="session-123")

    result = CliRunner().invoke(app, ["task", "status", "TASK-1"])
    assert result.exit_code == 0
    assert "phase: acked" in result.stdout
    assert "session_id: session-123" in result.stdout


def test_task_logs_prints_recent_journal_entries(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    repo = make_task_repo(tmp_path)
    repo.create_task(task_id="TASK-1", repo_path="/tmp/repo")
    journal = make_journal_repo(tmp_path)
    journal.append("TASK-1", "cli", "created", "Goal: test")
    journal.append("TASK-1", "daemon", "acked", "session-123")

    result = CliRunner().invoke(app, ["task", "logs", "TASK-1", "--limit", "2"])
    assert result.exit_code == 0
    assert "[daemon] acked: session-123" in result.stdout
    assert "[cli] created: Goal: test" in result.stdout


def test_task_start_marks_blocked_when_dispatch_fails(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    monkeypatch.setenv("AGPAIR_AGENT_BUS_BIN", str(tmp_path / "missing-agent-bus"))

    result = CliRunner().invoke(
        app,
        ["task", "start", "--repo-path", "/tmp/repo", "--body", "Goal: fix it", "--task-id", "TASK-FAIL-1"],
    )

    assert result.exit_code == 1
    assert "dispatch failed:" in result.stderr
    task = make_task_repo(tmp_path).get_task("TASK-FAIL-1")
    assert task is not None
    assert task.phase == "blocked"
    assert task.stuck_reason is not None


def test_task_logs_fails_when_task_is_missing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    result = CliRunner().invoke(app, ["task", "logs", "TASK-404"])
    assert result.exit_code == 1


def test_task_list_prints_recent_tasks(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    repo = make_task_repo(tmp_path)
    repo.create_task(task_id="TASK-1", repo_path="/tmp/repo-a")
    repo.create_task(task_id="TASK-2", repo_path="/tmp/repo-b")
    repo.mark_acked(task_id="TASK-2", session_id="session-123")

    result = CliRunner().invoke(app, ["task", "list"])

    assert result.exit_code == 0
    assert "TASK-2 acked attempt=1 retry=0 recommended=False repo=/tmp/repo-b" in result.stdout
    assert "TASK-1 new attempt=1 retry=0 recommended=False repo=/tmp/repo-a" in result.stdout


def test_task_list_can_filter_by_phase(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    repo = make_task_repo(tmp_path)
    repo.create_task(task_id="TASK-1", repo_path="/tmp/repo-a")
    repo.create_task(task_id="TASK-2", repo_path="/tmp/repo-b")
    repo.mark_acked(task_id="TASK-2", session_id="session-123")

    result = CliRunner().invoke(app, ["task", "list", "--phase", "acked"])

    assert result.exit_code == 0
    assert "TASK-2 acked" in result.stdout
    assert "TASK-1" not in result.stdout


def test_task_abandon_marks_task_terminal_locally(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    repo = make_task_repo(tmp_path)
    journal = make_journal_repo(tmp_path)
    repo.create_task(task_id="TASK-1", repo_path="/tmp/repo")
    repo.mark_acked(task_id="TASK-1", session_id="session-123")
    journal.append("TASK-1", "daemon", "acked", "session_id=session-123")

    result = CliRunner().invoke(app, ["task", "abandon", "TASK-1", "--reason", "manual cleanup"])

    assert result.exit_code == 0
    task = repo.get_task("TASK-1")
    assert task is not None
    assert task.phase == "abandoned"
    assert task.stuck_reason == "manual cleanup"
    rows = journal.tail("TASK-1", limit=5)
    assert any(row.event == "abandoned" and row.source == "cli" for row in rows)


def test_task_abandon_fails_when_task_is_missing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))

    result = CliRunner().invoke(app, ["task", "abandon", "TASK-404"])

    assert result.exit_code == 1
