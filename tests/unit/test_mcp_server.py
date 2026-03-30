from __future__ import annotations

import json
import subprocess
import pytest

from agpair import mcp_server


def test_start_tool_uses_no_wait_and_parses_task_id(monkeypatch, tmp_path) -> None:
    captured: list[list[str]] = []

    def fake_run_cli_text(args: list[str]) -> str:
        captured.append(args)
        return "TASK-MCP-1"

    monkeypatch.setattr(mcp_server, "_run_cli_text", fake_run_cli_text)

    result = mcp_server.agpair_start_task(
        repo_path=str(tmp_path),
        body="Goal: fix it",
        task_id="TASK-MCP-1",
        idempotency_key="caller-key-1",
        wait=False,
    )

    assert result == {"ok": True, "task_id": "TASK-MCP-1", "waited": False}
    assert captured == [[
        "task", "start",
        "--repo-path", str(tmp_path),
        "--body", "Goal: fix it",
        "--task-id", "TASK-MCP-1",
        "--idempotency-key", "caller-key-1",
        "--no-wait",
    ]]


def test_start_tool_waits_via_task_wait_json(monkeypatch, tmp_path) -> None:
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
        repo_path=str(tmp_path),
        body="Goal: wait",
        wait=True,
        interval_seconds=1.5,
        timeout_seconds=30,
    )

    assert text_calls == [[
        "task", "start",
        "--repo-path", str(tmp_path),
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


def test_extract_task_id_single_line() -> None:
    assert mcp_server._extract_task_id("TASK-123\n") == "TASK-123"


def test_extract_task_id_noisy_stdout_accepted() -> None:
    stdout = "WARNING: configuration missing\nINFO: fallback used\nTASK-MCP-55"
    assert mcp_server._extract_task_id(stdout) == "TASK-MCP-55"


def test_extract_task_id_ambiguous_rejected() -> None:
    stdout = "TASK-01\nTASK-02"
    with pytest.raises(RuntimeError, match="expected single-line task id output"):
        mcp_server._extract_task_id(stdout)


def test_start_task_rejects_relative_repo_path() -> None:
    with pytest.raises(RuntimeError, match="repo_path must be an absolute path"):
        mcp_server.agpair_start_task(
            repo_path="relative/path",
            body="test body",
        )


def test_start_task_rejects_missing_directory(tmp_path) -> None:
    missing_dir = tmp_path / "does_not_exist"
    with pytest.raises(RuntimeError, match="repo_path must be an existing directory"):
        mcp_server.agpair_start_task(
            repo_path=str(missing_dir),
            body="test body",
        )
