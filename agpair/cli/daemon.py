from __future__ import annotations

import json
import signal

import typer

from agpair.config import AppPaths
from agpair.daemon.loop import run_forever, run_once
from agpair.daemon.process import daemon_status, start_background_daemon, stop_background_daemon
from agpair.runtime_conflicts import (
    DesktopReaderLockError,
    acquire_shared_desktop_reader_lock,
    detect_supervisor_desktop_reader_conflict,
    release_shared_desktop_reader_lock,
)

app = typer.Typer(no_args_is_help=True)


def _paths() -> AppPaths:
    return AppPaths.default()


def _fail_on_desktop_reader_conflict(*, force: bool) -> None:
    conflict = detect_supervisor_desktop_reader_conflict()
    if conflict is None:
        return
    if force and conflict.get("source") != "lock":
        return
    pid = conflict.get("pid")
    command = conflict.get("command") or "desktop_agent_bus_watch.py"
    typer.echo(
        (
            "refusing to start agpair daemon while another desktop watcher is already "
            f"claiming code->desktop receipts (pid={pid}, command={command}). "
            "Stop the existing desktop watcher first, or pass --force if you know "
            "you are in a dedicated standalone environment."
        ),
        err=True,
    )
    raise typer.Exit(code=1)


@app.command("run")
def run_daemon(
    interval_ms: int = typer.Option(1000, "--interval-ms"),
    timeout_seconds: int = typer.Option(1800, "--timeout-seconds"),
    once: bool = typer.Option(False, "--once"),
    force: bool = typer.Option(False, "--force"),
) -> None:
    paths = _paths()
    _fail_on_desktop_reader_conflict(force=force)
    previous_sigint = signal.getsignal(signal.SIGINT)
    previous_sigterm = signal.getsignal(signal.SIGTERM)

    def _handle_stop(_signum, _frame) -> None:
        raise SystemExit(0)

    signal.signal(signal.SIGINT, _handle_stop)
    signal.signal(signal.SIGTERM, _handle_stop)
    try:
        acquire_shared_desktop_reader_lock(paths.shared_desktop_lock_path)
        if once:
            run_once(paths, timeout_seconds=timeout_seconds)
            return
        run_forever(paths, interval_ms=interval_ms, timeout_seconds=timeout_seconds)
    except DesktopReaderLockError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1)
    finally:
        release_shared_desktop_reader_lock(paths.shared_desktop_lock_path)
        signal.signal(signal.SIGINT, previous_sigint)
        signal.signal(signal.SIGTERM, previous_sigterm)


@app.command("start")
def start_daemon(
    interval_ms: int = typer.Option(1000, "--interval-ms"),
    timeout_seconds: int = typer.Option(1800, "--timeout-seconds"),
    force: bool = typer.Option(False, "--force"),
) -> None:
    _fail_on_desktop_reader_conflict(force=force)
    pid = start_background_daemon(_paths(), interval_ms=interval_ms, timeout_seconds=timeout_seconds)
    typer.echo(pid)


@app.command("stop")
def stop_daemon() -> None:
    stop_background_daemon(_paths())
    typer.echo("stopped")


@app.command("status")
def status_daemon() -> None:
    typer.echo(json.dumps(daemon_status(_paths()), ensure_ascii=False, indent=2))
