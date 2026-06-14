from __future__ import annotations

import json
from pathlib import Path

from agpair.config import AppPaths
from agpair.storage.db import ensure_database
from agpair.storage.tasks import TaskRepository
from agpair.workflows.schema import validate_manifest
from agpair.workflows.store import WorkflowRepository
from agpair.workflows.watch import workflow_event_payload, workflow_status_payload


def test_workflow_watch_payload_reports_terminal_node_and_stable_cursor(tmp_path: Path) -> None:
    paths = AppPaths.from_root(tmp_path / ".agpair")
    ensure_database(paths.db_path)
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    manifest = validate_manifest(
        {
            "version": 1,
            "name": "workflow-watch",
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
                    "executor": "antigravity-cli",
                },
                {"id": "gate", "kind": "gate", "depends_on": ["scan"]},
            ],
        },
        require_repo_path=True,
        repo_path=str(repo_dir),
    )
    workflows = WorkflowRepository(paths.db_path)
    tasks = TaskRepository(paths.db_path)
    workflow_id = workflows.create_workflow(manifest, workflow_id="WF-WATCH", repo_path=str(repo_dir))
    tasks.create_task(task_id="WF-WATCH-scan", repo_path=str(repo_dir), executor_backend="antigravity-cli")
    tasks.update_attempt_adoption(
        task_id="WF-WATCH-scan",
        attempt_no=1,
        protocol_warnings_json=json.dumps(["schema_version_alias"]),
        protocol_errors_json="[]",
        adoptable_result="partial",
        adoption_evidence_json=json.dumps(
            {
                "adoptable_result": "partial",
                "blockers": [],
                "warnings": ["schema_version_alias"],
                "evidence": {"has_report": True},
            }
        ),
    )
    evidence_path = paths.root / "workflows" / workflow_id / "evidence.json"
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_path.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "ok": True,
                "recovery_decision": {
                    "action": "inspect_evidence",
                    "reason": "workflow evidence is ready for controller inspection",
                    "next_executor": None,
                    "command": None,
                    "alternative_command": None,
                },
            }
        ),
        encoding="utf-8",
    )
    workflows.mark_node_phase(
        workflow_id,
        "scan",
        "ready_for_review",
        task_id="WF-WATCH-scan",
        evidence_json=json.dumps({"task_id": "WF-WATCH-scan"}),
    )
    workflows.mark_node_phase(
        workflow_id,
        "gate",
        "ready_for_review",
        evidence_json=json.dumps({"gate": "passed"}),
    )
    workflows.mark_workflow_phase(
        workflow_id,
        "ready_for_review",
        evidence_path=str(evidence_path),
        result_json=json.dumps({"schema_version": "1", "ok": True}),
    )

    status = workflow_status_payload(paths, workflow_id)
    event = workflow_event_payload(paths, workflow_id)
    unchanged = workflow_event_payload(paths, workflow_id, previous_cursor=status["cursor"])

    assert status["phase"] == "ready_for_review"
    assert status["evidence_path"] == str(evidence_path)
    assert status["result"] == {"schema_version": "1", "ok": True}
    assert status["recovery_decision"]["action"] == "inspect_evidence"
    scan = next(node for node in status["nodes"] if node["node_id"] == "scan")
    assert scan["protocol_result"]["warnings"] == ["schema_version_alias"]
    assert scan["adoption_result"]["adoptable_result"] == "partial"
    assert event["event"] == "node_state_changed"
    assert event["node_id"] == "gate"
    assert event["node_phase"] == "ready_for_review"
    assert event["evidence_path"] == str(evidence_path)
    assert event["recovery_decision"]["action"] == "inspect_evidence"
    assert unchanged["event"] == "unchanged"
