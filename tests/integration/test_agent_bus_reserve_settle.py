from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path

from agpair.cli import agent_bus


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _run_cli(argv: list[str], db_path: Path, capsys) -> dict:
    old = os.environ.get("RELAY_DB")
    os.environ["RELAY_DB"] = str(db_path)
    try:
        rc = agent_bus.main(argv)
        assert rc == 0
        out = capsys.readouterr().out
        return json.loads(out)
    finally:
        if old is None:
            os.environ.pop("RELAY_DB", None)
        else:
            os.environ["RELAY_DB"] = old


def test_reserve_returns_claim_without_marking_read(tmp_path: Path, capsys) -> None:
    db_path = tmp_path / "relay.db"

    _run_cli(
        [
            "--db",
            str(db_path),
            "send",
            "--sender",
            "desktop",
            "--task-id",
            "TASK-RES-1",
            "--status",
            "TASK",
            "--body",
            "repo_path: /tmp/repo\n\nGoal: reserve test",
        ],
        db_path,
        capsys,
    )

    reserved = _run_cli(
        [
            "--db",
            str(db_path),
            "reserve",
            "--sender",
            "desktop",
            "--reader",
            "code",
            "--task-id",
            "TASK-RES-1",
            "--full",
        ],
        db_path,
        capsys,
    )

    assert reserved["reserved"] == 1
    assert reserved["messages"][0]["task_id"] == "TASK-RES-1"
    assert reserved["messages"][0]["claim_id"]

    with _connect(db_path) as conn:
        row = conn.execute("SELECT read_by_code_at FROM messages").fetchone()
    assert row is not None
    assert row["read_by_code_at"] is None


def test_settle_marks_message_read_by_claim_id(tmp_path: Path, capsys) -> None:
    db_path = tmp_path / "relay.db"
    _run_cli(
        [
            "--db",
            str(db_path),
            "send",
            "--sender",
            "desktop",
            "--task-id",
            "TASK-SETTLE-1",
            "--status",
            "TASK",
            "--body",
            "repo_path: /tmp/repo\n\nGoal: settle test",
        ],
        db_path,
        capsys,
    )
    reserved = _run_cli(
        [
            "--db",
            str(db_path),
            "reserve",
            "--sender",
            "desktop",
            "--reader",
            "code",
            "--task-id",
            "TASK-SETTLE-1",
            "--full",
        ],
        db_path,
        capsys,
    )
    claim_id = reserved["messages"][0]["claim_id"]

    settled = _run_cli(
        [
            "--db",
            str(db_path),
            "settle",
            "--reader",
            "code",
            "--claims",
            claim_id,
        ],
        db_path,
        capsys,
    )

    assert settled["settled"] == 1
    with _connect(db_path) as conn:
        row = conn.execute("SELECT read_by_code_at FROM messages").fetchone()
    assert row is not None
    assert row["read_by_code_at"] is not None


def test_expired_reservation_can_be_reserved_again(tmp_path: Path, capsys) -> None:
    db_path = tmp_path / "relay.db"
    _run_cli(
        [
            "--db",
            str(db_path),
            "send",
            "--sender",
            "desktop",
            "--task-id",
            "TASK-LEASE-1",
            "--status",
            "TASK",
            "--body",
            "repo_path: /tmp/repo\n\nGoal: lease test",
        ],
        db_path,
        capsys,
    )

    first = _run_cli(
        [
            "--db",
            str(db_path),
            "reserve",
            "--sender",
            "desktop",
            "--reader",
            "code",
            "--task-id",
            "TASK-LEASE-1",
            "--lease-ms",
            "50",
            "--full",
        ],
        db_path,
        capsys,
    )
    first_claim = first["messages"][0]["claim_id"]
    time.sleep(0.08)
    second = _run_cli(
        [
            "--db",
            str(db_path),
            "reserve",
            "--sender",
            "desktop",
            "--reader",
            "code",
            "--task-id",
            "TASK-LEASE-1",
            "--lease-ms",
            "50",
            "--full",
        ],
        db_path,
        capsys,
    )

    assert second["reserved"] == 1
    assert second["messages"][0]["claim_id"] != first_claim
