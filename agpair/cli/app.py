from __future__ import annotations

import typer

from agpair.cli.daemon import app as daemon_app
from agpair.cli.doctor import emit_doctor_json
from agpair.cli.task import app as task_app
from agpair.config import AppPaths

app = typer.Typer(no_args_is_help=True)

app.add_typer(task_app, name="task")
app.add_typer(daemon_app, name="daemon")


@app.command("doctor")
def doctor(
    repo_path: str | None = typer.Option(None, "--repo-path"),
    fresh: bool = typer.Option(False, "--fresh", help="Bypass cache and force a full re-probe"),
) -> None:
    typer.echo(emit_doctor_json(AppPaths.default(), repo_path=repo_path, fresh=fresh))


@app.command()
def cleanup(
    older_than_days: int = typer.Option(30, "--older-than", help="Delete data older than this many days"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would be deleted without deleting"),
) -> None:
    """Remove old journals, receipts, and terminal tasks to reclaim space."""
    from datetime import UTC, datetime, timedelta

    from agpair.storage.db import ensure_database
    from agpair.storage.journal import JournalRepository
    from agpair.storage.receipts import ReceiptRepository
    from agpair.storage.tasks import TaskRepository

    paths = AppPaths.default()
    ensure_database(paths.db_path)
    cutoff = (datetime.now(UTC) - timedelta(days=older_than_days)).isoformat().replace("+00:00", "Z")

    journals = JournalRepository(paths.db_path)
    receipts = ReceiptRepository(paths.db_path)
    tasks = TaskRepository(paths.db_path)

    if dry_run:
        j = journals.count_older_than(cutoff)
        r = receipts.count_older_than(cutoff)
        t = tasks.count_terminal_older_than(cutoff)
        typer.echo(f"Dry run — data older than {older_than_days} days (before {cutoff}):")
        typer.echo(f"  journals: {j} would be deleted")
        typer.echo(f"  receipts: {r} would be deleted")
        typer.echo(f"  terminal tasks: {t} would be deleted")
        return

    j = journals.delete_older_than(cutoff)
    r = receipts.delete_older_than(cutoff)
    t = tasks.delete_terminal_older_than(cutoff)

    typer.echo(f"Cleaned up data older than {older_than_days} days:")
    typer.echo(f"  journals: {j} deleted")
    typer.echo(f"  receipts: {r} deleted")
    typer.echo(f"  terminal tasks: {t} deleted")


@app.command("inspect")
def inspect(
    repo_path: str = typer.Option(..., "--repo-path", help="The repository path to inspect."),
    task_id: str | None = typer.Option(None, "--task-id", help="Optionally focus on a specific task ID."),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
) -> None:
    import json
    from agpair.cli.doctor import build_doctor_report
    from agpair.cli.task import build_task_payload
    from agpair.storage.db import ensure_database
    from agpair.storage.journal import JournalRepository
    from agpair.storage.tasks import TaskRepository

    paths = AppPaths.default()
    ensure_database(paths.db_path)

    doctor_report = build_doctor_report(paths, repo_path=repo_path, fresh=False)
    tasks = TaskRepository(paths.db_path)
    
    task_record = tasks.get_task(task_id) if task_id else tasks.get_most_relevant_active_task(repo_path)
    task_payload = build_task_payload(paths, task_record) if task_record else None

    if json_output:
        out = {
            "repo_path": repo_path,
            "bridge": {
                "reachable": doctor_report.get("repo_bridge_reachable"),
                "session_ready": doctor_report.get("repo_bridge_session_ready"),
                "pending_task_count": doctor_report.get("repo_bridge_pending_task_count"),
                "concurrency_policy": doctor_report.get("repo_bridge_concurrency_policy"),
            },
            "task": None
        }
        if task_payload:
            task_dict = {
                "task_id": task_payload["task_id"],
                "phase": task_payload["phase"],
                "session_id": task_payload["session_id"],
                "attempt_no": task_payload["attempt_no"],
                "retry_recommended": task_payload["retry_recommended"],
                "last_heartbeat_at": task_payload["last_heartbeat_at"],
                "last_workspace_activity_at": task_payload["last_workspace_activity_at"],
            }
            if task_payload.get("terminal_receipt"):
                task_dict["latest_receipt_summary"] = task_payload["terminal_receipt"].get("summary")
            if task_payload.get("committed_result"):
                task_dict["committed_result"] = task_payload["committed_result"]
            if task_payload.get("failure_context"):
                task_dict["failure_context"] = task_payload["failure_context"]
                
            if not task_payload.get("terminal_receipt"):
                j = JournalRepository(paths.db_path)
                rows = j.tail(task_payload["task_id"], limit=1)
                if rows:
                    r = rows[0]
                    body = r.body
                    if len(body) > 100:
                        body = body[:97] + "..."
                    task_dict["latest_journal_event"] = {"event": r.event, "created_at": r.created_at, "summary": body}

            out["task"] = task_dict

        typer.echo(json.dumps(out, ensure_ascii=False, indent=2))
        return

    # Human readable text
    typer.echo(f"=== Inspect: {repo_path} ===")
    
    if doctor_report.get("repo_bridge_session_ready"):
        typer.echo("Bridge: Ready")
    else:
        err = doctor_report.get("repo_bridge_warning") or doctor_report.get("repo_bridge_error") or "Not Responding"
        typer.echo(f"Bridge: {err}")
    
    typer.echo(f"Pending Bridge Tasks: {doctor_report.get('repo_bridge_pending_task_count')}")

    if not task_record:
        typer.echo("\nActive Task: None")
    else:
        typer.echo(f"\nTask ID:     {task_record.task_id} (phase: {task_record.phase})")
        typer.echo(f"Session ID:  {task_record.antigravity_session_id}")
        typer.echo(f"Attempt No:  {task_record.attempt_no} (Retry recommended: {task_record.retry_recommended})")
        if task_payload:
            if task_payload["last_workspace_activity_at"]:
                typer.echo(f"Workspace:   {task_payload['last_workspace_activity_at']}")
            if task_payload["last_heartbeat_at"]:
                typer.echo(f"Heartbeat:   {task_payload['last_heartbeat_at']}")
            if task_payload.get("failure_context"):
                typer.echo(f"Blocked:     {task_payload['failure_context'].get('summary')}")
            if task_payload.get("committed_result"):
                typer.echo(f"Committed:   Yes")



def main() -> None:
    app()


if __name__ == "__main__":
    main()
