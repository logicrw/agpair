from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from agpair.models import utcnow_iso
from agpair.storage.db import connect
from agpair.workflows.models import WorkflowNodeRecord, WorkflowRecord
from agpair.workflows.schema import WorkflowManifest


class WorkflowNotFoundError(RuntimeError):
    pass


class WorkflowNodeNotFoundError(RuntimeError):
    pass


class WorkflowRepository:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def create_workflow(
        self,
        manifest: WorkflowManifest,
        *,
        workflow_id: str | None = None,
        repo_path: str | None = None,
    ) -> str:
        final_id = workflow_id or f"WF-{uuid4().hex[:12].upper()}"
        effective_repo_path = repo_path or manifest.repo_path or ""
        now = utcnow_iso()
        manifest_json = json.dumps(manifest.manifest, ensure_ascii=False, sort_keys=True)
        limits_json = json.dumps(manifest.limits, ensure_ascii=False, sort_keys=True)
        with connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO workflows (workflow_id, repo_path, name, controller, phase, manifest_json,
                  limits_json, result_json, evidence_path, created_at, updated_at, started_at, finished_at,
                  cancelled_at, stuck_reason, error)
                VALUES (?, ?, ?, ?, 'new', ?, ?, NULL, NULL, ?, ?, NULL, NULL, NULL, NULL, NULL)
                """,
                (final_id, effective_repo_path, manifest.name, manifest.controller, manifest_json, limits_json, now, now),
            )
            for node in manifest.nodes:
                node_id = str(node["id"])
                kind = str(node.get("kind", "task"))
                deps_json = json.dumps(node.get("depends_on") or [], ensure_ascii=False)
                body = node.get("body") or node.get("prompt")
                requested_completion_policy = str(node.get("completion_policy") or manifest.manifest.get("completion_policy") or "auto")
                authorization_profile = str(node.get("authorization_profile") or manifest.manifest.get("authorization_profile") or "local_mutating")
                executor_backend = node.get("executor") or manifest.manifest.get("executor")
                role = node.get("role")
                max_retries = int(node.get("max_retries", manifest.max_retries_per_node))
                conn.execute(
                    """
                    INSERT INTO workflow_nodes (workflow_id, node_id, kind, role, phase, depends_on,
                      depends_on_json, task_id, body, completion_policy, requested_completion_policy,
                      effective_policy_json, authorization_profile, executor_backend, attempt_no, max_retries,
                      allow_partial, isolated_worktree, evidence_json, result_json, error, last_error,
                      created_at, updated_at, started_at, finished_at)
                    VALUES (?, ?, ?, ?, 'pending', ?, ?, NULL, ?, ?, ?, '{}', ?, ?, 0, ?, ?, ?, NULL, NULL, NULL, NULL, ?, ?, NULL, NULL)
                    """,
                    (
                        final_id,
                        node_id,
                        kind,
                        role if isinstance(role, str) else None,
                        deps_json,
                        deps_json,
                        body if isinstance(body, str) else None,
                        requested_completion_policy,
                        requested_completion_policy,
                        authorization_profile,
                        executor_backend if isinstance(executor_backend, str) else None,
                        max_retries,
                        1 if bool(node.get("allow_partial", False)) else 0,
                        1 if bool(node.get("isolated_worktree", False)) else 0,
                        now,
                        now,
                    ),
                )
            conn.commit()
        return final_id

    def get_workflow(self, workflow_id: str) -> WorkflowRecord | None:
        with connect(self.db_path) as conn:
            row = conn.execute("SELECT * FROM workflows WHERE workflow_id=?", (workflow_id,)).fetchone()
        return self._workflow_from_row(row) if row else None

    def require_workflow(self, workflow_id: str) -> WorkflowRecord:
        workflow = self.get_workflow(workflow_id)
        if workflow is None:
            raise WorkflowNotFoundError(f"workflow not found: {workflow_id}")
        return workflow

    def list_workflows(
        self,
        *,
        phase: str | None = None,
        repo_path: str | None = None,
        limit: int = 20,
    ) -> list[WorkflowRecord]:
        sql = "SELECT * FROM workflows"
        clauses: list[str] = []
        params: list[object] = []
        if phase:
            clauses.append("phase=?")
            params.append(phase)
        if repo_path:
            clauses.append("repo_path=?")
            params.append(repo_path)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY updated_at DESC, workflow_id DESC LIMIT ?"
        params.append(limit)
        with connect(self.db_path) as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
        return [self._workflow_from_row(row) for row in rows]

    def list_runnable_workflows(self, *, limit: int = 50) -> list[WorkflowRecord]:
        with connect(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT * FROM workflows
                WHERE phase IN ('new', 'created', 'running')
                ORDER BY updated_at ASC, workflow_id ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [self._workflow_from_row(row) for row in rows]

    def list_nodes(self, workflow_id: str) -> list[WorkflowNodeRecord]:
        with connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM workflow_nodes WHERE workflow_id=? ORDER BY node_id ASC",
                (workflow_id,),
            ).fetchall()
        return [self._node_from_row(row) for row in rows]

    def get_node(self, workflow_id: str, node_id: str) -> WorkflowNodeRecord | None:
        with connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM workflow_nodes WHERE workflow_id=? AND node_id=?",
                (workflow_id, node_id),
            ).fetchone()
        return self._node_from_row(row) if row else None

    def require_node(self, workflow_id: str, node_id: str) -> WorkflowNodeRecord:
        node = self.get_node(workflow_id, node_id)
        if node is None:
            raise WorkflowNodeNotFoundError(f"workflow node not found: {workflow_id}/{node_id}")
        return node

    def mark_workflow_phase(
        self,
        workflow_id: str,
        phase: str,
        *,
        error: str | None = None,
        evidence_path: str | None = None,
        result_json: str | None = None,
    ) -> None:
        now = utcnow_iso()
        started = now if phase == "running" else None
        finished = now if phase in {"ready_for_review", "blocked", "stuck", "cancelled", "abandoned"} else None
        cancelled = now if phase == "cancelled" else None
        stuck_reason = error if phase in {"blocked", "stuck", "abandoned", "cancelled"} else None
        with connect(self.db_path) as conn:
            conn.execute(
                """
                UPDATE workflows
                SET phase=?, error=?, stuck_reason=COALESCE(?, stuck_reason),
                  evidence_path=COALESCE(?, evidence_path), result_json=COALESCE(?, result_json),
                  started_at=COALESCE(started_at, ?), finished_at=COALESCE(?, finished_at),
                  cancelled_at=COALESCE(?, cancelled_at), updated_at=?
                WHERE workflow_id=?
                """,
                (phase, error, stuck_reason, evidence_path, result_json, started, finished, cancelled, now, workflow_id),
            )
            conn.commit()

    def mark_node_phase(
        self,
        workflow_id: str,
        node_id: str,
        phase: str,
        *,
        task_id: str | None = None,
        error: str | None = None,
        evidence_json: str | None = None,
        result_json: str | None = None,
        effective_policy_json: str | None = None,
        executor_backend: str | None = None,
    ) -> None:
        now = utcnow_iso()
        started = now if phase in {"dispatching", "running"} else None
        finished = now if phase in {"ready_for_review", "blocked", "stuck", "skipped", "abandoned", "cancelled"} else None
        with connect(self.db_path) as conn:
            conn.execute(
                """
                UPDATE workflow_nodes
                SET phase=?, task_id=COALESCE(?, task_id), error=?, last_error=COALESCE(?, last_error),
                  evidence_json=COALESCE(?, evidence_json), result_json=COALESCE(?, result_json),
                  effective_policy_json=COALESCE(?, effective_policy_json),
                  executor_backend=COALESCE(?, executor_backend),
                  started_at=COALESCE(started_at, ?), finished_at=COALESCE(?, finished_at), updated_at=?
                WHERE workflow_id=? AND node_id=?
                """,
                (
                    phase,
                    task_id,
                    error,
                    error,
                    evidence_json,
                    result_json,
                    effective_policy_json,
                    executor_backend,
                    started,
                    finished,
                    now,
                    workflow_id,
                    node_id,
                ),
            )
            conn.commit()

    def reset_node_for_retry(
        self,
        workflow_id: str,
        node_id: str,
        *,
        authorization_profile: str | None = None,
        executor_backend: str | None = None,
        reason: str | None = None,
    ) -> WorkflowNodeRecord:
        node = self.require_node(workflow_id, node_id)
        now = utcnow_iso()
        next_attempt = node.attempt_no + 1
        with connect(self.db_path) as conn:
            conn.execute(
                """
                UPDATE workflow_nodes
                SET phase='pending', task_id=NULL, evidence_json=NULL, result_json=NULL,
                  error=NULL, last_error=COALESCE(?, last_error), attempt_no=?,
                  authorization_profile=COALESCE(?, authorization_profile),
                  executor_backend=COALESCE(?, executor_backend),
                  started_at=NULL, finished_at=NULL, updated_at=?
                WHERE workflow_id=? AND node_id=?
                """,
                (reason, next_attempt, authorization_profile, executor_backend, now, workflow_id, node_id),
            )
            conn.commit()
        return self.require_node(workflow_id, node_id)

    def cancel_active_nodes(self, workflow_id: str, *, phase: str = "abandoned", reason: str) -> int:
        now = utcnow_iso()
        with connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                UPDATE workflow_nodes
                SET phase=?, error=?, last_error=?, finished_at=COALESCE(finished_at, ?), updated_at=?
                WHERE workflow_id=? AND phase IN ('pending', 'dispatching', 'running')
                """,
                (phase, reason, reason, now, now, workflow_id),
            )
            conn.commit()
            return cursor.rowcount

    @staticmethod
    def _workflow_from_row(row) -> WorkflowRecord:
        def get(name: str, default=None):
            try:
                return row[name]
            except (IndexError, KeyError):
                return default

        return WorkflowRecord(
            workflow_id=row["workflow_id"],
            repo_path=get("repo_path", "") or "",
            name=get("name", "") or "",
            controller=row["controller"],
            phase=row["phase"],
            manifest_json=row["manifest_json"],
            limits_json=get("limits_json", "{}") or "{}",
            result_json=get("result_json"),
            evidence_path=get("evidence_path"),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            started_at=get("started_at"),
            finished_at=get("finished_at"),
            cancelled_at=get("cancelled_at"),
            stuck_reason=get("stuck_reason") or get("error"),
            error=get("error"),
        )

    @staticmethod
    def _node_from_row(row) -> WorkflowNodeRecord:
        def get(name: str, default=None):
            try:
                return row[name]
            except (IndexError, KeyError):
                return default

        depends = get("depends_on_json") or get("depends_on") or "[]"
        requested_completion_policy = get("requested_completion_policy") or get("completion_policy") or "auto"
        return WorkflowNodeRecord(
            workflow_id=row["workflow_id"],
            node_id=row["node_id"],
            kind=row["kind"],
            role=get("role"),
            phase=row["phase"],
            task_id=get("task_id"),
            depends_on_json=depends,
            authorization_profile=get("authorization_profile", "local_mutating") or "local_mutating",
            requested_completion_policy=requested_completion_policy,
            effective_policy_json=get("effective_policy_json", "{}") or "{}",
            executor_backend=get("executor_backend"),
            attempt_no=int(get("attempt_no", 0) or 0),
            max_retries=int(get("max_retries", 0) or 0),
            allow_partial=bool(get("allow_partial", 0)),
            isolated_worktree=bool(get("isolated_worktree", 0)),
            body=get("body"),
            result_json=get("result_json"),
            evidence_json=get("evidence_json"),
            last_error=get("last_error") or get("error"),
            error=get("error"),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            started_at=get("started_at"),
            finished_at=get("finished_at"),
        )
