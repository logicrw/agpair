from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys


DEFAULT_LABEL = "io.logicrw.agpair.daemon"
DEFAULT_PLIST = Path.home() / "Library" / "LaunchAgents" / f"{DEFAULT_LABEL}.plist"
DEFAULT_STDOUT = Path.home() / ".agpair" / "agpair_daemon.stdout.log"
DEFAULT_STDERR = Path.home() / ".agpair" / "agpair_daemon.stderr.log"


def build_launch_agent_plist(
    *,
    python_bin: str,
    agpair_home: str | None = None,
    interval_ms: int = 1000,
    timeout_seconds: int = 1800,
    agent_bus_bin: str | None = None,
    working_directory: str | None = None,
    stdout_path: str = str(DEFAULT_STDOUT),
    stderr_path: str = str(DEFAULT_STDERR),
) -> dict:
    program_arguments = [
        python_bin,
        "-m",
        "agpair.cli.app",
        "daemon",
        "run",
        "--interval-ms",
        str(interval_ms),
        "--timeout-seconds",
        str(timeout_seconds),
    ]
    plist: dict = {
        "Label": DEFAULT_LABEL,
        "ProgramArguments": program_arguments,
        "RunAtLoad": True,
        "KeepAlive": True,
        "ProcessType": "Background",
        "StandardOutPath": stdout_path,
        "StandardErrorPath": stderr_path,
    }
    env_vars: dict[str, str] = {}
    if agpair_home:
        env_vars["AGPAIR_HOME"] = agpair_home
    if agent_bus_bin:
        env_vars["AGPAIR_AGENT_BUS_BIN"] = agent_bus_bin
    if env_vars:
        plist["EnvironmentVariables"] = env_vars
    if working_directory:
        plist["WorkingDirectory"] = working_directory
    return plist


def _launchctl(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["launchctl", *args],
        capture_output=True,
        text=True,
        timeout=10,
    )


def install(*, plist_path: Path, payload: dict) -> None:
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    Path(payload["StandardOutPath"]).parent.mkdir(parents=True, exist_ok=True)
    Path(payload["StandardErrorPath"]).parent.mkdir(parents=True, exist_ok=True)
    with open(plist_path, "wb") as f:
        plistlib.dump(payload, f)

    domain = f"gui/{os.getuid()}"
    _launchctl("bootout", domain, str(plist_path))
    result = _launchctl("bootstrap", domain, str(plist_path))
    if result.returncode != 0 and "already loaded" not in result.stderr.lower():
        raise SystemExit(result.stderr.strip() or result.stdout.strip() or "launchctl bootstrap failed")


def uninstall(*, plist_path: Path) -> None:
    domain = f"gui/{os.getuid()}"
    _launchctl("bootout", domain, str(plist_path))
    try:
        plist_path.unlink()
    except FileNotFoundError:
        pass


def status(*, plist_path: Path) -> int:
    report = {
        "installed": plist_path.exists(),
        "plist_path": str(plist_path),
        "label": DEFAULT_LABEL,
    }
    if plist_path.exists():
        try:
            with open(plist_path, "rb") as f:
                plist = plistlib.load(f)
            report["program_arguments"] = plist.get("ProgramArguments", [])
            report["environment_variables"] = plist.get("EnvironmentVariables", {})
            report["keep_alive"] = plist.get("KeepAlive", False)
        except Exception:
            pass
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Install/remove launchd auto-start for agpair daemon")
    sub = parser.add_subparsers(dest="command", required=True)

    install_parser = sub.add_parser("install")
    install_parser.add_argument("--plist", default=str(DEFAULT_PLIST))
    install_parser.add_argument("--python", default=sys.executable)
    install_parser.add_argument("--agpair-home", default=os.environ.get("AGPAIR_HOME", ""))
    install_parser.add_argument("--interval-ms", type=int, default=1000)
    install_parser.add_argument("--timeout-seconds", type=int, default=1800)
    install_parser.add_argument("--agent-bus-bin", default=os.environ.get("AGPAIR_AGENT_BUS_BIN", ""))
    install_parser.add_argument("--working-directory", default=None)
    install_parser.add_argument("--stdout", default=str(DEFAULT_STDOUT))
    install_parser.add_argument("--stderr", default=str(DEFAULT_STDERR))

    uninstall_parser = sub.add_parser("uninstall")
    uninstall_parser.add_argument("--plist", default=str(DEFAULT_PLIST))

    status_parser = sub.add_parser("status")
    status_parser.add_argument("--plist", default=str(DEFAULT_PLIST))
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    plist_path = Path(args.plist)

    if args.command == "install":
        payload = build_launch_agent_plist(
            python_bin=args.python,
            agpair_home=args.agpair_home or None,
            interval_ms=args.interval_ms,
            timeout_seconds=args.timeout_seconds,
            agent_bus_bin=args.agent_bus_bin or None,
            working_directory=args.working_directory,
            stdout_path=args.stdout,
            stderr_path=args.stderr,
        )
        install(plist_path=plist_path, payload=payload)
        print(f"installed: {plist_path}")
        return 0

    if args.command == "uninstall":
        uninstall(plist_path=plist_path)
        print(f"removed: {plist_path}")
        return 0

    return status(plist_path=plist_path)


if __name__ == "__main__":
    raise SystemExit(main())
