import json
from pathlib import Path

from agpair.config import AppPaths
from agpair.storage.db import ensure_database
from agpair.storage.tasks import TaskRepository
from agpair.workflows.evidence import build_workflow_evidence_pack, persist_workflow_evidence_pack
from agpair.workflows.presets import build_fanout_manifest
from agpair.workflows.schema import validate_manifest
from agpair.workflows.store import WorkflowRepository
from agpair.workflows.watch import workflow_status_payload


def test_evidence_pack_includes_lane_cards_synthesis_and_panel_result(tmp_path: Path) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    paths = AppPaths.from_root(tmp_path / ".agpair")
    ensure_database(paths.db_path)
    workflows = WorkflowRepository(paths.db_path)
    tasks = TaskRepository(paths.db_path)
    manifest = validate_manifest(
        build_fanout_manifest(
            controller="codex",
            mode="review",
            topic="Review fanout synthesis",
            lanes=["grok-cli:primary", "grok-cli:adversarial"],
            repo_path=str(repo_dir),
        ),
        require_repo_path=True,
    )
    workflow_id = workflows.create_workflow(manifest, workflow_id="WF-FANOUT", repo_path=str(repo_dir))

    report = tmp_path / "report.md"
    report.write_text("Useful report body", encoding="utf-8")
    stdout = tmp_path / "stdout.log"
    stdout.write_text("Useful but malformed stdout", encoding="utf-8")
    for node_id, artifact_type, artifact_path, receipt in [
        (
            "primary",
            "report",
            report,
            {
                "status": "READY_FOR_REVIEW",
                "payload": {
                    "report": "Useful report body",
                    "changed_files": [],
                    "scope_violations": [],
                },
            },
        ),
        ("adversarial", "stdout", stdout, None),
    ]:
        task_id = f"WF-FANOUT-{node_id}"
        tasks.create_task(
            task_id=task_id,
            repo_path=str(repo_dir),
            executor_backend="grok-cli",
            authorization_profile="local_readonly",
            completion_policy="report",
            workflow_id=workflow_id,
            workflow_node_id=node_id,
            child_role=node_id,
        )
        tasks.mark_acked(task_id=task_id, session_id=f"session-{task_id}")
        tasks.record_artifact(task_id=task_id, attempt_no=1, artifact_type=artifact_type, path=str(artifact_path))
        if receipt is not None:
            tasks.mark_ready_for_review(
                task_id=task_id,
                terminal_source="test",
                terminal_receipt_json=json.dumps(receipt),
            )
            phase = "ready_for_review"
            error = None
        else:
            tasks.mark_blocked(task_id=task_id, reason="receipt missing")
            phase = "blocked"
            error = "receipt missing"
        workflows.mark_node_phase(workflow_id, node_id, phase, task_id=task_id, error=error)

    synthesis_result = {
        "schema_version": "1",
        "workflow_id": workflow_id,
        "consensus": ["Both lanes found useful evidence."],
        "contradictions": [],
        "unique_insights": ["The adversarial lane produced stdout-only evidence."],
        "blind_spots": ["No third executor lane was available."],
        "recommended_controller_action": "use_result",
    }
    workflows.mark_node_phase(
        workflow_id,
        "synthesis",
        "ready_for_review",
        result_json=json.dumps(synthesis_result),
        evidence_json=json.dumps({"synthesis_result": synthesis_result}),
    )
    workflows.mark_node_phase(workflow_id, "gate", "ready_for_review", result_json=json.dumps({"gate": "passed"}))

    evidence = build_workflow_evidence_pack(paths, workflow_id, phase="ready_for_review")

    lanes = {card["node_id"]: card for card in evidence["lane_cards"]}
    assert lanes["primary"]["summary_excerpt"] == "Useful report body"
    assert lanes["adversarial"]["adoptable_result"] == "partial"
    assert "stdout_report_salvaged" in lanes["adversarial"]["agent_result"]["soft_warnings"]
    assert evidence["synthesis_result"]["consensus"] == ["Both lanes found useful evidence."]
    assert evidence["panel_result"]["state"] == "needs_review"
    assert evidence["panel_result"]["controller_action"] == "inspect_evidence"
    evidence_path = persist_workflow_evidence_pack(paths, workflow_id, phase="ready_for_review")
    workflows.mark_workflow_phase(workflow_id, "ready_for_review", evidence_path=evidence_path)
    payload = workflow_status_payload(paths, workflow_id)
    assert payload["panel_result"]["state"] == "needs_review"


def test_scheduler_passes_lane_cards_to_synthesis_node(tmp_path: Path) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    paths = AppPaths.from_root(tmp_path / ".agpair")
    ensure_database(paths.db_path)
    workflows = WorkflowRepository(paths.db_path)
    tasks = TaskRepository(paths.db_path)
    manifest = validate_manifest(
        build_fanout_manifest(
            controller="generic",
            mode="review",
            topic="Review fanout synthesis",
            lanes=["grok-cli:primary"],
            repo_path=str(repo_dir),
        ),
        require_repo_path=True,
    )
    workflow_id = workflows.create_workflow(manifest, workflow_id="WF-CONTEXT", repo_path=str(repo_dir))
    task_id = "WF-CONTEXT-primary"
    tasks.create_task(
        task_id=task_id,
        repo_path=str(repo_dir),
        executor_backend="grok-cli",
        authorization_profile="local_readonly",
        completion_policy="report",
        workflow_id=workflow_id,
        workflow_node_id="primary",
        child_role="primary",
    )
    tasks.mark_acked(task_id=task_id, session_id="session-primary")
    tasks.mark_ready_for_review(
        task_id=task_id,
        terminal_source="test",
        terminal_receipt_json=json.dumps({"status": "READY_FOR_REVIEW", "payload": {"report": "Useful report"}}),
    )
    workflows.mark_node_phase(workflow_id, "primary", "ready_for_review", task_id=task_id)
    workflow = workflows.require_workflow(workflow_id)
    synthesis = workflows.require_node(workflow_id, "synthesis")

    from agpair.workflows.scheduler import WorkflowScheduler

    body = WorkflowScheduler(paths)._node_body(workflow, synthesis)

    assert '"lane_cards"' in body
    assert '"node_id": "primary"' in body


def test_gate_blocks_missing_synthesis_result(tmp_path: Path) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    paths = AppPaths.from_root(tmp_path / ".agpair")
    ensure_database(paths.db_path)
    workflows = WorkflowRepository(paths.db_path)
    manifest = validate_manifest(
        build_fanout_manifest(
            controller="generic",
            mode="review",
            topic="Review fanout synthesis",
            lanes=["grok-cli:primary"],
            repo_path=str(repo_dir),
        ),
        require_repo_path=True,
    )
    workflow_id = workflows.create_workflow(manifest, workflow_id="WF-GATE", repo_path=str(repo_dir))
    workflows.mark_node_phase(workflow_id, "primary", "ready_for_review")
    workflows.mark_node_phase(workflow_id, "synthesis", "ready_for_review", result_json=json.dumps({"status": "ok"}))

    from agpair.workflows.scheduler import WorkflowScheduler

    WorkflowScheduler(paths).tick(workflow_id, repo_path=str(repo_dir), dispatch=False)

    gate = workflows.require_node(workflow_id, "gate")
    assert gate.phase == "blocked"
    assert "missing synthesis result" in (gate.error or "")
