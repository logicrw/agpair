from __future__ import annotations

import json

import typer

from agpair.executors.policy import EXECUTOR_SPECS, executor_health_snapshot, resolve_controller_policy

app = typer.Typer(no_args_is_help=True)


@app.command("show")
def policy_show(
    controller: str = typer.Option("generic", "--controller", help="Controller id: codex, claude-code, or generic."),
    executor: str | None = typer.Option(None, "--executor", help="Requested executor id."),
    allow_self_executor: bool = typer.Option(False, "--allow-self-executor", help="Permit controller to delegate to the same external CLI."),
    require_available: bool = typer.Option(False, "--require-available", help="Filter executors without detected binaries."),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
) -> None:
    decision = resolve_controller_policy(
        controller=controller,
        requested_executor=executor,
        allow_self_executor=allow_self_executor,
        require_available=require_available,
    )
    payload = {
        "ok": decision.rejected_executor is None,
        "controller_policy": decision.to_dict(),
        "executor_specs": {key: spec.to_dict() for key, spec in EXECUTOR_SPECS.items()},
        "executor_health": executor_health_snapshot(),
    }
    if json_output:
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return
    typer.echo(f"controller: {payload['controller_policy']['controller']}")
    typer.echo(f"selected_executor: {payload['controller_policy']['selected_executor']}")
    typer.echo("eligible_executors: " + ", ".join(payload["controller_policy"]["eligible_executors"]))
    if payload["controller_policy"]["suppressed_executors"]:
        typer.echo("suppressed_executors: " + ", ".join(payload["controller_policy"]["suppressed_executors"]))
    if payload["controller_policy"]["reasons"]:
        for reason in payload["controller_policy"]["reasons"]:
            typer.echo(f"reason: {reason}")


@app.command("validate")
def policy_validate(
    controller: str = typer.Option("generic", "--controller"),
    executor: str | None = typer.Option(None, "--executor"),
    allow_self_executor: bool = typer.Option(False, "--allow-self-executor"),
) -> None:
    decision = resolve_controller_policy(
        controller=controller,
        requested_executor=executor,
        allow_self_executor=allow_self_executor,
    )
    if decision.rejected_executor:
        typer.echo(f"rejected: {decision.rejected_executor}", err=True)
        raise typer.Exit(code=1)
    typer.echo(decision.selected_executor or "none")
