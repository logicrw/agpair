from __future__ import annotations

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import json
import os
import subprocess
import sys
import threading
import time

import pytest
from typer.testing import CliRunner

from agpair.cli.app import app
from agpair.cli import doctor as doctor_module
from agpair.config import AppPaths
from agpair.storage.db import ensure_database
from agpair.storage.receipts import ReceiptRepository


def _clear_disk_cache(tmp_path: Path) -> None:
    """Remove the disk cache file so tests start with a clean slate."""
    cache_file = tmp_path / ".agpair" / doctor_module._DOCTOR_CACHE_FILENAME
    cache_file.unlink(missing_ok=True)


@contextmanager
def run_health_server(payload: dict):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            if self.path != "/health":
                self.send_response(404)
                self.end_headers()
                return
            body = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):  # noqa: A003
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_doctor_reports_missing_bus_or_database_paths(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    monkeypatch.setenv("AGPAIR_AGENT_BUS_BIN", str(tmp_path / "missing-agent-bus"))
    _clear_disk_cache(tmp_path)

    result = CliRunner().invoke(app, ["doctor"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["config_root"].endswith(".agpair")
    assert payload["db_exists"] is False
    assert payload["agent_bus_available"] is False


def test_doctor_reports_daemon_status_and_latest_receipt(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    _clear_disk_cache(tmp_path)
    paths = AppPaths.default()
    ensure_database(paths.db_path)
    ReceiptRepository(paths.db_path).record("11", "TASK-1", "ACK")
    paths.status_path.write_text(
        json.dumps(
            {
                "running": True,
                "last_tick_at": "2026-03-21T12:00:00Z",
                "processed_receipts": 1,
                "stuck_marked": 0,
            }
        ),
        encoding="utf-8",
    )
    paths.pid_path.write_text("999999", encoding="utf-8")

    result = CliRunner().invoke(app, ["doctor"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status_path_exists"] is True
    assert payload["last_tick_at"] == "2026-03-21T12:00:00Z"
    assert payload["latest_receipt_id"] == "11"


def test_doctor_handles_corrupt_database_file(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    _clear_disk_cache(tmp_path)
    paths = AppPaths.default()
    paths.root.mkdir(parents=True, exist_ok=True)
    paths.db_path.write_text("not a sqlite database", encoding="utf-8")

    result = CliRunner().invoke(app, ["doctor"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["db_exists"] is True
    assert payload["latest_receipt_id"] is None
    assert payload["db_error"] is not None


def test_doctor_reports_supervisor_desktop_reader_conflict(tmp_path: Path, monkeypatch) -> None:
    """Status-file conflict (real desktop watcher) is always reported."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    _clear_disk_cache(tmp_path)
    supervisor_dir = tmp_path / ".supervisor"
    supervisor_dir.mkdir(parents=True, exist_ok=True)
    (supervisor_dir / "agent_bus_watch_desktop.status.json").write_text(
        json.dumps(
            {
                "mode": "watching",
                "pid": os.getpid(),
                "command": "/repo/tools/desktop_agent_bus_watch.py --interval-ms 1000 --notify",
                "updated_at": "2026-03-21T10:00:00Z",
            }
        ),
        encoding="utf-8",
    )

    result = CliRunner().invoke(app, ["doctor"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["desktop_reader_conflict"] is True
    assert payload["desktop_reader_conflict_detail"]["pid"] == os.getpid()


def test_doctor_reports_shared_lock_conflict_without_status_file(tmp_path: Path, monkeypatch) -> None:
    """Lock held by a non-agpair owner is still reported as a conflict."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    _clear_disk_cache(tmp_path)
    supervisor_dir = tmp_path / ".supervisor"
    supervisor_dir.mkdir(parents=True, exist_ok=True)
    (supervisor_dir / "agent_bus_watch_desktop.lock").write_text(
        f'{{"pid":{os.getpid()},"owner":"desktop_watch","lock_path":"{supervisor_dir / "agent_bus_watch_desktop.lock"}"}}',
        encoding="utf-8",
    )

    result = CliRunner().invoke(app, ["doctor"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["desktop_reader_conflict"] is True
    assert payload["desktop_reader_conflict_detail"]["source"] == "lock"


# ---------------------------------------------------------------------------
# Self-lock exclusion tests
# ---------------------------------------------------------------------------


def test_doctor_does_not_report_self_owned_agpair_lock_as_conflict(tmp_path: Path, monkeypatch) -> None:
    """When the shared lock is held by the agpair daemon (owner=agpair, PID
    matches daemon_pid), doctor must NOT flag it as a conflict."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    _clear_disk_cache(tmp_path)

    # Write daemon PID file pointing to current process (simulates the daemon).
    paths = AppPaths.default()
    paths.root.mkdir(parents=True, exist_ok=True)
    paths.pid_path.write_text(str(os.getpid()), encoding="utf-8")

    # Write a lock file owned by the agpair daemon (same PID).
    supervisor_dir = tmp_path / ".supervisor"
    supervisor_dir.mkdir(parents=True, exist_ok=True)
    (supervisor_dir / "agent_bus_watch_desktop.lock").write_text(
        json.dumps(
            {
                "pid": os.getpid(),
                "owner": "agpair",
                "started_at": "2026-03-23T10:00:00Z",
                "lock_path": str(supervisor_dir / "agent_bus_watch_desktop.lock"),
            }
        ),
        encoding="utf-8",
    )

    result = CliRunner().invoke(app, ["doctor"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    # Self-owned lock → no conflict!
    assert payload["desktop_reader_conflict"] is False
    assert payload["desktop_reader_conflict_detail"] is None


def test_doctor_still_reports_external_lock_even_with_daemon_running(tmp_path: Path, monkeypatch) -> None:
    """When the lock is held by a different PID (not the daemon), doctor must
    still report it as a conflict, even if the daemon is running."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    _clear_disk_cache(tmp_path)

    # Daemon PID file — use current process PID.
    paths = AppPaths.default()
    paths.root.mkdir(parents=True, exist_ok=True)
    paths.pid_path.write_text(str(os.getpid()), encoding="utf-8")

    # Lock file held by a DIFFERENT alive process (parent PID is always alive
    # and accessible without root, unlike PID 1 on macOS).
    other_pid = os.getppid()
    supervisor_dir = tmp_path / ".supervisor"
    supervisor_dir.mkdir(parents=True, exist_ok=True)
    (supervisor_dir / "agent_bus_watch_desktop.lock").write_text(
        json.dumps(
            {
                "pid": other_pid,
                "owner": "desktop_watch",
                "started_at": "2026-03-23T10:00:00Z",
                "lock_path": str(supervisor_dir / "agent_bus_watch_desktop.lock"),
            }
        ),
        encoding="utf-8",
    )

    result = CliRunner().invoke(app, ["doctor"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["desktop_reader_conflict"] is True
    assert payload["desktop_reader_conflict_detail"]["source"] == "lock"
    assert payload["desktop_reader_conflict_detail"]["pid"] == other_pid


def test_doctor_still_reports_status_conflict_even_if_pid_would_be_excluded(tmp_path: Path, monkeypatch) -> None:
    """Status-file conflicts (desktop_agent_bus_watch.py running) are NEVER
    excluded, even if the PID matches the daemon PID.  This is critical for
    not weakening real conflict detection."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    _clear_disk_cache(tmp_path)

    # Daemon PID = current process.
    paths = AppPaths.default()
    paths.root.mkdir(parents=True, exist_ok=True)
    paths.pid_path.write_text(str(os.getpid()), encoding="utf-8")

    # Status file from the external desktop watcher with SAME PID.
    supervisor_dir = tmp_path / ".supervisor"
    supervisor_dir.mkdir(parents=True, exist_ok=True)
    (supervisor_dir / "agent_bus_watch_desktop.status.json").write_text(
        json.dumps(
            {
                "mode": "watching",
                "pid": os.getpid(),
                "command": "/repo/tools/desktop_agent_bus_watch.py --interval-ms 1000 --notify",
                "updated_at": "2026-03-23T10:00:00Z",
            }
        ),
        encoding="utf-8",
    )

    result = CliRunner().invoke(app, ["doctor"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    # Must still be a conflict!
    assert payload["desktop_reader_conflict"] is True


# ---------------------------------------------------------------------------
# Cache tests (in-process, using CliRunner)
# ---------------------------------------------------------------------------


def test_doctor_cache_returns_cached_result_on_second_call(tmp_path: Path, monkeypatch) -> None:
    """A second call within TTL returns a cached result with cache_hit=True."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    _clear_disk_cache(tmp_path)

    result1 = CliRunner().invoke(app, ["doctor"])
    assert result1.exit_code == 0
    payload1 = json.loads(result1.stdout)
    assert payload1["doctor_cache_hit"] is False

    # Second call — should be cached (disk-persisted).
    result2 = CliRunner().invoke(app, ["doctor"])
    assert result2.exit_code == 0
    payload2 = json.loads(result2.stdout)
    assert payload2["doctor_cache_hit"] is True
    assert isinstance(payload2["doctor_cache_age_s"], (int, float))
    assert payload2["doctor_cache_age_s"] >= 0


def test_doctor_cache_bypassed_with_fresh_flag(tmp_path: Path, monkeypatch) -> None:
    """The --fresh flag forces a full re-probe even if cache is valid."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    _clear_disk_cache(tmp_path)

    CliRunner().invoke(app, ["doctor"])  # warm the cache

    result = CliRunner().invoke(app, ["doctor", "--fresh"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["doctor_cache_hit"] is False


def test_doctor_cache_expires_after_ttl(tmp_path: Path, monkeypatch) -> None:
    """After TTL expires the cache is stale and a fresh probe runs."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    _clear_disk_cache(tmp_path)

    # Warm cache
    CliRunner().invoke(app, ["doctor"])

    # Manually expire the cache entry by rewriting cached_at to the past.
    cache_file = tmp_path / ".agpair" / doctor_module._DOCTOR_CACHE_FILENAME
    envelope = json.loads(cache_file.read_text(encoding="utf-8"))
    envelope["cached_at"] = time.time() - 999
    cache_file.write_text(json.dumps(envelope), encoding="utf-8")

    result = CliRunner().invoke(app, ["doctor"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["doctor_cache_hit"] is False


def test_doctor_cache_does_not_cache_unhealthy_results(tmp_path: Path, monkeypatch) -> None:
    """When a report has errors, it must NOT be cached."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    _clear_disk_cache(tmp_path)

    # Create a corrupt database to trigger db_error.
    paths = AppPaths.default()
    paths.root.mkdir(parents=True, exist_ok=True)
    paths.db_path.write_text("not a sqlite database", encoding="utf-8")

    result1 = CliRunner().invoke(app, ["doctor"])
    assert result1.exit_code == 0
    payload1 = json.loads(result1.stdout)
    assert payload1["db_error"] is not None
    assert payload1["doctor_cache_hit"] is False

    # Cache file should NOT exist.
    cache_file = tmp_path / ".agpair" / doctor_module._DOCTOR_CACHE_FILENAME
    assert not cache_file.exists(), "unhealthy report must not be cached to disk"

    # Second call — should NOT be cached because first result was unhealthy.
    result2 = CliRunner().invoke(app, ["doctor"])
    assert result2.exit_code == 0
    payload2 = json.loads(result2.stdout)
    assert payload2["doctor_cache_hit"] is False


def test_doctor_cache_tolerates_corrupt_cache_file(tmp_path: Path, monkeypatch) -> None:
    """A corrupt cache file is treated as a cache miss, not an error."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    paths = AppPaths.default()
    paths.root.mkdir(parents=True, exist_ok=True)

    # Write garbage to the cache file.
    cache_file = tmp_path / ".agpair" / doctor_module._DOCTOR_CACHE_FILENAME
    cache_file.write_text("NOT VALID JSON {{{{", encoding="utf-8")

    result = CliRunner().invoke(app, ["doctor"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["doctor_cache_hit"] is False


@pytest.mark.parametrize(
    "bad_envelope",
    [
        pytest.param(
            {"key": "__placeholder__", "cached_at": time.time(), "ttl": "bad", "report": {}},
            id="string-ttl",
        ),
        pytest.param(
            {"key": "__placeholder__", "cached_at": "not-a-number", "ttl": 30, "report": {}},
            id="string-cached_at",
        ),
        pytest.param(
            {"key": "__placeholder__", "cached_at": time.time(), "ttl": 30, "report": "oops"},
            id="string-report",
        ),
        pytest.param(
            {"key": "__placeholder__", "cached_at": time.time(), "ttl": None, "report": {}},
            id="null-ttl",
        ),
        pytest.param(
            {"key": "__placeholder__", "cached_at": time.time(), "ttl": 30, "report": [1, 2]},
            id="list-report",
        ),
        pytest.param(
            "just a string, not a dict",
            id="non-dict-envelope",
        ),
    ],
)
def test_doctor_cache_tolerates_semantically_corrupted_envelope(
    tmp_path: Path, monkeypatch, bad_envelope,
) -> None:
    """A structurally valid JSON cache file with wrong field types must be
    treated as a cache miss, never crash the CLI (regression: TypeError on
    non-numeric ttl)."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    paths = AppPaths.default()
    paths.root.mkdir(parents=True, exist_ok=True)

    cache_file = tmp_path / ".agpair" / doctor_module._DOCTOR_CACHE_FILENAME

    # Patch the key placeholder so the key check passes (where applicable).
    if isinstance(bad_envelope, dict) and bad_envelope.get("key") == "__placeholder__":
        bad_envelope["key"] = doctor_module._cache_key(paths, None)

    cache_file.write_text(json.dumps(bad_envelope), encoding="utf-8")

    result = CliRunner().invoke(app, ["doctor"])
    assert result.exit_code == 0, f"doctor crashed: {result.output}"
    payload = json.loads(result.stdout)
    assert payload["doctor_cache_hit"] is False


# ---------------------------------------------------------------------------
# Cross-process cache test (subprocess invocations)
# ---------------------------------------------------------------------------


def test_doctor_cache_survives_across_subprocess_invocations(tmp_path: Path) -> None:
    """Two separate subprocess CLI invocations prove the disk-persisted cache
    works across process boundaries.  The second invocation must be a cache hit."""
    agpair_home = str(tmp_path / ".agpair")
    env = {**os.environ, "AGPAIR_HOME": agpair_home, "HOME": str(tmp_path)}

    # First invocation — fresh probe.
    r1 = subprocess.run(
        [sys.executable, "-m", "agpair.cli.app", "doctor"],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(Path(__file__).resolve().parents[2]),  # repo root
    )
    assert r1.returncode == 0, f"first invocation failed: {r1.stderr}"
    p1 = json.loads(r1.stdout)
    assert p1["doctor_cache_hit"] is False

    # Second invocation — must be a cache hit.
    r2 = subprocess.run(
        [sys.executable, "-m", "agpair.cli.app", "doctor"],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(Path(__file__).resolve().parents[2]),
    )
    assert r2.returncode == 0, f"second invocation failed: {r2.stderr}"
    p2 = json.loads(r2.stdout)
    assert p2["doctor_cache_hit"] is True
    assert isinstance(p2["doctor_cache_age_s"], (int, float))
    assert p2["doctor_cache_age_s"] >= 0


def test_doctor_cache_fresh_flag_bypasses_across_subprocesses(tmp_path: Path) -> None:
    """--fresh must bypass the disk cache even from a separate process."""
    agpair_home = str(tmp_path / ".agpair")
    env = {**os.environ, "AGPAIR_HOME": agpair_home, "HOME": str(tmp_path)}
    cwd = str(Path(__file__).resolve().parents[2])

    # Warm cache.
    r1 = subprocess.run(
        [sys.executable, "-m", "agpair.cli.app", "doctor"],
        capture_output=True, text=True, env=env, cwd=cwd,
    )
    assert r1.returncode == 0
    assert json.loads(r1.stdout)["doctor_cache_hit"] is False

    # --fresh must bypass.
    r2 = subprocess.run(
        [sys.executable, "-m", "agpair.cli.app", "doctor", "--fresh"],
        capture_output=True, text=True, env=env, cwd=cwd,
    )
    assert r2.returncode == 0
    p2 = json.loads(r2.stdout)
    assert p2["doctor_cache_hit"] is False


# ---------------------------------------------------------------------------
# Repo bridge tests (unchanged from original)
# ---------------------------------------------------------------------------


def test_doctor_reports_repo_bridge_health_when_repo_marker_is_live(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    _clear_disk_cache(tmp_path)
    repo_path = tmp_path / "repo"
    marker_dir = repo_path / ".supervisor"
    marker_dir.mkdir(parents=True, exist_ok=True)

    with run_health_server(
        {
            "ok": True,
            "sdk_initialized": True,
            "ls_bridge_ready": True,
            "monitor_running": True,
            "workspace_paths": [str(repo_path)],
            "version": "0.1.12",
        }
    ) as port:
        (marker_dir / "bridge_port").write_text(str(port), encoding="utf-8")
        result = CliRunner().invoke(app, ["doctor", "--repo-path", str(repo_path)])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["repo_bridge_marker_exists"] is True
    assert payload["repo_bridge_port"] == port
    assert payload["repo_bridge_reachable"] is True
    assert payload["repo_bridge_sdk_initialized"] is True
    assert payload["repo_bridge_ls_ready"] is True
    assert payload["repo_bridge_monitor_running"] is True
    assert payload["repo_bridge_workspace_match"] is True
    assert payload["repo_bridge_session_ready"] is True
    assert payload["repo_bridge_version"] == "0.1.12"


def test_doctor_reports_repo_bridge_health_when_repo_agpair_marker_is_live(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    _clear_disk_cache(tmp_path)
    repo_path = tmp_path / "repo"
    marker_dir = repo_path / ".agpair"
    marker_dir.mkdir(parents=True, exist_ok=True)

    with run_health_server(
        {
            "ok": True,
            "sdk_initialized": True,
            "ls_bridge_ready": True,
            "monitor_running": True,
            "workspace_paths": [str(repo_path)],
            "version": "1.0.0",
            "extension_id": "logicrw.antigravity-companion-extension",
            "extension_path": str(repo_path / "companion-extension"),
        }
    ) as port:
        (marker_dir / "bridge_port").write_text(str(port), encoding="utf-8")
        result = CliRunner().invoke(app, ["doctor", "--repo-path", str(repo_path)])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["repo_bridge_marker_exists"] is True
    assert payload["repo_bridge_marker_source"] == "repo"
    assert payload["repo_bridge_marker_source_path"] == str(marker_dir / "bridge_port")
    assert payload["repo_bridge_port"] == port
    assert payload["repo_bridge_reachable"] is True
    assert payload["repo_bridge_session_ready"] is True
    assert payload["repo_bridge_version"] == "1.0.0"


def test_doctor_reports_repo_bridge_warning_when_ls_bridge_not_ready(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    _clear_disk_cache(tmp_path)
    repo_path = tmp_path / "repo"
    marker_dir = repo_path / ".supervisor"
    marker_dir.mkdir(parents=True, exist_ok=True)

    with run_health_server(
        {
            "ok": True,
            "sdk_initialized": True,
            "ls_bridge_ready": False,
            "monitor_running": True,
            "workspace_paths": [str(repo_path)],
            "version": "0.1.12",
        }
    ) as port:
        (marker_dir / "bridge_port").write_text(str(port), encoding="utf-8")
        result = CliRunner().invoke(app, ["doctor", "--repo-path", str(repo_path)])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["repo_bridge_reachable"] is True
    assert payload["repo_bridge_session_ready"] is False
    assert "ls_bridge_ready=false" in payload["repo_bridge_warning"]


def test_doctor_warns_when_running_extension_does_not_match_repo_checkout(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    _clear_disk_cache(tmp_path)
    repo_path = tmp_path / "repo"
    marker_dir = repo_path / ".supervisor"
    marker_dir.mkdir(parents=True, exist_ok=True)
    companion_dir = repo_path / "companion-extension"
    companion_dir.mkdir(parents=True, exist_ok=True)
    (companion_dir / "package.json").write_text(
        json.dumps({"name": "antigravity-companion-extension", "version": "1.0.0"}),
        encoding="utf-8",
    )

    with run_health_server(
        {
            "ok": True,
            "sdk_initialized": True,
            "ls_bridge_ready": True,
            "monitor_running": True,
            "workspace_paths": [str(repo_path)],
            "version": "0.1.12",
            "extension_path": str(tmp_path / ".antigravity" / "extensions" / "logicrw.antigravity-companion-extension-0.1.12"),
            "extension_id": "logicrw.antigravity-companion-extension",
        }
    ) as port:
        (marker_dir / "bridge_port").write_text(str(port), encoding="utf-8")
        result = CliRunner().invoke(app, ["doctor", "--repo-path", str(repo_path)])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["repo_bridge_expected_extension_id"] == "logicrw.antigravity-companion-extension"
    assert payload["repo_bridge_extension_id_match"] is True
    assert payload["repo_bridge_expected_version"] == "1.0.0"
    assert payload["repo_bridge_version"] == "0.1.12"
    assert payload["repo_bridge_version_match"] is False
    assert payload["repo_bridge_running_from_repo"] is False
    assert payload["repo_bridge_session_ready"] is False
    assert "extension version mismatch" in payload["repo_bridge_warning"]
    assert "extension_path mismatch" in payload["repo_bridge_warning"]


def test_doctor_accepts_installed_extension_when_id_and_version_match(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    _clear_disk_cache(tmp_path)
    repo_path = tmp_path / "repo"
    marker_dir = repo_path / ".agpair"
    marker_dir.mkdir(parents=True, exist_ok=True)
    companion_dir = repo_path / "companion-extension"
    companion_dir.mkdir(parents=True, exist_ok=True)
    (companion_dir / "package.json").write_text(
        json.dumps({"name": "antigravity-companion-extension", "version": "1.0.0"}),
        encoding="utf-8",
    )

    with run_health_server(
        {
            "ok": True,
            "sdk_initialized": True,
            "ls_bridge_ready": True,
            "monitor_running": True,
            "workspace_paths": [str(repo_path)],
            "version": "1.0.0",
            "extension_path": str(tmp_path / ".antigravity" / "extensions" / "logicrw.antigravity-companion-extension-1.0.0"),
            "extension_id": "logicrw.antigravity-companion-extension",
        }
    ) as port:
        (marker_dir / "bridge_port").write_text(str(port), encoding="utf-8")
        result = CliRunner().invoke(app, ["doctor", "--repo-path", str(repo_path)])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["repo_bridge_version_match"] is True
    assert payload["repo_bridge_extension_id_match"] is True
    assert payload["repo_bridge_running_from_repo"] is False
    assert payload["repo_bridge_session_ready"] is True
    assert payload["repo_bridge_warning"] is None


# ---------------------------------------------------------------------------
# Bridge auth posture tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "auth_mode,mutating_required",
    [
        ("generated", True),
        ("configured", True),
        ("insecure", False),
    ],
    ids=["generated", "configured", "insecure"],
)
def test_doctor_surfaces_bridge_auth_posture_from_health(
    tmp_path: Path, monkeypatch, auth_mode: str, mutating_required: bool,
) -> None:
    """When /health returns bridge_auth_mode and bridge_mutating_auth_required,
    doctor must include them verbatim as repo_bridge_auth_mode and
    repo_bridge_mutating_auth_required."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    _clear_disk_cache(tmp_path)
    repo_path = tmp_path / "repo"
    marker_dir = repo_path / ".supervisor"
    marker_dir.mkdir(parents=True, exist_ok=True)

    with run_health_server(
        {
            "ok": True,
            "sdk_initialized": True,
            "ls_bridge_ready": True,
            "monitor_running": True,
            "workspace_paths": [str(repo_path)],
            "version": "0.2.0",
            "bridge_auth_mode": auth_mode,
            "bridge_mutating_auth_required": mutating_required,
        }
    ) as port:
        (marker_dir / "bridge_port").write_text(str(port), encoding="utf-8")
        result = CliRunner().invoke(app, ["doctor", "--repo-path", str(repo_path)])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["repo_bridge_auth_mode"] == auth_mode
    assert payload["repo_bridge_mutating_auth_required"] is mutating_required


def test_doctor_returns_none_when_health_omits_auth_fields(
    tmp_path: Path, monkeypatch,
) -> None:
    """When /health does not include the auth metadata (older bridge versions),
    doctor must still succeed and report None for both auth posture fields."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    _clear_disk_cache(tmp_path)
    repo_path = tmp_path / "repo"
    marker_dir = repo_path / ".supervisor"
    marker_dir.mkdir(parents=True, exist_ok=True)

    with run_health_server(
        {
            "ok": True,
            "sdk_initialized": True,
            "ls_bridge_ready": True,
            "monitor_running": True,
            "workspace_paths": [str(repo_path)],
            "version": "0.1.12",
            # deliberately NO bridge_auth_mode or bridge_mutating_auth_required
        }
    ) as port:
        (marker_dir / "bridge_port").write_text(str(port), encoding="utf-8")
        result = CliRunner().invoke(app, ["doctor", "--repo-path", str(repo_path)])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["repo_bridge_auth_mode"] is None
    assert payload["repo_bridge_mutating_auth_required"] is None
    # Existing fields still healthy
    assert payload["repo_bridge_reachable"] is True
    assert payload["repo_bridge_session_ready"] is True


def test_doctor_does_not_leak_token_in_report(
    tmp_path: Path, monkeypatch,
) -> None:
    """Even if the health payload contains a token field (e.g. debug builds),
    doctor must never expose any secret token value in its report."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    _clear_disk_cache(tmp_path)
    repo_path = tmp_path / "repo"
    marker_dir = repo_path / ".supervisor"
    marker_dir.mkdir(parents=True, exist_ok=True)

    secret = "tok_super_secret_value_12345"
    with run_health_server(
        {
            "ok": True,
            "sdk_initialized": True,
            "ls_bridge_ready": True,
            "monitor_running": True,
            "workspace_paths": [str(repo_path)],
            "version": "0.2.0",
            "bridge_auth_mode": "generated",
            "bridge_mutating_auth_required": True,
            # Hypothetical token field that should NOT appear in doctor output
            "bridge_token": secret,
        }
    ) as port:
        (marker_dir / "bridge_port").write_text(str(port), encoding="utf-8")
        result = CliRunner().invoke(app, ["doctor", "--repo-path", str(repo_path)])

    assert result.exit_code == 0
    # The full JSON output must not contain the token
    assert secret not in result.stdout
    payload = json.loads(result.stdout)
    # Auth posture is still reported
    assert payload["repo_bridge_auth_mode"] == "generated"
    assert payload["repo_bridge_mutating_auth_required"] is True


def test_doctor_reports_pending_tasks_and_concurrency_policy(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    _clear_disk_cache(tmp_path)
    repo_path = tmp_path / "repo"
    marker_dir = repo_path / ".supervisor"
    marker_dir.mkdir(parents=True, exist_ok=True)

    with run_health_server(
        {
            "ok": True,
            "sdk_initialized": True,
            "ls_bridge_ready": True,
            "monitor_running": True,
            "workspace_paths": [str(repo_path)],
            "version": "0.1.12",
            "delegation_auto_return": {
                "tracker_summary": {
                    "pending": 2,
                    "tasks": [
                        {"taskId": "T-1", "terminalSentAt": None},
                        {"taskId": "T-2", "terminalSentAt": "2026-03-21T10:00:00Z"},
                        {"taskId": "T-3", "terminalSentAt": None},
                    ]
                }
            }
        }
    ) as port:
        (marker_dir / "bridge_port").write_text(str(port), encoding="utf-8")
        result = CliRunner().invoke(app, ["doctor", "--repo-path", str(repo_path)])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)

    assert payload["repo_bridge_pending_task_count"] == 2
    assert payload["repo_bridge_pending_task_ids"] == ["T-1", "T-3"]

    policy = payload["repo_bridge_concurrency_policy"]
    assert policy["same_worktree_parallel_safe"] is False
    assert policy["safe_isolation_boundary"] == "different repo or different git worktree"
