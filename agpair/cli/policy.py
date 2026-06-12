from __future__ import annotations

import json

import typer

from agpair.config import AppPaths
from agpair.executors.config import ExecutorPolicyConfigError, ExecutorPolicyManager
from agpair.executors.policy import EXECUTOR_SPECS, executor_health_snapshot, resolve_controller_policy

app = typer.Typer(no_args_is_help=True)


def _manager() -> ExecutorPolicyManager:
    return ExecutorPolicyManager(AppPaths.default().executor_policy_path)


def _policy_payload(
    *,
    controller: str,
    executor: str | None = None,
    allow_self_executor: bool = False,
    require_available: bool = False,
) -> dict:
    manager = _manager()
    overlay = manager.read()
    decision = resolve_controller_policy(
        controller=controller,
        requested_executor=executor,
        allow_self_executor=allow_self_executor,
        require_available=require_available,
        overlay=overlay,
    )
    return {
        "ok": decision.rejected_executor is None,
        "policy_path": str(manager.path),
        "runtime_overlay": overlay.to_dict(),
        "controller_policy": decision.to_dict(),
        "executor_specs": {key: spec.to_dict() for key, spec in EXECUTOR_SPECS.items()},
        "executor_health": executor_health_snapshot(),
    }


def _echo_payload(payload: dict, *, json_output: bool) -> None:
    if json_output:
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return
    policy = payload["controller_policy"]
    typer.echo(f"policy_path: {payload['policy_path']}")
    typer.echo(f"controller: {policy['controller']}")
    typer.echo(f"selected_executor: {policy['selected_executor']}")
    typer.echo("eligible_executors: " + ", ".join(policy["eligible_executors"]))
    if policy["suppressed_executors"]:
        typer.echo("suppressed_executors: " + ", ".join(policy["suppressed_executors"]))
    if policy["skipped_executors"]:
        for item in policy["skipped_executors"]:
            typer.echo(f"skipped: {item['executor_id']} [{item['blocker_type']}] {item['reason']}")
    if policy["reasons"]:
        for reason in policy["reasons"]:
            typer.echo(f"reason: {reason}")


def _handle_config_error(exc: Exception) -> None:
    typer.echo(f"policy error: {exc}", err=True)
    raise typer.Exit(code=1)


@app.command("show")
def policy_show(
    controller: str = typer.Option("generic", "--controller", help="Controller id: codex, claude-code, or generic."),
    executor: str | None = typer.Option(None, "--executor", help="Requested executor id."),
    allow_self_executor: bool = typer.Option(False, "--allow-self-executor", help="Permit controller to delegate to the same external CLI."),
    require_available: bool = typer.Option(False, "--require-available", help="Filter executors without detected binaries."),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
) -> None:
    try:
        _echo_payload(
            _policy_payload(
                controller=controller,
                executor=executor,
                allow_self_executor=allow_self_executor,
                require_available=require_available,
            ),
            json_output=json_output,
        )
    except (ExecutorPolicyConfigError, ValueError) as exc:
        _handle_config_error(exc)


@app.command("list")
def policy_list(
    controller: str = typer.Option("generic", "--controller", help="Controller id: codex, claude-code, or generic."),
    allow_self_executor: bool = typer.Option(False, "--allow-self-executor", help="Permit controller to delegate to the same external CLI."),
    require_available: bool = typer.Option(False, "--require-available", help="Filter executors without detected binaries."),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
) -> None:
    policy_show(
        controller=controller,
        executor=None,
        allow_self_executor=allow_self_executor,
        require_available=require_available,
        json_output=json_output,
    )


@app.command("disable")
def policy_disable(
    executor: str = typer.Argument(..., help="Executor id to disable."),
    controller: str = typer.Option("generic", "--controller", help="Controller-specific disable scope."),
    global_scope: bool = typer.Option(False, "--global", help="Disable for every controller."),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
) -> None:
    try:
        _manager().disable(executor, controller=controller, global_scope=global_scope)
        _echo_payload(_policy_payload(controller=controller), json_output=json_output)
    except (ExecutorPolicyConfigError, ValueError) as exc:
        _handle_config_error(exc)


@app.command("enable")
def policy_enable(
    executor: str = typer.Argument(..., help="Executor id to enable."),
    controller: str = typer.Option("generic", "--controller", help="Controller-specific enable scope."),
    global_scope: bool = typer.Option(False, "--global", help="Enable for every controller by removing the global disable entry."),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
) -> None:
    try:
        _manager().enable(executor, controller=controller, global_scope=global_scope)
        _echo_payload(_policy_payload(controller=controller), json_output=json_output)
    except (ExecutorPolicyConfigError, ValueError) as exc:
        _handle_config_error(exc)


@app.command("priority")
def policy_priority(
    executor_ids: list[str] = typer.Argument(..., help="Ordered executor ids for this controller."),
    controller: str = typer.Option("generic", "--controller", help="Controller-specific priority scope."),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
) -> None:
    try:
        _manager().set_priority(executor_ids, controller=controller)
        _echo_payload(_policy_payload(controller=controller), json_output=json_output)
    except (ExecutorPolicyConfigError, ValueError) as exc:
        _handle_config_error(exc)


@app.command("reset")
def policy_reset(
    controller: str = typer.Option("generic", "--controller", help="Controller-specific policy scope."),
    global_scope: bool = typer.Option(False, "--global", help="Reset global policy instead of controller policy."),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
) -> None:
    try:
        _manager().reset(controller=controller, global_scope=global_scope)
        _echo_payload(_policy_payload(controller=controller), json_output=json_output)
    except (ExecutorPolicyConfigError, ValueError) as exc:
        _handle_config_error(exc)


@app.command("validate")
def policy_validate(
    controller: str = typer.Option("generic", "--controller"),
    executor: str | None = typer.Option(None, "--executor"),
    allow_self_executor: bool = typer.Option(False, "--allow-self-executor"),
) -> None:
    try:
        decision = resolve_controller_policy(
            controller=controller,
            requested_executor=executor,
            allow_self_executor=allow_self_executor,
        )
        if decision.rejected_executor:
            typer.echo(f"rejected: {decision.rejected_executor}", err=True)
            raise typer.Exit(code=1)
        typer.echo(decision.selected_executor or "none")
    except (ExecutorPolicyConfigError, ValueError) as exc:
        _handle_config_error(exc)
