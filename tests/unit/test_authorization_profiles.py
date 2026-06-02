import sqlite3

import pytest

from agpair.models import (
    authorization_profile_summary,
    validate_authorization_profile,
)
from agpair.storage.db import ensure_database
from agpair.storage.tasks import TaskRepository


def test_known_authorization_profiles_are_accepted() -> None:
    assert validate_authorization_profile("local_readonly") == "local_readonly"
    assert validate_authorization_profile("local_mutating") == "local_mutating"
    assert validate_authorization_profile("local_test_heavy") == "local_test_heavy"
    assert validate_authorization_profile("external_network") == "external_network"


def test_unknown_authorization_profile_is_rejected() -> None:
    with pytest.raises(ValueError, match="authorization profile"):
        validate_authorization_profile("prod_root")


def test_authorization_summary_is_human_readable() -> None:
    summary = authorization_profile_summary("local_mutating")

    assert "allowed actions" in summary.lower()
    assert "denied actions" in summary.lower()


def test_task_repository_persists_authorization_profile(tmp_path) -> None:
    db_path = tmp_path / "agpair.db"
    ensure_database(db_path)
    repo = TaskRepository(db_path)

    repo.create_task(
        task_id="TASK-AUTH-1",
        repo_path="/tmp/repo",
        authorization_profile="local_readonly",
        authorization_summary="Allowed actions: inspect files. Denied actions: edit files.",
    )

    task = repo.get_task("TASK-AUTH-1")
    assert task is not None
    assert task.authorization_profile == "local_readonly"
    assert task.authorization_summary == "Allowed actions: inspect files. Denied actions: edit files."


def test_task_repository_defaults_legacy_rows_to_local_mutating(tmp_path) -> None:
    db_path = tmp_path / "legacy.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE tasks (
              task_id TEXT PRIMARY KEY,
              repo_path TEXT NOT NULL,
              phase TEXT NOT NULL,
              antigravity_session_id TEXT,
              attempt_no INTEGER NOT NULL DEFAULT 1,
              retry_count INTEGER NOT NULL DEFAULT 0,
              last_receipt_id TEXT,
              stuck_reason TEXT,
              retry_recommended INTEGER NOT NULL DEFAULT 0,
              last_activity_at TEXT NOT NULL,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE TABLE receipts (message_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE TABLE journal (id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL, source TEXT NOT NULL, event TEXT NOT NULL, body TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE TABLE daemon_health (name TEXT PRIMARY KEY, updated_at TEXT NOT NULL, body TEXT NOT NULL);
            """
        )
        conn.execute(
            "INSERT INTO tasks (task_id, repo_path, phase, attempt_no, retry_count, retry_recommended, last_activity_at, created_at, updated_at, antigravity_session_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("TASK-LEGACY-AUTH", "/tmp/repo", "acked", 1, 0, 0, "now", "now", "now", "legacy-session"),
        )
        conn.commit()

    ensure_database(db_path)
    task = TaskRepository(db_path).get_task("TASK-LEGACY-AUTH")

    assert task is not None
    assert task.authorization_profile == "local_mutating"
    assert task.authorization_summary is None
    assert task.executor_session_id == "legacy-session"
