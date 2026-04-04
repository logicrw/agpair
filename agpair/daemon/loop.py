from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
import json
import re
import sqlite3
import time

from agpair.config import AppPaths
from agpair.models import utcnow_iso
from agpair.terminal_receipts import blocked_reason_from_receipt, parse_structured_terminal_receipt
from agpair.storage.db import connect, ensure_database
from agpair.storage.journal import JournalRepository
from agpair.storage.receipts import ReceiptRepository
from agpair.storage.tasks import IllegalTransitionError, TaskNotFoundError, TaskRepository
from agpair.transport.bus import AgentBusClient, BusPullError
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
    processed, touched_task_ids, bus_errors = ingest_new_receipts(paths, client, current=current)
    scan_workspace_activity(paths, current=current)
    auto_closed = auto_close_evidence_ready_tasks(paths, skip_task_ids=touched_task_ids)
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
    health: dict = {
        "running": True,
        "last_tick_at": to_iso(current),
        "processed_receipts": processed,
        "auto_closed_from_repo": auto_closed,
        "watchdog_recommended": watchdog_count,
        "stuck_marked": stuck,
    }
    if bus_errors:
        health["bus_errors"] = bus_errors
    write_daemon_health(paths, health)


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


def detect_committed_task_in_repo(repo_path: str, task_id: str) -> str | None:
    """Check if a git commit containing *task_id* exists in *repo_path*.

    Uses ``git log --all --grep=<task_id> --format=%H -1`` to find a commit
    whose message contains the task_id.  This is strong repo-side evidence
    that the delegated work already landed as a commit.

    Returns the full commit SHA if found, or ``None`` if not found or if the
    directory is not a valid git repository.
    """
    import subprocess as _subprocess

    try:
        result = _subprocess.run(
            ["git", "log", "--all", f"--grep={task_id}", "--format=%H", "-1"],
            capture_output=True,
            text=True,
            cwd=repo_path,
            timeout=10,
        )
        if result.returncode != 0:
            return None
    except (_subprocess.SubprocessError, FileNotFoundError, OSError):
        return None

    sha = result.stdout.strip()
    return sha if sha else None


def auto_close_evidence_ready_tasks(
    paths: AppPaths,
    *,
    skip_task_ids: set[str] | None = None,
) -> int:
    """Auto-close evidence_ready tasks whose delegated commit already landed.

    For each evidence_ready task that was NOT just touched by receipt ingestion,
    check if a git commit containing the task_id exists in the task's repo.
    If so, transition the task to ``committed`` and record a journal entry
    explaining the auto-close.

    Returns the number of tasks auto-closed.
    """
    tasks = TaskRepository(paths.db_path)
    journal = JournalRepository(paths.db_path)
    excluded = skip_task_ids or set()
    count = 0

    for task in tasks.list_tasks(phase="evidence_ready", limit=100):
        if task.task_id in excluded:
            continue

        commit_sha = detect_committed_task_in_repo(task.repo_path, task.task_id)
        if commit_sha is None:
            continue

        try:
            tasks.mark_committed(task_id=task.task_id)
            journal.append(
                task.task_id,
                "daemon",
                "auto_committed_from_repo_evidence",
                f"Auto-closed: git commit {commit_sha} in {task.repo_path} "
                f"contains task_id {task.task_id}. "
                f"Terminal receipt was never received but repo evidence confirms commit landed.",
            )
            count += 1
        except (TaskNotFoundError, IllegalTransitionError):
            continue

    return count


def ingest_new_receipts(paths: AppPaths, client, *, current: datetime) -> tuple[int, set[str], int]:
    """Pull receipts from agent-bus and process them.

    Returns ``(processed_count, touched_task_ids, bus_error_count)``.
    A non-zero *bus_error_count* means some per-task pulls failed transiently;
    the daemon tick should continue regardless.
    """
    tasks = TaskRepository(paths.db_path)
    receipts = ReceiptRepository(paths.db_path)
    journal = JournalRepository(paths.db_path)
    count = 0
    bus_errors = 0
    touched_task_ids: set[str] = set()
    # Pull receipts only for tasks this daemon owns (by task_id).
    # Uses per-task --task-id filter so agent-bus only consumes matching
    # messages, leaving other daemons' messages untouched.
    active_tasks = (
        tasks.list_tasks(phase="new", limit=100)
        + tasks.list_tasks(phase="acked", limit=100)
        + tasks.list_tasks(phase="evidence_ready", limit=100)
    )
    if not active_tasks:
        return 0, set(), 0
    all_messages: list[dict] = []
    from agpair.executors import get_executor
    for task in active_tasks:
        exec_instance = get_executor(task.executor_backend)
        if exec_instance and task.phase == "acked" and task.antigravity_session_id:
            state = exec_instance.poll(task.task_id, task.antigravity_session_id, attempt_no=task.attempt_no)
            if state is not None:
                if state.is_done:
                    msg_id = f"{task.executor_backend}-{task.task_id}-done"
                    receipt = state.receipt or {}
                    msg = {
                        "id": msg_id,
                        "task_id": task.task_id,
                        "status": receipt.get("status", messages.BLOCKED),
                        "body": json.dumps(receipt, ensure_ascii=False)
                    }
                    all_messages.append(msg)
                else:
                    msg_id = f"{task.executor_backend}-{task.task_id}-running-{int(current.timestamp()) // 10}"
                    msg = {
                        "id": msg_id,
                        "task_id": task.task_id,
                        "status": messages.RUNNING,
                        "body": f"local {task.executor_backend} is still running"
                    }
                    all_messages.append(msg)
                continue
                
        # If executor didn't handle it locally, try to pull from bus
        if task.executor_backend not in {"codex_cli", "gemini_cli"}:
            try:
                all_messages.extend(client.pull_receipts(task_id=task.task_id))
            except BusPullError as exc:
                bus_errors += 1
                journal.append(
                    task.task_id, "daemon", "bus_pull_error",
                    f"transient bus pull failure: {exc}", "warning",
                )
            
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
        structured_receipt = None
        if status in {messages.EVIDENCE_PACK, messages.BLOCKED, messages.COMMITTED}:
            structured_receipt = parse_structured_terminal_receipt(
                clean_body,
                expected_status=status,
                expected_task_id=task_id,
            )
        journal_body = structured_receipt.raw_body if structured_receipt is not None else clean_body

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
                journal.append(task_id, "daemon", "heartbeat", body or "RUNNING", classification="transient")
            elif status == messages.EVIDENCE_PACK:
                tasks.mark_evidence_ready(task_id=task_id, last_receipt_id=message_id)
                journal.append(task_id, "daemon", "evidence_ready", journal_body)
                if current_task and current_task.antigravity_session_id:
                    from agpair.executors import get_executor
                    exec_instance = get_executor(current_task.executor_backend)
                    if exec_instance:
                        exec_instance.cleanup(current_task.antigravity_session_id)
            elif status == messages.BLOCKED:
                reason = clean_body or "blocked"
                if structured_receipt is not None:
                    reason = blocked_reason_from_receipt(structured_receipt, reason)
                tasks.mark_blocked(task_id=task_id, reason=reason)
                journal.append(task_id, "daemon", "blocked", journal_body)
                if current_task and current_task.antigravity_session_id:
                    from agpair.executors import get_executor
                    exec_instance = get_executor(current_task.executor_backend)
                    if exec_instance:
                        exec_instance.cleanup(current_task.antigravity_session_id)
            elif status == messages.COMMITTED:
                tasks.mark_committed(task_id=task_id, last_receipt_id=message_id)
                journal.append(task_id, "daemon", "committed", journal_body)
            elif status == messages.REVIEW_ACK:
                journal.append(task_id, "daemon", "review_ack", clean_body)
            elif status == messages.REVIEW_NACK:
                journal.append(task_id, "daemon", "review_nack", clean_body)
            elif status == messages.APPROVE_ACK:
                journal.append(task_id, "daemon", "approve_ack", clean_body)
            elif status == messages.APPROVE_NACK:
                journal.append(task_id, "daemon", "approve_nack", clean_body)
            else:
                journal.append(task_id, "daemon", "receipt_ignored", f"{status}: {body}", "invalid")
        except (TaskNotFoundError, IllegalTransitionError):
            continue
        count += 1
        touched_task_ids.add(task_id)
    return count, touched_task_ids, bus_errors


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
        if task.antigravity_session_id:
            from agpair.executors import get_executor
            exec_instance = get_executor(task.executor_backend)
            if exec_instance:
                exec_instance.cleanup(task.antigravity_session_id)
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
