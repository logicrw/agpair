"""Tests for RUNNING heartbeat liveness — phase 2 delegated-task semantics.

Covers:
  - ACK -> RUNNING heartbeat ingestion without terminal transition
  - Soft watchdog does NOT trigger while recent heartbeats are arriving
  - Silent task still gets retry_recommended / stuck after heartbeat silence
  - Task wait / auto-wait does not early-fail on live heartbeats but still
    fails after silence or terminal failure
  - Schema upgrade path for existing DB
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
import sqlite3

import pytest

from agpair.config import AppPaths
from agpair.models import TaskRecord
from agpair.storage.db import connect, ensure_database
from agpair.storage.journal import JournalRepository
from agpair.storage.tasks import TaskRepository
from agpair.transport import messages


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class FakePullBus:
    def __init__(self, receipts: list[dict]) -> None:
        self._receipts = receipts
        self.sent_messages: list[tuple[str, tuple, dict]] = []
        self.settled_claims: list[tuple[str, list[str]]] = []

    def pull_receipts(self, *, task_id: str | None = None, limit: int = 20) -> list[dict]:
        return list(self._receipts)

    def reserve_receipts(self, *, task_id: str | None = None, limit: int = 20, lease_ms: int = 30000) -> list[dict]:
        return [{**receipt, "claim_id": f"clm-{idx}"} for idx, receipt in enumerate(self._receipts, start=1)]

    def settle_claims(self, *, reader: str, claims: list[str]) -> int:
        self.settled_claims.append((reader, list(claims)))
        return len(claims)

    def send_task(self, *args, **kwargs):
        self.sent_messages.append(("send_task", args, kwargs))


class EmptyBus(FakePullBus):
    def __init__(self) -> None:
        super().__init__([])


def make_paths(tmp_path: Path) -> AppPaths:
    return AppPaths.from_root(tmp_path / ".agpair")


def seed_acked_task(tmp_path: Path, task_id: str = "TASK-HB1") -> AppPaths:
    paths = make_paths(tmp_path)
    ensure_database(paths.db_path)
    repo = TaskRepository(paths.db_path)
    repo.create_task(task_id=task_id, repo_path="/tmp/repo")
    repo.mark_acked(task_id=task_id, session_id="session-hb")
    return paths


def _to_iso(dt: datetime) -> str:
    return dt.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# 1. RUNNING constant is present and non-terminal
# ---------------------------------------------------------------------------


def test_running_constant_exists():
    assert messages.RUNNING == "RUNNING"


def test_running_is_not_terminal():
    from agpair.cli.wait import TERMINAL_PHASES
    assert "running" not in TERMINAL_PHASES
    assert "RUNNING" not in TERMINAL_PHASES


# ---------------------------------------------------------------------------
# 2. ACK -> RUNNING heartbeat ingestion without terminal transition
# ---------------------------------------------------------------------------


def test_daemon_ingests_running_heartbeat_without_terminal_transition(tmp_path: Path) -> None:
    """RUNNING receipt should update last_heartbeat_at but leave phase as acked."""
    from agpair.daemon.loop import run_once

    paths = seed_acked_task(tmp_path)
    repo = TaskRepository(paths.db_path)

    task_before = repo.get_task("TASK-HB1")
    assert task_before is not None
    assert task_before.phase == "acked"
    assert task_before.last_heartbeat_at is None

    bus = FakePullBus([
        {
            "id": 10,
            "task_id": "TASK-HB1",
            "status": "RUNNING",
            "body": "Working on step 3...",
        }
    ])

    now = datetime(2026, 3, 24, 12, 0, tzinfo=UTC)
    run_once(paths, now=now, bus=bus)

    task_after = repo.get_task("TASK-HB1")
    assert task_after is not None
    # Phase must remain acked — RUNNING is NOT terminal
    assert task_after.phase == "acked"
    # Heartbeat must be recorded
    assert task_after.last_heartbeat_at is not None
    assert task_after.last_heartbeat_at == _to_iso(now)
    # last_activity_at must NOT be updated by heartbeat
    assert task_after.last_activity_at == task_before.last_activity_at

    # Journal should record heartbeat event
    rows = JournalRepository(paths.db_path).tail("TASK-HB1", limit=5)
    assert any(row.event == "heartbeat" for row in rows)


def test_multiple_running_heartbeats_update_timestamp(tmp_path: Path) -> None:
    """Multiple RUNNING receipts should keep updating last_heartbeat_at."""
    from agpair.daemon.loop import run_once

    paths = seed_acked_task(tmp_path)
    repo = TaskRepository(paths.db_path)

    t1 = datetime(2026, 3, 24, 12, 0, tzinfo=UTC)
    bus1 = FakePullBus([{"id": 10, "task_id": "TASK-HB1", "status": "RUNNING", "body": "step 1"}])
    run_once(paths, now=t1, bus=bus1)

    task1 = repo.get_task("TASK-HB1")
    assert task1 is not None
    assert task1.last_heartbeat_at == _to_iso(t1)

    t2 = datetime(2026, 3, 24, 12, 5, tzinfo=UTC)
    bus2 = FakePullBus([{"id": 11, "task_id": "TASK-HB1", "status": "RUNNING", "body": "step 2"}])
    run_once(paths, now=t2, bus=bus2)

    task2 = repo.get_task("TASK-HB1")
    assert task2 is not None
    assert task2.last_heartbeat_at == _to_iso(t2)


def test_running_followed_by_terminal_receipt(tmp_path: Path) -> None:
    """Terminal receipt after RUNNING should transition phase normally."""
    from agpair.daemon.loop import run_once

    paths = seed_acked_task(tmp_path)
    repo = TaskRepository(paths.db_path)

    # First: heartbeat
    t1 = datetime(2026, 3, 24, 12, 0, tzinfo=UTC)
    bus1 = FakePullBus([{"id": 10, "task_id": "TASK-HB1", "status": "RUNNING", "body": "working"}])
    run_once(paths, now=t1, bus=bus1)
    assert repo.get_task("TASK-HB1").phase == "acked"

    # Then: terminal committed
    t2 = datetime(2026, 3, 24, 12, 10, tzinfo=UTC)
    bus2 = FakePullBus([{"id": 11, "task_id": "TASK-HB1", "status": "COMMITTED", "body": "done"}])
    run_once(paths, now=t2, bus=bus2)

    task = repo.get_task("TASK-HB1")
    assert task.phase == "committed"
    assert task.last_receipt_id == "11"


# ---------------------------------------------------------------------------
# 3. Soft watchdog does NOT trigger while recent heartbeats are arriving
# ---------------------------------------------------------------------------


def test_watchdog_does_not_trigger_with_recent_heartbeat(tmp_path: Path) -> None:
    """Watchdog should skip tasks with recent heartbeats."""
    from agpair.daemon.loop import run_once

    paths = seed_acked_task(tmp_path)
    repo = TaskRepository(paths.db_path)

    # Backdate last_activity_at to 16 minutes ago (past watchdog threshold of 15 min)
    old_time = _to_iso(datetime(2026, 3, 24, 11, 44, tzinfo=UTC))
    with sqlite3.connect(paths.db_path) as conn:
        conn.execute(
            "UPDATE tasks SET last_activity_at=?, updated_at=? WHERE task_id=?",
            (old_time, old_time, "TASK-HB1"),
        )
        conn.commit()

    # But record a recent heartbeat (2 minutes ago)
    recent_hb = _to_iso(datetime(2026, 3, 24, 11, 58, tzinfo=UTC))
    repo.record_heartbeat(task_id="TASK-HB1", heartbeat_at=recent_hb)

    now = datetime(2026, 3, 24, 12, 0, tzinfo=UTC)
    run_once(
        paths,
        now=now,
        bus=EmptyBus(),
        timeout_seconds=1800,
        watchdog_seconds=900,
    )

    task = repo.get_task("TASK-HB1")
    assert task is not None
    # Watchdog should NOT have fired — heartbeat is recent
    assert task.retry_recommended is False
    assert task.phase == "acked"


def test_hard_timeout_does_not_trigger_with_recent_heartbeat(tmp_path: Path) -> None:
    """Hard timeout (stuck) should also skip tasks with recent heartbeats."""
    from agpair.daemon.loop import run_once

    paths = seed_acked_task(tmp_path)
    repo = TaskRepository(paths.db_path)

    # Backdate last_activity_at to 31 minutes ago (past hard timeout of 30 min)
    old_time = _to_iso(datetime(2026, 3, 24, 11, 29, tzinfo=UTC))
    with sqlite3.connect(paths.db_path) as conn:
        conn.execute(
            "UPDATE tasks SET last_activity_at=?, updated_at=? WHERE task_id=?",
            (old_time, old_time, "TASK-HB1"),
        )
        conn.commit()

    # But record a recent heartbeat (1 minute ago)
    recent_hb = _to_iso(datetime(2026, 3, 24, 11, 59, tzinfo=UTC))
    repo.record_heartbeat(task_id="TASK-HB1", heartbeat_at=recent_hb)

    now = datetime(2026, 3, 24, 12, 0, tzinfo=UTC)
    run_once(
        paths,
        now=now,
        bus=EmptyBus(),
        timeout_seconds=1800,
        watchdog_seconds=900,
    )

    task = repo.get_task("TASK-HB1")
    assert task is not None
    # Neither stuck nor retry_recommended — heartbeat is fresh
    assert task.phase == "acked"
    assert task.retry_recommended is False


# ---------------------------------------------------------------------------
# 4. Silent task still gets retry_recommended / stuck after silence
# ---------------------------------------------------------------------------


def test_watchdog_triggers_after_heartbeat_goes_silent(tmp_path: Path) -> None:
    """Even if heartbeats were received earlier, once they go silent the watchdog fires."""
    from agpair.daemon.loop import run_once

    paths = seed_acked_task(tmp_path)
    repo = TaskRepository(paths.db_path)

    # Backdate both last_activity_at and last_heartbeat_at to 16 minutes ago
    old_time = _to_iso(datetime(2026, 3, 24, 11, 44, tzinfo=UTC))
    old_hb = _to_iso(datetime(2026, 3, 24, 11, 44, tzinfo=UTC))
    with sqlite3.connect(paths.db_path) as conn:
        conn.execute(
            "UPDATE tasks SET last_activity_at=?, last_heartbeat_at=?, updated_at=? WHERE task_id=?",
            (old_time, old_hb, old_time, "TASK-HB1"),
        )
        conn.commit()

    now = datetime(2026, 3, 24, 12, 0, tzinfo=UTC)
    run_once(
        paths,
        now=now,
        bus=EmptyBus(),
        timeout_seconds=1800,
        watchdog_seconds=900,
    )

    task = repo.get_task("TASK-HB1")
    assert task is not None
    assert task.phase == "acked"
    assert task.retry_recommended is True

    rows = JournalRepository(paths.db_path).tail("TASK-HB1", limit=5)
    assert any(row.event == "watchdog_retry_recommended" for row in rows)


def test_stuck_triggers_after_heartbeat_goes_silent(tmp_path: Path) -> None:
    """Hard timeout still marks stuck when both activity and heartbeats are stale."""
    from agpair.daemon.loop import run_once

    paths = seed_acked_task(tmp_path)
    repo = TaskRepository(paths.db_path)

    # Both 31 minutes old
    old_time = _to_iso(datetime(2026, 3, 24, 11, 29, tzinfo=UTC))
    with sqlite3.connect(paths.db_path) as conn:
        conn.execute(
            "UPDATE tasks SET last_activity_at=?, last_heartbeat_at=?, updated_at=? WHERE task_id=?",
            (old_time, old_time, old_time, "TASK-HB1"),
        )
        conn.commit()

    now = datetime(2026, 3, 24, 12, 0, tzinfo=UTC)
    run_once(
        paths,
        now=now,
        bus=EmptyBus(),
        timeout_seconds=1800,
        watchdog_seconds=900,
    )

    task = repo.get_task("TASK-HB1")
    assert task is not None
    assert task.phase == "stuck"
    assert task.retry_recommended is True


def test_no_heartbeat_null_still_triggers_watchdog(tmp_path: Path) -> None:
    """Tasks that never received any heartbeat (NULL) should still be watchdogged."""
    from agpair.daemon.loop import run_once

    paths = seed_acked_task(tmp_path)
    repo = TaskRepository(paths.db_path)

    # Backdate last_activity_at to 16 minutes ago, no heartbeat at all
    old_time = _to_iso(datetime(2026, 3, 24, 11, 44, tzinfo=UTC))
    with sqlite3.connect(paths.db_path) as conn:
        conn.execute(
            "UPDATE tasks SET last_activity_at=?, updated_at=? WHERE task_id=?",
            (old_time, old_time, "TASK-HB1"),
        )
        conn.commit()

    now = datetime(2026, 3, 24, 12, 0, tzinfo=UTC)
    run_once(
        paths,
        now=now,
        bus=EmptyBus(),
        timeout_seconds=1800,
        watchdog_seconds=900,
    )

    task = repo.get_task("TASK-HB1")
    assert task is not None
    assert task.phase == "acked"
    assert task.retry_recommended is True


# ---------------------------------------------------------------------------
# 5. Task wait / auto-wait respects heartbeat liveness
# ---------------------------------------------------------------------------


class FakeClock:
    def __init__(self, start: float = 0.0):
        self._now = start

    def time(self) -> float:
        return self._now

    def sleep(self, seconds: float) -> None:
        self._now += seconds


def test_wait_does_not_watchdog_exit_with_fresh_heartbeat(tmp_path: Path) -> None:
    """Wait should NOT trigger watchdog early-exit when task has recent heartbeats."""
    from agpair.cli.wait import wait_for_terminal_phase

    paths = make_paths(tmp_path)
    ensure_database(paths.db_path)
    repo = TaskRepository(paths.db_path)
    repo.create_task(task_id="T-WH1", repo_path="/r")
    repo.mark_acked(task_id="T-WH1", session_id="s-1")
    repo.recommend_retry(task_id="T-WH1")

    # Record a very recent heartbeat
    hb_time = _to_iso(datetime(2026, 3, 24, 12, 0, tzinfo=UTC))
    repo.record_heartbeat(task_id="T-WH1", heartbeat_at=hb_time)

    clock = FakeClock()
    # Use a fixed utcnow that makes the heartbeat appear fresh (< 300s silence)
    fixed_now = datetime(2026, 3, 24, 12, 1, tzinfo=UTC)  # 1 minute after heartbeat

    poll_count = 0

    class TrackingClock(FakeClock):
        def sleep(self, seconds: float) -> None:
            nonlocal poll_count
            poll_count += 1
            super().sleep(seconds)
            # After 1 poll, transition to terminal
            if poll_count == 1:
                repo.mark_evidence_ready(task_id="T-WH1")

    clock = TrackingClock()
    result = wait_for_terminal_phase(
        paths.db_path, "T-WH1",
        interval_seconds=5, timeout_seconds=60,
        heartbeat_silence_seconds=300,
        _clock=clock,
        _utcnow=lambda: fixed_now,
    )
    # Should reach terminal phase, NOT watchdog
    assert result.phase == "evidence_ready"
    assert result.timed_out is False
    assert result.watchdog_triggered is False


def test_wait_watchdog_exits_after_heartbeat_silence(tmp_path: Path) -> None:
    """Wait should trigger watchdog exit when heartbeat is stale."""
    from agpair.cli.wait import wait_for_terminal_phase

    paths = make_paths(tmp_path)
    ensure_database(paths.db_path)
    repo = TaskRepository(paths.db_path)
    repo.create_task(task_id="T-WH2", repo_path="/r")
    repo.mark_acked(task_id="T-WH2", session_id="s-1")
    repo.recommend_retry(task_id="T-WH2")

    # Record a stale heartbeat (10 minutes ago)
    stale_hb = _to_iso(datetime(2026, 3, 24, 11, 50, tzinfo=UTC))
    repo.record_heartbeat(task_id="T-WH2", heartbeat_at=stale_hb)

    clock = FakeClock()
    fixed_now = datetime(2026, 3, 24, 12, 1, tzinfo=UTC)  # 11 minutes after heartbeat

    result = wait_for_terminal_phase(
        paths.db_path, "T-WH2",
        interval_seconds=5, timeout_seconds=60,
        heartbeat_silence_seconds=300,
        _clock=clock,
        _utcnow=lambda: fixed_now,
    )
    assert result.phase == "acked"
    assert result.watchdog_triggered is True
    assert result.timed_out is False


def test_wait_no_heartbeat_watchdog_still_triggers(tmp_path: Path) -> None:
    """Existing behavior preserved: acked + retry_recommended + no heartbeat -> watchdog."""
    from agpair.cli.wait import wait_for_terminal_phase

    paths = make_paths(tmp_path)
    ensure_database(paths.db_path)
    repo = TaskRepository(paths.db_path)
    repo.create_task(task_id="T-WH3", repo_path="/r")
    repo.mark_acked(task_id="T-WH3", session_id="s-1")
    repo.recommend_retry(task_id="T-WH3")
    # No heartbeat recorded — NULL last_heartbeat_at

    clock = FakeClock()
    result = wait_for_terminal_phase(
        paths.db_path, "T-WH3",
        interval_seconds=5, timeout_seconds=60,
        _clock=clock,
    )
    assert result.phase == "acked"
    assert result.watchdog_triggered is True


# ---------------------------------------------------------------------------
# 6. Schema upgrade path for existing DB
# ---------------------------------------------------------------------------


def test_schema_migration_adds_heartbeat_column(tmp_path: Path) -> None:
    """An existing DB without last_heartbeat_at should get it via migration."""
    db_path = tmp_path / "old.db"

    # Create old-schema DB without last_heartbeat_at
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
        conn.execute(
            "INSERT INTO tasks VALUES (?, ?, 'acked', 'session-1', 1, 0, NULL, NULL, 0, ?, ?, ?)",
            ("OLD-TASK", "/repo", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
        )
        conn.commit()

    # Verify old schema has no last_heartbeat_at
    with sqlite3.connect(db_path) as conn:
        cols_before = {row[1] for row in conn.execute("PRAGMA table_info(tasks)").fetchall()}
    assert "last_heartbeat_at" not in cols_before

    # Now call ensure_database which triggers migration
    ensure_database(db_path)

    # Verify column exists after migration
    with sqlite3.connect(db_path) as conn:
        cols_after = {row[1] for row in conn.execute("PRAGMA table_info(tasks)").fetchall()}
    assert "last_heartbeat_at" in cols_after

    # Verify existing data is intact and last_heartbeat_at is NULL
    repo = TaskRepository(db_path)
    task = repo.get_task("OLD-TASK")
    assert task is not None
    assert task.phase == "acked"
    assert task.last_heartbeat_at is None

    # Verify we can write heartbeats to the migrated DB
    repo.record_heartbeat(task_id="OLD-TASK", heartbeat_at="2026-03-24T12:00:00Z")
    task = repo.get_task("OLD-TASK")
    assert task.last_heartbeat_at == "2026-03-24T12:00:00Z"


def test_schema_migration_is_idempotent(tmp_path: Path) -> None:
    """Calling ensure_database multiple times should not fail."""
    db_path = tmp_path / "test.db"
    ensure_database(db_path)
    ensure_database(db_path)
    ensure_database(db_path)

    with sqlite3.connect(db_path) as conn:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(tasks)").fetchall()}
    assert "last_heartbeat_at" in cols


# ---------------------------------------------------------------------------
# 7. Integration: end-to-end heartbeat prevents soft watchdog then silence trips it
# ---------------------------------------------------------------------------


def test_end_to_end_heartbeat_prevents_then_silence_trips_watchdog(tmp_path: Path) -> None:
    """Full scenario: heartbeats keep task alive, then silence triggers watchdog.

    Timeline (watchdog=900s/15min, hard_timeout=1800s/30min):
    - last_activity_at = base+5min (set once, never changes)
    - t_hb1 = base+22min: RUNNING heartbeat -> heartbeat fresh, no watchdog
    - t_hb2 = base+24min: RUNNING heartbeat -> heartbeat fresh, no watchdog
    - t_silence = base+42min: no heartbeat for 18min ->
        watchdog_cutoff = base+27min, hard_cutoff = base+12min
        last_activity_at(base+5) >= hard_cutoff(base+12)? NO -> stuck path
      So use t_silence = base+35min:
        watchdog_cutoff = base+20min, hard_cutoff = base+5min
        last_activity_at(base+5) >= hard_cutoff(base+5)? YES (>=)
        last_activity_at(base+5) < watchdog_cutoff(base+20)? YES -> watchdog band
        last_heartbeat_at(base+24) < watchdog_cutoff(base+20)? NO -> heartbeat fresh!
      Need later: t_silence = base+40min:
        watchdog_cutoff = base+25min, hard_cutoff = base+10min
        last_activity_at(base+5) >= hard_cutoff(base+10)? NO -> stuck path not watchdog

    Simplest correct timeline:
    - last_activity_at = base+10min
    - t_hb = base+22min: heartbeat
    - t_silence = base+40min:
        watchdog_cutoff = base+25min, hard_cutoff = base+10min
        last_activity_at(base+10) >= hard_cutoff(base+10)? YES
        last_activity_at(base+10) < watchdog_cutoff(base+25)? YES -> watchdog band ✓
        last_heartbeat_at(base+22) < watchdog_cutoff(base+25)? YES -> silent ✓
    """
    from agpair.daemon.loop import run_once

    paths = seed_acked_task(tmp_path, task_id="TASK-E2E")
    repo = TaskRepository(paths.db_path)

    base = datetime(2026, 3, 24, 12, 0, tzinfo=UTC)

    # Fix last_activity_at = base+10min
    activity_time = _to_iso(base + timedelta(minutes=10))
    with sqlite3.connect(paths.db_path) as conn:
        conn.execute(
            "UPDATE tasks SET last_activity_at=?, updated_at=? WHERE task_id=?",
            (activity_time, activity_time, "TASK-E2E"),
        )
        conn.commit()

    # Phase 1: heartbeat at base+22min keeps task alive
    # At this point activity is 12min old (not yet past watchdog 15min) — but
    # even if it were, the fresh heartbeat would protect the task.
    t_hb = base + timedelta(minutes=22)
    bus_hb = FakePullBus([{"id": 100, "task_id": "TASK-E2E", "status": "RUNNING", "body": "alive"}])
    run_once(paths, now=t_hb, bus=bus_hb, timeout_seconds=1800, watchdog_seconds=900)

    task = repo.get_task("TASK-E2E")
    assert task.phase == "acked"
    assert task.retry_recommended is False, "heartbeat should prevent watchdog"
    assert task.last_heartbeat_at == _to_iso(t_hb)

    # Phase 2: silence at base+40min — both activity(30min old) and heartbeat(18min old)
    # are past watchdog_cutoff(base+25min).
    t_silence = base + timedelta(minutes=40)
    run_once(paths, now=t_silence, bus=EmptyBus(), timeout_seconds=1800, watchdog_seconds=900)

    task = repo.get_task("TASK-E2E")
    assert task.phase == "acked"
    assert task.retry_recommended is True, "silence should trigger watchdog"

    rows = JournalRepository(paths.db_path).tail("TASK-E2E", limit=10)
    assert any(row.event == "watchdog_retry_recommended" for row in rows)


# ---------------------------------------------------------------------------
# 8. TaskRecord.last_heartbeat_at defaults and retry resets it
# ---------------------------------------------------------------------------


def test_new_task_has_null_heartbeat(tmp_path: Path) -> None:
    paths = make_paths(tmp_path)
    ensure_database(paths.db_path)
    repo = TaskRepository(paths.db_path)
    repo.create_task(task_id="T-NEW", repo_path="/r")
    task = repo.get_task("T-NEW")
    assert task.last_heartbeat_at is None


def test_retry_dispatch_resets_heartbeat(tmp_path: Path) -> None:
    paths = make_paths(tmp_path)
    ensure_database(paths.db_path)
    repo = TaskRepository(paths.db_path)
    repo.create_task(task_id="T-RETRY", repo_path="/r")
    repo.mark_acked(task_id="T-RETRY", session_id="s-1")
    repo.record_heartbeat(task_id="T-RETRY", heartbeat_at="2026-03-24T12:00:00Z")

    task = repo.get_task("T-RETRY")
    assert task.last_heartbeat_at is not None

    updated = repo.apply_retry_dispatch(task_id="T-RETRY")
    assert updated.last_heartbeat_at is None
    assert updated.phase == "new"
