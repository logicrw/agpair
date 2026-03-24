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


def main() -> None:
    app()


if __name__ == "__main__":
    main()
