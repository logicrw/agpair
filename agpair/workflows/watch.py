from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agpair.config import AppPaths
from agpair.storage.tasks import TaskRepository
from agpair.workflows.store import WorkflowRepository


def workflow_status_payload(paths: AppPaths, workflow_id: str) -> dict[str, Any]:
    workflows = WorkflowRepository(paths.db_path)
    tasks = TaskRepository(paths.db_path)
    workflow = workflows.require_workflow(workflow_id)
    nodes = workflows.list_nodes(workflow_id)
    node_payloads = []
    cursor_parts = [workflow.updated_at, workflow.phase]
    for node in nodes:
        artifact_paths: dict[str, str] = {}
        protocol_result = None
        adoption_result = None
        if node.task_id:
            task = tasks.get_task(node.task_id)
            if task is not None:
                protocol_result, adoption_result = _attempt_protocol_adoption(tasks, node.task_id)
                for artifact in tasks.list_artifacts(task_id=task.task_id, attempt_no=task.attempt_no):
                    artifact_paths[artifact.artifact_type] = artifact.path
        cursor_parts.append(f"{node.node_id}:{node.phase}:{node.updated_at}:{node.task_id or ''}")
        node_payloads.append({
            "node_id": node.node_id,
            "kind": node.kind,
            "role": node.role,
            "phase": node.phase,
            "depends_on": node.depends_list(),
            "task_id": node.task_id,
            "attempt_no": node.attempt_no,
            "authorization_profile": node.authorization_profile,
            "requested_completion_policy": node.requested_completion_policy,
            "completion_policy": node.requested_completion_policy,
            "effective_policy_json": node.effective_policy_json,
            "executor_backend": node.executor_backend,
            "allow_partial": node.allow_partial,
            "max_retries": node.max_retries,
            "artifact_paths": artifact_paths,
            "protocol_result": protocol_result,
            "adoption_result": adoption_result,
            "error": node.error or node.last_error,
            "evidence": _json_object(node.evidence_json),
            "result": _json_object(node.result_json),
        })
    evidence_payload = _workflow_evidence_payload(workflow.evidence_path)
    return {
        "ok": True,
        "workflow_id": workflow.workflow_id,
        "name": workflow.name,
        "controller": workflow.controller,
        "repo_path": workflow.repo_path,
        "phase": workflow.phase,
        "evidence_path": workflow.evidence_path,
        "result": _json_object(workflow.result_json),
        "created_at": workflow.created_at,
        "updated_at": workflow.updated_at,
        "started_at": workflow.started_at,
        "finished_at": workflow.finished_at,
        "cancelled_at": workflow.cancelled_at,
        "error": workflow.error or workflow.stuck_reason,
        "cursor": "|".join(cursor_parts),
        "panel_result": _dict_value(evidence_payload.get("panel_result")),
        "synthesis_result": _dict_value(evidence_payload.get("synthesis_result")),
        "lane_cards": evidence_payload.get("lane_cards") if isinstance(evidence_payload.get("lane_cards"), list) else [],
        "nodes": node_payloads,
    }


def _workflow_evidence_payload(evidence_path: str | None) -> dict[str, Any]:
    if not evidence_path:
        return {}
    try:
        return _dict_value(_json_object(Path(evidence_path).read_text(encoding="utf-8")))
    except OSError:
        return {}


def workflow_event_payload(paths: AppPaths, workflow_id: str, *, previous_cursor: str | None = None) -> dict[str, Any]:
    status = workflow_status_payload(paths, workflow_id)
    cursor = str(status.get("cursor") or "")
    if previous_cursor == cursor:
        return {
            "schema_version": "1",
            "workflow_id": workflow_id,
            "event": "unchanged",
            "cursor": cursor,
            "phase": status["phase"],
        }
    summary = f"Workflow reached {status['phase']}"
    event = "workflow_state_changed"
    node_id = None
    node_phase = None
    task_id = None
    receipt_path = None
    raw_log_path = None
    for node in status["nodes"]:
        if node.get("phase") in {"ready_for_review", "blocked", "stuck", "skipped", "abandoned", "cancelled"}:
            event = "node_state_changed"
            node_id = node.get("node_id")
            node_phase = node.get("phase")
            task_id = node.get("task_id")
            artifact_paths = _dict_value(node.get("artifact_paths"))
            receipt_path = artifact_paths.get("receipt")
            raw_log_path = artifact_paths.get("stdout")
            summary = f"Node {node_id} reached {node_phase}"
            break
    return {
        "schema_version": "1",
        "workflow_id": workflow_id,
        "event": event,
        "cursor": cursor,
        "phase": status["phase"],
        "node_id": node_id,
        "node_phase": node_phase,
        "task_id": task_id,
        "summary": summary,
        "receipt_path": receipt_path,
        "raw_log_path": raw_log_path,
        "evidence_path": status.get("evidence_path"),
        "error": status.get("error"),
    }


def _json_object(text: str | None) -> dict[str, Any] | None:
    if not text:
        return None
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _dict_value(raw: Any) -> dict[str, Any]:
    return raw if isinstance(raw, dict) else {}


def _attempt_protocol_adoption(tasks: TaskRepository, task_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    attempt = tasks.current_attempt(task_id)
    if attempt is None:
        return (
            {"ok": True, "warnings": [], "errors": []},
            {"adoptable_result": "unknown", "blockers": [], "warnings": [], "evidence": {}},
        )
    warnings = _json_list(attempt.protocol_warnings_json)
    errors = _json_list(attempt.protocol_errors_json)
    adoption = _json_object(attempt.adoption_evidence_json) or {}
    adoption.setdefault("adoptable_result", attempt.adoptable_result)
    adoption.setdefault("blockers", [])
    adoption.setdefault("warnings", [])
    adoption.setdefault("evidence", {})
    return (
        {"ok": not bool(errors), "warnings": warnings, "errors": errors},
        adoption,
    )


def _json_list(text: str | None) -> list[Any]:
    if not text:
        return []
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return []
    return value if isinstance(value, list) else []
