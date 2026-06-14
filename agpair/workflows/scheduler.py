from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path

from agpair.completion import resolve_effective_task_policy
from agpair.config import AppPaths
from agpair.executors import get_executor, is_local_cli_backend
from agpair.executors.local_cli import WorktreeProvisionError
from agpair.executors.policy import resolve_controller_policy
from agpair.models import SUCCESS_REVIEW_PHASES
from agpair.storage.journal import JournalRepository
from agpair.storage.tasks import TaskRepository
from agpair.transport.bus import AgentBusClient, BusSendError
from agpair.workflows.evidence import build_workflow_evidence_pack, persist_workflow_evidence_pack
from agpair.workflows.models import FAILED_NODE_PHASES, SUCCESS_NODE_PHASES, WorkflowNodeRecord, WorkflowRecord
from agpair.workflows.store import WorkflowRepository

TERMINAL_WORKFLOW_PHASES = {"ready_for_review", "blocked", "stuck", "cancelled", "abandoned"}


class WorkflowScheduler:
    def __init__(self, paths: AppPaths) -> None:
        self.paths = paths
        self.workflows = WorkflowRepository(paths.db_path)
        self.tasks = TaskRepository(paths.db_path)
        self.journal = JournalRepository(paths.db_path)

    def tick(self, workflow_id: str, *, repo_path: str | None = None, dispatch: bool = True) -> dict:
        workflow = self.workflows.require_workflow(workflow_id)
        if workflow.phase in TERMINAL_WORKFLOW_PHASES:
            return {"workflow_id": workflow_id, "phase": workflow.phase, "dispatched": 0}
        effective_repo_path = repo_path or workflow.repo_path
        if not effective_repo_path:
            self.workflows.mark_workflow_phase(workflow_id, "blocked", error="workflow repo_path is missing")
            return {"workflow_id": workflow_id, "phase": "blocked", "dispatched": 0}
        self.workflows.mark_workflow_phase(workflow_id, "running")
        workflow = self.workflows.require_workflow(workflow_id)
        self._reconcile_node_tasks(workflow_id)
        nodes = self.workflows.list_nodes(workflow_id)
        node_by_id = {node.node_id: node for node in nodes}
        running_count = sum(1 for node in nodes if node.phase in {"dispatching", "running"})
        max_parallel = workflow.limits().get("max_parallel_tasks", 4)
        dispatched = 0
        for node in nodes:
            if node.phase != "pending":
                continue
            if not self._dependencies_ready(node, node_by_id):
                if self._dependencies_failed(node, node_by_id):
                    self.workflows.mark_node_phase(
                        workflow_id,
                        node.node_id,
                        "blocked",
                        error="dependency reached terminal failure",
                    )
                continue
            if node.kind == "gate":
                self._run_gate_node(workflow, node)
                continue
            if running_count >= int(max_parallel):
                continue
            if dispatch:
                self._dispatch_node(workflow, node, repo_path=effective_repo_path)
            else:
                self.workflows.mark_node_phase(workflow_id, node.node_id, "dispatching")
            dispatched += 1
            running_count += 1
        self._reconcile_node_tasks(workflow_id)
        final_phase = self._finalize_workflow_if_terminal(workflow_id)
        return {"workflow_id": workflow_id, "phase": final_phase, "dispatched": dispatched}

    def advance_running_workflows(self, *, limit: int = 50) -> int:
        count = 0
        for workflow in self.workflows.list_runnable_workflows(limit=limit):
            repo_path = workflow.repo_path
            if not repo_path:
                self.workflows.mark_workflow_phase(workflow.workflow_id, "blocked", error="workflow repo_path is missing")
                continue
            self.tick(workflow.workflow_id, repo_path=repo_path)
            count += 1
        return count

    def _dependencies_ready(self, node: WorkflowNodeRecord, node_by_id: dict[str, WorkflowNodeRecord]) -> bool:
        return all(node_by_id.get(dep) is not None and node_by_id[dep].phase in SUCCESS_NODE_PHASES for dep in node.depends_list())

    def _dependencies_failed(self, node: WorkflowNodeRecord, node_by_id: dict[str, WorkflowNodeRecord]) -> bool:
        return any(node_by_id.get(dep) is not None and node_by_id[dep].phase in FAILED_NODE_PHASES for dep in node.depends_list())

    def _run_gate_node(self, workflow: WorkflowRecord, node: WorkflowNodeRecord) -> None:
        nodes = self.workflows.list_nodes(workflow.workflow_id)
        required = [item for item in nodes if item.node_id != node.node_id and not item.allow_partial]
        blocked = [item.node_id for item in required if item.phase in FAILED_NODE_PHASES]
        incomplete = [item.node_id for item in required if item.phase not in SUCCESS_NODE_PHASES]
        if blocked:
            self.workflows.mark_node_phase(
                workflow.workflow_id,
                node.node_id,
                "blocked",
                error="gate failed: required nodes blocked or stuck: " + ", ".join(blocked),
            )
            return
        if incomplete:
            return
        evidence_pack = build_workflow_evidence_pack(self.paths, workflow.workflow_id, phase=workflow.phase)
        has_synthesis = any(item.kind == "synthesis" for item in required)
        if has_synthesis and not evidence_pack.get("synthesis_result"):
            self.workflows.mark_node_phase(
                workflow.workflow_id,
                node.node_id,
                "blocked",
                error="gate failed: missing synthesis result",
            )
            return
        panel_result = evidence_pack.get("panel_result") if isinstance(evidence_pack.get("panel_result"), dict) else {}
        hard_blockers = panel_result.get("hard_blockers") if isinstance(panel_result, dict) else []
        if has_synthesis and isinstance(hard_blockers, list) and hard_blockers:
            self.workflows.mark_node_phase(
                workflow.workflow_id,
                node.node_id,
                "blocked",
                error="gate failed: panel hard blockers: " + ", ".join(str(item) for item in hard_blockers),
            )
            return
        evidence = {
            "schema_version": "1",
            "gate": "passed",
            "required_nodes": [item.node_id for item in required],
            "synthesis_result": evidence_pack.get("synthesis_result"),
            "panel_result": panel_result,
        }
        self.workflows.mark_node_phase(
            workflow.workflow_id,
            node.node_id,
            "ready_for_review",
            evidence_json=json.dumps(evidence, ensure_ascii=False, sort_keys=True),
            result_json=json.dumps(evidence, ensure_ascii=False, sort_keys=True),
        )

    def _dispatch_node(self, workflow: WorkflowRecord, node: WorkflowNodeRecord, *, repo_path: str) -> None:
        body = self._node_body(workflow, node)
        controller = workflow.controller
        try:
            policy_decision = resolve_controller_policy(
                controller=controller,
                requested_executor=node.executor_backend,
                allow_self_executor=False,
                require_available=True,
            )
            executor_id = policy_decision.selected_executor
        except ValueError as exc:
            self.workflows.mark_node_phase(workflow.workflow_id, node.node_id, "blocked", error=str(exc))
            return
        if not executor_id:
            self.workflows.mark_node_phase(workflow.workflow_id, node.node_id, "blocked", error="no eligible executor")
            return
        effective_policy = resolve_effective_task_policy(
            requested_completion_policy=node.requested_completion_policy,
            authorization_profile=node.authorization_profile,
            body=body,
            controller=controller,
        )
        effective_policy_json = json.dumps(effective_policy.to_dict(), ensure_ascii=False, sort_keys=True)
        task_id = _child_task_id(workflow.workflow_id, node.node_id, node.attempt_no)
        idempotency_key = f"workflow:{workflow.workflow_id}:node:{node.node_id}:attempt:{node.attempt_no}"
        self.workflows.mark_node_phase(
            workflow.workflow_id,
            node.node_id,
            "dispatching",
            effective_policy_json=effective_policy_json,
            executor_backend=executor_id,
        )
        dirty_snapshot_mode = (
            "tracked"
            if node.isolated_worktree and node.authorization_profile != "local_readonly"
            else "off"
        )
        try:
            self.tasks.create_task(
                task_id=task_id,
                repo_path=repo_path,
                client_idempotency_key=idempotency_key,
                executor_backend=executor_id,
                authorization_profile=node.authorization_profile,
                authorization_summary=None,
                completion_policy=node.requested_completion_policy,
                effective_policy_json=effective_policy_json,
                workflow_id=workflow.workflow_id,
                workflow_node_id=node.node_id,
                parent_task_id=workflow.workflow_id,
                child_role=node.role or node.kind,
                isolated_worktree=node.isolated_worktree,
                dirty_snapshot_mode=dirty_snapshot_mode,
            )
            self.journal.append(task_id, "workflow", "created", body)
        except sqlite3.IntegrityError:
            existing = self.tasks.get_task_by_idempotency_key(repo_path=repo_path, client_idempotency_key=idempotency_key)
            if existing is None:
                self.workflows.mark_node_phase(workflow.workflow_id, node.node_id, "blocked", error="task idempotency collision")
                return
            task_id = existing.task_id
        exec_instance = get_executor(executor_id, agent_bus_bin=self.paths.agent_bus_bin)
        if exec_instance is None:
            self.tasks.mark_blocked(task_id=task_id, reason=f"executor unavailable: {executor_id}")
            self.workflows.mark_node_phase(workflow.workflow_id, node.node_id, "blocked", task_id=task_id, error=f"executor unavailable: {executor_id}")
            return
        task = self.tasks.get_task(task_id)
        if task and task.phase != "new":
            self.workflows.mark_node_phase(workflow.workflow_id, node.node_id, "running", task_id=task_id)
            return
        try:
            if is_local_cli_backend(executor_id):
                dispatch_result = exec_instance.dispatch(
                    task_id=task_id,
                    body=body,
                    repo_path=repo_path,
                    authorization_profile=node.authorization_profile,
                    isolated_worktree=node.isolated_worktree,
                    dirty_snapshot_mode=dirty_snapshot_mode,
                )
                if dispatch_result.execution_repo_path:
                    self.tasks.set_execution_repo_path(task_id=task_id, execution_repo_path=dispatch_result.execution_repo_path)
                if dispatch_result.session_id:
                    state_path = Path(str(dispatch_result.session_id)) / "state.json"
                    try:
                        state = json.loads(state_path.read_text(encoding="utf-8"))
                    except (OSError, json.JSONDecodeError):
                        state = {}
                    snapshot = state.get("dirty_snapshot_json") if isinstance(state, dict) else {}
                    if not isinstance(snapshot, dict):
                        snapshot = {}
                    self.tasks.update_attempt_dirty_snapshot(
                        task_id=task_id,
                        attempt_no=1,
                        dirty_snapshot_mode=str(state.get("dirty_snapshot_mode") or dirty_snapshot_mode) if isinstance(state, dict) else dirty_snapshot_mode,
                        dirty_snapshot_json=json.dumps(snapshot, ensure_ascii=False, sort_keys=True),
                        dirty_snapshot_applied=bool(state.get("dirty_snapshot_applied")) if isinstance(state, dict) else False,
                    )
                if not dispatch_result.session_id:
                    raise RuntimeError(f"local executor {executor_id} did not return a session_id")
                self.tasks.mark_acked(task_id=task_id, session_id=dispatch_result.session_id)
                self.journal.append(task_id, "workflow", "dispatched", f"node={node.node_id} session={dispatch_result.session_id}")
            else:
                bus = AgentBusClient(self.paths.agent_bus_bin)
                message_id = bus.send_task(task_id=task_id, body=body, repo_path=repo_path)
                self.journal.append(task_id, "workflow", "dispatched", f"node={node.node_id} msg={message_id}")
        except (subprocess.SubprocessError, FileNotFoundError, BusSendError, WorktreeProvisionError, RuntimeError) as exc:
            self.tasks.mark_blocked(task_id=task_id, reason=f"workflow node dispatch failed: {exc}")
            self.workflows.mark_node_phase(workflow.workflow_id, node.node_id, "blocked", task_id=task_id, error=str(exc))
            return
        self.workflows.mark_node_phase(workflow.workflow_id, node.node_id, "running", task_id=task_id)

    def _node_body(self, workflow: WorkflowRecord, node: WorkflowNodeRecord) -> str:
        body = node.body or f"Workflow node {node.node_id} has no body."
        manifest = _safe_json(workflow.manifest_json) or {}
        source_policy = manifest.get("source_policy")
        if isinstance(source_policy, dict) and node.kind != "gate":
            body += "\n\nWorkflow source policy, JSON:\n"
            body += json.dumps(source_policy, ensure_ascii=False, sort_keys=True)
        if node.kind in {"synthesis", "verification"}:
            dependencies = []
            for dep in node.depends_list():
                dep_node = self.workflows.get_node(workflow.workflow_id, dep)
                if dep_node is None:
                    continue
                dependencies.append({
                    "node_id": dep_node.node_id,
                    "phase": dep_node.phase,
                    "task_id": dep_node.task_id,
                    "evidence": _safe_json(dep_node.evidence_json),
                    "result": _safe_json(dep_node.result_json),
                })
            context = {
                "workflow_id": workflow.workflow_id,
                "node_id": node.node_id,
                "node_kind": node.kind,
                "dependencies": dependencies,
                "instruction": "Use durable artifact paths and structured receipt summaries. Do not rely on raw executor prose as proof.",
            }
            if isinstance(source_policy, dict):
                context["source_policy"] = source_policy
            if node.kind == "synthesis":
                context["lane_cards"] = build_workflow_evidence_pack(
                    self.paths,
                    workflow.workflow_id,
                    phase=workflow.phase,
                ).get("lane_cards", [])
            body += "\n\nWorkflow context for this node, JSON:\n"
            body += json.dumps(context, ensure_ascii=False, sort_keys=True)
        if node.attempt_no > 0:
            body += "\n\nRetry context:\n"
            body += self._retry_context(workflow, node)
        return body

    def _retry_context(self, workflow: WorkflowRecord, node: WorkflowNodeRecord) -> str:
        previous_attempt = max(node.attempt_no - 1, 0)
        previous_task_id = _child_task_id(workflow.workflow_id, node.node_id, previous_attempt)
        previous_task = self.tasks.get_task(previous_task_id)
        paths: dict[str, str] = {}
        blocker = None
        if previous_task is not None:
            blocker = previous_task.stuck_reason
            for artifact in self.tasks.list_artifacts(task_id=previous_task.task_id, attempt_no=previous_task.attempt_no):
                paths[artifact.artifact_type] = artifact.path
        return json.dumps(
            {
                "previous_task_id": previous_task_id,
                "previous_blocker": blocker,
                "previous_artifact_paths": paths,
                "new_authorization_profile": node.authorization_profile,
                "node_id": node.node_id,
            },
            ensure_ascii=False,
            sort_keys=True,
        )

    def _reconcile_node_tasks(self, workflow_id: str) -> None:
        workflow = self.workflows.require_workflow(workflow_id)
        nodes = self.workflows.list_nodes(workflow_id)
        for node in nodes:
            if not node.task_id:
                continue
            task = self.tasks.get_task(node.task_id)
            if task is None:
                continue
            if task.phase in SUCCESS_REVIEW_PHASES:
                evidence = {
                    "schema_version": "1",
                    "task_id": task.task_id,
                    "phase": task.phase,
                    "attempt_no": task.attempt_no,
                    "receipt_path": _artifact_path(self.tasks, task.task_id, task.attempt_no, "receipt"),
                    "stdout_path": _artifact_path(self.tasks, task.task_id, task.attempt_no, "stdout"),
                    "stderr_path": _artifact_path(self.tasks, task.task_id, task.attempt_no, "stderr"),
                    "report_path": _artifact_path(self.tasks, task.task_id, task.attempt_no, "report"),
                }
                self.workflows.mark_node_phase(
                    workflow_id,
                    node.node_id,
                    "ready_for_review",
                    task_id=task.task_id,
                    evidence_json=json.dumps(evidence, ensure_ascii=False, sort_keys=True),
                    result_json=json.dumps(evidence, ensure_ascii=False, sort_keys=True),
                )
            elif task.phase == "blocked":
                if node.allow_partial:
                    self.workflows.mark_node_phase(
                        workflow_id,
                        node.node_id,
                        "skipped",
                        task_id=task.task_id,
                        error=task.stuck_reason,
                    )
                else:
                    self.workflows.mark_node_phase(
                        workflow_id,
                        node.node_id,
                        "blocked",
                        task_id=task.task_id,
                        error=task.stuck_reason,
                    )
            elif task.phase == "stuck":
                if node.attempt_no < node.max_retries:
                    failed_executor = task.executor_backend or node.executor_backend
                    next_executor = self._next_policy_executor(workflow, failed_executor)
                    reason = task.stuck_reason or "child task stuck; retry budget available"
                    if next_executor and next_executor != failed_executor:
                        reason = f"{reason}; rerouting from {failed_executor} to {next_executor}"
                    self.workflows.reset_node_for_retry(
                        workflow_id,
                        node.node_id,
                        executor_backend=next_executor,
                        reason=reason,
                    )
                else:
                    self.workflows.mark_node_phase(
                        workflow_id,
                        node.node_id,
                        "stuck",
                        task_id=task.task_id,
                        error=task.stuck_reason or "child task stuck and retry budget exhausted",
                    )
            elif task.phase == "abandoned":
                self.workflows.mark_node_phase(workflow_id, node.node_id, "abandoned", task_id=task.task_id, error=task.stuck_reason)

    def _next_policy_executor(self, workflow: WorkflowRecord, failed_executor: str | None) -> str | None:
        try:
            decision = resolve_controller_policy(
                controller=workflow.controller,
                allow_self_executor=False,
                require_available=True,
            )
        except ValueError:
            return None
        eligible = list(decision.eligible_executors)
        if failed_executor in eligible:
            failed_index = eligible.index(failed_executor)
            eligible = eligible[failed_index + 1:] + eligible[:failed_index]
        for executor_id in eligible:
            if executor_id != failed_executor:
                return executor_id
        return None

    def _finalize_workflow_if_terminal(self, workflow_id: str) -> str:
        nodes = self.workflows.list_nodes(workflow_id)
        if any(node.phase == "stuck" for node in nodes):
            path = persist_workflow_evidence_pack(self.paths, workflow_id, phase="stuck")
            self.workflows.mark_workflow_phase(workflow_id, "stuck", error="one or more workflow nodes are stuck", evidence_path=path)
            return "stuck"
        if any(node.phase in {"blocked", "abandoned", "cancelled"} for node in nodes):
            path = persist_workflow_evidence_pack(self.paths, workflow_id, phase="blocked")
            self.workflows.mark_workflow_phase(workflow_id, "blocked", error="one or more workflow nodes failed", evidence_path=path)
            return "blocked"
        if nodes and all(node.phase in SUCCESS_NODE_PHASES or node.phase == "skipped" for node in nodes):
            evidence = build_workflow_evidence_pack(self.paths, workflow_id, phase="ready_for_review")
            if evidence.get("residual_risks"):
                path = persist_workflow_evidence_pack(self.paths, workflow_id, phase="blocked")
                self.workflows.mark_workflow_phase(
                    workflow_id,
                    "blocked",
                    error="workflow evidence pack has residual risks",
                    evidence_path=path,
                )
                return "blocked"
            path = persist_workflow_evidence_pack(self.paths, workflow_id, phase="ready_for_review")
            self.workflows.mark_workflow_phase(workflow_id, "ready_for_review", evidence_path=path)
            return "ready_for_review"
        self.workflows.mark_workflow_phase(workflow_id, "running")
        return "running"


def _child_task_id(workflow_id: str, node_id: str, attempt_no: int) -> str:
    suffix = f"-A{attempt_no}" if attempt_no else ""
    return f"{workflow_id}-{node_id}{suffix}"[:120]


def _artifact_path(tasks: TaskRepository, task_id: str, attempt_no: int, kind: str) -> str | None:
    for artifact in tasks.list_artifacts(task_id=task_id, attempt_no=attempt_no):
        if artifact.artifact_type == kind:
            return artifact.path
    return None


def _safe_json(text: str | None) -> dict | None:
    if not text:
        return None
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None
