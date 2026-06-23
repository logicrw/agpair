from __future__ import annotations

from pathlib import Path

from agpair.artifacts import sha256_file
from agpair.completion import normalize_completion_policy
from agpair.executors.policy import resolve_environment_metadata
from agpair.models import (
    TaskArtifactRecord,
    TaskAttemptRecord,
    TaskRecord,
    authorization_profile_summary as default_authorization_summary,
    utcnow_iso,
    validate_authorization_profile,
)
from agpair.roles import normalize_coordination_role
from agpair.storage.db import connect
from agpair.wait_policy import WaitBudget, normalize_task_kind, normalize_wait_policy, resolve_wait_budget


class TaskNotFoundError(RuntimeError):
    """Raised when a requested task does not exist."""


class IllegalTransitionError(RuntimeError):
    """Raised when a phase transition is not allowed."""


_VALID_TRANSITIONS: dict[str, set[str] | None] = {
    "acked": {"new", "ready_for_review", "evidence_ready", "blocked", "committed", "stuck", "abandoned"},
    "ready_for_review": {"acked", "evidence_ready", "committed"},
    "evidence_ready": {"acked"},
    "blocked": {"acked", "new", "ready_for_review", "evidence_ready", "committed"},
    "committed": {"acked", "evidence_ready", "ready_for_review"},
    "stuck": {"acked"},
    "abandoned": None,
    "new": None,
}


def _resolve_attempt_environment_metadata(
    executor_backend: str | None,
    *,
    environment_mode: str | None,
    environment_mode_source: str | None,
):
    try:
        return resolve_environment_metadata(
            executor_backend,
            environment_mode=environment_mode,
            source=environment_mode_source,
        )
    except ValueError:
        return resolve_environment_metadata(
            "antigravity-cli",
            environment_mode=None,
            source=environment_mode_source or "executor_default",
        )


def _preserved_wait_budget(task: TaskRecord) -> WaitBudget:
    return WaitBudget(
        task_kind=normalize_task_kind(task.task_kind),
        wait_policy=normalize_wait_policy(task.wait_policy, task_kind=task.task_kind),
        controller_wait_seconds=task.controller_wait_seconds,
        execution_budget_seconds=task.execution_budget_seconds,
        background_ok=task.background_ok,
    )


def _check_transition(task: TaskRecord, target_phase: str) -> None:
    valid_sources = _VALID_TRANSITIONS.get(target_phase)
    if valid_sources is not None and task.phase not in valid_sources:
        raise IllegalTransitionError(
            f"cannot transition {task.task_id!r} from {task.phase!r} to {target_phase!r}"
        )


class TaskRepository:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def create_task(
        self,
        *,
        task_id: str,
        repo_path: str,
        client_idempotency_key: str | None = None,
        executor_backend: str | None = None,
        depends_on: str | None = None,
        isolated_worktree: bool = False,
        setup_commands: str | None = None,
        teardown_commands: str | None = None,
        env_vars: str | None = None,
        worktree_boundary: str | None = None,
        spotlight_testing: bool = False,
        broad_repo_path_override: bool = False,
        authorization_profile: str = "local_mutating",
        authorization_summary: str | None = None,
        completion_policy: str = "auto",
        task_kind: str | None = None,
        wait_policy: str | None = None,
        controller_wait_seconds: float | None = None,
        execution_budget_seconds: float | None = None,
        background_ok: bool | None = None,
        effective_policy_json: str | None = None,
        environment_mode: str | None = None,
        environment_mode_source: str | None = None,
        skill_policy: str | None = None,
        mcp_policy: str | None = None,
        dirty_snapshot_mode: str = "off",
        workflow_id: str | None = None,
        workflow_node_id: str | None = None,
        parent_task_id: str | None = None,
        child_role: str | None = None,
        coordination_role: str | None = None,
    ) -> None:
        now = utcnow_iso()
        normalized_authorization_profile = validate_authorization_profile(authorization_profile)
        normalized_completion_policy = normalize_completion_policy(completion_policy)
        normalized_coordination_role = normalize_coordination_role(coordination_role)
        normalized_authorization_summary = authorization_summary or default_authorization_summary(
            normalized_authorization_profile
        )
        wait_budget = resolve_wait_budget(
            task_kind=task_kind,
            wait_policy=wait_policy,
            controller_wait_seconds=controller_wait_seconds,
            execution_budget_seconds=execution_budget_seconds,
            background_ok=background_ok,
        )
        environment = _resolve_attempt_environment_metadata(
            executor_backend,
            environment_mode=environment_mode,
            environment_mode_source=environment_mode_source,
        )
        selected_skill_policy = skill_policy or environment.skill_policy
        selected_mcp_policy = mcp_policy or environment.mcp_policy
        with connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO tasks (
                  task_id, repo_path, execution_repo_path, phase, antigravity_session_id, attempt_no, retry_count,
                  last_receipt_id, stuck_reason, retry_recommended, last_activity_at, created_at, updated_at,
                  last_heartbeat_at, last_workspace_activity_at, client_idempotency_key, executor_backend,
                  depends_on, isolated_worktree, setup_commands, teardown_commands, env_vars, worktree_boundary,
                  spotlight_testing, broad_repo_path_override, completion_policy, terminal_source, terminal_receipt_json, is_approved,
                  authorization_profile, authorization_summary,
                  task_kind, wait_policy, controller_wait_seconds, execution_budget_seconds, background_ok,
                  workflow_id, workflow_node_id, parent_task_id, child_role, coordination_role
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    repo_path,
                    None,
                    "new",
                    None,
                    1,
                    0,
                    None,
                    None,
                    0,
                    now,
                    now,
                    now,
                    None,
                    None,
                    client_idempotency_key,
                    executor_backend,
                    depends_on,
                    1 if isolated_worktree else 0,
                    setup_commands,
                    teardown_commands,
                    env_vars,
                    worktree_boundary,
                    1 if spotlight_testing else 0,
                    1 if broad_repo_path_override else 0,
                    normalized_completion_policy,
                    None,
                    None,
                    0,
                    normalized_authorization_profile,
                    normalized_authorization_summary,
                    wait_budget.task_kind,
                    wait_budget.wait_policy,
                    wait_budget.controller_wait_seconds,
                    wait_budget.execution_budget_seconds,
                    1 if wait_budget.background_ok else 0,
                    workflow_id,
                    workflow_node_id,
                    parent_task_id,
                    child_role,
                    normalized_coordination_role,
                ),
            )
            conn.execute(
                """
                INSERT INTO task_attempts (
                  task_id, attempt_no, executor_backend, authorization_profile,
                  requested_completion_policy, effective_policy_json,
                  task_kind, wait_policy, controller_wait_seconds, execution_budget_seconds, background_ok,
                  environment_mode, environment_mode_source, skill_policy, mcp_policy,
                  protocol_warnings_json, protocol_errors_json, adoptable_result,
                  adoption_evidence_json, controller_rework_json,
                  dirty_snapshot_mode, dirty_snapshot_json, dirty_snapshot_applied,
                  executor_session_id, phase, terminal_receipt_json, terminal_source, started_at, finished_at,
                  created_at, updated_at
                ) VALUES (?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '[]', '[]', 'unknown', '{}', '{}', ?, '{}', 0, NULL, 'new', NULL, NULL, ?, NULL, ?, ?)
                """,
                (
                    task_id,
                    executor_backend,
                    normalized_authorization_profile,
                    normalized_completion_policy,
                    effective_policy_json,
                    wait_budget.task_kind,
                    wait_budget.wait_policy,
                    wait_budget.controller_wait_seconds,
                    wait_budget.execution_budget_seconds,
                    1 if wait_budget.background_ok else 0,
                    environment.environment_mode,
                    environment.environment_mode_source,
                    selected_skill_policy,
                    selected_mcp_policy,
                    dirty_snapshot_mode,
                    now,
                    now,
                    now,
                ),
            )
            conn.commit()

    def get_task_by_idempotency_key(self, *, repo_path: str, client_idempotency_key: str) -> TaskRecord | None:
        with connect(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT * FROM tasks
                WHERE repo_path = ? AND client_idempotency_key = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (repo_path, client_idempotency_key),
            ).fetchone()
        if row is None:
            return None
        return self._task_from_row(row)

    def mark_acked(self, *, task_id: str, session_id: str) -> None:
        task = self.get_task(task_id)
        if task is None:
            raise TaskNotFoundError(f"task not found: {task_id}")
        _check_transition(task, "acked")
        now = utcnow_iso()
        with connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                UPDATE tasks
                SET phase='acked', antigravity_session_id=?,
                    stuck_reason=NULL, retry_recommended=0,
                    last_receipt_id=NULL, last_heartbeat_at=NULL,
                    last_workspace_activity_at=NULL,
                    last_activity_at=?, updated_at=?
                WHERE task_id=?
                """,
                (session_id, now, now, task_id),
            )
            if cursor.rowcount == 0:
                raise TaskNotFoundError(f"task not found: {task_id}")
            conn.execute(
                """
                UPDATE task_attempts
                SET phase='acked', executor_session_id=?, started_at=?, updated_at=?
                WHERE task_id=? AND attempt_no=?
                """,
                (session_id, now, now, task_id, task.attempt_no),
            )
            conn.commit()

    def mark_evidence_ready(self, *, task_id: str, last_receipt_id: str | None = None) -> None:
        # Legacy writer retained for old integrations. New terminal success should use mark_ready_for_review().
        task = self.get_task(task_id)
        if task is None:
            raise TaskNotFoundError(f"task not found: {task_id}")
        _check_transition(task, "evidence_ready")
        now = utcnow_iso()
        self._update(
            task_id,
            """
            UPDATE tasks
            SET phase='evidence_ready', last_receipt_id=?, last_activity_at=?, updated_at=?
            WHERE task_id=?
            """,
            (last_receipt_id, now, now, task_id),
        )

    def mark_ready_for_review(
        self,
        *,
        task_id: str,
        last_receipt_id: str | None = None,
        terminal_source: str | None = None,
        terminal_receipt_json: str | None = None,
    ) -> None:
        task = self.get_task(task_id)
        if task is None:
            raise TaskNotFoundError(f"task not found: {task_id}")
        _check_transition(task, "ready_for_review")
        now = utcnow_iso()
        self._update(
            task_id,
            """
            UPDATE tasks
            SET phase='ready_for_review', last_receipt_id=?, terminal_source=?, terminal_receipt_json=?,
                last_activity_at=?, updated_at=?
            WHERE task_id=?
            """,
            (last_receipt_id, terminal_source, terminal_receipt_json, now, now, task_id),
        )

    def mark_blocked(
        self,
        *,
        task_id: str,
        reason: str | None = None,
        last_receipt_id: str | None = None,
        terminal_source: str | None = None,
        terminal_receipt_json: str | None = None,
    ) -> None:
        task = self.get_task(task_id)
        if task is None:
            raise TaskNotFoundError(f"task not found: {task_id}")
        _check_transition(task, "blocked")
        now = utcnow_iso()
        self._update(
            task_id,
            """
            UPDATE tasks
            SET phase='blocked', stuck_reason=?, last_receipt_id=COALESCE(?, last_receipt_id),
                terminal_source=COALESCE(?, terminal_source), terminal_receipt_json=COALESCE(?, terminal_receipt_json),
                last_activity_at=?, updated_at=?
            WHERE task_id=?
            """,
            (reason, last_receipt_id, terminal_source, terminal_receipt_json, now, now, task_id),
        )
        self.record_attempt_terminal(
            task_id=task_id,
            attempt_no=task.attempt_no,
            phase="blocked",
            terminal_receipt_json=terminal_receipt_json,
            terminal_source=terminal_source or "cli",
        )

    def mark_committed(self, *, task_id: str, last_receipt_id: str | None = None, terminal_source: str | None = None) -> None:
        # Legacy writer retained for readable historical phase values.
        task = self.get_task(task_id)
        if task is None:
            raise TaskNotFoundError(f"task not found: {task_id}")
        _check_transition(task, "committed")
        now = utcnow_iso()
        self._update(
            task_id,
            """
            UPDATE tasks
            SET phase='committed', last_receipt_id=?, terminal_source=?, last_activity_at=?, updated_at=?
            WHERE task_id=?
            """,
            (last_receipt_id, terminal_source, now, now, task_id),
        )

    def mark_approved(self, *, task_id: str) -> None:
        task = self.get_task(task_id)
        if task is None:
            raise TaskNotFoundError(f"task not found: {task_id}")
        now = utcnow_iso()
        self._update(
            task_id,
            """
            UPDATE tasks
            SET is_approved=1, last_activity_at=?, updated_at=?
            WHERE task_id=?
            """,
            (now, now, task_id),
        )

    def mark_stuck(self, *, task_id: str, reason: str) -> None:
        task = self.get_task(task_id)
        if task is None:
            raise TaskNotFoundError(f"task not found: {task_id}")
        _check_transition(task, "stuck")
        now = utcnow_iso()
        self._update(
            task_id,
            """
            UPDATE tasks
            SET phase='stuck', stuck_reason=?, last_activity_at=?, updated_at=?
            WHERE task_id=?
            """,
            (reason, now, now, task_id),
        )
        self.record_attempt_terminal(task_id=task_id, attempt_no=task.attempt_no, phase="stuck", terminal_receipt_json=None, terminal_source="timeout")

    def mark_abandoned(self, *, task_id: str, reason: str) -> None:
        task = self.get_task(task_id)
        if task is None:
            raise TaskNotFoundError(f"task not found: {task_id}")
        _check_transition(task, "abandoned")
        now = utcnow_iso()
        self._update(
            task_id,
            """
            UPDATE tasks
            SET phase='abandoned', stuck_reason=?, last_activity_at=?, updated_at=?
            WHERE task_id=?
            """,
            (reason, now, now, task_id),
        )
        self.record_attempt_terminal(task_id=task_id, attempt_no=task.attempt_no, phase="abandoned", terminal_receipt_json=None, terminal_source="cli")

    def delete_terminal_older_than(self, cutoff_iso: str) -> int:
        from agpair.models import TERMINAL_PHASES

        with connect(self.db_path) as conn:
            placeholders = ",".join("?" for _ in TERMINAL_PHASES)
            conn.execute(
                f"DELETE FROM waiters WHERE task_id IN "
                f"(SELECT task_id FROM tasks WHERE phase IN ({placeholders}) AND created_at < ?)",
                (*TERMINAL_PHASES, cutoff_iso),
            )
            cursor = conn.execute(
                f"DELETE FROM tasks WHERE phase IN ({placeholders}) AND created_at < ?",
                (*TERMINAL_PHASES, cutoff_iso),
            )
            conn.commit()
            return cursor.rowcount

    def count_terminal_older_than(self, cutoff_iso: str) -> int:
        from agpair.models import TERMINAL_PHASES

        with connect(self.db_path) as conn:
            placeholders = ",".join("?" for _ in TERMINAL_PHASES)
            row = conn.execute(
                f"SELECT COUNT(*) FROM tasks WHERE phase IN ({placeholders}) AND created_at < ?",
                (*TERMINAL_PHASES, cutoff_iso),
            ).fetchone()
            return row[0]

    def record_heartbeat(self, *, task_id: str, heartbeat_at: str | None = None) -> None:
        now = heartbeat_at or utcnow_iso()
        self._update(
            task_id,
            """
            UPDATE tasks
            SET last_heartbeat_at=?, updated_at=?
            WHERE task_id=?
            """,
            (now, now, task_id),
        )

    def update_workspace_activity(self, *, task_id: str, activity_at: str) -> None:
        self._update(
            task_id,
            """
            UPDATE tasks
            SET last_workspace_activity_at=?, updated_at=?
            WHERE task_id=?
            """,
            (activity_at, utcnow_iso(), task_id),
        )

    def clear_session_id(self, *, task_id: str) -> None:
        self._update(
            task_id,
            """
            UPDATE tasks
            SET antigravity_session_id=NULL, updated_at=?
            WHERE task_id=?
            """,
            (utcnow_iso(), task_id),
        )

    def set_execution_repo_path(self, *, task_id: str, execution_repo_path: str | None) -> None:
        self._update(
            task_id,
            """
            UPDATE tasks
            SET execution_repo_path=?, updated_at=?
            WHERE task_id=?
            """,
            (execution_repo_path, utcnow_iso(), task_id),
        )

    def recommend_retry(self, *, task_id: str, retry_count: int | None = None) -> None:
        now = utcnow_iso()
        if retry_count is None:
            sql = """
            UPDATE tasks
            SET retry_recommended=1, updated_at=?
            WHERE task_id=?
            """
            params = (now, task_id)
        else:
            sql = """
            UPDATE tasks
            SET retry_recommended=1, retry_count=?, updated_at=?
            WHERE task_id=?
            """
            params = (retry_count, now, task_id)
        self._update(task_id, sql, params)

    def prepare_retry(self, *, task_id: str) -> TaskRecord:
        task = self.get_task(task_id)
        if task is None:
            raise TaskNotFoundError(f"task not found: {task_id}")
        return task

    def apply_retry_dispatch(
        self,
        *,
        task_id: str,
        executor_backend: str | None = None,
        authorization_profile: str | None = None,
        authorization_summary: str | None = None,
        completion_policy: str | None = None,
        task_kind: str | None = None,
        wait_policy: str | None = None,
        controller_wait_seconds: float | None = None,
        execution_budget_seconds: float | None = None,
        background_ok: bool | None = None,
        effective_policy_json: str | None = None,
        environment_mode: str | None = None,
        environment_mode_source: str | None = None,
        skill_policy: str | None = None,
        mcp_policy: str | None = None,
        dirty_snapshot_mode: str = "off",
    ) -> TaskRecord:
        task = self.get_task(task_id)
        if task is None:
            raise TaskNotFoundError(f"task not found: {task_id}")
        now = utcnow_iso()
        next_attempt = task.attempt_no + 1
        next_retry_count = task.retry_count + 1
        next_executor_backend = executor_backend if executor_backend is not None else task.executor_backend
        next_authorization_profile = validate_authorization_profile(
            authorization_profile or task.authorization_profile
        )
        next_authorization_summary = authorization_summary or default_authorization_summary(
            next_authorization_profile
        )
        next_completion_policy = normalize_completion_policy(completion_policy or task.completion_policy)
        if any(
            value is not None
            for value in (
                task_kind,
                wait_policy,
                controller_wait_seconds,
                execution_budget_seconds,
                background_ok,
            )
        ):
            next_wait_budget = resolve_wait_budget(
                task_kind=task_kind or task.task_kind,
                wait_policy=wait_policy or task.wait_policy,
                controller_wait_seconds=(
                    controller_wait_seconds
                    if controller_wait_seconds is not None
                    else task.controller_wait_seconds
                ),
                execution_budget_seconds=(
                    execution_budget_seconds
                    if execution_budget_seconds is not None
                    else task.execution_budget_seconds
                ),
                background_ok=background_ok if background_ok is not None else task.background_ok,
            )
        else:
            next_wait_budget = _preserved_wait_budget(task)
        environment = _resolve_attempt_environment_metadata(
            next_executor_backend,
            environment_mode=environment_mode,
            environment_mode_source=environment_mode_source,
        )
        selected_skill_policy = skill_policy or environment.skill_policy
        selected_mcp_policy = mcp_policy or environment.mcp_policy
        with connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                UPDATE tasks
                SET phase='new',
                    antigravity_session_id=NULL,
                    execution_repo_path=NULL,
                    attempt_no=?,
                    retry_count=?,
                    last_receipt_id=NULL,
                    stuck_reason=NULL,
                    retry_recommended=0,
                    last_activity_at=?,
                    updated_at=?,
                    last_heartbeat_at=NULL,
                    last_workspace_activity_at=NULL,
                    is_approved=0,
                    terminal_source=NULL,
                    terminal_receipt_json=NULL,
                    executor_backend=?,
                    completion_policy=?,
                    authorization_profile=?,
                    authorization_summary=?,
                    task_kind=?,
                    wait_policy=?,
                    controller_wait_seconds=?,
                    execution_budget_seconds=?,
                    background_ok=?
                WHERE task_id=?
                """,
                (
                    next_attempt,
                    next_retry_count,
                    now,
                    now,
                    next_executor_backend,
                    next_completion_policy,
                    next_authorization_profile,
                    next_authorization_summary,
                    next_wait_budget.task_kind,
                    next_wait_budget.wait_policy,
                    next_wait_budget.controller_wait_seconds,
                    next_wait_budget.execution_budget_seconds,
                    1 if next_wait_budget.background_ok else 0,
                    task_id,
                ),
            )
            if cursor.rowcount == 0:
                raise TaskNotFoundError(f"task not found: {task_id}")
            conn.execute(
                """
                INSERT INTO task_attempts (
                  task_id, attempt_no, executor_backend, authorization_profile,
                  requested_completion_policy, effective_policy_json,
                  task_kind, wait_policy, controller_wait_seconds, execution_budget_seconds, background_ok,
                  environment_mode, environment_mode_source, skill_policy, mcp_policy,
                  protocol_warnings_json, protocol_errors_json, adoptable_result,
                  adoption_evidence_json, controller_rework_json,
                  dirty_snapshot_mode, dirty_snapshot_json, dirty_snapshot_applied,
                  executor_session_id, phase, terminal_receipt_json, terminal_source, started_at, finished_at,
                  created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '[]', '[]', 'unknown', '{}', '{}', ?, '{}', 0, NULL, 'new', NULL, NULL, ?, NULL, ?, ?)
                """,
                (
                    task_id,
                    next_attempt,
                    next_executor_backend,
                    next_authorization_profile,
                    next_completion_policy,
                    effective_policy_json,
                    next_wait_budget.task_kind,
                    next_wait_budget.wait_policy,
                    next_wait_budget.controller_wait_seconds,
                    next_wait_budget.execution_budget_seconds,
                    1 if next_wait_budget.background_ok else 0,
                    environment.environment_mode,
                    environment.environment_mode_source,
                    selected_skill_policy,
                    selected_mcp_policy,
                    dirty_snapshot_mode,
                    now,
                    now,
                    now,
                ),
            )
            conn.commit()
        updated = self.get_task(task_id)
        assert updated is not None
        return updated

    def get_most_relevant_active_task(self, repo_path: str) -> TaskRecord | None:
        with connect(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT * FROM tasks
                WHERE repo_path = ?
                  AND is_approved = 0
                ORDER BY
                  CASE
                    WHEN phase IN ('new', 'acked', 'ready_for_review', 'evidence_ready') THEN 0
                    WHEN phase IN ('blocked', 'stuck') THEN 1
                    ELSE 2
                  END ASC,
                  updated_at DESC
                LIMIT 1
                """,
                (repo_path,),
            ).fetchone()
        if row is None:
            return None
        return self._task_from_row(row)

    def get_task(self, task_id: str) -> TaskRecord | None:
        with connect(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT tasks.*, task_attempts.executor_session_id AS attempt_executor_session_id
                FROM tasks
                LEFT JOIN task_attempts
                  ON task_attempts.task_id = tasks.task_id
                 AND task_attempts.attempt_no = tasks.attempt_no
                WHERE tasks.task_id = ?
                """,
                (task_id,),
            ).fetchone()
        if row is None:
            return None
        return self._task_from_row(row)

    def list_stale_acked_tasks(self, cutoff_iso: str) -> list[TaskRecord]:
        return self._query_tasks(
            """
            SELECT * FROM tasks
            WHERE phase='acked'
              AND last_activity_at < ?
              AND (last_heartbeat_at IS NULL OR last_heartbeat_at < ?)
              AND (last_workspace_activity_at IS NULL OR last_workspace_activity_at < ?)
            ORDER BY last_activity_at ASC
            """,
            (cutoff_iso, cutoff_iso, cutoff_iso),
        )

    def list_watchdog_candidates(self, *, watchdog_cutoff_iso: str, hard_timeout_cutoff_iso: str) -> list[TaskRecord]:
        return self._query_tasks(
            """
            SELECT * FROM tasks
            WHERE phase='acked'
              AND retry_recommended=0
              AND last_activity_at < ?
              AND last_activity_at >= ?
              AND (last_heartbeat_at IS NULL OR last_heartbeat_at < ?)
              AND (last_workspace_activity_at IS NULL OR last_workspace_activity_at < ?)
            ORDER BY last_activity_at ASC
            """,
            (watchdog_cutoff_iso, hard_timeout_cutoff_iso, watchdog_cutoff_iso, watchdog_cutoff_iso),
        )

    def list_execution_budget_expired_candidates(self, now_iso: str) -> list[TaskRecord]:
        return self._query_tasks(
            """
            SELECT * FROM tasks
            WHERE phase='acked'
              AND execution_budget_seconds IS NOT NULL
              AND datetime(created_at, '+' || execution_budget_seconds || ' seconds') <= datetime(?)
            ORDER BY created_at ASC
            """,
            (now_iso,),
        )

    def list_tasks(
        self,
        *,
        phase: str | None = None,
        repo_path: str | None = None,
        workflow_id: str | None = None,
        parent_task_id: str | None = None,
        limit: int = 20,
    ) -> list[TaskRecord]:
        sql = "SELECT * FROM tasks"
        where_clauses: list[str] = []
        params: list[object] = []
        if phase is not None:
            where_clauses.append("phase = ?")
            params.append(phase)
        if repo_path is not None:
            where_clauses.append("repo_path = ?")
            params.append(repo_path)
        if workflow_id is not None:
            where_clauses.append("workflow_id = ?")
            params.append(workflow_id)
        if parent_task_id is not None:
            where_clauses.append("parent_task_id = ?")
            params.append(parent_task_id)
        if where_clauses:
            sql += " WHERE " + " AND ".join(where_clauses)
        sql += " ORDER BY updated_at DESC, task_id DESC LIMIT ?"
        params.append(limit)
        with connect(self.db_path) as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
        return [self._task_from_row(row) for row in rows]

    def list_local_cli_cleanup_candidates(self, *, limit: int = 100) -> list[TaskRecord]:
        terminal_phases = ("ready_for_review", "evidence_ready", "committed", "blocked", "stuck", "abandoned")
        placeholders = ",".join("?" for _ in terminal_phases)
        return self._query_tasks(
            f"""
            SELECT * FROM tasks
            WHERE phase IN ({placeholders})
              AND antigravity_session_id IS NOT NULL
            ORDER BY updated_at DESC, task_id DESC
            LIMIT ?
            """,
            (*terminal_phases, limit),
        )

    def current_attempt(self, task_id: str) -> TaskAttemptRecord | None:
        with connect(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT * FROM task_attempts
                WHERE task_id=?
                ORDER BY attempt_no DESC
                LIMIT 1
                """,
                (task_id,),
            ).fetchone()
        if row is None:
            return None
        return self._attempt_from_row(row)

    def list_attempts(self, task_id: str) -> list[TaskAttemptRecord]:
        with connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM task_attempts WHERE task_id=? ORDER BY attempt_no ASC",
                (task_id,),
            ).fetchall()
        return [self._attempt_from_row(row) for row in rows]

    def record_attempt_terminal(
        self,
        *,
        task_id: str,
        attempt_no: int,
        phase: str,
        terminal_receipt_json: str | None,
        terminal_source: str | None,
        effective_policy_json: str | None = None,
    ) -> None:
        now = utcnow_iso()
        with connect(self.db_path) as conn:
            conn.execute(
                """
                UPDATE task_attempts
                SET phase=?, terminal_receipt_json=?, terminal_source=?,
                    effective_policy_json=COALESCE(?, effective_policy_json),
                    finished_at=COALESCE(finished_at, ?), updated_at=?
                WHERE task_id=? AND attempt_no=?
                """,
                (phase, terminal_receipt_json, terminal_source, effective_policy_json, now, now, task_id, attempt_no),
            )
            conn.commit()

    def update_attempt_adoption(
        self,
        *,
        task_id: str,
        attempt_no: int,
        protocol_warnings_json: str,
        protocol_errors_json: str,
        adoptable_result: str,
        adoption_evidence_json: str,
        controller_rework_json: str = "{}",
    ) -> None:
        now = utcnow_iso()
        with connect(self.db_path) as conn:
            conn.execute(
                """
                UPDATE task_attempts
                SET protocol_warnings_json=?,
                    protocol_errors_json=?,
                    adoptable_result=?,
                    adoption_evidence_json=?,
                    controller_rework_json=?,
                    updated_at=?
                WHERE task_id=? AND attempt_no=?
                """,
                (
                    protocol_warnings_json,
                    protocol_errors_json,
                    adoptable_result,
                    adoption_evidence_json,
                    controller_rework_json,
                    now,
                    task_id,
                    attempt_no,
                ),
            )
            conn.commit()

    def update_attempt_dirty_snapshot(
        self,
        *,
        task_id: str,
        attempt_no: int,
        dirty_snapshot_mode: str,
        dirty_snapshot_json: str,
        dirty_snapshot_applied: bool,
    ) -> None:
        now = utcnow_iso()
        with connect(self.db_path) as conn:
            conn.execute(
                """
                UPDATE task_attempts
                SET dirty_snapshot_mode=?,
                    dirty_snapshot_json=?,
                    dirty_snapshot_applied=?,
                    updated_at=?
                WHERE task_id=? AND attempt_no=?
                """,
                (
                    dirty_snapshot_mode,
                    dirty_snapshot_json,
                    1 if dirty_snapshot_applied else 0,
                    now,
                    task_id,
                    attempt_no,
                ),
            )
            conn.commit()

    def record_artifact(self, *, task_id: str, attempt_no: int, artifact_type: str, path: str) -> None:
        p = Path(path)
        try:
            size = p.stat().st_size if p.exists() else None
        except OSError:
            size = None
        digest = sha256_file(path)
        now = utcnow_iso()
        with connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO task_artifacts (task_id, attempt_no, artifact_type, path, size_bytes, sha256, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(task_id, attempt_no, artifact_type) DO UPDATE SET
                  path=excluded.path,
                  size_bytes=excluded.size_bytes,
                  sha256=excluded.sha256,
                  created_at=excluded.created_at
                """,
                (task_id, attempt_no, artifact_type, path, size, digest, now),
            )
            conn.commit()

    def list_artifacts(self, *, task_id: str, attempt_no: int | None = None) -> list[TaskArtifactRecord]:
        params: list[object] = [task_id]
        sql = "SELECT * FROM task_artifacts WHERE task_id=?"
        if attempt_no is not None:
            sql += " AND attempt_no=?"
            params.append(attempt_no)
        sql += " ORDER BY attempt_no ASC, artifact_type ASC"
        with connect(self.db_path) as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
        return [self._artifact_from_row(row) for row in rows]

    def _update(self, task_id: str, sql: str, params: tuple[object, ...]) -> None:
        with connect(self.db_path) as conn:
            cursor = conn.execute(sql, params)
            if cursor.rowcount == 0:
                raise TaskNotFoundError(f"task not found: {task_id}")
            conn.commit()

    def _query_tasks(self, sql: str, params: tuple[object, ...]) -> list[TaskRecord]:
        with connect(self.db_path) as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._task_from_row(row) for row in rows]

    @staticmethod
    def _task_from_row(row) -> TaskRecord:
        def get(name: str, default=None):
            try:
                return row[name]
            except (IndexError, KeyError):
                return default

        try:
            authorization_profile = validate_authorization_profile(get("authorization_profile", "local_mutating"))
        except ValueError:
            authorization_profile = "local_mutating"
        try:
            completion_policy = normalize_completion_policy(get("completion_policy", "auto"))
        except ValueError:
            completion_policy = "auto"
        antigravity_session_id = get("antigravity_session_id")
        executor_session_id = get("attempt_executor_session_id") or antigravity_session_id
        return TaskRecord(
            task_id=row["task_id"],
            repo_path=row["repo_path"],
            execution_repo_path=get("execution_repo_path"),
            phase=row["phase"],
            antigravity_session_id=antigravity_session_id,
            attempt_no=row["attempt_no"],
            retry_count=row["retry_count"],
            last_receipt_id=get("last_receipt_id"),
            stuck_reason=get("stuck_reason"),
            retry_recommended=bool(get("retry_recommended", 0)),
            last_activity_at=row["last_activity_at"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            last_heartbeat_at=get("last_heartbeat_at"),
            last_workspace_activity_at=get("last_workspace_activity_at"),
            client_idempotency_key=get("client_idempotency_key"),
            executor_backend=get("executor_backend"),
            depends_on=get("depends_on"),
            isolated_worktree=bool(get("isolated_worktree", 0)),
            setup_commands=get("setup_commands"),
            teardown_commands=get("teardown_commands"),
            env_vars=get("env_vars"),
            worktree_boundary=get("worktree_boundary"),
            spotlight_testing=bool(get("spotlight_testing", 0)),
            broad_repo_path_override=bool(get("broad_repo_path_override", 0)),
            completion_policy=completion_policy,
            terminal_source=get("terminal_source"),
            terminal_receipt_json=get("terminal_receipt_json"),
            is_approved=bool(get("is_approved", 0)),
            authorization_profile=authorization_profile,
            authorization_summary=get("authorization_summary"),
            task_kind=str(get("task_kind") or "generic"),
            wait_policy=str(get("wait_policy") or "terminal"),
            controller_wait_seconds=get("controller_wait_seconds"),
            execution_budget_seconds=get("execution_budget_seconds"),
            background_ok=bool(get("background_ok", 0)),
            executor_session_id=executor_session_id,
            workflow_id=get("workflow_id"),
            workflow_node_id=get("workflow_node_id"),
            parent_task_id=get("parent_task_id"),
            child_role=get("child_role"),
            coordination_role=get("coordination_role"),
        )

    @staticmethod
    def _attempt_from_row(row) -> TaskAttemptRecord:
        def get(name: str, default=None):
            try:
                return row[name]
            except (IndexError, KeyError):
                return default

        return TaskAttemptRecord(
            task_id=row["task_id"],
            attempt_no=row["attempt_no"],
            executor_backend=row["executor_backend"],
            authorization_profile=row["authorization_profile"],
            requested_completion_policy=row["requested_completion_policy"],
            effective_policy_json=get("effective_policy_json"),
            task_kind=str(get("task_kind") or "generic"),
            wait_policy=str(get("wait_policy") or "terminal"),
            controller_wait_seconds=get("controller_wait_seconds"),
            execution_budget_seconds=get("execution_budget_seconds"),
            background_ok=bool(get("background_ok", 0)),
            environment_mode=str(get("environment_mode") or "managed-natural"),
            environment_mode_source=str(get("environment_mode_source") or "executor_default"),
            skill_policy=str(get("skill_policy") or "inherit"),
            mcp_policy=str(get("mcp_policy") or "inherit"),
            protocol_warnings_json=str(get("protocol_warnings_json") or "[]"),
            protocol_errors_json=str(get("protocol_errors_json") or "[]"),
            adoptable_result=str(get("adoptable_result") or "unknown"),
            adoption_evidence_json=str(get("adoption_evidence_json") or "{}"),
            controller_rework_json=str(get("controller_rework_json") or "{}"),
            dirty_snapshot_mode=str(get("dirty_snapshot_mode") or "off"),
            dirty_snapshot_json=str(get("dirty_snapshot_json") or "{}"),
            dirty_snapshot_applied=bool(get("dirty_snapshot_applied", 0)),
            executor_session_id=row["executor_session_id"],
            phase=row["phase"],
            terminal_receipt_json=row["terminal_receipt_json"],
            terminal_source=row["terminal_source"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _artifact_from_row(row) -> TaskArtifactRecord:
        return TaskArtifactRecord(
            task_id=row["task_id"],
            attempt_no=row["attempt_no"],
            artifact_type=row["artifact_type"],
            path=row["path"],
            size_bytes=row["size_bytes"],
            sha256=row["sha256"],
            created_at=row["created_at"],
        )
