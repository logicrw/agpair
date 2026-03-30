from __future__ import annotations

import json
import subprocess

from agpair import mcp_server


def test_start_tool_uses_no_wait_and_parses_task_id(monkeypatch) -> None:
    captured: list[list[str]] = []

    def fake_run_cli_text(args: list[str]) -> str:
        captured.append(args)
        return "TASK-MCP-1"

    monkeypatch.setattr(mcp_server, "_run_cli_text", fake_run_cli_text)

    result = mcp_server.agpair_start_task(
        repo_path="/tmp/repo",
        body="Goal: fix it",
        task_id="TASK-MCP-1",
        idempotency_key="caller-key-1",
        wait=False,
    )

    assert result == {"ok": True, "task_id": "TASK-MCP-1", "waited": False}
    assert captured == [[
        "task", "start",
        "--repo-path", "/tmp/repo",
        "--body", "Goal: fix it",
        "--task-id", "TASK-MCP-1",
        "--idempotency-key", "caller-key-1",
        "--no-wait",
    ]]


def test_start_tool_waits_via_task_wait_json(monkeypatch) -> None:
    text_calls: list[list[str]] = []
    json_calls: list[tuple[list[str], bool]] = []

    monkeypatch.setattr(
        mcp_server,
        "_run_cli_text",
        lambda args: text_calls.append(args) or "TASK-MCP-2",
    )

    def fake_run_cli_json(args: list[str], *, allow_nonzero: bool = False):
        json_calls.append((args, allow_nonzero))
        return {"ok": False, "task_id": "TASK-MCP-2", "phase": "blocked"}

    monkeypatch.setattr(mcp_server, "_run_cli_json", fake_run_cli_json)

    result = mcp_server.agpair_start_task(
        repo_path="/tmp/repo",
        body="Goal: wait",
        wait=True,
        interval_seconds=1.5,
        timeout_seconds=30,
    )

    assert text_calls == [[
        "task", "start",
        "--repo-path", "/tmp/repo",
        "--body", "Goal: wait",
        "--no-wait",
    ]]
    assert json_calls == [([
        "task", "wait", "TASK-MCP-2", "--json",
        "--interval-seconds", "1.5",
        "--timeout-seconds", "30",
    ], True)]
    assert result == {
        "ok": False,
        "task_id": "TASK-MCP-2",
        "waited": True,
        "result": {"ok": False, "task_id": "TASK-MCP-2", "phase": "blocked"},
    }


def test_run_cli_json_allows_nonzero_when_stdout_is_json(monkeypatch) -> None:
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args[0],
            returncode=1,
            stdout=json.dumps({"ok": False, "error": "task_not_found"}),
            stderr="",
        ),
    )

    payload = mcp_server._run_cli_json(["task", "status", "TASK-404", "--json"], allow_nonzero=True)

    assert payload == {"ok": False, "error": "task_not_found"}
