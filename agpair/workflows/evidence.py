from __future__ import annotations

import json
from typing import Any

from agpair.artifacts import write_json
from agpair.config import AppPaths
from agpair.storage.tasks import TaskRepository
from agpair.workflows.models import SUCCESS_NODE_PHASES
from agpair.workflows.store import WorkflowRepository


def build_workflow_evidence_pack(paths: AppPaths, workflow_id: str, *, phase: str | None = None) -> dict[str, Any]:
    workflows = WorkflowRepository(paths.db_path)
    tasks = TaskRepository(paths.db_path)
    workflow = workflows.require_workflow(workflow_id)
    nodes = workflows.list_nodes(workflow_id)
    required_nodes = [node.node_id for node in nodes if not node.allow_partial]
    completed_nodes: list[str] = []
    blocked_nodes: list[str] = []
    stuck_nodes: list[str] = []
    skipped_nodes: list[str] = []
    receipts: list[dict[str, Any]] = []
    node_payloads: list[dict[str, Any]] = []
    changed_files: set[str] = set()
    scope_violations: list[Any] = []
    residual_risks: list[str] = []
    validation: list[dict[str, Any]] = []

    for node in nodes:
        if node.phase in SUCCESS_NODE_PHASES:
            completed_nodes.append(node.node_id)
        elif node.phase == "skipped":
            skipped_nodes.append(node.node_id)
        elif node.phase == "blocked":
            blocked_nodes.append(node.node_id)
        elif node.phase == "stuck":
            stuck_nodes.append(node.node_id)

        artifacts = []
        artifact_paths: dict[str, str] = {}
        terminal_receipt_payload = None
        task_phase = None
        attempt_no = None
        if node.task_id:
            task = tasks.get_task(node.task_id)
            if task is not None:
                task_phase = task.phase
                attempt_no = task.attempt_no
                terminal_receipt_payload = _parse_json_object(task.terminal_receipt_json)
                if isinstance(terminal_receipt_payload, dict):
                    payload = terminal_receipt_payload.get("payload")
                    terminal_payload = payload if isinstance(payload, dict) else terminal_receipt_payload
                    for item in terminal_payload.get("changed_files") or []:
                        if isinstance(item, str):
                            changed_files.add(item)
                    for item in terminal_payload.get("scope_violations") or []:
                        scope_violations.append({"node_id": node.node_id, "violation": item})
                for artifact in tasks.list_artifacts(task_id=node.task_id, attempt_no=task.attempt_no):
                    artifact_paths[artifact.artifact_type] = artifact.path
                    artifacts.append({
                        "type": artifact.artifact_type,
                        "path": artifact.path,
                        "size_bytes": artifact.size_bytes,
                        "sha256": artifact.sha256,
                    })
                if node.phase in SUCCESS_NODE_PHASES and not artifact_paths.get("receipt"):
                    residual_risks.append(f"node {node.node_id} completed without durable receipt artifact")
                receipts.append({
                    "node_id": node.node_id,
                    "task_id": node.task_id,
                    "attempt_no": attempt_no,
                    "receipt_path": artifact_paths.get("receipt"),
                    "raw_log_path": artifact_paths.get("stdout"),
                    "stderr_path": artifact_paths.get("stderr"),
                    "report_path": artifact_paths.get("report"),
                })
        elif node.phase in SUCCESS_NODE_PHASES and node.kind != "gate":
            residual_risks.append(f"node {node.node_id} completed without child task")

        if node.kind == "gate" and node.phase in SUCCESS_NODE_PHASES:
            validation.append({
                "node_id": node.node_id,
                "status": "passed",
                "command": "internal gate",
                "exit_code": 0,
                "evidence": node.evidence_json or node.result_json or "dependencies satisfied",
            })

        node_payloads.append({
            "node_id": node.node_id,
            "kind": node.kind,
            "phase": node.phase,
            "task_id": node.task_id,
            "task_phase": task_phase,
            "artifacts": artifacts,
            "terminal_receipt": terminal_receipt_payload,
            "error": node.error or node.last_error,
        })

    if not residual_risks:
        validation.append({
            "node_id": "workflow",
            "status": "passed",
            "command": "evidence aggregation",
            "exit_code": 0,
            "evidence": "all completed nodes have durable artifact references or internal gate evidence",
        })

    return {
        "schema_version": "1",
        "workflow_id": workflow.workflow_id,
        "phase": phase or workflow.phase,
        "controller": workflow.controller,
        "repo_path": workflow.repo_path,
        "required_nodes": required_nodes,
        "completed_nodes": completed_nodes,
        "blocked_nodes": blocked_nodes,
        "stuck_nodes": stuck_nodes,
        "skipped_nodes": skipped_nodes,
        "changed_files": sorted(changed_files),
        "validation": validation,
        "receipts": receipts,
        "scope_violations": scope_violations,
        "residual_risks": residual_risks,
        "nodes": node_payloads,
    }


def persist_workflow_evidence_pack(paths: AppPaths, workflow_id: str, *, phase: str | None = None) -> str:
    payload = build_workflow_evidence_pack(paths, workflow_id, phase=phase)
    path = paths.root / "workflows" / workflow_id / "evidence.json"
    write_json(path, payload)
    WorkflowRepository(paths.db_path).mark_workflow_phase(
        workflow_id,
        payload["phase"],
        evidence_path=str(path),
        result_json=json.dumps(payload, ensure_ascii=False, sort_keys=True),
    )
    return str(path)


def _parse_json_object(text: str | None) -> dict[str, Any] | None:
    if not text:
        return None
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None
