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


def main() -> None:
    app()


if __name__ == "__main__":
    main()
