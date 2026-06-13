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
        self.calls = []

    def dispatch(self, **kwargs):
        self.calls.append(kwargs)
        return DispatchResult(session_id=f"session-{kwargs['task_id']}")


def make_paths(tmp_path: Path) -> AppPaths:
    paths = AppPaths.from_root(tmp_path / ".agpair")
    ensure_database(paths.db_path)
    return paths


def make_manifest(repo_path: str):
    return validate_manifest(
        {
            "version": 1,
            "name": "scheduler-test",
            "controller": "generic",
            "authorization_profile": "local_readonly",
            "completion_policy": "report",
            "nodes": [
                {
                    "id": "scan",
                    "kind": "task",
                    "body": "Goal: scan. Required changes: none.",
                    "authorization_profile": "local_readonly",
                    "completion_policy": "report",
                    "depends_on": [],
                },
                {"id": "gate", "kind": "gate", "depends_on": ["scan"]},
            ],
        },
        require_repo_path=True,
        repo_path=repo_path,
    )


def patch_executor(monkeypatch):
    fake = FakeExecutor()
    monkeypatch.setattr("agpair.workflows.scheduler.get_executor", lambda *args, **kwargs: fake)
    monkeypatch.setattr("agpair.workflows.scheduler.is_local_cli_backend", lambda executor_id: True)
    return fake


def test_scheduler_dispatches_dependency_free_nodes(tmp_path: Path, monkeypatch) -> None:
    fake = patch_executor(monkeypatch)
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    paths = make_paths(tmp_path)
    repo = WorkflowRepository(paths.db_path)
    workflow_id = repo.create_workflow(make_manifest(str(repo_dir)), workflow_id="WF-SCHED", repo_path=str(repo_dir))

    result = WorkflowScheduler(paths).tick(workflow_id, repo_path=str(repo_dir))

    assert result["dispatched"] == 1
    node = repo.require_node(workflow_id, "scan")
    assert node.phase == "running"
    assert node.task_id == "WF-SCHED-scan"
    task = TaskRepository(paths.db_path).get_task("WF-SCHED-scan")
    assert task is not None
    assert task.workflow_id == workflow_id
    assert fake.calls[0]["authorization_profile"] == "local_readonly"


def test_scheduler_does_not_dispatch_duplicate_child_after_restart(tmp_path: Path, monkeypatch) -> None:
    patch_executor(monkeypatch)
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    paths = make_paths(tmp_path)
    workflows = WorkflowRepository(paths.db_path)
    workflow_id = workflows.create_workflow(make_manifest(str(repo_dir)), workflow_id="WF-DEDUP", repo_path=str(repo_dir))
    scheduler = WorkflowScheduler(paths)

    scheduler.tick(workflow_id, repo_path=str(repo_dir))
    scheduler.tick(workflow_id, repo_path=str(repo_dir))

    tasks = TaskRepository(paths.db_path).list_tasks(workflow_id=workflow_id, limit=10)
    assert [task.task_id for task in tasks] == ["WF-DEDUP-scan"]


def test_scheduler_reroutes_stuck_node_with_retry_budget(tmp_path: Path, monkeypatch) -> None:
    patch_executor(monkeypatch)
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    paths = make_paths(tmp_path)
    workflows = WorkflowRepository(paths.db_path)
    workflow_id = workflows.create_workflow(make_manifest(str(repo_dir)), workflow_id="WF-REROUTE", repo_path=str(repo_dir))
    scheduler = WorkflowScheduler(paths)

    scheduler.tick(workflow_id, repo_path=str(repo_dir))
    tasks = TaskRepository(paths.db_path)
    first_task = tasks.get_task("WF-REROUTE-scan")
    assert first_task is not None
    assert first_task.executor_backend == "grok-cli"
    tasks.mark_stuck(task_id=first_task.task_id, reason="executor stopped sending heartbeats")

    result = scheduler.tick(workflow_id, repo_path=str(repo_dir))

    assert result["phase"] == "running"
    node = workflows.require_node(workflow_id, "scan")
    assert node.phase == "running"
    assert node.attempt_no == 1
    assert node.task_id == "WF-REROUTE-scan-A1"
    assert node.executor_backend == "antigravity-cli"
    assert "rerouting from grok-cli to antigravity-cli" in (node.last_error or "")
    retry_task = tasks.get_task("WF-REROUTE-scan-A1")
    assert retry_task is not None
    assert retry_task.executor_backend == "antigravity-cli"


def test_scheduler_marks_workflow_ready_after_child_and_gate(tmp_path: Path, monkeypatch) -> None:
    patch_executor(monkeypatch)
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    paths = make_paths(tmp_path)
    workflows = WorkflowRepository(paths.db_path)
    workflow_id = workflows.create_workflow(make_manifest(str(repo_dir)), workflow_id="WF-READY", repo_path=str(repo_dir))
    scheduler = WorkflowScheduler(paths)
    scheduler.tick(workflow_id, repo_path=str(repo_dir))

    tasks = TaskRepository(paths.db_path)
    task = tasks.get_task("WF-READY-scan")
    assert task is not None
    receipt = tmp_path / "receipt.json"
    receipt.write_text(json.dumps({"status": "EVIDENCE_PACK"}), encoding="utf-8")
    tasks.record_artifact(task_id=task.task_id, attempt_no=task.attempt_no, artifact_type="receipt", path=str(receipt))
    tasks.mark_ready_for_review(
        task_id=task.task_id,
        terminal_source="test",
        terminal_receipt_json=json.dumps(
            {
                "status": "EVIDENCE_PACK",
                "payload": {
                    "changed_files": ["docs/scan.md"],
                    "scope_violations": [{"path": "../outside.txt"}],
                },
            }
        ),
    )

    result = scheduler.tick(workflow_id, repo_path=str(repo_dir))

    assert result["phase"] == "ready_for_review"
    workflow = workflows.require_workflow(workflow_id)
    assert workflow.evidence_path is not None
    evidence = json.loads(Path(workflow.evidence_path).read_text(encoding="utf-8"))
    assert evidence["changed_files"] == ["docs/scan.md"]
    assert evidence["scope_violations"] == [
        {"node_id": "scan", "violation": {"path": "../outside.txt"}}
    ]
