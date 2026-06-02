import json
import pathlib
from datetime import UTC, datetime
from unittest import mock

from typer.testing import CliRunner

from agpair.cli.task import app
from agpair.config import AppPaths
from agpair.daemon.loop import run_once
from agpair.storage.db import ensure_database
from agpair.storage.tasks import TaskRepository

VALID_BRIEF = "Goal: test\nScope: test\nRequired changes: test\nExit criteria: test"


def test_legacy_gemini_cli_row_can_be_inspected(tmp_path: pathlib.Path, monkeypatch) -> None:
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    paths = AppPaths.default()
    ensure_database(paths.db_path)
    tasks = TaskRepository(paths.db_path)
    tasks.create_task(
        task_id="TASK-GEMINI-LEGACY",
        repo_path=str(tmp_path),
        executor_backend="gemini_cli",
    )
    tasks.mark_acked(task_id="TASK-GEMINI-LEGACY", session_id="/tmp/legacy-gemini-session")

    result = CliRunner().invoke(app, ["status", "TASK-GEMINI-LEGACY", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["active_executor_backend"] == "gemini_cli"
    assert payload["active_executor_continuation_capability"] == "unsupported"
    assert "gemini" not in payload["supported_backends"]
    assert "gemini_cli" not in payload["supported_backends"]
    assert payload["session_id"] == "/tmp/legacy-gemini-session"


def test_new_gemini_cli_start_is_rejected(tmp_path: pathlib.Path, monkeypatch) -> None:
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    runner = CliRunner()

    for executor_id in ("gemini", "gemini_cli"):
        result = runner.invoke(
            app,
            [
                "start",
                "--repo-path",
                str(tmp_path),
                "--task-id",
                f"TASK-{executor_id.upper().replace('_', '-')}",
                "--executor",
                executor_id,
                "--body",
                VALID_BRIEF,
                "--no-wait",
            ],
        )

        assert result.exit_code != 0
        assert "gemini is no longer supported" in result.output


def test_legacy_gemini_cli_is_not_polled_by_daemon(tmp_path: pathlib.Path, monkeypatch) -> None:
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    paths = AppPaths.default()
    ensure_database(paths.db_path)
    tasks = TaskRepository(paths.db_path)
    session_dir = tmp_path / "legacy-gemini-session"
    session_dir.mkdir()
    (session_dir / "rc.txt").write_text("0", encoding="utf-8")
    tasks.create_task(
        task_id="TASK-GEMINI-NO-POLL",
        repo_path=str(tmp_path),
        executor_backend="gemini_cli",
    )
    tasks.mark_acked(task_id="TASK-GEMINI-NO-POLL", session_id=str(session_dir))
    bus = mock.MagicMock()
    bus.reserve_receipts.return_value = []

    run_once(paths, now=datetime.now(UTC), bus=bus)

    task = tasks.get_task("TASK-GEMINI-NO-POLL")
    assert task is not None
    assert task.phase == "acked"
    assert task.antigravity_session_id == str(session_dir)
    bus.reserve_receipts.assert_called_once_with(task_id="TASK-GEMINI-NO-POLL")
