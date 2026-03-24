"""Tests for delivery-id deduplication of terminal receipts.

Covers:
- parse_delivery_header parsing (terminal vs non-terminal, presence vs absence)
- DB-backed dedup via (task_id, delivery_id) unique index
- Existing message-id dedup preserved
- ACK / RUNNING unaffected even when body contains X-Delivery-Id
- Journal / task state sees clean body, not header
- Migration evidence for delivery_id column + unique index
- Stale message-id watermark still works alongside delivery-id dedup
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from agpair.config import AppPaths
from agpair.delivery import ParsedBody, parse_delivery_header
from agpair.storage.db import connect, ensure_database
from agpair.storage.journal import JournalRepository
from agpair.storage.receipts import ReceiptRepository
from agpair.storage.tasks import TaskRepository


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class FakePullBus:
    def __init__(self, receipts: list[dict]) -> None:
        self._receipts = receipts
        self.sent_messages: list[tuple[str, tuple, dict]] = []

    def pull_receipts(self, *, task_id: str | None = None, limit: int = 20) -> list[dict]:
        return list(self._receipts)

    def send_task(self, *args, **kwargs):
        self.sent_messages.append(("send_task", args, kwargs))

    def send_review(self, *args, **kwargs):
        self.sent_messages.append(("send_review", args, kwargs))

    def send_approved(self, *args, **kwargs):
        self.sent_messages.append(("send_approved", args, kwargs))


def make_paths(tmp_path: Path) -> AppPaths:
    return AppPaths.from_root(tmp_path / ".agpair")


def seed_task(tmp_path: Path, task_id: str = "TASK-1") -> AppPaths:
    paths = make_paths(tmp_path)
    ensure_database(paths.db_path)
    TaskRepository(paths.db_path).create_task(task_id=task_id, repo_path="/tmp/repo")
    TaskRepository(paths.db_path).mark_acked(task_id=task_id, session_id="session-1")
    return paths


# ===========================================================================
# 1. parse_delivery_header unit tests
# ===========================================================================


class TestParseDeliveryHeader:
    def test_terminal_with_header(self) -> None:
        body = "X-Delivery-Id: dlv-abc-123\nlines of evidence\nmore"
        result = parse_delivery_header("EVIDENCE_PACK", body)
        assert result == ParsedBody(delivery_id="dlv-abc-123", clean_body="lines of evidence\nmore")

    def test_terminal_without_header(self) -> None:
        body = "just plain body"
        result = parse_delivery_header("BLOCKED", body)
        assert result == ParsedBody(delivery_id=None, clean_body="just plain body")

    def test_nonterminal_ack_ignores_header(self) -> None:
        body = "X-Delivery-Id: dlv-should-not-parse\nsession_id=foo"
        result = parse_delivery_header("ACK", body)
        assert result.delivery_id is None
        assert result.clean_body == body  # unchanged

    def test_nonterminal_running_ignores_header(self) -> None:
        body = "X-Delivery-Id: dlv-999\nheartbeat"
        result = parse_delivery_header("RUNNING", body)
        assert result.delivery_id is None
        assert result.clean_body == body

    def test_committed_with_header(self) -> None:
        body = "X-Delivery-Id: dlv-commit-42\nCommit SHA: abc123"
        result = parse_delivery_header("COMMITTED", body)
        assert result.delivery_id == "dlv-commit-42"
        assert result.clean_body == "Commit SHA: abc123"

    def test_empty_body(self) -> None:
        result = parse_delivery_header("EVIDENCE_PACK", "")
        assert result.delivery_id is None
        assert result.clean_body == ""

    def test_header_only_body(self) -> None:
        body = "X-Delivery-Id: dlv-only\n"
        result = parse_delivery_header("BLOCKED", body)
        assert result.delivery_id == "dlv-only"
        assert result.clean_body == ""


# ===========================================================================
# 2. Receipt repository – message-id dedup still works
# ===========================================================================


def test_message_id_duplicate_still_rejected(tmp_path: Path) -> None:
    paths = make_paths(tmp_path)
    ensure_database(paths.db_path)
    repo = ReceiptRepository(paths.db_path)
    assert repo.record("msg-1", "TASK-1", "EVIDENCE_PACK") is True
    assert repo.record("msg-1", "TASK-1", "EVIDENCE_PACK") is False  # same msg


# ===========================================================================
# 3. Receipt repository – delivery-id dedup
# ===========================================================================


def test_delivery_id_duplicate_rejected_different_message_id(tmp_path: Path) -> None:
    """Two receipts with different message_id but same (task_id, delivery_id) → second rejected."""
    paths = make_paths(tmp_path)
    ensure_database(paths.db_path)
    repo = ReceiptRepository(paths.db_path)

    ok1 = repo.record("msg-100", "TASK-1", "EVIDENCE_PACK", delivery_id="dlv-abc")
    assert ok1 is True

    ok2 = repo.record("msg-200", "TASK-1", "EVIDENCE_PACK", delivery_id="dlv-abc")
    assert ok2 is False  # duplicate logical delivery


def test_delivery_id_null_allows_multiple(tmp_path: Path) -> None:
    """Receipts without delivery_id (legacy) can coexist."""
    paths = make_paths(tmp_path)
    ensure_database(paths.db_path)
    repo = ReceiptRepository(paths.db_path)

    assert repo.record("msg-1", "TASK-1", "EVIDENCE_PACK", delivery_id=None) is True
    assert repo.record("msg-2", "TASK-1", "EVIDENCE_PACK", delivery_id=None) is True


def test_same_delivery_id_different_task_allowed(tmp_path: Path) -> None:
    """Different tasks can share the same delivery_id string (unlikely but allowed)."""
    paths = make_paths(tmp_path)
    ensure_database(paths.db_path)
    repo = ReceiptRepository(paths.db_path)

    assert repo.record("msg-1", "TASK-A", "EVIDENCE_PACK", delivery_id="dlv-x") is True
    assert repo.record("msg-2", "TASK-B", "EVIDENCE_PACK", delivery_id="dlv-x") is True


# ===========================================================================
# 4. End-to-end: same delivery_id, different message_id → second ignored
# ===========================================================================


def test_e2e_duplicate_delivery_id_ignored(tmp_path: Path) -> None:
    """Two EVIDENCE_PACK receipts with different msg ids but same delivery id:
    only the first should advance the task phase."""
    from agpair.daemon.loop import run_once

    paths = seed_task(tmp_path)
    header_body = "X-Delivery-Id: dlv-42\ngit diff --stat\n 1 file changed"

    # First delivery
    bus1 = FakePullBus([{
        "id": 10,
        "task_id": "TASK-1",
        "status": "EVIDENCE_PACK",
        "body": header_body,
    }])
    run_once(paths, now=datetime(2026, 3, 24, 12, 0, tzinfo=UTC), bus=bus1)

    task = TaskRepository(paths.db_path).get_task("TASK-1")
    assert task is not None
    assert task.phase == "evidence_ready"
    assert task.last_receipt_id == "10"

    # Second delivery — same delivery id, different message id
    bus2 = FakePullBus([{
        "id": 20,
        "task_id": "TASK-1",
        "status": "EVIDENCE_PACK",
        "body": header_body,
    }])
    run_once(paths, now=datetime(2026, 3, 24, 12, 1, tzinfo=UTC), bus=bus2)

    # Phase and receipt id must NOT have changed
    task2 = TaskRepository(paths.db_path).get_task("TASK-1")
    assert task2 is not None
    assert task2.phase == "evidence_ready"
    assert task2.last_receipt_id == "10"  # NOT overwritten to 20


def test_e2e_duplicate_blocked_delivery_id_ignored(tmp_path: Path) -> None:
    """BLOCKED replays with same delivery-id are ignored."""
    from agpair.daemon.loop import run_once

    paths = seed_task(tmp_path)
    body = "X-Delivery-Id: dlv-blk-1\nI am blocked because of X"

    bus1 = FakePullBus([{
        "id": 30, "task_id": "TASK-1", "status": "BLOCKED", "body": body,
    }])
    run_once(paths, now=datetime(2026, 3, 24, 12, 0, tzinfo=UTC), bus=bus1)
    task = TaskRepository(paths.db_path).get_task("TASK-1")
    assert task is not None
    assert task.phase == "blocked"

    # Replay
    bus2 = FakePullBus([{
        "id": 40, "task_id": "TASK-1", "status": "BLOCKED", "body": body,
    }])
    run_once(paths, now=datetime(2026, 3, 24, 12, 1, tzinfo=UTC), bus=bus2)
    # Should still be blocked from the first delivery, no double journal
    journal = JournalRepository(paths.db_path).tail("TASK-1", limit=50)
    blocked_events = [j for j in journal if j.event == "blocked"]
    assert len(blocked_events) == 1


# ===========================================================================
# 5. Terminal receipt without header → old path still works
# ===========================================================================


def test_terminal_without_header_still_works(tmp_path: Path) -> None:
    from agpair.daemon.loop import run_once

    paths = seed_task(tmp_path)
    bus = FakePullBus([{
        "id": 50,
        "task_id": "TASK-1",
        "status": "EVIDENCE_PACK",
        "body": "plain evidence body, no header",
    }])

    run_once(paths, now=datetime(2026, 3, 24, 12, 0, tzinfo=UTC), bus=bus)

    task = TaskRepository(paths.db_path).get_task("TASK-1")
    assert task is not None
    assert task.phase == "evidence_ready"
    journal = JournalRepository(paths.db_path).tail("TASK-1", limit=5)
    assert any("plain evidence body" in j.body for j in journal)


# ===========================================================================
# 6. ACK / RUNNING unaffected even if body contains X-Delivery-Id text
# ===========================================================================


def test_ack_unaffected_by_delivery_header_in_body(tmp_path: Path) -> None:
    """ACK bodies are never parsed for X-Delivery-Id."""
    from agpair.daemon.loop import run_once

    paths = make_paths(tmp_path)
    ensure_database(paths.db_path)
    TaskRepository(paths.db_path).create_task(task_id="TASK-1", repo_path="/tmp/repo")

    bus = FakePullBus([{
        "id": 60,
        "task_id": "TASK-1",
        "status": "ACK",
        "body": "X-Delivery-Id: dlv-sneaky\nsession_id=session-ack-test",
    }])

    run_once(paths, now=datetime(2026, 3, 24, 12, 0, tzinfo=UTC), bus=bus)

    task = TaskRepository(paths.db_path).get_task("TASK-1")
    assert task is not None
    assert task.phase == "acked"
    assert task.antigravity_session_id == "session-ack-test"


def test_running_unaffected_by_delivery_header_in_body(tmp_path: Path) -> None:
    """RUNNING bodies are never parsed for X-Delivery-Id."""
    from agpair.daemon.loop import run_once

    paths = seed_task(tmp_path)
    bus = FakePullBus([{
        "id": 70,
        "task_id": "TASK-1",
        "status": "RUNNING",
        "body": "X-Delivery-Id: dlv-running\nstill alive",
    }])

    run_once(paths, now=datetime(2026, 3, 24, 12, 0, tzinfo=UTC), bus=bus)

    task = TaskRepository(paths.db_path).get_task("TASK-1")
    assert task is not None
    assert task.phase == "acked"  # not changed
    # delivery_id should NOT have been persisted in receipts (None for non-terminal)
    with connect(paths.db_path) as conn:
        row = conn.execute("SELECT delivery_id FROM receipts WHERE message_id='70'").fetchone()
    assert row is not None
    assert row["delivery_id"] is None


# ===========================================================================
# 7. Journal / task state sees stripped body (no header)
# ===========================================================================


def test_journal_body_is_clean(tmp_path: Path) -> None:
    """The journal and task stuck_reason must NOT contain the X-Delivery-Id header."""
    from agpair.daemon.loop import run_once

    paths = seed_task(tmp_path)
    bus_ep = FakePullBus([{
        "id": 80,
        "task_id": "TASK-1",
        "status": "EVIDENCE_PACK",
        "body": "X-Delivery-Id: dlv-ep-1\nevidence body here",
    }])
    run_once(paths, now=datetime(2026, 3, 24, 12, 0, tzinfo=UTC), bus=bus_ep)

    journal = JournalRepository(paths.db_path).tail("TASK-1", limit=5)
    ep_entries = [j for j in journal if j.event == "evidence_ready"]
    assert len(ep_entries) == 1
    assert "X-Delivery-Id" not in ep_entries[0].body
    assert "evidence body here" in ep_entries[0].body


def test_blocked_reason_is_clean(tmp_path: Path) -> None:
    from agpair.daemon.loop import run_once

    paths = seed_task(tmp_path)
    bus = FakePullBus([{
        "id": 90,
        "task_id": "TASK-1",
        "status": "BLOCKED",
        "body": "X-Delivery-Id: dlv-blk-clean\nblocked because of X",
    }])
    run_once(paths, now=datetime(2026, 3, 24, 12, 0, tzinfo=UTC), bus=bus)

    task = TaskRepository(paths.db_path).get_task("TASK-1")
    assert task is not None
    assert task.stuck_reason == "blocked because of X"
    assert "X-Delivery-Id" not in (task.stuck_reason or "")


# ===========================================================================
# 8. DB migration evidence
# ===========================================================================


def test_migration_adds_delivery_id_column(tmp_path: Path) -> None:
    """Prove the migration adds delivery_id to the receipts table."""
    paths = make_paths(tmp_path)
    ensure_database(paths.db_path)

    with connect(paths.db_path) as conn:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(receipts)").fetchall()}

    assert "delivery_id" in cols


def test_migration_creates_unique_index(tmp_path: Path) -> None:
    """Prove the unique partial index exists."""
    paths = make_paths(tmp_path)
    ensure_database(paths.db_path)

    with connect(paths.db_path) as conn:
        indexes = {row[1] for row in conn.execute("PRAGMA index_list(receipts)").fetchall()}

    assert "uq_receipts_task_delivery" in indexes


# ===========================================================================
# 9. Stale message-id watermark still works alongside delivery-id dedup
# ===========================================================================


def test_stale_receipt_watermark_still_works(tmp_path: Path) -> None:
    """Receipts with message_id <= last_receipt_id are still rejected as stale."""
    from agpair.daemon.loop import run_once

    paths = seed_task(tmp_path)

    # First evidence pack sets last_receipt_id = "100"
    bus1 = FakePullBus([{
        "id": 100,
        "task_id": "TASK-1",
        "status": "EVIDENCE_PACK",
        "body": "X-Delivery-Id: dlv-first\nfirst evidence",
    }])
    run_once(paths, now=datetime(2026, 3, 24, 12, 0, tzinfo=UTC), bus=bus1)

    task1 = TaskRepository(paths.db_path).get_task("TASK-1")
    assert task1 is not None
    assert task1.last_receipt_id == "100"

    # Now a stale receipt with a LOWER id and a DIFFERENT delivery id
    # This should be rejected by the stale watermark, not the delivery dedup.
    # But note: re-ack the task first so that a new terminal can be sent.
    TaskRepository(paths.db_path).mark_acked(task_id="TASK-1", session_id="session-retry")

    bus2 = FakePullBus([{
        "id": 50,
        "task_id": "TASK-1",
        "status": "EVIDENCE_PACK",
        "body": "X-Delivery-Id: dlv-stale\nstale evidence",
    }])
    run_once(paths, now=datetime(2026, 3, 24, 12, 1, tzinfo=UTC), bus=bus2)

    journal = JournalRepository(paths.db_path).tail("TASK-1", limit=20)
    stale_events = [j for j in journal if j.event == "receipt_stale"]
    assert len(stale_events) >= 1
    assert "50" in stale_events[0].body
