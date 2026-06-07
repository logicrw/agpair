CREATE TABLE IF NOT EXISTS tasks (
  task_id TEXT PRIMARY KEY,
  repo_path TEXT NOT NULL,
  execution_repo_path TEXT,
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
  client_idempotency_key TEXT,
  executor_backend TEXT,
  depends_on TEXT,
  isolated_worktree INTEGER NOT NULL DEFAULT 0,
  setup_commands TEXT,
  teardown_commands TEXT,
  env_vars TEXT,
  worktree_boundary TEXT,
  spotlight_testing INTEGER NOT NULL DEFAULT 0,
  broad_repo_path_override INTEGER NOT NULL DEFAULT 0,
  completion_policy TEXT NOT NULL DEFAULT 'auto',
  terminal_source TEXT,
  terminal_receipt_json TEXT,
  is_approved INTEGER NOT NULL DEFAULT 0,
  authorization_profile TEXT NOT NULL DEFAULT 'local_mutating',
  authorization_summary TEXT,
  workflow_id TEXT,
  workflow_node_id TEXT,
  parent_task_id TEXT,
  child_role TEXT
);
-- NOTE: uq_tasks_repo_idempotency index on (repo_path, client_idempotency_key)
-- is created by _migrate_schema() in db.py to support both fresh and migrated databases.

CREATE TABLE IF NOT EXISTS task_attempts (
  task_id TEXT NOT NULL,
  attempt_no INTEGER NOT NULL,
  executor_backend TEXT,
  authorization_profile TEXT NOT NULL DEFAULT 'local_mutating',
  requested_completion_policy TEXT NOT NULL DEFAULT 'auto',
  effective_policy_json TEXT,
  environment_mode TEXT NOT NULL DEFAULT 'managed-natural',
  environment_mode_source TEXT NOT NULL DEFAULT 'executor_default',
  skill_policy TEXT NOT NULL DEFAULT 'inherit',
  mcp_policy TEXT NOT NULL DEFAULT 'inherit',
  fallback_environment_mode TEXT,
  fallback_reason TEXT,
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
