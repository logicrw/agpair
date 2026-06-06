from __future__ import annotations

import json
from pathlib import Path

from agpair.config import AppPaths
from agpair.executors.base import DispatchResult
from agpair.storage.db import connect, ensure_database
from agpair.storage.journal import JournalRepository
from agpair.storage.tasks import TaskRepository
from agpair.workflows.control import cancel_workflow
from agpair.workflows.schema import validate_manifest
from agpair.workflows.scheduler import WorkflowScheduler
from agpair.workflows.store import WorkflowRepository


class FakeExecutor:
    def __init__(self) -> None:
        self.dispatch_calls: list[dict] = []
        self.cancel_calls: list[tuple[str, str]] = []
        self.cleanup_calls: list[str] = []

    def dispatch(self, **kwargs):
        self.dispatch_calls.append(kwargs)
        return DispatchResult(session_id=f"session-{kwargs['task_id']}")

    def cancel(self, task_id: str, session_id: str) -> None:
        self.cancel_calls.append((task_id, session_id))

    def cleanup(self, session_id: str) -> None:
        self.cleanup_calls.append(session_id)
        Path(session_id).rmdir()


def make_paths(tmp_path: Path) -> AppPaths:
    paths = AppPaths.from_root(tmp_path / ".agpair")
    ensure_database(paths.db_path)
    return paths


def make_manifest(repo_path: str, *, max_retries: int = 1):
    return validate_manifest(
        {
            "version": 1,
            "name": "workflow-recovery-pressure",
            "controller": "generic",
            "authorization_profile": "local_readonly",
            "completion_policy": "report",
            "nodes": [
                {
                    "id": "scan",
                    "kind": "task",
                    "body": "Goal: scan. Required changes: none. Exit criteria: report findings.",
                    "authorization_profile": "local_readonly",
                    "completion_policy": "report",
                    "executor": "antigravity-cli",
                    "max_retries": max_retries,
                    "depends_on": [],
                },
                {"id": "gate", "kind": "gate", "depends_on": ["scan"]},
            ],
        },
        require_repo_path=True,
        repo_path=repo_path,
    )


def patch_scheduler_executor(monkeypatch, fake: FakeExecutor) -> None:
    monkeypatch.setattr("agpair.workflows.scheduler.get_executor", lambda *args, **kwargs: fake)
    monkeypatch.setattr("agpair.workflows.scheduler.is_local_cli_backend", lambda executor_id: True)


def create_workflow(tmp_path: Path, *, workflow_id: str, max_retries: int = 1):
    repo_dir = tmp_path / f"repo-{workflow_id}"
    repo_dir.mkdir()
    paths = make_paths(tmp_path)
    workflows = WorkflowRepository(paths.db_path)
    workflows.create_workflow(
        make_manifest(str(repo_dir), max_retries=max_retries),
        workflow_id=workflow_id,
        repo_path=str(repo_dir),
    )
    return paths, workflows, repo_dir


def test_restart_reroutes_stuck_node_once_and_keeps_retry_context(tmp_path: Path, monkeypatch) -> None:
    fake = FakeExecutor()
    patch_scheduler_executor(monkeypatch, fake)
    paths, workflows, repo_dir = create_workflow(tmp_path, workflow_id="WF-RESTART")
    tasks = TaskRepository(paths.db_path)

    WorkflowScheduler(paths).tick("WF-RESTART", repo_path=str(repo_dir))
    first = tasks.get_task("WF-RESTART-scan")
    assert first is not None
    assert first.executor_backend == "antigravity-cli"
    tasks.mark_stuck(task_id=first.task_id, reason="child stopped making progress")

    restart_scheduler = WorkflowScheduler(paths)
    result = restart_scheduler.tick("WF-RESTART", repo_path=str(repo_dir))
    restart_scheduler.tick("WF-RESTART", repo_path=str(repo_dir))

    assert result["phase"] == "running"
    node = workflows.require_node("WF-RESTART", "scan")
    assert node.phase == "running"
    assert node.attempt_no == 1
    assert node.task_id == "WF-RESTART-scan-A1"
    assert node.executor_backend == "grok-cli"
    assert "rerouting from antigravity-cli to grok-cli" in (node.last_error or "")
    workflow_tasks = tasks.list_tasks(workflow_id="WF-RESTART", limit=10)
    assert {task.task_id for task in workflow_tasks} == {"WF-RESTART-scan", "WF-RESTART-scan-A1"}
    assert len(workflow_tasks) == 2

    created_events = [row for row in JournalRepository(paths.db_path).tail("WF-RESTART-scan-A1", limit=10) if row.event == "created"]
    assert created_events, "retry attempt should persist the generated child body"
    assert '"previous_blocker": "child stopped making progress"' in created_events[0].body
    assert '"new_authorization_profile": "local_readonly"' in created_events[0].body


def test_reroute_policy_failure_blocks_node_instead_of_spinning(tmp_path: Path, monkeypatch) -> None:
    fake = FakeExecutor()
    patch_scheduler_executor(monkeypatch, fake)
    paths, workflows, repo_dir = create_workflow(tmp_path, workflow_id="WF-NO-EXECUTOR")
    tasks = TaskRepository(paths.db_path)
    WorkflowScheduler(paths).tick("WF-NO-EXECUTOR", repo_path=str(repo_dir))
    tasks.mark_stuck(task_id="WF-NO-EXECUTOR-scan", reason="initial executor failed")

    def fail_policy(*args, **kwargs):
        raise ValueError("no available executor")

    monkeypatch.setattr("agpair.workflows.scheduler.resolve_controller_policy", fail_policy)

    result = WorkflowScheduler(paths).tick("WF-NO-EXECUTOR", repo_path=str(repo_dir))

    assert result["phase"] == "blocked"
    node = workflows.require_node("WF-NO-EXECUTOR", "scan")
    assert node.phase == "blocked"
    assert node.attempt_no == 1
    assert node.task_id is None
    assert node.last_error == "no available executor"
    workflow = workflows.require_workflow("WF-NO-EXECUTOR")
    assert workflow.phase == "blocked"
    assert workflow.evidence_path is not None


def test_repeated_stuck_attempts_rotate_through_executor_policy_order(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-anthropic-api-key")
    fake = FakeExecutor()
    patch_scheduler_executor(monkeypatch, fake)
    paths, workflows, repo_dir = create_workflow(tmp_path, workflow_id="WF-ROTATE", max_retries=2)
    tasks = TaskRepository(paths.db_path)

    WorkflowScheduler(paths).tick("WF-ROTATE", repo_path=str(repo_dir))
    tasks.mark_stuck(task_id="WF-ROTATE-scan", reason="antigravity failed")
    WorkflowScheduler(paths).tick("WF-ROTATE", repo_path=str(repo_dir))
    tasks.mark_stuck(task_id="WF-ROTATE-scan-A1", reason="grok failed")
    WorkflowScheduler(paths).tick("WF-ROTATE", repo_path=str(repo_dir))

    node = workflows.require_node("WF-ROTATE", "scan")
    assert node.phase == "running"
    assert node.attempt_no == 2
    assert node.task_id == "WF-ROTATE-scan-A2"
    assert node.executor_backend == "claude-code"
    backends = {
        task.task_id: task.executor_backend
        for task in tasks.list_tasks(workflow_id="WF-ROTATE", limit=10)
    }
    assert backends == {
        "WF-ROTATE-scan": "antigravity-cli",
        "WF-ROTATE-scan-A1": "grok-cli",
        "WF-ROTATE-scan-A2": "claude-code",
    }


def test_cancel_workflow_cancels_and_abandons_active_local_child(tmp_path: Path, monkeypatch) -> None:
    fake = FakeExecutor()
    patch_scheduler_executor(monkeypatch, fake)
    monkeypatch.setattr("agpair.workflows.control.get_executor", lambda *args, **kwargs: fake)
    monkeypatch.setattr("agpair.workflows.control.is_local_cli_backend", lambda executor_id: True)
    paths, workflows, repo_dir = create_workflow(tmp_path, workflow_id="WF-CANCEL-PRESSURE")

    WorkflowScheduler(paths).tick("WF-CANCEL-PRESSURE", repo_path=str(repo_dir))
    payload = cancel_workflow(paths, "WF-CANCEL-PRESSURE", reason="operator cancelled workflow")

    assert payload["ok"] is True
    assert payload["phase"] == "abandoned"
    task = TaskRepository(paths.db_path).get_task("WF-CANCEL-PRESSURE-scan")
    assert task is not None
    assert task.phase == "abandoned"
    node = workflows.require_node("WF-CANCEL-PRESSURE", "scan")
    assert node.phase == "abandoned"
    assert workflows.require_workflow("WF-CANCEL-PRESSURE").phase == "abandoned"
    assert fake.cancel_calls == [("WF-CANCEL-PRESSURE-scan", "session-WF-CANCEL-PRESSURE-scan")]
    events = [row.event for row in JournalRepository(paths.db_path).tail("WF-CANCEL-PRESSURE-scan", limit=10)]
    assert "executor_cancelled" in events
    assert "abandoned" in events


def test_abandoned_child_blocks_workflow_with_evidence_pack(tmp_path: Path, monkeypatch) -> None:
    fake = FakeExecutor()
    patch_scheduler_executor(monkeypatch, fake)
    paths, workflows, repo_dir = create_workflow(tmp_path, workflow_id="WF-ABANDONED")
    tasks = TaskRepository(paths.db_path)
    WorkflowScheduler(paths).tick("WF-ABANDONED", repo_path=str(repo_dir))
    tasks.mark_abandoned(task_id="WF-ABANDONED-scan", reason="watchdog abandon")

    result = WorkflowScheduler(paths).tick("WF-ABANDONED", repo_path=str(repo_dir))

    assert result["phase"] == "blocked"
    workflow = workflows.require_workflow("WF-ABANDONED")
    assert workflow.phase == "blocked"
    assert workflow.evidence_path is not None
    evidence = json.loads(Path(workflow.evidence_path).read_text(encoding="utf-8"))
    assert evidence["phase"] == "blocked"
    node_payload = next(node for node in evidence["nodes"] if node["node_id"] == "scan")
    assert node_payload["phase"] == "abandoned"
    assert node_payload["task_phase"] == "abandoned"
    assert node_payload["error"] == "watchdog abandon"


def test_retry_budget_exhaustion_marks_workflow_stuck_with_evidence_pack(tmp_path: Path, monkeypatch) -> None:
    fake = FakeExecutor()
    patch_scheduler_executor(monkeypatch, fake)
    paths, workflows, repo_dir = create_workflow(tmp_path, workflow_id="WF-STUCK", max_retries=0)
    tasks = TaskRepository(paths.db_path)
    WorkflowScheduler(paths).tick("WF-STUCK", repo_path=str(repo_dir))
    tasks.mark_stuck(task_id="WF-STUCK-scan", reason="retry budget exhausted")

    result = WorkflowScheduler(paths).tick("WF-STUCK", repo_path=str(repo_dir))

    assert result["phase"] == "stuck"
    workflow = workflows.require_workflow("WF-STUCK")
    assert workflow.phase == "stuck"
    assert workflow.evidence_path is not None
    evidence = json.loads(Path(workflow.evidence_path).read_text(encoding="utf-8"))
    assert evidence["phase"] == "stuck"
    assert evidence["stuck_nodes"] == ["scan"]
    node = workflows.require_node("WF-STUCK", "scan")
    assert node.phase == "stuck"
    assert node.attempt_no == 0


def test_daemon_sweep_cleans_terminal_workflow_child_session_without_phase_scan(tmp_path: Path, monkeypatch) -> None:
    from agpair.daemon.loop import sweep_local_cli_sessions

    fake = FakeExecutor()
    patch_scheduler_executor(monkeypatch, fake)
    paths, workflows, repo_dir = create_workflow(tmp_path, workflow_id="WF-SWEEP")
    WorkflowScheduler(paths).tick("WF-SWEEP", repo_path=str(repo_dir))
    tasks = TaskRepository(paths.db_path)
    tasks.mark_ready_for_review(task_id="WF-SWEEP-scan", terminal_source="test")
    session_dir = tmp_path / "agpair_antigravity_cli_WF-SWEEP_cleanup"
    session_dir.mkdir()
    with connect(paths.db_path) as conn:
        conn.execute(
            "UPDATE tasks SET antigravity_session_id=? WHERE task_id=?",
            (str(session_dir), "WF-SWEEP-scan"),
        )
        conn.commit()

    monkeypatch.setattr("agpair.executors.get_executor", lambda *args, **kwargs: fake)

    def fail_list_tasks(*args, **kwargs):
        raise AssertionError("sweep_local_cli_sessions must use cleanup candidates, not phase scans")

    monkeypatch.setattr("agpair.storage.tasks.TaskRepository.list_tasks", fail_list_tasks)

    cleaned = sweep_local_cli_sessions(paths)

    task = tasks.get_task("WF-SWEEP-scan")
    assert cleaned == 1
    assert task is not None
    assert task.antigravity_session_id is None
    assert fake.cleanup_calls == [str(session_dir)]
    assert workflows.require_node("WF-SWEEP", "scan").phase == "running"
