"""Test the cleanup command and auto-cleanup."""
from datetime import UTC, datetime, timedelta

from typer.testing import CliRunner

from agpair.cli.app import app
from agpair.config import AppPaths
from agpair.storage.db import connect, ensure_database
from agpair.storage.journal import JournalRepository
from agpair.storage.receipts import ReceiptRepository
from agpair.storage.tasks import TaskRepository


def _make_old(conn, table, old_time):
    conn.execute(f"UPDATE {table} SET created_at = ?", (old_time,))
    conn.commit()


def _old_cutoff(days=30):
    return (datetime.now(UTC) - timedelta(days=days)).isoformat().replace("+00:00", "Z")


def test_cleanup_removes_old_journals(tmp_path):
    paths = AppPaths.from_root(tmp_path / ".agpair")
    ensure_database(paths.db_path)
    old_time = (datetime.now(UTC) - timedelta(days=60)).isoformat().replace("+00:00", "Z")

    journal = JournalRepository(paths.db_path)
    journal.append("old-task", "test", "test_event", "old data")
    with connect(paths.db_path) as conn:
        _make_old(conn, "journal", old_time)

    assert journal.count_older_than(_old_cutoff()) >= 1
    deleted = journal.delete_older_than(_old_cutoff())
    assert deleted >= 1


def test_cleanup_removes_old_receipts(tmp_path):
    paths = AppPaths.from_root(tmp_path / ".agpair")
    ensure_database(paths.db_path)
    old_time = (datetime.now(UTC) - timedelta(days=60)).isoformat().replace("+00:00", "Z")

    receipts = ReceiptRepository(paths.db_path)
    receipts.record("msg-1", "old-task", "ACK")
    with connect(paths.db_path) as conn:
        _make_old(conn, "receipts", old_time)

    assert receipts.count_older_than(_old_cutoff()) >= 1
    deleted = receipts.delete_older_than(_old_cutoff())
    assert deleted >= 1


def test_cleanup_removes_old_terminal_tasks(tmp_path):
    paths = AppPaths.from_root(tmp_path / ".agpair")
    ensure_database(paths.db_path)
    old_time = (datetime.now(UTC) - timedelta(days=60)).isoformat().replace("+00:00", "Z")

    tasks = TaskRepository(paths.db_path)
    tasks.create_task(task_id="old-task", repo_path="/tmp/test")
    tasks.mark_acked(task_id="old-task", session_id="sess-1")
    tasks.mark_stuck(task_id="old-task", reason="test")
    with connect(paths.db_path) as conn:
        _make_old(conn, "tasks", old_time)

    assert tasks.count_terminal_older_than(_old_cutoff()) >= 1
    deleted = tasks.delete_terminal_older_than(_old_cutoff())
    assert deleted >= 1


def test_cleanup_preserves_active_tasks(tmp_path):
    paths = AppPaths.from_root(tmp_path / ".agpair")
    ensure_database(paths.db_path)
    old_time = (datetime.now(UTC) - timedelta(days=60)).isoformat().replace("+00:00", "Z")

    tasks = TaskRepository(paths.db_path)
    tasks.create_task(task_id="active-task", repo_path="/tmp/test")
    tasks.mark_acked(task_id="active-task", session_id="sess-1")
    with connect(paths.db_path) as conn:
        _make_old(conn, "tasks", old_time)

    # acked is not terminal — should not be deleted
    deleted = tasks.delete_terminal_older_than(_old_cutoff())
    assert deleted == 0
    assert tasks.get_task("active-task") is not None


def test_cleanup_cleans_orphaned_waiters(tmp_path):
    paths = AppPaths.from_root(tmp_path / ".agpair")
    ensure_database(paths.db_path)
    old_time = (datetime.now(UTC) - timedelta(days=60)).isoformat().replace("+00:00", "Z")

    tasks = TaskRepository(paths.db_path)
    tasks.create_task(task_id="done-task", repo_path="/tmp/test")
    tasks.mark_acked(task_id="done-task", session_id="sess-1")
    tasks.mark_stuck(task_id="done-task", reason="test")

    from agpair.storage.waiters import WaiterRepository
    waiters = WaiterRepository(paths.db_path)
    waiters.start_waiter(task_id="done-task", command="wait")

    with connect(paths.db_path) as conn:
        _make_old(conn, "tasks", old_time)

    tasks.delete_terminal_older_than(_old_cutoff())

    # Waiter should also be gone
    with connect(paths.db_path) as conn:
        row = conn.execute("SELECT COUNT(*) FROM waiters WHERE task_id = 'done-task'").fetchone()
        assert row[0] == 0


def test_auto_cleanup(tmp_path):
    paths = AppPaths.from_root(tmp_path / ".agpair")
    ensure_database(paths.db_path)
    old_time = (datetime.now(UTC) - timedelta(days=60)).isoformat().replace("+00:00", "Z")

    journal = JournalRepository(paths.db_path)
    journal.append("old-task", "test", "old_event", "old")
    with connect(paths.db_path) as conn:
        _make_old(conn, "journal", old_time)

    from agpair.daemon.loop import auto_cleanup
    auto_cleanup(paths, retention_days=30)

    # Old journal should be gone, but auto_cleanup logs a new entry
    entries = journal.tail("daemon", limit=5)
    assert any(e.event == "auto_cleanup" for e in entries)


def test_cleanup_cli_dry_run(tmp_path):
    paths = AppPaths.from_root(tmp_path / ".agpair")
    ensure_database(paths.db_path)

    journal = JournalRepository(paths.db_path)
    journal.append("task-1", "test", "evt", "data")

    runner = CliRunner()
    result = runner.invoke(app, ["cleanup", "--dry-run"])
    assert result.exit_code == 0
    assert "would be deleted" in result.stdout


def test_cleanup_cli_help():
    runner = CliRunner()
    result = runner.invoke(app, ["cleanup", "--help"])
    assert result.exit_code == 0
    assert "--older-than" in result.stdout or "older" in result.stdout.lower()
