from pathlib import Path

from agpair.config import AppPaths
from agpair.storage.db import ensure_database
from agpair.workflows.schema import validate_manifest
from agpair.workflows.store import WorkflowRepository


def make_paths(tmp_path: Path) -> AppPaths:
    paths = AppPaths.from_root(tmp_path / ".agpair")
    ensure_database(paths.db_path)
    return paths


def manifest():
    return validate_manifest(
        {
            "version": 1,
            "name": "store-test",
            "controller": "codex",
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
                {
                    "id": "gate",
                    "kind": "gate",
                    "depends_on": ["scan"],
                },
            ],
        },
        require_repo_path=True,
        repo_path="/tmp/repo",
    )


def test_create_workflow_persists_nodes_and_limits(tmp_path: Path) -> None:
    paths = make_paths(tmp_path)
    repo = WorkflowRepository(paths.db_path)
    workflow_id = repo.create_workflow(manifest(), workflow_id="WF-TEST", repo_path="/tmp/repo")

    workflow = repo.require_workflow(workflow_id)
    nodes = repo.list_nodes(workflow_id)

    assert workflow.repo_path == "/tmp/repo"
    assert workflow.phase == "new"
    assert workflow.limits()["max_parallel_tasks"] == 4
    assert [node.node_id for node in nodes] == ["gate", "scan"]
    assert repo.require_node(workflow_id, "scan").requested_completion_policy == "report"


def test_reset_node_for_retry_increments_attempt(tmp_path: Path) -> None:
    paths = make_paths(tmp_path)
    repo = WorkflowRepository(paths.db_path)
    workflow_id = repo.create_workflow(manifest(), workflow_id="WF-TEST", repo_path="/tmp/repo")

    updated = repo.reset_node_for_retry(
        workflow_id,
        "scan",
        authorization_profile="local_mutating",
        reason="approval expanded",
    )

    assert updated.attempt_no == 1
    assert updated.authorization_profile == "local_mutating"
    assert updated.phase == "pending"
