"""Test the cleanup command."""
from datetime import UTC, datetime, timedelta

from typer.testing import CliRunner

from agpair.cli.app import app
from agpair.config import AppPaths
from agpair.storage.db import connect, ensure_database
from agpair.storage.journal import JournalRepository
from agpair.storage.receipts import ReceiptRepository
from agpair.storage.tasks import TaskRepository


def test_cleanup_removes_old_data(tmp_path):
    paths = AppPaths.from_root(tmp_path / ".agpair")
    ensure_database(paths.db_path)

    old_time = (datetime.now(UTC) - timedelta(days=60)).isoformat().replace("+00:00", "Z")

    journal = JournalRepository(paths.db_path)
    journal.append("old-task", "test", "test_event", "old data")

    # Manually update created_at to be old
    with connect(paths.db_path) as conn:
        conn.execute("UPDATE journal SET created_at = ?", (old_time,))
        conn.commit()

    deleted = journal.delete_older_than(
        (datetime.now(UTC) - timedelta(days=30)).isoformat().replace("+00:00", "Z")
    )
    assert deleted >= 1


def test_cleanup_cli_help():
    runner = CliRunner()
    result = runner.invoke(app, ["cleanup", "--help"])
    assert result.exit_code == 0
    assert "--older-than" in result.stdout or "older" in result.stdout.lower()
