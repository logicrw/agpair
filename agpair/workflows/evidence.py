from __future__ import annotations

import json
from typing import Any

from agpair.artifacts import write_json
from agpair.config import AppPaths
from agpair.storage.tasks import TaskRepository
from agpair.workflows.models import SUCCESS_NODE_PHASES
from agpair.workflows.synthesis import (
    SynthesisValidationError,
    build_lane_card,
    derive_panel_result,
    validate_synthesis_result,
)
from agpair.workflows.store import WorkflowRepository


def build_workflow_evidence_pack(paths: AppPaths, workflow_id: str, *, phase: str | None = None) -> dict[str, Any]:
    workflows = WorkflowRepository(paths.db_path)
    tasks = TaskRepository(paths.db_path)
    workflow = workflows.require_workflow(workflow_id)
    nodes = workflows.list_nodes(workflow_id)
    manifest = _parse_json_object(workflow.manifest_json) or {}
    raw_coordination_policy = manifest.get("coordination_policy")
    coordination_policy = raw_coordination_policy if isinstance(raw_coordination_policy, dict) else None
    coordination_roles = _coordination_roles_by_node(manifest)
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
        protocol_result = None
        adoption_result = None
        if node.task_id:
            task = tasks.get_task(node.task_id)
            if task is not None:
                task_phase = task.phase
                attempt_no = task.attempt_no
                protocol_result, adoption_result = _attempt_protocol_adoption(tasks, node.task_id)
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
                    "protocol_result": protocol_result,
                    "adoption_result": adoption_result,
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
            "role": node.role,
            "coordination_role": coordination_roles.get(node.node_id, "general"),
            "phase": node.phase,
            "task_id": node.task_id,
            "task_phase": task_phase,
            "executor_backend": node.executor_backend,
            "artifacts": artifacts,
            "protocol_result": protocol_result,
            "adoption_result": adoption_result,
            "terminal_receipt": terminal_receipt_payload,
            "error": node.error or node.last_error,
            "evidence": _parse_json_object(node.evidence_json),
            "result": _parse_json_object(node.result_json),
        })

    if not residual_risks:
        validation.append({
            "node_id": "workflow",
            "status": "passed",
            "command": "evidence aggregation",
            "exit_code": 0,
            "evidence": "all completed nodes have durable artifact references or internal gate evidence",
        })

    lane_cards = [build_lane_card(payload) for payload in node_payloads if payload.get("kind") == "task"]
    synthesis_result = _extract_synthesis_result(workflow.workflow_id, node_payloads, residual_risks)
    panel_result = derive_panel_result(
        workflow_id=workflow.workflow_id,
        lane_cards=lane_cards,
        synthesis_result=synthesis_result,
    )
    role_coverage = _role_coverage(coordination_policy, lane_cards)
    panel_result["role_coverage"] = role_coverage

    return {
        "schema_version": "1",
        "workflow_id": workflow.workflow_id,
        "phase": phase or workflow.phase,
        "controller": workflow.controller,
        "repo_path": workflow.repo_path,
        "coordination_policy": coordination_policy,
        "role_coverage": role_coverage,
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
        "lane_cards": lane_cards,
        "synthesis_result": synthesis_result,
        "panel_result": panel_result,
        "recovery_decision": panel_result.get("recovery_decision"),
        "nodes": node_payloads,
    }


def _extract_synthesis_result(
    workflow_id: str,
    node_payloads: list[dict[str, Any]],
    residual_risks: list[str],
) -> dict[str, Any] | None:
    for payload in node_payloads:
        if payload.get("kind") != "synthesis":
            continue
        for raw in (payload.get("result"), payload.get("evidence")):
            candidate = _synthesis_candidate(raw)
            if candidate is None:
                continue
            try:
                return validate_synthesis_result(candidate)
            except SynthesisValidationError as exc:
                residual_risks.append(f"synthesis node {payload.get('node_id')} invalid: {exc}")
    if any(payload.get("kind") == "synthesis" for payload in node_payloads):
        residual_risks.append(f"workflow {workflow_id} has no valid synthesis result")
    return None


def _synthesis_candidate(raw: Any) -> dict[str, Any] | None:
    value = raw if isinstance(raw, dict) else {}
    nested = value.get("synthesis_result")
    if isinstance(nested, dict):
        return nested
    if "recommended_controller_action" in value:
        return value
    return None


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


def _coordination_roles_by_node(manifest: dict[str, Any]) -> dict[str, str]:
    nodes = manifest.get("nodes")
    if not isinstance(nodes, list):
        return {}
    roles: dict[str, str] = {}
    for node in nodes:
        if not isinstance(node, dict):
            continue
        node_id = node.get("id")
        role = node.get("coordination_role")
        if isinstance(node_id, str) and isinstance(role, str):
            roles[node_id] = role
    return roles


def _role_coverage(coordination_policy: dict[str, Any] | None, lane_cards: list[dict[str, Any]]) -> dict[str, Any]:
    expected_roles = _string_list(coordination_policy.get("expected_roles")) if coordination_policy else []
    role_counts: dict[str, int] = {}
    for card in lane_cards:
        role = card.get("coordination_role")
        normalized_role = role if isinstance(role, str) and role else "general"
        role_counts[normalized_role] = role_counts.get(normalized_role, 0) + 1
    observed_roles = sorted(role_counts)
    missing_expected_roles = [role for role in expected_roles if role not in role_counts]
    soft_warnings = [f"expected_role_missing:{role}" for role in missing_expected_roles]
    return {
        "expected_roles": expected_roles,
        "observed_roles": observed_roles,
        "missing_expected_roles": missing_expected_roles,
        "role_counts": role_counts,
        "soft_warnings": soft_warnings,
        "advisory_only": True,
    }


def _string_list(raw: Any) -> list[str]:
    return [item for item in raw if isinstance(item, str)] if isinstance(raw, list) else []


def _attempt_protocol_adoption(tasks: TaskRepository, task_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    attempt = tasks.current_attempt(task_id)
    if attempt is None:
        return (
            {"ok": True, "warnings": [], "errors": []},
            {"adoptable_result": "unknown", "blockers": [], "warnings": [], "evidence": {}},
        )
    warnings = _parse_json_list(attempt.protocol_warnings_json)
    errors = _parse_json_list(attempt.protocol_errors_json)
    adoption = _parse_json_object(attempt.adoption_evidence_json) or {}
    adoption.setdefault("adoptable_result", attempt.adoptable_result)
    adoption.setdefault("blockers", [])
    adoption.setdefault("warnings", [])
    adoption.setdefault("evidence", {})
    return (
        {"ok": not bool(errors), "warnings": warnings, "errors": errors},
        adoption,
    )


def _parse_json_list(text: str | None) -> list[Any]:
    if not text:
        return []
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return []
    return value if isinstance(value, list) else []
