from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import sqlite3


SCHEMA_SQL = (Path(__file__).with_name("schema.sql")).read_text(encoding="utf-8")
DEFAULT_BUSY_TIMEOUT_MS = 5000


def _migrate_schema(conn: sqlite3.Connection) -> None:
    """Apply incremental schema migrations for existing databases."""
    task_cols = {row[1] for row in conn.execute("PRAGMA table_info(tasks)").fetchall()}
    # Migration 1: add last_heartbeat_at
    if "last_heartbeat_at" not in task_cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN last_heartbeat_at TEXT")
        conn.commit()
    # Migration 2: add last_workspace_activity_at
    if "last_workspace_activity_at" not in task_cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN last_workspace_activity_at TEXT")
        conn.commit()
    # Migration 3: add delivery_id column + unique partial index on receipts
    receipt_cols = {row[1] for row in conn.execute("PRAGMA table_info(receipts)").fetchall()}
    if "delivery_id" not in receipt_cols:
        conn.execute("ALTER TABLE receipts ADD COLUMN delivery_id TEXT")
        conn.commit()
    existing_indexes = {row[1] for row in conn.execute("PRAGMA index_list(receipts)").fetchall()}
    if "uq_receipts_task_delivery" not in existing_indexes:
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_receipts_task_delivery "
            "ON receipts (task_id, delivery_id) WHERE delivery_id IS NOT NULL"
        )
        conn.commit()
    # Migration 4: add classification column to journal
    journal_cols = {row[1] for row in conn.execute("PRAGMA table_info(journal)").fetchall()}
    if "classification" not in journal_cols:
        conn.execute("ALTER TABLE journal ADD COLUMN classification TEXT NOT NULL DEFAULT 'normal'")
        conn.commit()
    # Migration 5: add waiters table (persisted wait state)
    tables = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    if "waiters" not in tables:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS waiters (
              waiter_id TEXT PRIMARY KEY,
              task_id TEXT NOT NULL,
              command TEXT NOT NULL,
              state TEXT NOT NULL DEFAULT 'waiting',
              started_at TEXT NOT NULL,
              last_poll_at TEXT NOT NULL,
              finished_at TEXT,
              outcome TEXT
            );
            CREATE UNIQUE INDEX IF NOT EXISTS uq_waiters_active_task
              ON waiters (task_id) WHERE state = 'waiting';
        """)
        conn.commit()
    # Migration 6: add caller idempotency key on tasks
    task_cols = {row[1] for row in conn.execute("PRAGMA table_info(tasks)").fetchall()}
    if "client_idempotency_key" not in task_cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN client_idempotency_key TEXT")
        conn.commit()
    task_indexes = {row[1] for row in conn.execute("PRAGMA index_list(tasks)").fetchall()}
    if "uq_tasks_repo_idempotency" not in task_indexes:
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_tasks_repo_idempotency "
            "ON tasks (repo_path, client_idempotency_key) "
            "WHERE client_idempotency_key IS NOT NULL"
        )
        conn.commit()
    # Migration 7: add executor_backend
    if "executor_backend" not in task_cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN executor_backend TEXT")
        conn.commit()
    # Migration 8: add depends_on and isolated_worktree
    if "depends_on" not in task_cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN depends_on TEXT")
        conn.commit()
    if "isolated_worktree" not in task_cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN isolated_worktree INTEGER NOT NULL DEFAULT 0")
        conn.commit()
    # Migration 9: add setup_commands and teardown_commands
    if "setup_commands" not in task_cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN setup_commands TEXT")
        conn.commit()
    if "teardown_commands" not in task_cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN teardown_commands TEXT")
        conn.commit()
    # Migration 10: add env_vars
    if "env_vars" not in task_cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN env_vars TEXT")
        conn.commit()
    # Migration 11: add worktree_boundary
    if "worktree_boundary" not in task_cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN worktree_boundary TEXT")
        conn.commit()


def _configure_connection(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(f"PRAGMA busy_timeout={DEFAULT_BUSY_TIMEOUT_MS}")


def ensure_database(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.executescript(SCHEMA_SQL)
        conn.commit()
        _migrate_schema(conn)
        _configure_connection(conn)
        conn.commit()
    _initialized.add(db_path)


_initialized: set[Path] = set()


@contextmanager
def connect(db_path: Path):
    if db_path not in _initialized:
        ensure_database(db_path)
        _initialized.add(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    _configure_connection(conn)
    try:
        yield conn
    finally:
        conn.close()
