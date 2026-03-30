CREATE TABLE IF NOT EXISTS tasks (
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
  updated_at TEXT NOT NULL,
  last_heartbeat_at TEXT,
  last_workspace_activity_at TEXT,
  client_idempotency_key TEXT
);
-- NOTE: uq_tasks_repo_idempotency index on (repo_path, client_idempotency_key)
-- is created by _migrate_schema() in db.py to support both fresh and migrated databases.

CREATE TABLE IF NOT EXISTS receipts (
  message_id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL,
  status TEXT NOT NULL,
  delivery_id TEXT,
  created_at TEXT NOT NULL
);

-- NOTE: uq_receipts_task_delivery index on (task_id, delivery_id) is created
-- by _migrate_schema() in db.py to support both fresh and migrated databases.

CREATE TABLE IF NOT EXISTS journal (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id TEXT NOT NULL,
  source TEXT NOT NULL,
  event TEXT NOT NULL,
  body TEXT NOT NULL,
  classification TEXT NOT NULL DEFAULT 'normal',
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS daemon_health (
  name TEXT PRIMARY KEY,
  updated_at TEXT NOT NULL,
  body TEXT NOT NULL
);

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
