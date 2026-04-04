from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os


@dataclass(frozen=True)
class AppPaths:
    root: Path
    db_path: Path
    status_path: Path
    pid_path: Path
    agent_bus_bin: str
    shared_desktop_lock_path: Path
    targets_path: Path
    daemon_stdout_path: Path
    daemon_stderr_path: Path

    @classmethod
    def from_root(cls, root: Path) -> "AppPaths":
        base = root.expanduser().resolve()
        return cls(
            root=base,
            db_path=base / "agpair.db",
            status_path=base / "daemon.status.json",
            pid_path=base / "daemon.pid",
            agent_bus_bin=os.environ.get("AGPAIR_AGENT_BUS_BIN", "agent-bus"),
            shared_desktop_lock_path=base / "agent_bus_watch_desktop.lock",
            targets_path=base / "targets.json",
            daemon_stdout_path=base / "daemon.stdout.log",
            daemon_stderr_path=base / "daemon.stderr.log",
        )

    @classmethod
    def default(cls) -> "AppPaths":
        root = Path(os.environ.get("AGPAIR_HOME", Path.home() / ".agpair"))
        return cls.from_root(root)
