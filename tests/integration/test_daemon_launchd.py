from __future__ import annotations

from pathlib import Path
import plistlib

from agpair.tools.install_agpair_daemon_launchd import (
    DEFAULT_LABEL,
    build_launch_agent_plist,
    install,
    status,
    uninstall,
)


def test_build_launch_agent_plist_includes_daemon_run_arguments_and_env() -> None:
    payload = build_launch_agent_plist(
        python_bin="/tmp/venv/bin/python",
        agpair_home="/tmp/.agpair",
        interval_ms=1500,
        timeout_seconds=1200,
        agent_bus_bin="/tmp/bin/agent-bus",
        stdout_path="/tmp/agpair.stdout.log",
        stderr_path="/tmp/agpair.stderr.log",
    )

    assert payload["Label"] == DEFAULT_LABEL
    assert payload["ProgramArguments"] == [
        "/tmp/venv/bin/python",
        "-m",
        "agpair.cli.app",
        "daemon",
        "run",
        "--interval-ms",
        "1500",
        "--timeout-seconds",
        "1200",
    ]
    assert payload["EnvironmentVariables"] == {
        "AGPAIR_HOME": "/tmp/.agpair",
        "AGPAIR_AGENT_BUS_BIN": "/tmp/bin/agent-bus",
    }


def test_install_and_status_round_trip(tmp_path: Path, monkeypatch) -> None:
    calls: list[tuple[str, ...]] = []

    def fake_launchctl(*args: str):
        calls.append(args)

        class Result:
            returncode = 0
            stdout = ""
            stderr = ""

        return Result()

    monkeypatch.setattr("agpair.tools.install_agpair_daemon_launchd._launchctl", fake_launchctl)
    plist_path = tmp_path / "LaunchAgents" / "io.logicrw.agpair.daemon.plist"
    payload = build_launch_agent_plist(
        python_bin="/tmp/venv/bin/python",
        agpair_home="/tmp/.agpair",
        stdout_path=str(tmp_path / "stdout.log"),
        stderr_path=str(tmp_path / "stderr.log"),
    )

    install(plist_path=plist_path, payload=payload)

    assert plist_path.exists() is True
    written = plistlib.loads(plist_path.read_bytes())
    assert written["ProgramArguments"][:4] == ["/tmp/venv/bin/python", "-m", "agpair.cli.app", "daemon"]
    assert calls[0][0] == "bootout"
    assert calls[1][0] == "bootstrap"

    exit_code = status(plist_path=plist_path)
    assert exit_code == 0

    uninstall(plist_path=plist_path)
    assert plist_path.exists() is False
