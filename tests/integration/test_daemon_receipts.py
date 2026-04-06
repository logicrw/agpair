from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import json

from agpair.config import AppPaths
from agpair.terminal_receipts import StructuredTerminalReceipt, blocked_reason_from_receipt
from agpair.storage.db import connect, ensure_database
from agpair.storage.journal import JournalRepository
from agpair.storage.tasks import TaskRepository


class FakePullBus:
    def __init__(self, receipts: list[dict]) -> None:
        self._receipts = receipts
        self.sent_messages: list[tuple[str, tuple, dict]] = []

    def pull_receipts(self, *, task_id: str | None = None, limit: int = 20) -> list[dict]:
        return list(self._receipts)

    def send_task(self, *args, **kwargs):
        self.sent_messages.append(("send_task", args, kwargs))


def make_paths(tmp_path: Path) -> AppPaths:
    return AppPaths.from_root(tmp_path / ".agpair")


def seed_task(tmp_path: Path, task_id: str = "TASK-1", completion_policy: str = "direct_commit") -> AppPaths:
    paths = make_paths(tmp_path)
    ensure_database(paths.db_path)
    TaskRepository(paths.db_path).create_task(task_id=task_id, repo_path="/tmp/repo", completion_policy=completion_policy)
    return paths


def test_daemon_ingests_ack_and_updates_session_mapping(tmp_path: Path) -> None:
    from agpair.daemon.loop import run_once

    paths = seed_task(tmp_path)
    bus = FakePullBus(
        [
            {
                "id": 1,
                "task_id": "TASK-1",
                "status": "ACK",
                "body": "session_id=session-123\nrepo_path=/tmp/repo",
            }
        ]
    )

    run_once(paths, now=datetime(2026, 3, 21, 12, 0, tzinfo=UTC), bus=bus)

    task = TaskRepository(paths.db_path).get_task("TASK-1")
    assert task is not None
    assert task.phase == "acked"
    assert task.antigravity_session_id == "session-123"
    rows = JournalRepository(paths.db_path).tail("TASK-1", limit=2)
    assert rows[0].event == "acked"
    assert "session-123" in rows[0].body


def test_daemon_ingests_evidence_pack_marks_task_ready(tmp_path: Path) -> None:
    from agpair.daemon.loop import run_once

    paths = seed_task(tmp_path, completion_policy="review_then_commit")
    repo = TaskRepository(paths.db_path)
    repo.mark_acked(task_id="TASK-1", session_id="session-123")
    bus = FakePullBus(
        [
            {
                "id": 2,
                "task_id": "TASK-1",
                "status": "EVIDENCE_PACK",
                "body": "git diff --stat\n 1 file changed",
            }
        ]
    )

    run_once(paths, now=datetime(2026, 3, 21, 12, 1, tzinfo=UTC), bus=bus)

    task = repo.get_task("TASK-1")
    assert task is not None
    assert task.phase == "evidence_ready"
    assert task.last_receipt_id == "2"


def test_daemon_accepts_colon_space_session_id_format(tmp_path: Path) -> None:
    from agpair.daemon.loop import run_once

    paths = seed_task(tmp_path)
    bus = FakePullBus(
        [
            {
                "id": 3,
                "task_id": "TASK-1",
                "status": "ACK",
                "body": "Accepted\nsession_id: session-456\nrepo_path: /tmp/repo",
            }
        ]
    )

    run_once(paths, now=datetime(2026, 3, 21, 12, 2, tzinfo=UTC), bus=bus)

    task = TaskRepository(paths.db_path).get_task("TASK-1")
    assert task is not None
    assert task.phase == "acked"
    assert task.antigravity_session_id == "session-456"


def test_daemon_ingests_structured_blocked_receipt_uses_summary_for_reason(tmp_path: Path) -> None:
    from agpair.daemon.loop import run_once

    paths = seed_task(tmp_path)
    repo = TaskRepository(paths.db_path)
    repo.mark_acked(task_id="TASK-1", session_id="session-123")
    structured_body = json.dumps(
        {
            "schema_version": "1",
            "task_id": "TASK-1",
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
    )
    bus = FakePullBus(
        [
            {
                "id": 4,
                "task_id": "TASK-1",
                "status": "BLOCKED",
                "body": structured_body,
            }
        ]
    )

    run_once(paths, now=datetime(2026, 3, 21, 12, 3, tzinfo=UTC), bus=bus)

    task = repo.get_task("TASK-1")
    assert task is not None
    assert task.phase == "blocked"
    assert task.stuck_reason == "Need a human credential"
    rows = JournalRepository(paths.db_path).tail("TASK-1", limit=1)
    assert json.loads(rows[0].body)["schema_version"] == "1"


def test_daemon_ingests_structured_committed_receipt_preserves_payload(tmp_path: Path) -> None:
    from agpair.daemon.loop import run_once

    paths = seed_task(tmp_path)
    repo = TaskRepository(paths.db_path)
    repo.mark_acked(task_id="TASK-1", session_id="session-123")
    structured_body = json.dumps(
        {
            "schema_version": "1",
            "task_id": "TASK-1",
            "attempt_no": 1,
            "review_round": 0,
            "status": "COMMITTED",
            "summary": "Committed cleanly",
            "payload": {
                "commit_sha": "abc1234",
                "branch": "main",
                "diff_stat": "1 file changed",
                "changed_files": ["companion-extension/src/services/delegationReceiptWatcher.ts"],
                "validation": "npm test",
                "residual_risks": "none",
            },
        }
    )
    bus = FakePullBus(
        [
            {
                "id": 5,
                "task_id": "TASK-1",
                "status": "COMMITTED",
                "body": structured_body,
            }
        ]
    )

    run_once(paths, now=datetime(2026, 3, 21, 12, 4, tzinfo=UTC), bus=bus)

    task = repo.get_task("TASK-1")
    assert task is not None
    assert task.phase == "committed"
    rows = JournalRepository(paths.db_path).tail("TASK-1", limit=1)
    parsed = json.loads(rows[0].body)
    assert parsed["summary"] == "Committed cleanly"
    assert parsed["payload"]["commit_sha"] == "abc1234"


def test_daemon_cancels_local_cli_before_cleanup_on_terminal_receipt(tmp_path: Path, monkeypatch) -> None:
    from agpair.daemon.loop import run_once
    from agpair.executors.base import TaskState

    paths = seed_task(tmp_path)
    repo = TaskRepository(paths.db_path)
    with connect(paths.db_path) as conn:
        conn.execute(
            "UPDATE tasks SET executor_backend=? WHERE task_id=?",
            ("codex_cli", "TASK-1"),
        )
        conn.commit()
    repo.mark_acked(task_id="TASK-1", session_id="session-local-123")

    class FakeExecutor:
        def __init__(self) -> None:
            self.calls: list[tuple] = []

        def poll(self, task_id: str, session_id: str, attempt_no: int = 1):
            return TaskState(
                is_done=True,
                receipt={
                    "schema_version": "1",
                    "task_id": task_id,
                    "attempt_no": attempt_no,
                    "review_round": 0,
                    "status": "COMMITTED",
                    "summary": "Committed cleanly",
                    "payload": {"commit_sha": "abc1234"},
                },
            )

        def cancel(self, task_id: str, session_id: str) -> None:
            self.calls.append(("cancel", task_id, session_id))

        def cleanup(self, session_id: str) -> None:
            self.calls.append(("cleanup", session_id))

    fake_executor = FakeExecutor()
    monkeypatch.setattr("agpair.executors.get_executor", lambda backend_id, **kwargs: fake_executor)

    run_once(paths, now=datetime(2026, 3, 21, 12, 5, tzinfo=UTC), bus=FakePullBus([]))

    assert fake_executor.calls == [
        ("cancel", "TASK-1", "session-local-123"),
        ("cleanup", "session-local-123"),
    ]


def test_blocked_reason_from_receipt_prefers_payload_message_when_summary_empty() -> None:
    receipt = StructuredTerminalReceipt(
        schema_version="1",
        task_id="TASK-1",
        attempt_no=1,
        review_round=0,
        status="BLOCKED",
        summary="",
        payload={"message": "Missing credential"},
        raw_body="{}",
    )

    assert blocked_reason_from_receipt(receipt, "fallback") == "Missing credential"


def test_blocked_reason_from_receipt_uses_fallback_when_summary_and_message_missing() -> None:
    receipt = StructuredTerminalReceipt(
        schema_version="1",
        task_id="TASK-1",
        attempt_no=1,
        review_round=0,
        status="BLOCKED",
        summary="",
        payload={"message": 401},
        raw_body="{}",
    )

    assert blocked_reason_from_receipt(receipt, "fallback") == "fallback"
