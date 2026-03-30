from pathlib import Path
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import sqlite3
import threading
from typer.testing import CliRunner

from agpair.cli.app import app
from agpair.config import AppPaths
from agpair.storage.db import connect, ensure_database
from agpair.storage.journal import JournalRepository
from agpair.storage.receipts import ReceiptRepository
from agpair.storage.tasks import TaskNotFoundError, TaskRepository
from tests.fixtures.fake_agent_bus import read_calls, write_fake_agent_bus


def make_paths(tmp_path: Path) -> AppPaths:
    return AppPaths.from_root(tmp_path / ".agpair")


def make_task_repo(tmp_path: Path) -> TaskRepository:
    paths = make_paths(tmp_path)
    ensure_database(paths.db_path)
    return TaskRepository(paths.db_path)


def make_receipt_repo(tmp_path: Path) -> ReceiptRepository:
    paths = make_paths(tmp_path)
    ensure_database(paths.db_path)
    return ReceiptRepository(paths.db_path)


def make_journal_repo(tmp_path: Path) -> JournalRepository:
    paths = make_paths(tmp_path)
    ensure_database(paths.db_path)
    return JournalRepository(paths.db_path)


@contextmanager
def run_bridge_server(*, expected_token: str):
    requests: list[dict] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            if self.path != "/health":
                self.send_response(404)
                self.end_headers()
                return
            body = json.dumps(
                {
                    "ok": True,
                    "bridge_auth_mode": "generated",
                    "bridge_mutating_auth_required": True,
                }
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):  # noqa: N802
            raw_length = self.headers.get("Content-Length", "0")
            length = int(raw_length) if raw_length.isdigit() else 0
            payload = self.rfile.read(length).decode("utf-8") if length else ""
            requests.append(
                {
                    "path": self.path,
                    "authorization": self.headers.get("Authorization"),
                    "body": json.loads(payload or "{}"),
                }
            )
            if self.path != "/cancel_task":
                self.send_response(404)
                self.end_headers()
                return
            if self.headers.get("Authorization") != f"Bearer {expected_token}":
                self.send_response(401)
                self.end_headers()
                return
            body = json.dumps({"ok": True, "message": "cancelled"}).encode("utf-8")
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
        port = server.server_address[1]
        yield port, requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_ensure_database_creates_sqlite_schema(tmp_path: Path) -> None:
    paths = make_paths(tmp_path)
    ensure_database(paths.db_path)
    assert paths.db_path.exists()


def test_ensure_database_enables_wal_and_connect_sets_busy_timeout(tmp_path: Path) -> None:
    paths = make_paths(tmp_path)
    ensure_database(paths.db_path)

    with sqlite3.connect(paths.db_path) as conn:
        journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert str(journal_mode).lower() == "wal"

    with connect(paths.db_path) as conn:
        busy_timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
    assert busy_timeout == 5000


def test_ensure_database_migrates_existing_db_to_wal(tmp_path: Path) -> None:
    db_path = tmp_path / "existing.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS tasks (
              task_id TEXT PRIMARY KEY,
              repo_path TEXT NOT NULL,
              phase TEXT NOT NULL,
              antigravity_session_id TEXT,
              attempt_no INTEGER NOT NULL DEFAULT 1,
              retry_count INTEGER NOT NULL DEFAULT 0,
              last_receipt_id TEXT,
              stuck_reason TEXT,
              retry_recommended INTEGER NOT NULL DEFAULT 0,
              last_activity_at TEXT NOT NULL,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS receipts (
              message_id TEXT PRIMARY KEY,
              task_id TEXT NOT NULL,
              status TEXT NOT NULL,
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS journal (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              task_id TEXT NOT NULL,
              source TEXT NOT NULL,
              event TEXT NOT NULL,
              body TEXT NOT NULL,
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS daemon_health (
              name TEXT PRIMARY KEY,
              updated_at TEXT NOT NULL,
              body TEXT NOT NULL
            );
        """)
        conn.execute("PRAGMA journal_mode=DELETE")
        conn.commit()

    ensure_database(db_path)

    with sqlite3.connect(db_path) as conn:
        journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert str(journal_mode).lower() == "wal"


def test_task_repository_persists_session_mapping(tmp_path: Path) -> None:
    repo = make_task_repo(tmp_path)
    repo.create_task(task_id="TASK-1", repo_path="/tmp/repo")
    repo.mark_acked(task_id="TASK-1", session_id="session-123")

    task = repo.get_task("TASK-1")
    assert task is not None
    assert task.phase == "acked"
    assert task.antigravity_session_id == "session-123"


def test_receipt_repository_deduplicates_by_message_id(tmp_path: Path) -> None:
    receipts = make_receipt_repo(tmp_path)
    assert receipts.record("msg-1", "TASK-1", "ACK") is True
    assert receipts.record("msg-1", "TASK-1", "ACK") is False


def test_journal_repository_appends_and_reads_tail(tmp_path: Path) -> None:
    journal = make_journal_repo(tmp_path)
    journal.append("TASK-1", "cli", "created", "Goal: test")
    journal.append("TASK-1", "daemon", "acked", "session-123")

    rows = journal.tail("TASK-1", limit=2)
    assert len(rows) == 2
    assert rows[0].event == "acked"
    assert rows[0].source == "daemon"
    assert rows[1].event == "created"
    assert rows[1].source == "cli"


def test_task_repository_raises_when_task_is_missing(tmp_path: Path) -> None:
    repo = make_task_repo(tmp_path)
    try:
        repo.mark_acked(task_id="TASK-404", session_id="session-123")
    except TaskNotFoundError as exc:
        assert "TASK-404" in str(exc)
    else:
        raise AssertionError("expected TaskNotFoundError")


def test_task_start_creates_local_record_and_sends_task(tmp_path: Path, monkeypatch) -> None:
    binary, calls_path, pull_path = write_fake_agent_bus(tmp_path)
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    monkeypatch.setenv("AGPAIR_AGENT_BUS_BIN", binary)
    monkeypatch.setenv("FAKE_AGENT_BUS_CALLS", str(calls_path))
    monkeypatch.setenv("FAKE_AGENT_BUS_PULL", str(pull_path))

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["task", "start", "--repo-path", "/tmp/repo", "--body", "Goal: fix it", "--task-id", "TASK-CLI-1", "--no-wait"],
    )

    assert result.exit_code == 0
    assert "TASK-CLI-1" in result.stdout

    task = make_task_repo(tmp_path).get_task("TASK-CLI-1")
    assert task is not None
    assert task.phase == "new"
    recorded = read_calls(calls_path)
    assert recorded[-1]["argv"][:4] == ["agent-bus", "send", "--sender", "desktop"]


def test_task_start_reuses_existing_task_for_same_repo_and_idempotency_key(tmp_path: Path, monkeypatch) -> None:
    binary, calls_path, pull_path = write_fake_agent_bus(tmp_path)
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    monkeypatch.setenv("AGPAIR_AGENT_BUS_BIN", binary)
    monkeypatch.setenv("FAKE_AGENT_BUS_CALLS", str(calls_path))
    monkeypatch.setenv("FAKE_AGENT_BUS_PULL", str(pull_path))

    runner = CliRunner()
    first = runner.invoke(
        app,
        [
            "task",
            "start",
            "--repo-path",
            "/tmp/repo-a",
            "--body",
            "Goal: first dispatch",
            "--task-id",
            "TASK-IDEMP-1",
            "--idempotency-key",
            "caller-key-1",
            "--no-wait",
        ],
    )
    second = runner.invoke(
        app,
        [
            "task",
            "start",
            "--repo-path",
            "/tmp/repo-a",
            "--body",
            "Goal: duplicate dispatch",
            "--task-id",
            "TASK-IDEMP-2",
            "--idempotency-key",
            "caller-key-1",
            "--no-wait",
        ],
    )

    assert first.exit_code == 0
    assert second.exit_code == 0
    assert first.stdout.strip() == "TASK-IDEMP-1"
    assert second.stdout.strip() == "TASK-IDEMP-1"
    assert make_task_repo(tmp_path).get_task("TASK-IDEMP-2") is None
    recorded = read_calls(calls_path)
    assert len(recorded) == 1
    assert recorded[0]["argv"][:4] == ["agent-bus", "send", "--sender", "desktop"]


def test_task_start_idempotency_key_is_scoped_to_repo_path(tmp_path: Path, monkeypatch) -> None:
    binary, calls_path, pull_path = write_fake_agent_bus(tmp_path)
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    monkeypatch.setenv("AGPAIR_AGENT_BUS_BIN", binary)
    monkeypatch.setenv("FAKE_AGENT_BUS_CALLS", str(calls_path))
    monkeypatch.setenv("FAKE_AGENT_BUS_PULL", str(pull_path))

    runner = CliRunner()
    first = runner.invoke(
        app,
        [
            "task",
            "start",
            "--repo-path",
            "/tmp/repo-a",
            "--body",
            "Goal: first dispatch",
            "--task-id",
            "TASK-IDEMP-REPO-A",
            "--idempotency-key",
            "caller-key-2",
            "--no-wait",
        ],
    )
    second = runner.invoke(
        app,
        [
            "task",
            "start",
            "--repo-path",
            "/tmp/repo-b",
            "--body",
            "Goal: second dispatch",
            "--task-id",
            "TASK-IDEMP-REPO-B",
            "--idempotency-key",
            "caller-key-2",
            "--no-wait",
        ],
    )

    assert first.exit_code == 0
    assert second.exit_code == 0
    assert first.stdout.strip() == "TASK-IDEMP-REPO-A"
    assert second.stdout.strip() == "TASK-IDEMP-REPO-B"
    assert make_task_repo(tmp_path).get_task("TASK-IDEMP-REPO-A") is not None
    assert make_task_repo(tmp_path).get_task("TASK-IDEMP-REPO-B") is not None
    recorded = read_calls(calls_path)
    assert len(recorded) == 2


def test_task_status_shows_phase_and_session(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    repo = make_task_repo(tmp_path)
    repo.create_task(task_id="TASK-1", repo_path="/tmp/repo")
    repo.mark_acked(task_id="TASK-1", session_id="session-123")

    result = CliRunner().invoke(app, ["task", "status", "TASK-1"])
    assert result.exit_code == 0
    assert "phase: acked" in result.stdout
    assert "a2a_state_hint: working" in result.stdout
    assert "session_id: session-123" in result.stdout


def test_task_status_json_returns_structured_payload(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    repo = make_task_repo(tmp_path)
    repo.create_task(task_id="TASK-JSON-1", repo_path="/tmp/repo")
    repo.mark_acked(task_id="TASK-JSON-1", session_id="session-json-1")

    result = CliRunner().invoke(app, ["task", "status", "TASK-JSON-1", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["task_id"] == "TASK-JSON-1"
    assert payload["phase"] == "acked"
    assert payload["a2a_state_hint"] == "working"
    assert payload["session_id"] == "session-json-1"
    assert payload["waiter"] is None
    assert payload["liveness_state"] in {"active_via_heartbeat", "silent", "active_via_workspace"}


def test_task_status_json_includes_structured_terminal_receipt(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    repo = make_task_repo(tmp_path)
    repo.create_task(task_id="TASK-JSON-TERM", repo_path="/tmp/repo")
    repo.mark_acked(task_id="TASK-JSON-TERM", session_id="session-json-term")
    repo.mark_committed(task_id="TASK-JSON-TERM")
    journal = make_journal_repo(tmp_path)
    journal.append(
        "TASK-JSON-TERM",
        "daemon",
        "committed",
        json.dumps(
            {
                "schema_version": "1",
                "task_id": "TASK-JSON-TERM",
                "attempt_no": 1,
                "review_round": 0,
                "status": "COMMITTED",
                "summary": "Committed cleanly",
                "payload": {"commit_sha": "abc1234", "branch": "main"},
            }
        ),
    )

    result = CliRunner().invoke(app, ["task", "status", "TASK-JSON-TERM", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["terminal_receipt"]["schema_version"] == "1"
    assert payload["terminal_receipt"]["summary"] == "Committed cleanly"
    assert payload["terminal_receipt"]["payload"]["commit_sha"] == "abc1234"
    assert payload["committed_result"]["commit_sha"] == "abc1234"
    assert payload["committed_result"]["branch"] == "main"


def test_task_status_json_includes_failure_context_for_structured_blocked_receipt(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    repo = make_task_repo(tmp_path)
    repo.create_task(task_id="TASK-JSON-BLOCKED", repo_path="/tmp/repo")
    repo.mark_acked(task_id="TASK-JSON-BLOCKED", session_id="session-json-blocked")
    repo.mark_blocked(task_id="TASK-JSON-BLOCKED", reason="Need a human credential")
    journal = make_journal_repo(tmp_path)
    journal.append(
        "TASK-JSON-BLOCKED",
        "daemon",
        "blocked",
        json.dumps(
            {
                "schema_version": "1",
                "task_id": "TASK-JSON-BLOCKED",
                "attempt_no": 1,
                "review_round": 0,
                "status": "BLOCKED",
                "summary": "Need a human credential",
                "payload": {
                    "blocker_type": "auth",
                    "message": "Missing credential",
                    "recoverable": True,
                    "suggested_action": "Provide token",
                    "last_error_excerpt": "401 unauthorized",
                },
            }
        ),
    )

    result = CliRunner().invoke(app, ["task", "status", "TASK-JSON-BLOCKED", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["failure_context"]["blocker_type"] == "auth"
    assert payload["failure_context"]["recoverable"] is True
    assert payload["failure_context"]["recommended_next_action"] == "Provide token"
    assert payload["failure_context"]["last_error_excerpt"] == "401 unauthorized"
    assert payload["failure_context"]["details"]["message"] == "Missing credential"


def test_task_status_json_maps_auth_blocker_to_auth_required(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    repo = make_task_repo(tmp_path)
    repo.create_task(task_id="TASK-JSON-AUTH", repo_path="/tmp/repo")
    repo.mark_acked(task_id="TASK-JSON-AUTH", session_id="session-json-auth")
    repo.mark_blocked(task_id="TASK-JSON-AUTH", reason="Browser requested human solve")
    journal = make_journal_repo(tmp_path)
    journal.append(
        "TASK-JSON-AUTH",
        "daemon",
        "blocked",
        json.dumps(
            {
                "schema_version": "1",
                "task_id": "TASK-JSON-AUTH",
                "attempt_no": 1,
                "review_round": 0,
                "status": "BLOCKED",
                "summary": "Need human auth",
                "payload": {
                    "blocker_type": "auth",
                },
            }
        ),
    )

    result = CliRunner().invoke(app, ["task", "status", "TASK-JSON-AUTH", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["failure_context"]["blocker_type"] == "auth"
    assert payload["a2a_state_hint"] == "auth-required"


def test_task_status_json_ignores_malformed_structured_receipt(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    repo = make_task_repo(tmp_path)
    repo.create_task(task_id="TASK-JSON-MALFORMED", repo_path="/tmp/repo")
    repo.mark_acked(task_id="TASK-JSON-MALFORMED", session_id="session-json-malformed")
    repo.mark_committed(task_id="TASK-JSON-MALFORMED")
    journal = make_journal_repo(tmp_path)
    journal.append(
        "TASK-JSON-MALFORMED",
        "daemon",
        "committed",
        json.dumps(
            {
                "schema_version": "1",
                "task_id": "TASK-JSON-MALFORMED",
                "attempt_no": "NOT AN INTEGER",
                "review_round": 0,
                "status": "COMMITTED",
                "summary": "Committed cleanly",
                "payload": {"commit_sha": "abc1234"},
            }
        ),
    )

    result = CliRunner().invoke(app, ["task", "status", "TASK-JSON-MALFORMED", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["terminal_receipt"] is None
    assert payload["committed_result"] is None


def test_task_status_json_returns_not_found_error(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))

    result = CliRunner().invoke(app, ["task", "status", "TASK-404", "--json"])

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload == {
        "ok": False,
        "error": "task_not_found",
        "task_id": "TASK-404",
    }


def test_task_logs_prints_recent_journal_entries(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    repo = make_task_repo(tmp_path)
    repo.create_task(task_id="TASK-1", repo_path="/tmp/repo")
    journal = make_journal_repo(tmp_path)
    journal.append("TASK-1", "cli", "created", "Goal: test")
    journal.append("TASK-1", "daemon", "acked", "session-123")

    result = CliRunner().invoke(app, ["task", "logs", "TASK-1", "--limit", "2"])
    assert result.exit_code == 0
    assert "[daemon] acked: session-123" in result.stdout
    assert "[cli] created: Goal: test" in result.stdout


def test_task_logs_json_returns_structured_rows(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    repo = make_task_repo(tmp_path)
    repo.create_task(task_id="TASK-LOGS-JSON", repo_path="/tmp/repo")
    journal = make_journal_repo(tmp_path)
    journal.append("TASK-LOGS-JSON", "cli", "created", "Goal: test")
    journal.append("TASK-LOGS-JSON", "daemon", "acked", "session-123")

    result = CliRunner().invoke(app, ["task", "logs", "TASK-LOGS-JSON", "--limit", "2", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["task_id"] == "TASK-LOGS-JSON"
    assert len(payload["logs"]) == 2
    assert payload["logs"][0]["event"] == "acked"
    assert payload["logs"][0]["source"] == "daemon"
    assert payload["logs"][1]["event"] == "created"
    assert payload["logs"][1]["classification"] == "normal"


def test_task_logs_json_includes_structured_receipt_payload(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    repo = make_task_repo(tmp_path)
    repo.create_task(task_id="TASK-LOGS-STRUCT", repo_path="/tmp/repo")
    journal = make_journal_repo(tmp_path)
    journal.append(
        "TASK-LOGS-STRUCT",
        "daemon",
        "blocked",
        json.dumps(
            {
                "schema_version": "1",
                "task_id": "TASK-LOGS-STRUCT",
                "attempt_no": 1,
                "review_round": 0,
                "status": "BLOCKED",
                "summary": "Need a credential",
                "payload": {"blocker_type": "auth", "recoverable": True},
            }
        ),
    )

    result = CliRunner().invoke(app, ["task", "logs", "TASK-LOGS-STRUCT", "--limit", "1", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["logs"][0]["structured_receipt"]["summary"] == "Need a credential"
    assert payload["logs"][0]["structured_receipt"]["payload"]["blocker_type"] == "auth"


def test_task_logs_json_returns_not_found_error(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))

    result = CliRunner().invoke(app, ["task", "logs", "TASK-404", "--json"])

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload == {
        "ok": False,
        "error": "task_not_found",
        "task_id": "TASK-404",
    }


def test_task_start_marks_blocked_when_dispatch_fails(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    monkeypatch.setenv("AGPAIR_AGENT_BUS_BIN", str(tmp_path / "missing-agent-bus"))

    result = CliRunner().invoke(
        app,
        ["task", "start", "--repo-path", "/tmp/repo", "--body", "Goal: fix it", "--task-id", "TASK-FAIL-1"],
    )

    assert result.exit_code == 1
    assert "dispatch failed:" in result.stderr
    task = make_task_repo(tmp_path).get_task("TASK-FAIL-1")
    assert task is not None
    assert task.phase == "blocked"
    assert task.stuck_reason is not None

    status = CliRunner().invoke(app, ["task", "status", "TASK-FAIL-1", "--json"])
    assert status.exit_code == 0
    payload = json.loads(status.stdout)
    assert payload["failure_context"]["blocker_type"] == "session_transport_failure"
    assert payload["failure_context"]["recoverable"] is True
    assert payload["failure_context"]["recommended_next_action"] == "retry"


def test_task_logs_fails_when_task_is_missing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    result = CliRunner().invoke(app, ["task", "logs", "TASK-404"])
    assert result.exit_code == 1


def test_task_list_prints_recent_tasks(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    repo = make_task_repo(tmp_path)
    repo.create_task(task_id="TASK-1", repo_path="/tmp/repo-a")
    repo.create_task(task_id="TASK-2", repo_path="/tmp/repo-b")
    repo.mark_acked(task_id="TASK-2", session_id="session-123")

    result = CliRunner().invoke(app, ["task", "list"])

    assert result.exit_code == 0
    assert "TASK-2 acked attempt=1 retry=0 recommended=False repo=/tmp/repo-b" in result.stdout
    assert "TASK-1 new attempt=1 retry=0 recommended=False repo=/tmp/repo-a" in result.stdout


def test_task_list_can_filter_by_phase(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    repo = make_task_repo(tmp_path)
    repo.create_task(task_id="TASK-1", repo_path="/tmp/repo-a")
    repo.create_task(task_id="TASK-2", repo_path="/tmp/repo-b")
    repo.mark_acked(task_id="TASK-2", session_id="session-123")

    result = CliRunner().invoke(app, ["task", "list", "--phase", "acked"])

    assert result.exit_code == 0
    assert "TASK-2 acked" in result.stdout
    assert "TASK-1" not in result.stdout


def test_task_abandon_marks_task_terminal_locally(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    repo = make_task_repo(tmp_path)
    journal = make_journal_repo(tmp_path)
    repo.create_task(task_id="TASK-1", repo_path="/tmp/repo")
    repo.mark_acked(task_id="TASK-1", session_id="session-123")
    journal.append("TASK-1", "daemon", "acked", "session_id=session-123")

    result = CliRunner().invoke(app, ["task", "abandon", "TASK-1", "--reason", "manual cleanup"])

    assert result.exit_code == 0
    task = repo.get_task("TASK-1")
    assert task is not None
    assert task.phase == "abandoned"
    assert task.stuck_reason == "manual cleanup"
    rows = journal.tail("TASK-1", limit=5)
    assert any(row.event == "abandoned" and row.source == "cli" for row in rows)


def test_task_abandon_notifies_bridge_cancel_when_auth_marker_present(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    repo_path = tmp_path / "repo"
    repo_path.mkdir(parents=True, exist_ok=True)
    repo_marker_dir = repo_path / ".agpair"
    repo_marker_dir.mkdir(parents=True, exist_ok=True)

    repo = make_task_repo(tmp_path)
    journal = make_journal_repo(tmp_path)
    repo.create_task(task_id="TASK-BRIDGE-ABANDON", repo_path=str(repo_path))
    repo.mark_acked(task_id="TASK-BRIDGE-ABANDON", session_id="session-bridge-1")
    journal.append("TASK-BRIDGE-ABANDON", "daemon", "acked", "session_id=session-bridge-1")

    expected_token = "bridge-auth-token-123"
    with run_bridge_server(expected_token=expected_token) as (port, requests):
        (repo_marker_dir / "bridge_port").write_text(str(port), encoding="utf-8")
        (repo_marker_dir / "bridge_auth_token").write_text(expected_token, encoding="utf-8")

        result = CliRunner().invoke(
            app,
            ["task", "abandon", "TASK-BRIDGE-ABANDON", "--reason", "manual cleanup"],
        )

    assert result.exit_code == 0
    task = repo.get_task("TASK-BRIDGE-ABANDON")
    assert task is not None
    assert task.phase == "abandoned"
    cancel_request = next((item for item in requests if item["path"] == "/cancel_task"), None)
    assert cancel_request is not None
    assert cancel_request["authorization"] == f"Bearer {expected_token}"
    assert cancel_request["body"] == {"task_id": "TASK-BRIDGE-ABANDON", "attempt_no": 1}


def test_task_abandon_fails_when_task_is_missing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))

    result = CliRunner().invoke(app, ["task", "abandon", "TASK-404"])

    assert result.exit_code == 1


def test_inspect_json_with_no_active_task(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    
    result = CliRunner().invoke(app, ["inspect", "--repo-path", "/tmp/repo", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["repo_path"] == "/tmp/repo"
    assert payload["task"] is None
    assert "reachable" in payload["bridge"]


def test_inspect_json_with_specific_task_id(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    repo = make_task_repo(tmp_path)
    repo.create_task(task_id="TASK-INSPECT-JSON-1", repo_path="/tmp/repo")
    repo.mark_acked(task_id="TASK-INSPECT-JSON-1", session_id="sesh-1")
    
    result = CliRunner().invoke(app, ["inspect", "--repo-path", "/tmp/repo", "--task-id", "TASK-INSPECT-JSON-1", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["task"]["task_id"] == "TASK-INSPECT-JSON-1"
    assert payload["task"]["phase"] == "acked"


def test_inspect_chooses_relevant_active_task(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    repo = make_task_repo(tmp_path)
    
    repo.create_task(task_id="TASK-TERM", repo_path="/tmp/repo")
    repo.mark_abandoned(task_id="TASK-TERM", reason="test")

    repo.create_task(task_id="TASK-ACTIVE", repo_path="/tmp/repo")
    repo.mark_acked(task_id="TASK-ACTIVE", session_id="sesh-active")
    
    result = CliRunner().invoke(app, ["inspect", "--repo-path", "/tmp/repo", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["task"] is not None
    assert payload["task"]["task_id"] == "TASK-ACTIVE"


def test_inspect_human_readable_output(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    repo = make_task_repo(tmp_path)
    repo.create_task(task_id="TASK-HUMAN", repo_path="/tmp/repo")
    repo.mark_acked(task_id="TASK-HUMAN", session_id="sesh-2")
    
    result = CliRunner().invoke(app, ["inspect", "--repo-path", "/tmp/repo"])
    assert result.exit_code == 0
    assert "=== Inspect: /tmp/repo ===" in result.stdout
    assert "Task ID:     TASK-HUMAN" in result.stdout

