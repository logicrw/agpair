#!/usr/bin/env python3
"""
relay_bus.py — Local SQLite Agent Bus CLI

A stable interface for Codex ↔ Antigravity task traffic over ~/.relay_buffer.db.
Replaces ad-hoc hand-written SQL with clean subcommands:

    relay_bus.py send   --sender code --task-id X --status ACK --body "..."
    relay_bus.py fetch  [--sender desktop] [--unread] [--limit 10]
    relay_bus.py pull   --sender desktop --reader code --full
    relay_bus.py watch  --sender desktop --reader code --full
    relay_bus.py ack    --ids 501,502 --reader code
    relay_bus.py health

Environment:
    RELAY_DB  — override DB path (default: ~/.relay_buffer.db)
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import signal
import sqlite3
import sys
import time
from pathlib import Path

DEFAULT_DB = os.path.expanduser("~/.relay_buffer.db")
VALID_SENDERS = ("desktop", "code")
VALID_STATUSES = (
    "TASK",
    "ACK",
    "EVIDENCE_PACK",
    "BLOCKED",
    "REVIEW",
    "APPROVED",
    "COMMITTED",
    "NEXT",
)
LOCK_RETRIES = 3
LOCK_RETRY_DELAY_MS = 250
STOP = False

CREATE_TABLE_SQL = """\
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sender TEXT NOT NULL,
    message TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    read_by_desktop_at TEXT,
    read_by_code_at TEXT
);
"""


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _db_path() -> str:
    return os.environ.get("RELAY_DB", DEFAULT_DB)


def _connect(db: str | None = None, *, timeout: float = 10) -> sqlite3.Connection:
    path = db or _db_path()
    exists = Path(path).exists()
    conn = sqlite3.connect(path, timeout=timeout)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    if not exists:
        conn.execute(CREATE_TABLE_SQL)
        conn.commit()
    else:
        conn.execute(CREATE_TABLE_SQL)
        conn.commit()
    return conn


def _is_lock_error(exc: sqlite3.Error) -> bool:
    msg = str(exc).lower()
    return "database is locked" in msg or "database table is locked" in msg or "busy" in msg


def _with_lock_retry(fn, *, action: str):
    last_exc: sqlite3.Error | None = None
    for attempt in range(LOCK_RETRIES + 1):
        try:
            return fn()
        except sqlite3.OperationalError as exc:
            if not _is_lock_error(exc):
                raise
            last_exc = exc
            if attempt >= LOCK_RETRIES:
                break
            time.sleep((LOCK_RETRY_DELAY_MS * (attempt + 1)) / 1000)
    raise RuntimeError(
        f"{action} failed after {LOCK_RETRIES + 1} attempts: relay DB remained locked"
    ) from last_exc


def _format_message(task_id: str, status: str, body: str) -> str:
    parts = [f"TASK_ID: {task_id}", f"STATUS: {status}"]
    if body.strip():
        parts.append("")
        parts.append(body.strip())
    return "\n".join(parts)


def _parse_message(raw: str) -> dict:
    result: dict = {"raw": raw, "task_id": None, "status": None, "body": ""}
    lines = raw.split("\n")
    body_start = 0
    for i, line in enumerate(lines):
        if line.startswith("TASK_ID:"):
            result["task_id"] = line.split(":", 1)[1].strip()
            body_start = i + 1
        elif line.startswith("STATUS:"):
            result["status"] = line.split(":", 1)[1].strip()
            body_start = i + 1
    body_lines = lines[body_start:]
    while body_lines and not body_lines[0].strip():
        body_lines.pop(0)
    result["body"] = "\n".join(body_lines)
    return result


def _resolve_body(args: argparse.Namespace) -> str:
    sources: list[tuple[str, str]] = []
    inline = getattr(args, "body", None) or ""

    body_file = getattr(args, "body_file", None)
    if body_file:
        path = Path(body_file)
        if not path.is_file():
            print(f"ERROR: --body-file not found: {body_file}", file=sys.stderr)
            sys.exit(1)
        sources.append(("--body-file", path.read_text(encoding="utf-8")))

    body_stdin = getattr(args, "body_stdin", False)
    if body_stdin:
        if sys.stdin.isatty():
            print(
                "ERROR: --body-stdin specified but stdin is a TTY (pipe content instead)",
                file=sys.stderr,
            )
            sys.exit(1)
        sources.append(("--body-stdin", sys.stdin.read()))

    if len(sources) > 1:
        names = [s[0] for s in sources]
        print(f"ERROR: conflicting body sources: {', '.join(names)}. Use only one.", file=sys.stderr)
        sys.exit(1)

    if sources:
        if inline.strip():
            print(f"ERROR: --body and {sources[0][0]} both provided. Use only one.", file=sys.stderr)
            sys.exit(1)
        return sources[0][1]

    return inline


def _pull_messages(
    *,
    db: str | None,
    reader: str,
    sender: str | None,
    task_id: str | None,
    repo_path: str | None,
    limit: int,
    full: bool,
) -> list[dict]:
    conditions = [f"read_by_{reader}_at IS NULL"]
    params: list = []

    if sender:
        conditions.append("sender = ?")
        params.append(sender)

    if task_id:
        conditions.append("message LIKE ?")
        params.append(f"%TASK_ID: {task_id}%")

    if repo_path:
        conditions.append("message LIKE ?")
        params.append(f"%repo_path: {repo_path}%")

    where = " AND ".join(conditions)
    limit = min(limit or 20, 100)
    now = _now_iso()
    col = f"read_by_{reader}_at"

    def _op():
        conn = _connect(db)
        try:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                f"SELECT id, sender, message, timestamp, read_by_desktop_at, read_by_code_at "
                f"FROM messages WHERE {where} ORDER BY id ASC LIMIT ?",
                [*params, limit],
            ).fetchall()
            ids = [r[0] for r in rows]
            if ids:
                placeholders = ",".join("?" for _ in ids)
                conn.execute(
                    f"UPDATE messages SET {col} = ? WHERE id IN ({placeholders}) AND {col} IS NULL",
                    [now, *ids],
                )
            conn.commit()

            results = []
            for row in rows:
                parsed = _parse_message(row[2])
                results.append(
                    {
                        "id": row[0],
                        "sender": row[1],
                        "task_id": parsed["task_id"],
                        "status": parsed["status"],
                        "body": parsed["body"][:500] if not full else parsed["body"],
                        "timestamp": row[3],
                        "read_by_desktop": True if reader == "desktop" else (row[4] is not None),
                        "read_by_code": True if reader == "code" else (row[5] is not None),
                    }
                )
            return results
        finally:
            conn.close()

    return _with_lock_retry(_op, action="pull")


def _handle_signal(signum, frame) -> None:  # type: ignore[no-untyped-def]
    global STOP
    STOP = True


def cmd_send(args: argparse.Namespace) -> int:
    sender = args.sender
    if sender not in VALID_SENDERS:
        print(f"ERROR: sender must be one of {VALID_SENDERS}", file=sys.stderr)
        return 1

    status = args.status.upper()
    if status not in VALID_STATUSES:
        print(
            f"WARNING: non-standard status '{status}' (expected one of {VALID_STATUSES})",
            file=sys.stderr,
        )

    task_id = args.task_id
    body = _resolve_body(args)
    msg = _format_message(task_id, status, body)
    now = _now_iso()

    try:
        def _op():
            conn = _connect(args.db)
            try:
                cur = conn.execute(
                    "INSERT INTO messages (sender, message, timestamp) VALUES (?, ?, ?)",
                    (sender, msg, now),
                )
                conn.commit()
                return cur.lastrowid
            finally:
                conn.close()

        row_id = _with_lock_retry(_op, action="send")
        print(json.dumps({"ok": True, "id": row_id, "task_id": task_id, "status": status, "timestamp": now}))
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc), "action": "send"}), file=sys.stderr)
        return 1


def cmd_fetch(args: argparse.Namespace) -> int:
    try:
        conditions = []
        params: list = []

        if args.sender:
            conditions.append("sender = ?")
            params.append(args.sender)

        if args.unread:
            reader = args.reader or ("code" if args.sender == "desktop" else "desktop")
            col = f"read_by_{reader}_at"
            conditions.append(f"{col} IS NULL")

        if args.task_id:
            conditions.append("message LIKE ?")
            params.append(f"%TASK_ID: {args.task_id}%")

        if getattr(args, "repo_path", None):
            conditions.append("message LIKE ?")
            params.append(f"%repo_path: {args.repo_path}%")

        where = " AND ".join(conditions) if conditions else "1=1"
        limit = min(args.limit or 20, 100)
        sql = (
            "SELECT id, sender, message, timestamp, read_by_desktop_at, read_by_code_at "
            f"FROM messages WHERE {where} ORDER BY id DESC LIMIT ?"
        )
        params.append(limit)

        def _op():
            conn = _connect(args.db)
            try:
                rows = conn.execute(sql, params).fetchall()
                results = []
                for row in rows:
                    parsed = _parse_message(row[2])
                    results.append(
                        {
                            "id": row[0],
                            "sender": row[1],
                            "task_id": parsed["task_id"],
                            "status": parsed["status"],
                            "body": parsed["body"][:500] if not args.full else parsed["body"],
                            "timestamp": row[3],
                            "read_by_desktop": row[4] is not None,
                            "read_by_code": row[5] is not None,
                        }
                    )
                return results
            finally:
                conn.close()

        results = _with_lock_retry(_op, action="fetch")
        print(json.dumps({"count": len(results), "messages": results}, indent=2))
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc), "action": "fetch"}), file=sys.stderr)
        return 1


def cmd_pull(args: argparse.Namespace) -> int:
    reader = args.reader
    if reader not in VALID_SENDERS:
        print(
            json.dumps({"ok": False, "error": f"reader must be one of {VALID_SENDERS}", "action": "pull"}),
            file=sys.stderr,
        )
        return 1

    try:
        results = _pull_messages(
            db=args.db,
            reader=reader,
            sender=args.sender,
            task_id=args.task_id,
            repo_path=getattr(args, "repo_path", None),
            limit=args.limit,
            full=args.full,
        )
        print(json.dumps({"ok": True, "reader": reader, "claimed": len(results), "messages": results}, indent=2))
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc), "action": "pull"}), file=sys.stderr)
        return 1


def cmd_watch(args: argparse.Namespace) -> int:
    global STOP
    STOP = False
    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    reader = args.reader
    if reader not in VALID_SENDERS:
        print(
            json.dumps({"ok": False, "error": f"reader must be one of {VALID_SENDERS}", "action": "watch"}),
            file=sys.stderr,
        )
        return 1

    interval_s = max(args.interval_ms, 10) / 1000
    idle_polls = 0
    emitted_batches = 0

    try:
        while not STOP:
            messages = _pull_messages(
                db=args.db,
                reader=reader,
                sender=args.sender,
                task_id=args.task_id,
                repo_path=getattr(args, "repo_path", None),
                limit=args.limit,
                full=args.full,
            )
            if messages:
                emitted_batches += 1
                idle_polls = 0
                event = {
                    "ok": True,
                    "mode": "watch",
                    "reader": reader,
                    "claimed": len(messages),
                    "messages": messages,
                    "emitted_at": _now_iso(),
                }
                print(json.dumps(event), flush=True)
                if args.max_batches and emitted_batches >= args.max_batches:
                    return 0
            else:
                idle_polls += 1
                if args.idle_exit and idle_polls >= args.idle_exit:
                    return 0
            time.sleep(interval_s)
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc), "action": "watch"}), file=sys.stderr)
        return 1


def cmd_ack(args: argparse.Namespace) -> int:
    reader = args.reader
    if reader not in VALID_SENDERS:
        print(f"ERROR: reader must be one of {VALID_SENDERS}", file=sys.stderr)
        return 1

    ids = [int(item.strip()) for item in args.ids.split(",") if item.strip()]
    if not ids:
        print("ERROR: no message IDs provided", file=sys.stderr)
        return 1

    col = f"read_by_{reader}_at"
    now = _now_iso()

    try:
        def _op():
            conn = _connect(args.db)
            try:
                placeholders = ",".join("?" for _ in ids)
                cur = conn.execute(
                    f"UPDATE messages SET {col} = ? WHERE id IN ({placeholders}) AND {col} IS NULL",
                    [now, *ids],
                )
                conn.commit()
                return cur.rowcount
            finally:
                conn.close()

        marked = _with_lock_retry(_op, action="ack")
        print(json.dumps({"ok": True, "marked": marked, "reader": reader, "ids": ids}))
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc), "action": "ack"}), file=sys.stderr)
        return 1


def cmd_health(args: argparse.Namespace) -> int:
    report: dict = {"ok": True, "issues": []}
    db_path = args.db or _db_path()
    report["db_path"] = db_path

    if not Path(db_path).exists():
        report["ok"] = False
        report["issues"].append(f"DB file not found: {db_path}")
        print(json.dumps(report, indent=2))
        return 1

    home = os.environ.get("HOME", "")
    if not home or not Path(home).is_dir():
        report["issues"].append(f"HOME env var suspicious: '{home}'")

    try:
        conn = _connect(db_path)
    except Exception as exc:
        report["ok"] = False
        report["issues"].append(f"Cannot connect to DB: {exc}")
        print(json.dumps(report, indent=2))
        return 1

    try:
        tables = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        if "messages" not in tables:
            report["ok"] = False
            report["issues"].append("'messages' table missing")
            print(json.dumps(report, indent=2))
            return 1

        total = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        unread_by_code = conn.execute(
            "SELECT COUNT(*) FROM messages WHERE sender='desktop' AND read_by_code_at IS NULL"
        ).fetchone()[0]
        unread_by_desktop = conn.execute(
            "SELECT COUNT(*) FROM messages WHERE sender='code' AND read_by_desktop_at IS NULL"
        ).fetchone()[0]
        report["total_messages"] = total
        report["unread_by_code"] = unread_by_code
        report["unread_by_desktop"] = unread_by_desktop

        latest = conn.execute(
            "SELECT id, sender, timestamp, substr(message,1,120) FROM messages ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if latest:
            report["latest"] = {
                "id": latest[0],
                "sender": latest[1],
                "timestamp": latest[2],
                "preview": latest[3],
            }
        else:
            report["issues"].append("No messages in DB (empty bus)")

        journal = conn.execute("PRAGMA journal_mode;").fetchone()[0]
        report["journal_mode"] = journal
        if journal.lower() != "wal":
            report["issues"].append(f"Journal mode is '{journal}', expected 'wal'")
        report["busy_timeout_ms"] = 5000
        report["lock_retry_attempts"] = LOCK_RETRIES + 1
    finally:
        conn.close()

    print(json.dumps(report, indent=2))
    return 0 if report["ok"] else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Local SQLite Agent Bus CLI")
    parser.add_argument("--db", help="Override relay DB path (default: ~/.relay_buffer.db)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    send = sub.add_parser("send", help="Send a message to the relay bus")
    send.add_argument("--sender", required=True, help="desktop or code")
    send.add_argument("--task-id", required=True, help="Task ID header")
    send.add_argument("--status", required=True, help="TASK / ACK / EVIDENCE_PACK / ...")
    send.add_argument("--body", default="", help="Optional body text (fragile for complex content; prefer --body-file)")
    send.add_argument("--body-file", help="Read body from a UTF-8 text file")
    send.add_argument("--body-stdin", action="store_true", help="Read body from stdin")
    send.set_defaults(func=cmd_send)

    fetch = sub.add_parser("fetch", help="Fetch messages from the relay bus")
    fetch.add_argument("--sender", help="Filter by sender")
    fetch.add_argument("--unread", action="store_true", help="Only unread messages")
    fetch.add_argument("--reader", help="Reader role when using --unread")
    fetch.add_argument("--task-id", help="Filter by task ID")
    fetch.add_argument("--repo-path", help="Filter by repo path included in the body")
    fetch.add_argument("--limit", type=int, default=20, help="Max results (<=100)")
    fetch.add_argument("--full", action="store_true", help="Show full body (default truncates)")
    fetch.set_defaults(func=cmd_fetch)

    pull = sub.add_parser("pull", help="Atomically fetch unread messages and mark them read")
    pull.add_argument("--sender", help="Filter by sender")
    pull.add_argument("--reader", required=True, help="Reader role that is claiming the unread messages")
    pull.add_argument("--task-id", help="Filter by task ID")
    pull.add_argument("--repo-path", help="Filter by repo path included in the body")
    pull.add_argument("--limit", type=int, default=20, help="Max results (<=100)")
    pull.add_argument("--full", action="store_true", help="Show full body (default truncates)")
    pull.set_defaults(func=cmd_pull)

    watch = sub.add_parser("watch", help="Continuously claim unread messages and emit JSONL batches")
    watch.add_argument("--sender", help="Filter by sender")
    watch.add_argument("--reader", required=True, help="Reader role that is claiming the unread messages")
    watch.add_argument("--task-id", help="Filter by task ID")
    watch.add_argument("--repo-path", help="Filter by repo path included in the body")
    watch.add_argument("--limit", type=int, default=20, help="Max results per claimed batch (<=100)")
    watch.add_argument("--full", action="store_true", help="Show full body (default truncates)")
    watch.add_argument("--interval-ms", type=int, default=1000, help="Polling interval in milliseconds")
    watch.add_argument("--idle-exit", type=int, default=0, help="Exit after N consecutive empty polls (0 = never)")
    watch.add_argument("--max-batches", type=int, default=0, help="Exit after N claimed batches (0 = never)")
    watch.set_defaults(func=cmd_watch)

    ack = sub.add_parser("ack", help="Mark messages as read")
    ack.add_argument("--ids", required=True, help="Comma-separated message IDs")
    ack.add_argument("--reader", required=True, help="desktop or code")
    ack.set_defaults(func=cmd_ack)

    health = sub.add_parser("health", help="Check relay bus DB and schema")
    health.set_defaults(func=cmd_health)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
