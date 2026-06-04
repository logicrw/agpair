from __future__ import annotations

from agpair.config import AppPaths
from agpair.executors import get_executor, is_local_cli_backend
from agpair.storage.journal import JournalRepository
from agpair.storage.tasks import TaskRepository
from agpair.workflows.store import WorkflowRepository
from agpair.workflows.watch import workflow_status_payload

TERMINAL_WORKFLOW_PHASES = {"ready_for_review", "blocked", "stuck", "cancelled", "abandoned"}
ACTIVE_NODE_PHASES = {"pending", "dispatching", "running"}
ACTIVE_TASK_PHASES = {"new", "acked"}


def cancel_workflow(paths: AppPaths, workflow_id: str, *, reason: str) -> dict:
    workflows = WorkflowRepository(paths.db_path)
    workflow = workflows.require_workflow(workflow_id)
    if workflow.phase in TERMINAL_WORKFLOW_PHASES:
        payload = workflow_status_payload(paths, workflow_id)
        payload["ok"] = True
        return payload

    tasks = TaskRepository(paths.db_path)
    journal = JournalRepository(paths.db_path)
    for node in workflows.list_nodes(workflow_id):
        if node.phase not in ACTIVE_NODE_PHASES or not node.task_id:
            continue
        task = tasks.get_task(node.task_id)
        if task is None or task.phase not in ACTIVE_TASK_PHASES:
            continue
        if task.phase == "acked" and is_local_cli_backend(task.executor_backend):
            executor = get_executor(task.executor_backend, agent_bus_bin=paths.agent_bus_bin)
            if executor is not None:
                try:
                    session_id = task.executor_session_id or task.antigravity_session_id or ""
                    executor.cancel(task_id=task.task_id, session_id=session_id)
                    journal.append(task.task_id, "workflow", "executor_cancelled", reason)
                except Exception as exc:
                    journal.append(task.task_id, "workflow", "executor_cancel_failed", str(exc))
        tasks.mark_abandoned(task_id=task.task_id, reason=reason)
        journal.append(task.task_id, "workflow", "abandoned", reason)

    workflows.cancel_active_nodes(workflow_id, phase="abandoned", reason=reason)
    workflows.mark_workflow_phase(workflow_id, "abandoned", error=reason)
    payload = workflow_status_payload(paths, workflow_id)
    payload["ok"] = True
    return payload
