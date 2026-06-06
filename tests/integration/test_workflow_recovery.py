from __future__ import annotations

import json
from pathlib import Path

from agpair.config import AppPaths
from agpair.executors.base import DispatchResult
from agpair.storage.db import ensure_database
from agpair.storage.tasks import TaskRepository
from agpair.workflows.schema import validate_manifest
from agpair.workflows.scheduler import WorkflowScheduler
from agpair.workflows.store import WorkflowRepository


class FakeExecutor:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def dispatch(self, **kwargs):
        self.calls.append(kwargs)
        return DispatchResult(session_id=f"session-{kwargs['task_id']}")


def test_advance_running_workflows_after_restart_dispatches_each_pending_child_once(
    tmp_path: Path,
    monkeypatch,
) -> None:
    fake = FakeExecutor()
    monkeypatch.setattr("agpair.workflows.scheduler.get_executor", lambda *args, **kwargs: fake)
    monkeypatch.setattr("agpair.workflows.scheduler.is_local_cli_backend", lambda executor_id: True)
    paths = AppPaths.from_root(tmp_path / ".agpair")
    ensure_database(paths.db_path)
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    manifest = validate_manifest(
        {
            "version": 1,
            "name": "workflow-restart",
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
                    "depends_on": [],
                },
                {"id": "gate", "kind": "gate", "depends_on": ["scan"]},
            ],
        },
        require_repo_path=True,
        repo_path=str(repo_dir),
    )
    workflows = WorkflowRepository(paths.db_path)
    workflows.create_workflow(manifest, workflow_id="WF-ADVANCE-RESTART", repo_path=str(repo_dir))

    assert WorkflowScheduler(paths).advance_running_workflows() == 1
    assert WorkflowScheduler(paths).advance_running_workflows() == 1

    tasks = TaskRepository(paths.db_path).list_tasks(workflow_id="WF-ADVANCE-RESTART", limit=10)
    assert [task.task_id for task in tasks] == ["WF-ADVANCE-RESTART-scan"]
    assert fake.calls == [
        {
            "task_id": "WF-ADVANCE-RESTART-scan",
            "body": "Goal: scan. Required changes: none. Exit criteria: report findings.",
            "repo_path": str(repo_dir),
            "authorization_profile": "local_readonly",
            "isolated_worktree": False,
        }
    ]
    node = workflows.require_node("WF-ADVANCE-RESTART", "scan")
    assert node.phase == "running"
    assert json.loads(node.effective_policy_json)["effective_completion_policy"] == "report"
