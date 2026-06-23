from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import sqlite3


SCHEMA_SQL = (Path(__file__).with_name("schema.sql")).read_text(encoding="utf-8")
DEFAULT_BUSY_TIMEOUT_MS = 5000


def _migrate_schema(conn: sqlite3.Connection) -> None:
    """Apply incremental schema migrations for existing databases."""
    task_cols = {row[1] for row in conn.execute("PRAGMA table_info(tasks)").fetchall()}
    # Migration 0: add execution_repo_path
    if "execution_repo_path" not in task_cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN execution_repo_path TEXT")
        conn.commit()
        task_cols.add("execution_repo_path")
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
    # Migration 12: add spotlight_testing
    if "spotlight_testing" not in task_cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN spotlight_testing INTEGER NOT NULL DEFAULT 0")
        conn.commit()
    if "broad_repo_path_override" not in task_cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN broad_repo_path_override INTEGER NOT NULL DEFAULT 0")
        conn.commit()
    # Migration 13: add completion_policy, terminal_source, is_approved
    if "completion_policy" not in task_cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN completion_policy TEXT NOT NULL DEFAULT 'direct_commit'")
        conn.commit()
    if "terminal_source" not in task_cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN terminal_source TEXT")
        conn.commit()
    if "is_approved" not in task_cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN is_approved INTEGER NOT NULL DEFAULT 0")
        conn.commit()
    # Migration 14: add execution_repo_path
    if "execution_repo_path" not in task_cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN execution_repo_path TEXT")
        conn.commit()
    # Migration 15: add authorization profile metadata
    task_cols = {row[1] for row in conn.execute("PRAGMA table_info(tasks)").fetchall()}
    if "authorization_profile" not in task_cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN authorization_profile TEXT NOT NULL DEFAULT 'local_mutating'")
        conn.commit()
    if "authorization_summary" not in task_cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN authorization_summary TEXT")
        conn.commit()
    # Migration 15b: task kind and controller wait budget defaults.
    task_cols = {row[1] for row in conn.execute("PRAGMA table_info(tasks)").fetchall()}
    task_wait_defaults = {
        "task_kind": "TEXT NOT NULL DEFAULT 'generic'",
        "wait_policy": "TEXT NOT NULL DEFAULT 'terminal'",
        "controller_wait_seconds": "REAL",
        "execution_budget_seconds": "REAL",
        "background_ok": "INTEGER NOT NULL DEFAULT 0",
    }
    for column, ddl in task_wait_defaults.items():
        if column not in task_cols:
            conn.execute(f"ALTER TABLE tasks ADD COLUMN {column} {ddl}")
    conn.commit()

    # Migration 16: V1.1 attempts/artifacts, normalized terminal receipt, workflow links.
    task_cols = {row[1] for row in conn.execute("PRAGMA table_info(tasks)").fetchall()}
    if "terminal_receipt_json" not in task_cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN terminal_receipt_json TEXT")
        conn.commit()
    for column in ("workflow_id", "workflow_node_id", "parent_task_id", "child_role"):
        if column not in task_cols:
            conn.execute(f"ALTER TABLE tasks ADD COLUMN {column} TEXT")
            conn.commit()
    task_cols = {row[1] for row in conn.execute("PRAGMA table_info(tasks)").fetchall()}
    if "coordination_role" not in task_cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN coordination_role TEXT")
        conn.commit()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS task_attempts (
          task_id TEXT NOT NULL,
          attempt_no INTEGER NOT NULL,
          executor_backend TEXT,
          authorization_profile TEXT NOT NULL DEFAULT 'local_mutating',
          requested_completion_policy TEXT NOT NULL DEFAULT 'auto',
          effective_policy_json TEXT,
          executor_session_id TEXT,
          phase TEXT NOT NULL DEFAULT 'new',
          terminal_receipt_json TEXT,
          terminal_source TEXT,
          started_at TEXT NOT NULL,
          finished_at TEXT,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          PRIMARY KEY (task_id, attempt_no)
        );
        CREATE TABLE IF NOT EXISTS task_artifacts (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          task_id TEXT NOT NULL,
          attempt_no INTEGER NOT NULL,
          artifact_type TEXT NOT NULL,
          path TEXT NOT NULL,
          size_bytes INTEGER,
          sha256 TEXT,
          created_at TEXT NOT NULL,
          UNIQUE(task_id, attempt_no, artifact_type)
        );
        CREATE TABLE IF NOT EXISTS workflows (
          workflow_id TEXT PRIMARY KEY,
          repo_path TEXT NOT NULL DEFAULT '',
          name TEXT NOT NULL DEFAULT '',
          controller TEXT NOT NULL DEFAULT 'generic',
          phase TEXT NOT NULL DEFAULT 'new',
          manifest_json TEXT NOT NULL,
          limits_json TEXT NOT NULL DEFAULT '{}',
          result_json TEXT,
          evidence_path TEXT,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          started_at TEXT,
          finished_at TEXT,
          cancelled_at TEXT,
          stuck_reason TEXT,
          error TEXT
        );
        CREATE TABLE IF NOT EXISTS workflow_nodes (
          workflow_id TEXT NOT NULL,
          node_id TEXT NOT NULL,
          kind TEXT NOT NULL,
          role TEXT,
          phase TEXT NOT NULL DEFAULT 'pending',
          depends_on TEXT,
          depends_on_json TEXT NOT NULL DEFAULT '[]',
          task_id TEXT,
          body TEXT,
          completion_policy TEXT NOT NULL DEFAULT 'auto',
          requested_completion_policy TEXT NOT NULL DEFAULT 'auto',
          effective_policy_json TEXT NOT NULL DEFAULT '{}',
          authorization_profile TEXT NOT NULL DEFAULT 'local_mutating',
          executor_backend TEXT,
          attempt_no INTEGER NOT NULL DEFAULT 0,
          max_retries INTEGER NOT NULL DEFAULT 0,
          allow_partial INTEGER NOT NULL DEFAULT 0,
          isolated_worktree INTEGER NOT NULL DEFAULT 0,
          evidence_json TEXT,
          result_json TEXT,
          error TEXT,
          last_error TEXT,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          started_at TEXT,
          finished_at TEXT,
          PRIMARY KEY (workflow_id, node_id)
        );
    """)
    conn.commit()
    attempt_cols = {row[1] for row in conn.execute("PRAGMA table_info(task_attempts)").fetchall()}
    attempt_defaults = {
        "environment_mode": "TEXT NOT NULL DEFAULT 'managed-natural'",
        "environment_mode_source": "TEXT NOT NULL DEFAULT 'executor_default'",
        "skill_policy": "TEXT NOT NULL DEFAULT 'inherit'",
        "mcp_policy": "TEXT NOT NULL DEFAULT 'inherit'",
        "protocol_warnings_json": "TEXT NOT NULL DEFAULT '[]'",
        "protocol_errors_json": "TEXT NOT NULL DEFAULT '[]'",
        "adoptable_result": "TEXT NOT NULL DEFAULT 'unknown'",
        "adoption_evidence_json": "TEXT NOT NULL DEFAULT '{}'",
        "controller_rework_json": "TEXT NOT NULL DEFAULT '{}'",
        "dirty_snapshot_mode": "TEXT NOT NULL DEFAULT 'off'",
        "dirty_snapshot_json": "TEXT NOT NULL DEFAULT '{}'",
        "dirty_snapshot_applied": "INTEGER NOT NULL DEFAULT 0",
    }
    for column, ddl in attempt_defaults.items():
        if column not in attempt_cols:
            conn.execute(f"ALTER TABLE task_attempts ADD COLUMN {column} {ddl}")
    conn.commit()
    attempt_cols = {row[1] for row in conn.execute("PRAGMA table_info(task_attempts)").fetchall()}
    attempt_wait_defaults = {
        "task_kind": "TEXT NOT NULL DEFAULT 'generic'",
        "wait_policy": "TEXT NOT NULL DEFAULT 'terminal'",
        "controller_wait_seconds": "REAL",
        "execution_budget_seconds": "REAL",
        "background_ok": "INTEGER NOT NULL DEFAULT 0",
    }
    for column, ddl in attempt_wait_defaults.items():
        if column not in attempt_cols:
            conn.execute(f"ALTER TABLE task_attempts ADD COLUMN {column} {ddl}")
    conn.commit()

    # Migration 17: add V2 workflow columns to databases that already had early workflow tables.
    tables = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    if "workflows" in tables:
        workflow_cols = {row[1] for row in conn.execute("PRAGMA table_info(workflows)").fetchall()}
        workflow_defaults = {
            "repo_path": "TEXT NOT NULL DEFAULT ''",
            "limits_json": "TEXT NOT NULL DEFAULT '{}'",
            "result_json": "TEXT",
            "stuck_reason": "TEXT",
        }
        for column, ddl in workflow_defaults.items():
            if column not in workflow_cols:
                conn.execute(f"ALTER TABLE workflows ADD COLUMN {column} {ddl}")
        conn.commit()
    if "workflow_nodes" in tables:
        node_cols = {row[1] for row in conn.execute("PRAGMA table_info(workflow_nodes)").fetchall()}
        node_defaults = {
            "depends_on_json": "TEXT NOT NULL DEFAULT '[]'",
            "requested_completion_policy": "TEXT NOT NULL DEFAULT 'auto'",
            "effective_policy_json": "TEXT NOT NULL DEFAULT '{}'",
            "attempt_no": "INTEGER NOT NULL DEFAULT 0",
            "max_retries": "INTEGER NOT NULL DEFAULT 0",
            "allow_partial": "INTEGER NOT NULL DEFAULT 0",
            "isolated_worktree": "INTEGER NOT NULL DEFAULT 0",
            "result_json": "TEXT",
            "last_error": "TEXT",
        }
        for column, ddl in node_defaults.items():
            if column not in node_cols:
                conn.execute(f"ALTER TABLE workflow_nodes ADD COLUMN {column} {ddl}")
        conn.execute("UPDATE workflow_nodes SET depends_on_json=COALESCE(depends_on_json, depends_on, '[]')")
        conn.execute("UPDATE workflow_nodes SET requested_completion_policy=COALESCE(requested_completion_policy, completion_policy, 'auto')")
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
