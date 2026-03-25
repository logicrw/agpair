from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
import json
import re
import sqlite3
import time

from agpair.config import AppPaths
from agpair.models import utcnow_iso
from agpair.storage.db import connect, ensure_database
from agpair.storage.journal import JournalRepository
from agpair.storage.receipts import ReceiptRepository
from agpair.storage.tasks import IllegalTransitionError, TaskNotFoundError, TaskRepository
from agpair.transport.bus import AgentBusClient
from agpair.transport import messages

SESSION_ID_RE = re.compile(r"session_id\s*[:=]\s*(?P<session>[^\s]+)")
DEFAULT_WATCHDOG_SECONDS = 900
DEFAULT_CLEANUP_RETENTION_DAYS = 30
CLEANUP_INTERVAL_SECONDS = 2592000  # 30 days


def run_forever(
    paths: AppPaths,
    *,
    interval_ms: int = 1000,
    timeout_seconds: int = 1800,
    watchdog_seconds: int = DEFAULT_WATCHDOG_SECONDS,
    bus=None,
    shutdown_check=None,
) -> None:
    while True:
        if shutdown_check and shutdown_check():
            return
        run_once(paths, timeout_seconds=timeout_seconds, watchdog_seconds=watchdog_seconds, bus=bus)
        if _cleanup_due(paths):
            auto_cleanup(paths)
            _write_cleanup_marker(paths)
        time.sleep(interval_ms / 1000.0)


def _cleanup_marker_path(paths: AppPaths) -> Path:
    return paths.root / ".last_cleanup"


def _cleanup_due(paths: AppPaths) -> bool:
    """Check if enough time has passed since last cleanup (persisted to disk)."""
    marker = _cleanup_marker_path(paths)
    if not marker.exists():
        return True
    try:
        last = float(marker.read_text().strip())
        return time.time() - last >= CLEANUP_INTERVAL_SECONDS
    except (ValueError, OSError):
        return True


def _write_cleanup_marker(paths: AppPaths) -> None:
    marker = _cleanup_marker_path(paths)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(str(time.time()))


def run_once(
    paths: AppPaths,
    *,
    now: datetime | None = None,
    bus=None,
    timeout_seconds: int = 1800,
    watchdog_seconds: int = DEFAULT_WATCHDOG_SECONDS,
) -> None:
    ensure_database(paths.db_path)
    current = now or datetime.now(UTC)
    client = bus or AgentBusClient(paths.agent_bus_bin)
    processed, touched_task_ids = ingest_new_receipts(paths, client, current=current)
    scan_workspace_activity(paths, current=current)
    watchdog_count, watchdog_task_ids = mark_watchdog_tasks(
        paths,
        current=current,
        watchdog_seconds=watchdog_seconds,
        timeout_seconds=timeout_seconds,
        skip_task_ids=touched_task_ids,
    )
    stuck = mark_stuck_tasks(
        paths,
        current=current,
        timeout_seconds=timeout_seconds,
        skip_task_ids=touched_task_ids | watchdog_task_ids,
    )
    write_daemon_health(
        paths,
        {
            "running": True,
            "last_tick_at": to_iso(current),
            "processed_receipts": processed,
            "watchdog_recommended": watchdog_count,
            "stuck_marked": stuck,
        },
    )


def scan_workspace_activity(paths: AppPaths, *, current: datetime) -> None:
    """Inspect acked tasks' repos for fresh workspace activity."""
    from agpair.runtime_liveness import detect_workspace_activity

    tasks = TaskRepository(paths.db_path)
    for task in tasks.list_tasks(phase="acked", limit=100):
        activity_at = detect_workspace_activity(task.repo_path)
        if activity_at is not None:
            try:
                tasks.update_workspace_activity(task_id=task.task_id, activity_at=activity_at)
            except TaskNotFoundError:
                pass


def ingest_new_receipts(paths: AppPaths, client, *, current: datetime) -> tuple[int, set[str]]:
    tasks = TaskRepository(paths.db_path)
    receipts = ReceiptRepository(paths.db_path)
    journal = JournalRepository(paths.db_path)
    count = 0
    touched_task_ids: set[str] = set()
    # Pull receipts only for tasks this daemon owns (by task_id).
    # This prevents multiple daemons from stealing each other's messages.
    active_tasks = tasks.list_tasks(phase="new", limit=100) + tasks.list_tasks(phase="acked", limit=100)
    if not active_tasks:
        return 0, set()
    all_messages: list[dict] = []
    for task in active_tasks:
        all_messages.extend(client.pull_receipts(task_id=task.task_id))
    for message in all_messages:
        message_id = str(message.get("id", ""))
        task_id = str(message.get("task_id", ""))
        status = str(message.get("status", ""))
        body = str(message.get("body", ""))
        if not message_id or not task_id or not status:
            continue

        # Parse delivery header for terminal statuses only
        from agpair.delivery import parse_delivery_header

        parsed = parse_delivery_header(status, body)
        delivery_id = parsed.delivery_id
        clean_body = parsed.clean_body

        is_new = receipts.record(message_id, task_id, status, delivery_id=delivery_id)
        if not is_new:
            continue
        current_task = tasks.get_task(task_id)
        if current_task is not None and is_stale_receipt(current_task.last_receipt_id, message_id):
            journal.append(task_id, "daemon", "receipt_stale", f"{status} id={message_id}", "stale")
            continue
        try:
            if status == messages.ACK:
                session_id = extract_session_id(body)
                if not session_id:
                    journal.append(task_id, "daemon", "ack_invalid", body, "invalid")
                    continue
                tasks.mark_acked(task_id=task_id, session_id=session_id)
                journal.append(task_id, "daemon", "acked", f"session_id={session_id}")
            elif status == messages.RUNNING:
                # Non-terminal liveness heartbeat — record timestamp only,
                # do NOT change phase or last_activity_at.
                tasks.record_heartbeat(task_id=task_id, heartbeat_at=to_iso(current))
                journal.append(task_id, "daemon", "heartbeat", body or "RUNNING")
            elif status == messages.EVIDENCE_PACK:
                tasks.mark_evidence_ready(task_id=task_id, last_receipt_id=message_id)
                journal.append(task_id, "daemon", "evidence_ready", clean_body)
            elif status == messages.BLOCKED:
                tasks.mark_blocked(task_id=task_id, reason=clean_body or "blocked")
                journal.append(task_id, "daemon", "blocked", clean_body)
            elif status == messages.COMMITTED:
                tasks.mark_committed(task_id=task_id, last_receipt_id=message_id)
                journal.append(task_id, "daemon", "committed", clean_body)
            else:
                journal.append(task_id, "daemon", "receipt_ignored", f"{status}: {body}", "invalid")
        except (TaskNotFoundError, IllegalTransitionError):
            continue
        count += 1
        touched_task_ids.add(task_id)
    return count, touched_task_ids


def mark_stuck_tasks(
    paths: AppPaths,
    *,
    current: datetime,
    timeout_seconds: int,
    skip_task_ids: set[str] | None = None,
) -> int:
    tasks = TaskRepository(paths.db_path)
    journal = JournalRepository(paths.db_path)
    cutoff = to_iso(current - timedelta(seconds=timeout_seconds))
    count = 0
    excluded = skip_task_ids or set()
    for task in tasks.list_stale_acked_tasks(cutoff):
        if task.task_id in excluded:
            continue
        tasks.mark_stuck(task_id=task.task_id, reason="no progress before timeout")
        tasks.recommend_retry(task_id=task.task_id, retry_count=task.retry_count)
        journal.append(task.task_id, "daemon", "stuck", "retry recommended after timeout")
        count += 1
    return count


def mark_watchdog_tasks(
    paths: AppPaths,
    *,
    current: datetime,
    watchdog_seconds: int,
    timeout_seconds: int,
    skip_task_ids: set[str] | None = None,
) -> tuple[int, set[str]]:
    if watchdog_seconds <= 0 or watchdog_seconds >= timeout_seconds:
        return 0, set()

    tasks = TaskRepository(paths.db_path)
    journal = JournalRepository(paths.db_path)
    watchdog_cutoff = to_iso(current - timedelta(seconds=watchdog_seconds))
    hard_timeout_cutoff = to_iso(current - timedelta(seconds=timeout_seconds))
    count = 0
    touched: set[str] = set()
    excluded = skip_task_ids or set()
    for task in tasks.list_watchdog_candidates(
        watchdog_cutoff_iso=watchdog_cutoff,
        hard_timeout_cutoff_iso=hard_timeout_cutoff,
    ):
        if task.task_id in excluded:
            continue
        tasks.recommend_retry(task_id=task.task_id, retry_count=task.retry_count)
        journal.append(task.task_id, "daemon", "watchdog_retry_recommended", "no progress after watchdog threshold")
        count += 1
        touched.add(task.task_id)
    return count, touched


def write_daemon_health(paths: AppPaths, payload: dict) -> None:
    paths.root.mkdir(parents=True, exist_ok=True)
    paths.status_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    with connect(paths.db_path) as conn:
        conn.execute(
            """
            INSERT INTO daemon_health (name, updated_at, body)
            VALUES ('main', ?, ?)
            ON CONFLICT(name) DO UPDATE SET updated_at=excluded.updated_at, body=excluded.body
            """,
            (payload["last_tick_at"], json.dumps(payload, ensure_ascii=False)),
        )
        conn.commit()


def read_daemon_status(paths: AppPaths) -> dict:
    if not paths.status_path.exists():
        return {
            "running": False,
            "last_tick_at": None,
            "processed_receipts": 0,
            "watchdog_recommended": 0,
            "stuck_marked": 0,
        }
    return json.loads(paths.status_path.read_text(encoding="utf-8"))


def extract_session_id(body: str) -> str | None:
    match = SESSION_ID_RE.search(body)
    if not match:
        return None
    return match.group("session")


def to_iso(value: datetime) -> str:
    return value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def auto_cleanup(paths: AppPaths, *, retention_days: int = DEFAULT_CLEANUP_RETENTION_DAYS) -> None:
    """Delete old journals, receipts, and terminal tasks. Called by daemon every 30 days."""
    cutoff = to_iso(datetime.now(UTC) - timedelta(days=retention_days))
    journal = JournalRepository(paths.db_path)
    receipts = ReceiptRepository(paths.db_path)
    tasks = TaskRepository(paths.db_path)
    j = journal.delete_older_than(cutoff)
    r = receipts.delete_older_than(cutoff)
    t = tasks.delete_terminal_older_than(cutoff)
    if j or r or t:
        journal.append(
            "daemon", "daemon", "auto_cleanup",
            f"deleted journals={j} receipts={r} tasks={t} older_than={retention_days}d",
        )


def is_stale_receipt(last_receipt_id: str | None, incoming_receipt_id: str) -> bool:
    if not last_receipt_id:
        return False
    try:
        return int(incoming_receipt_id) <= int(last_receipt_id)
    except ValueError:
        return False
