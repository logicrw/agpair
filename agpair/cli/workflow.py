from __future__ import annotations

import json
import time
from pathlib import Path

import typer

from agpair.config import AppPaths
from agpair.models import validate_authorization_profile
from agpair.storage.db import ensure_database
from agpair.targets import resolve_repo_path
from agpair.workflows.schema import load_manifest_file, validate_manifest
from agpair.workflows.scheduler import TERMINAL_WORKFLOW_PHASES, WorkflowScheduler
from agpair.workflows.store import WorkflowNotFoundError, WorkflowRepository
from agpair.workflows.watch import workflow_event_payload, workflow_status_payload

app = typer.Typer(no_args_is_help=True)


def _paths() -> AppPaths:
    paths = AppPaths.default()
    ensure_database(paths.db_path)
    return paths


def _emit_json(payload: dict) -> None:
    typer.echo(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def _resolve_workflow_repo_path(
    *,
    manifest_repo_path: str | None = None,
    repo_path: str | None = None,
    target: str | None = None,
    paths: AppPaths,
) -> str:
    resolved = resolve_repo_path(repo_path, target, paths=paths)
    if resolved:
        return str(Path(resolved).expanduser().resolve())
    if manifest_repo_path:
        return str(Path(manifest_repo_path).expanduser().resolve())
    raise typer.BadParameter("Either --repo-path, --target, manifest repo_path, or current git root is required.")


def _wait_for_workflow(paths: AppPaths, workflow_id: str, *, repo_path: str, interval_seconds: float, timeout_seconds: float) -> dict:
    deadline = time.monotonic() + timeout_seconds
    scheduler = WorkflowScheduler(paths)
    status = workflow_status_payload(paths, workflow_id)
    while status.get("phase") not in TERMINAL_WORKFLOW_PHASES and time.monotonic() < deadline:
        scheduler.tick(workflow_id, repo_path=repo_path)
        status = workflow_status_payload(paths, workflow_id)
        if status.get("phase") in TERMINAL_WORKFLOW_PHASES:
            break
        time.sleep(interval_seconds)
    if status.get("phase") not in TERMINAL_WORKFLOW_PHASES:
        status["ok"] = False
        status["error"] = "timeout"
    return status


@app.command("validate")
def validate(
    manifest_file: Path = typer.Option(..., "--file", "--manifest-file", "-f", exists=True, dir_okay=False, readable=True),
    allow_large_workflow: bool = typer.Option(False, "--allow-large-workflow", help="Allow more than 100 child-capable nodes."),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
) -> None:
    """Validate a workflow manifest without creating workflow records."""
    try:
        manifest = load_manifest_file(manifest_file, allow_large_workflow=allow_large_workflow)
    except Exception as exc:
        payload = {"ok": False, "error": str(exc), "manifest_file": str(manifest_file)}
        if json_output:
            _emit_json(payload)
        else:
            typer.echo(str(exc), err=True)
        raise typer.Exit(code=1)
    payload = {
        "ok": True,
        "manifest_file": str(manifest_file),
        "manifest": manifest.manifest,
        "version": manifest.version,
        "controller": manifest.controller,
        "name": manifest.name,
        "node_count": len(manifest.nodes),
    }
    if json_output:
        _emit_json(payload)
        return
    typer.echo(json.dumps(manifest.manifest, ensure_ascii=False, indent=2, sort_keys=True))


@app.command("start")
def start(
    manifest_file: Path = typer.Option(..., "--file", "--manifest-file", "-f", exists=True, dir_okay=False, readable=True),
    controller: str | None = typer.Option(None, "--controller", help="Override manifest controller."),
    repo_path: str | None = typer.Option(None, "--repo-path", help="Repository path for delegated tasks."),
    target: str | None = typer.Option(None, "--target", help="Target alias alternative to --repo-path."),
    workflow_id: str | None = typer.Option(None, "--workflow-id", help="Optional deterministic workflow id."),
    allow_large_workflow: bool = typer.Option(False, "--allow-large-workflow", help="Allow more than 100 child-capable nodes."),
    wait: bool = typer.Option(False, "--wait", help="Wait until terminal workflow phase."),
    interval_seconds: float = typer.Option(2.0, "--interval-seconds", min=0.1),
    timeout_seconds: float = typer.Option(3600.0, "--timeout-seconds", min=1.0),
    no_dispatch: bool = typer.Option(False, "--no-dispatch", help="Create records without invoking executors."),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
) -> None:
    """Create a validated workflow and run the scheduler."""
    paths = _paths()
    try:
        manifest = load_manifest_file(manifest_file, allow_large_workflow=allow_large_workflow)
        manifest_dict = dict(manifest.manifest)
        if controller:
            manifest_dict["controller"] = controller
        effective_repo_path = _resolve_workflow_repo_path(
            manifest_repo_path=manifest.repo_path,
            repo_path=repo_path,
            target=target,
            paths=paths,
        )
        manifest = validate_manifest(
            manifest_dict,
            allow_large_workflow=allow_large_workflow,
            require_repo_path=True,
            repo_path=effective_repo_path,
        )
        repo = WorkflowRepository(paths.db_path)
        final_workflow_id = repo.create_workflow(manifest, workflow_id=workflow_id, repo_path=effective_repo_path)
        tick = WorkflowScheduler(paths).tick(final_workflow_id, repo_path=effective_repo_path, dispatch=not no_dispatch)
        payload = _wait_for_workflow(paths, final_workflow_id, repo_path=effective_repo_path, interval_seconds=interval_seconds, timeout_seconds=timeout_seconds) if wait and not no_dispatch else workflow_status_payload(paths, final_workflow_id)
        payload.update({"ok": bool(payload.get("ok", True)), "tick": tick, "repo_path": effective_repo_path})
    except Exception as exc:
        payload = {"ok": False, "error": str(exc), "manifest_file": str(manifest_file)}
        if json_output:
            _emit_json(payload)
        else:
            typer.echo(str(exc), err=True)
        raise typer.Exit(code=1)
    if json_output:
        _emit_json(payload)
        return
    typer.echo(f"workflow_id: {payload['workflow_id']}")
    typer.echo(f"phase: {payload['phase']}")
    typer.echo(f"dispatched: {payload['tick']['dispatched']}")


@app.command("status")
def status(
    workflow_id: str = typer.Argument(..., help="Workflow id."),
    repo_path: str | None = typer.Option(None, "--repo-path", help="Repository path for scheduler ticks."),
    target: str | None = typer.Option(None, "--target", help="Target alias alternative to --repo-path."),
    tick: bool = typer.Option(False, "--tick", help="Run one scheduler tick before reporting."),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
) -> None:
    paths = _paths()
    try:
        if tick:
            workflow = WorkflowRepository(paths.db_path).require_workflow(workflow_id)
            effective_repo_path = _resolve_workflow_repo_path(
                manifest_repo_path=workflow.repo_path,
                repo_path=repo_path,
                target=target,
                paths=paths,
            )
            WorkflowScheduler(paths).tick(workflow_id, repo_path=effective_repo_path)
        payload = workflow_status_payload(paths, workflow_id)
    except WorkflowNotFoundError as exc:
        payload = {"ok": False, "error": str(exc), "workflow_id": workflow_id}
        if json_output:
            _emit_json(payload)
        else:
            typer.echo(str(exc), err=True)
        raise typer.Exit(code=1)
    except Exception as exc:
        payload = {"ok": False, "error": str(exc), "workflow_id": workflow_id}
        if json_output:
            _emit_json(payload)
        else:
            typer.echo(str(exc), err=True)
        raise typer.Exit(code=1)
    if json_output:
        _emit_json(payload)
        return
    typer.echo(f"workflow_id: {payload['workflow_id']}")
    typer.echo(f"phase: {payload['phase']}")
    if payload.get("evidence_path"):
        typer.echo(f"evidence_path: {payload['evidence_path']}")
    for node in payload["nodes"]:
        typer.echo(f"{node['node_id']}: {node['phase']} task={node.get('task_id') or '-'}")


@app.command("list")
def list_workflows(
    repo_path: str | None = typer.Option(None, "--repo-path", help="Filter by repository path."),
    target: str | None = typer.Option(None, "--target", help="Target alias alternative to --repo-path."),
    phase: str | None = typer.Option(None, "--phase", help="Filter by workflow phase."),
    limit: int = typer.Option(20, "--limit", min=1, max=200),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
) -> None:
    paths = _paths()
    resolved_repo_path = resolve_repo_path(repo_path, target, paths=paths)
    rows = WorkflowRepository(paths.db_path).list_workflows(phase=phase, repo_path=resolved_repo_path, limit=limit)
    payload = {
        "ok": True,
        "workflows": [
            {
                "workflow_id": row.workflow_id,
                "repo_path": row.repo_path,
                "name": row.name,
                "controller": row.controller,
                "phase": row.phase,
                "evidence_path": row.evidence_path,
                "created_at": row.created_at,
                "updated_at": row.updated_at,
                "finished_at": row.finished_at,
                "error": row.error or row.stuck_reason,
            }
            for row in rows
        ],
    }
    if json_output:
        _emit_json(payload)
        return
    if not rows:
        typer.echo("No workflows found.")
        return
    for row in payload["workflows"]:
        typer.echo(f"{row['workflow_id']} {row['phase']} {row.get('name') or ''}".rstrip())


@app.command("cancel")
def cancel(
    workflow_id: str = typer.Argument(..., help="Workflow id."),
    reason: str = typer.Option("cancelled by operator", "--reason", help="Cancellation reason."),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
) -> None:
    """Abandon a workflow and all pending, dispatching, or running nodes."""
    paths = _paths()
    try:
        from agpair.workflows.control import cancel_workflow

        payload = cancel_workflow(paths, workflow_id, reason=reason)
    except Exception as exc:
        payload = {"ok": False, "error": str(exc), "workflow_id": workflow_id}
        if json_output:
            _emit_json(payload)
        else:
            typer.echo(str(exc), err=True)
        raise typer.Exit(code=1)
    if json_output:
        _emit_json(payload)
        return
    typer.echo(f"workflow_id: {payload['workflow_id']}")
    typer.echo(f"phase: {payload['phase']}")


@app.command("retry-node")
def retry_node(
    workflow_id: str = typer.Argument(..., help="Workflow id."),
    node_id: str = typer.Argument(..., help="Workflow node id."),
    authorization_profile: str | None = typer.Option(None, "--authorization-profile", help="Authorization profile for the new child attempt."),
    executor: str | None = typer.Option(None, "--executor", help="Requested executor for the new child attempt."),
    repo_path: str | None = typer.Option(None, "--repo-path", help="Repository path for scheduler tick."),
    target: str | None = typer.Option(None, "--target", help="Target alias alternative to --repo-path."),
    no_dispatch: bool = typer.Option(False, "--no-dispatch", help="Reset node without invoking executors."),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
) -> None:
    paths = _paths()
    workflows = WorkflowRepository(paths.db_path)
    try:
        workflow = workflows.require_workflow(workflow_id)
        if authorization_profile:
            authorization_profile = validate_authorization_profile(authorization_profile)
        if workflows.get_node(workflow_id, node_id) is None:
            raise ValueError(f"workflow node not found: {node_id}")
        workflows.reset_node_for_retry(
            workflow_id,
            node_id,
            authorization_profile=authorization_profile,
            executor_backend=executor,
            reason="operator retry-node",
        )
        workflows.mark_workflow_phase(workflow_id, "running", error=None)
        effective_repo_path = _resolve_workflow_repo_path(
            manifest_repo_path=workflow.repo_path,
            repo_path=repo_path,
            target=target,
            paths=paths,
        )
        tick_payload = WorkflowScheduler(paths).tick(workflow_id, repo_path=effective_repo_path, dispatch=not no_dispatch)
        payload = workflow_status_payload(paths, workflow_id)
        payload.update({"ok": True, "tick": tick_payload})
    except Exception as exc:
        payload = {"ok": False, "error": str(exc), "workflow_id": workflow_id, "node_id": node_id}
        if json_output:
            _emit_json(payload)
        else:
            typer.echo(str(exc), err=True)
        raise typer.Exit(code=1)
    if json_output:
        _emit_json(payload)
        return
    typer.echo(f"workflow_id: {payload['workflow_id']}")
    typer.echo(f"phase: {payload['phase']}")
    typer.echo(f"dispatched: {payload['tick']['dispatched']}")


@app.command("watch")
def watch(
    workflow_id: str = typer.Argument(..., help="Workflow id."),
    cursor: str | None = typer.Option(None, "--cursor", help="Suppress output until status cursor changes."),
    once: bool = typer.Option(False, "--once", help="Emit one event and exit."),
    interval: float = typer.Option(2.0, "--interval", min=0.1, help="Polling interval seconds."),
    json_output: bool = typer.Option(True, "--json/--text", help="Emit JSON lines by default."),
) -> None:
    paths = _paths()
    last_cursor = cursor
    while True:
        payload = workflow_event_payload(paths, workflow_id, previous_cursor=last_cursor)
        current_cursor = str(payload.get("cursor") or "")
        if payload.get("event") != "unchanged":
            if json_output:
                typer.echo(json.dumps(payload, ensure_ascii=False, sort_keys=True))
            else:
                typer.echo(f"{payload['workflow_id']} {payload['phase']} cursor={current_cursor}")
            last_cursor = current_cursor
        if once:
            return
        time.sleep(interval)
