from __future__ import annotations

import json
import subprocess
from unittest.mock import patch

import pytest

from agpair import mcp_server


# ---------------------------------------------------------------------------
# Helpers – fake subprocess.CompletedProcess builders
# ---------------------------------------------------------------------------

def _ok_proc(stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout, stderr=stderr)


def _err_proc(rc: int = 1, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=rc, stdout=stdout, stderr=stderr)


# ===================================================================
# _base_command
# ===================================================================

class TestBaseCommand:
    def test_returns_python_m_agpair_cli_app(self) -> None:
        import sys
        cmd = mcp_server._base_command()
        assert cmd == [sys.executable, "-m", "agpair.cli.app"]


# ===================================================================
# _run_cli (integration-style – through _run_cli_text / _run_cli_json)
# ===================================================================

class TestRunCli:
    def test_passes_args_to_subprocess(self, monkeypatch) -> None:
        captured = {}

        def spy_run(*args, **kwargs):
            captured["args"] = args
            captured["kwargs"] = kwargs
            return _ok_proc(stdout="hello\n")

        monkeypatch.setattr(subprocess, "run", spy_run)
        mcp_server._run_cli(["task", "status", "T-1", "--json"])

        full_args = captured["args"][0]
        assert full_args[-4:] == ["task", "status", "T-1", "--json"]
        assert captured["kwargs"]["capture_output"] is True
        assert captured["kwargs"]["text"] is True
        assert captured["kwargs"]["check"] is False


# ===================================================================
# _run_cli_text
# ===================================================================

class TestRunCliText:
    def test_returns_stripped_stdout_on_success(self, monkeypatch) -> None:
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _ok_proc(stdout="  TASK-1\n"))
        assert mcp_server._run_cli_text(["task", "start"]) == "TASK-1"

    def test_raises_on_nonzero_with_stderr(self, monkeypatch) -> None:
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _err_proc(stderr="fatal error"))
        with pytest.raises(RuntimeError, match="fatal error"):
            mcp_server._run_cli_text(["task", "start"])

    def test_raises_on_nonzero_with_stdout_fallback(self, monkeypatch) -> None:
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _err_proc(stdout="stdout hint"))
        with pytest.raises(RuntimeError, match="stdout hint"):
            mcp_server._run_cli_text(["task", "start"])

    def test_raises_on_nonzero_with_generic_fallback(self, monkeypatch) -> None:
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _err_proc(rc=42))
        with pytest.raises(RuntimeError, match="command exited 42"):
            mcp_server._run_cli_text(["task", "start"])


# ===================================================================
# _run_cli_json
# ===================================================================

class TestRunCliJson:
    def test_parses_valid_json_on_success(self, monkeypatch) -> None:
        monkeypatch.setattr(
            subprocess, "run",
            lambda *a, **kw: _ok_proc(stdout=json.dumps({"ok": True, "phase": "done"})),
        )
        result = mcp_server._run_cli_json(["task", "status", "T-1"])
        assert result == {"ok": True, "phase": "done"}

    def test_empty_stdout_returns_empty_dict_on_success(self, monkeypatch) -> None:
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _ok_proc(stdout=""))
        result = mcp_server._run_cli_json(["task", "status", "T-1"])
        assert result == {}

    def test_raises_on_invalid_json(self, monkeypatch) -> None:
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _ok_proc(stdout="not json"))
        with pytest.raises(RuntimeError, match="invalid JSON response"):
            mcp_server._run_cli_json(["task", "status", "T-1"])

    def test_raises_on_non_object_json(self, monkeypatch) -> None:
        monkeypatch.setattr(
            subprocess, "run",
            lambda *a, **kw: _ok_proc(stdout=json.dumps([1, 2, 3])),
        )
        with pytest.raises(RuntimeError, match="non-object JSON payload"):
            mcp_server._run_cli_json(["task", "status", "T-1"])

    def test_nonzero_raises_with_json_error_field(self, monkeypatch) -> None:
        monkeypatch.setattr(
            subprocess, "run",
            lambda *a, **kw: _err_proc(stdout=json.dumps({"error": "task_not_found"})),
        )
        with pytest.raises(RuntimeError, match="task_not_found"):
            mcp_server._run_cli_json(["task", "status", "T-1"])

    def test_nonzero_raises_with_stderr_when_error_field_absent(self, monkeypatch) -> None:
        monkeypatch.setattr(
            subprocess, "run",
            lambda *a, **kw: _err_proc(
                stdout=json.dumps({"ok": False}),
                stderr="internal panic",
            ),
        )
        with pytest.raises(RuntimeError, match="internal panic"):
            mcp_server._run_cli_json(["task", "status", "T-1"])

    def test_nonzero_raises_generic_when_no_error_no_stderr(self, monkeypatch) -> None:
        monkeypatch.setattr(
            subprocess, "run",
            lambda *a, **kw: _err_proc(rc=7, stdout=json.dumps({"ok": False})),
        )
        with pytest.raises(RuntimeError, match="command exited 7"):
            mcp_server._run_cli_json(["task", "status", "T-1"])

    def test_allow_nonzero_returns_payload(self, monkeypatch) -> None:
        monkeypatch.setattr(
            subprocess, "run",
            lambda *a, **kw: _err_proc(
                stdout=json.dumps({"ok": False, "error": "task_not_found"}),
            ),
        )
        payload = mcp_server._run_cli_json(
            ["task", "status", "T-404", "--json"], allow_nonzero=True,
        )
        assert payload == {"ok": False, "error": "task_not_found"}

    def test_nonzero_empty_stdout_uses_stderr(self, monkeypatch) -> None:
        """Empty stdout falls back to '{}' (valid JSON), so error comes from non-zero path."""
        monkeypatch.setattr(
            subprocess, "run",
            lambda *a, **kw: _err_proc(stderr="crash trace"),
        )
        with pytest.raises(RuntimeError, match="crash trace"):
            mcp_server._run_cli_json(["task", "status", "T-1"])

    def test_nonzero_garbage_stdout_raises_invalid_json(self, monkeypatch) -> None:
        """Non-empty, non-JSON stdout on non-zero exit triggers JSONDecodeError path."""
        monkeypatch.setattr(
            subprocess, "run",
            lambda *a, **kw: _err_proc(stdout="not json at all", stderr="some error"),
        )
        with pytest.raises(RuntimeError, match="invalid JSON response.*some error"):
            mcp_server._run_cli_json(["task", "status", "T-1"])

    def test_nonzero_with_error_field_empty_string_uses_stderr(self, monkeypatch) -> None:
        """When the JSON 'error' field is an empty string, stderr should be used."""
        monkeypatch.setattr(
            subprocess, "run",
            lambda *a, **kw: _err_proc(
                stdout=json.dumps({"error": ""}),
                stderr="real error from stderr",
            ),
        )
        with pytest.raises(RuntimeError, match="real error from stderr"):
            mcp_server._run_cli_json(["task", "status", "T-1"])

    def test_nonzero_with_error_field_non_string_uses_stderr(self, monkeypatch) -> None:
        """When the JSON 'error' field is not a string (e.g., an int), stderr should be used."""
        monkeypatch.setattr(
            subprocess, "run",
            lambda *a, **kw: _err_proc(
                stdout=json.dumps({"error": 42}),
                stderr="stderr fallback",
            ),
        )
        with pytest.raises(RuntimeError, match="stderr fallback"):
            mcp_server._run_cli_json(["task", "status", "T-1"])


# ===================================================================
# _extract_task_id
# ===================================================================

class TestExtractTaskId:
    def test_single_line(self) -> None:
        assert mcp_server._extract_task_id("TASK-123\n") == "TASK-123"

    def test_single_line_no_trailing_newline(self) -> None:
        assert mcp_server._extract_task_id("TASK-123") == "TASK-123"

    def test_noisy_stdout_with_space_prefix_lines_accepted(self) -> None:
        stdout = "WARNING: configuration missing\nINFO: fallback used\nTASK-MCP-55"
        assert mcp_server._extract_task_id(stdout) == "TASK-MCP-55"

    def test_ambiguous_multi_line_rejected(self) -> None:
        stdout = "TASK-01\nTASK-02"
        with pytest.raises(RuntimeError, match="expected single-line task id output"):
            mcp_server._extract_task_id(stdout)

    def test_empty_stdout_rejected(self) -> None:
        with pytest.raises(RuntimeError, match="expected single-line task id output"):
            mcp_server._extract_task_id("")

    def test_whitespace_only_rejected(self) -> None:
        with pytest.raises(RuntimeError, match="expected single-line task id output"):
            mcp_server._extract_task_id("   \n  \n  ")

    def test_task_id_with_extra_whitespace_stripped(self) -> None:
        assert mcp_server._extract_task_id("  TASK-99  \n") == "TASK-99"

    def test_last_line_with_space_is_rejected(self) -> None:
        """If the last line contains a space, it's not a valid task id."""
        stdout = "INFO: booting\nTASK 123"
        with pytest.raises(RuntimeError, match="expected single-line task id output"):
            mcp_server._extract_task_id(stdout)


# ===================================================================
# _dispatch_then_maybe_wait (internal helper)
# ===================================================================

class TestDispatchThenMaybeWait:
    def test_no_wait_returns_task_id_immediately(self, monkeypatch) -> None:
        monkeypatch.setattr(mcp_server, "_run_cli_text", lambda args: "TASK-D1")
        result = mcp_server._dispatch_then_maybe_wait(
            ["task", "start", "--body", "x"],
            wait=False, interval_seconds=5.0, timeout_seconds=60.0,
        )
        assert result == {"ok": True, "task_id": "TASK-D1", "waited": False}

    def test_wait_calls_task_wait_with_correct_args(self, monkeypatch) -> None:
        json_calls: list[tuple] = []
        monkeypatch.setattr(mcp_server, "_run_cli_text", lambda args: "TASK-D2")

        def fake_json(args, *, allow_nonzero=False):
            json_calls.append((args, allow_nonzero))
            return {"ok": True, "phase": "committed"}

        monkeypatch.setattr(mcp_server, "_run_cli_json", fake_json)

        result = mcp_server._dispatch_then_maybe_wait(
            ["task", "start", "--body", "y"],
            wait=True, interval_seconds=2.0, timeout_seconds=120.0,
        )

        assert result["waited"] is True
        assert result["task_id"] == "TASK-D2"
        assert result["ok"] is True
        assert result["result"]["phase"] == "committed"
        assert json_calls == [([
            "task", "wait", "TASK-D2", "--json",
            "--interval-seconds", "2.0",
            "--timeout-seconds", "120.0",
        ], True)]

    def test_wait_result_reflects_false_ok(self, monkeypatch) -> None:
        monkeypatch.setattr(mcp_server, "_run_cli_text", lambda args: "TASK-D3")
        monkeypatch.setattr(
            mcp_server, "_run_cli_json",
            lambda args, *, allow_nonzero=False: {"ok": False, "phase": "blocked"},
        )

        result = mcp_server._dispatch_then_maybe_wait(
            ["task", "start", "--body", "z"],
            wait=True, interval_seconds=5.0, timeout_seconds=60.0,
        )
        assert result["ok"] is False

    def test_dispatch_propagates_cli_text_error(self, monkeypatch) -> None:
        def failing_cli_text(args):
            raise RuntimeError("dispatch failed")

        monkeypatch.setattr(mcp_server, "_run_cli_text", failing_cli_text)

        with pytest.raises(RuntimeError, match="dispatch failed"):
            mcp_server._dispatch_then_maybe_wait(
                ["task", "start"], wait=False,
                interval_seconds=5.0, timeout_seconds=60.0,
            )


# ===================================================================
# agpair_start_task tool
# ===================================================================

class TestStartTask:
    def test_no_wait_happy_path(self, monkeypatch, tmp_path) -> None:
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

    def test_wait_path(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setattr(mcp_server, "_run_cli_text", lambda args: "TASK-MCP-2")
        monkeypatch.setattr(
            mcp_server, "_run_cli_json",
            lambda args, *, allow_nonzero=False: {
                "ok": False, "task_id": "TASK-MCP-2", "phase": "blocked",
            },
        )

        result = mcp_server.agpair_start_task(
            repo_path=str(tmp_path),
            body="Goal: wait",
            wait=True,
            interval_seconds=1.5,
            timeout_seconds=30,
        )

        assert result["waited"] is True
        assert result["ok"] is False

    def test_rejects_relative_repo_path(self) -> None:
        with pytest.raises(RuntimeError, match="repo_path must be an absolute path"):
            mcp_server.agpair_start_task(repo_path="relative/path", body="test body")

    def test_rejects_missing_directory(self, tmp_path) -> None:
        missing_dir = tmp_path / "does_not_exist"
        with pytest.raises(RuntimeError, match="repo_path must be an existing directory"):
            mcp_server.agpair_start_task(repo_path=str(missing_dir), body="test body")

    def test_minimal_args_no_task_id_no_key(self, monkeypatch, tmp_path) -> None:
        """Verify that omitting optional task_id and idempotency_key does not add extra flags."""
        captured: list[list[str]] = []
        monkeypatch.setattr(
            mcp_server, "_run_cli_text",
            lambda args: captured.append(args) or "TASK-MIN-1",
        )

        mcp_server.agpair_start_task(repo_path=str(tmp_path), body="minimal")

        args = captured[0]
        assert "--task-id" not in args
        assert "--idempotency-key" not in args
        assert "--no-wait" in args

    def test_supports_target_alias_without_repo_path(self, monkeypatch) -> None:
        captured: list[list[str]] = []
        monkeypatch.setattr(
            mcp_server, "_run_cli_text",
            lambda args: captured.append(args) or "TASK-TARGET-1",
        )

        result = mcp_server.agpair_start_task(target="my-repo", body="Goal: target flow", wait=False)

        assert result == {"ok": True, "task_id": "TASK-TARGET-1", "waited": False}
        assert captured == [[
            "task", "start",
            "--target", "my-repo",
            "--body", "Goal: target flow",
            "--no-wait",
        ]]

    def test_accepts_structured_metadata_and_executor(self, monkeypatch, tmp_path) -> None:
        captured: list[list[str]] = []
        monkeypatch.setattr(
            mcp_server, "_run_cli_text",
            lambda args: captured.append(args) or "TASK-META-1",
        )

        result = mcp_server.agpair_start_task(
            repo_path=str(tmp_path),
            body="Goal: orchestrate",
            executor="codex",
            depends_on=["TASK-A", "TASK-B"],
            isolated_worktree=True,
            setup_commands=["git worktree add ../wt feature-branch"],
            teardown_commands=["git worktree remove ../wt"],
            env_vars={"PORT": "3100", "FOO": "bar"},
            worktree_boundary="../wt",
            spotlight_testing=True,
            wait=False,
        )

        assert result == {"ok": True, "task_id": "TASK-META-1", "waited": False}
        args = captured[0]
        assert args[:5] == ["task", "start", "--repo-path", str(tmp_path), "--body"]
        assert "--executor" in args
        assert args[args.index("--executor") + 1] == "codex"
        assert json.loads(args[args.index("--depends-on") + 1]) == ["TASK-A", "TASK-B"]
        assert "--isolated-worktree" in args
        assert json.loads(args[args.index("--setup-commands") + 1]) == ["git worktree add ../wt feature-branch"]
        assert json.loads(args[args.index("--teardown-commands") + 1]) == ["git worktree remove ../wt"]
        assert json.loads(args[args.index("--env-vars") + 1]) == {"FOO": "bar", "PORT": "3100"}
        assert args[args.index("--worktree-boundary") + 1] == "../wt"
        assert "--spotlight-testing" in args

    def test_rejects_invalid_executor_name(self, tmp_path) -> None:
        with pytest.raises(RuntimeError, match="executor must be one of"):
            mcp_server.agpair_start_task(
                repo_path=str(tmp_path),
                body="Goal: invalid executor",
                executor="claude",
            )

    def test_rejects_repo_path_and_target_together(self, tmp_path) -> None:
        with pytest.raises(RuntimeError, match="Specify either repo_path or target"):
            mcp_server.agpair_start_task(
                repo_path=str(tmp_path),
                target="my-repo",
                body="Goal: ambiguous locator",
            )


# ===================================================================
# agpair_get_task tool
# ===================================================================

class TestGetTask:
    def test_happy_path(self, monkeypatch) -> None:
        json_calls: list[tuple] = []

        def fake_json(args, *, allow_nonzero=False):
            json_calls.append((args, allow_nonzero))
            return {"ok": True, "task_id": "T-1", "phase": "committed"}

        monkeypatch.setattr(mcp_server, "_run_cli_json", fake_json)

        result = mcp_server.agpair_get_task("T-1")
        assert result == {"ok": True, "task_id": "T-1", "phase": "committed"}
        assert json_calls == [
            (["task", "status", "T-1", "--json"], True),
        ]

    def test_task_not_found_still_returns(self, monkeypatch) -> None:
        """allow_nonzero=True means non-zero exit still returns JSON payload."""
        monkeypatch.setattr(
            mcp_server, "_run_cli_json",
            lambda args, *, allow_nonzero=False: {"ok": False, "error": "task not found"},
        )
        result = mcp_server.agpair_get_task("T-MISSING")
        assert result["ok"] is False
        assert result["error"] == "task not found"

    def test_propagates_json_parse_error(self, monkeypatch) -> None:
        """If CLI returns garbage, _run_cli_json raises and it propagates."""
        def exploding_json(args, *, allow_nonzero=False):
            raise RuntimeError("invalid JSON response: garbled output")

        monkeypatch.setattr(mcp_server, "_run_cli_json", exploding_json)
        with pytest.raises(RuntimeError, match="invalid JSON response"):
            mcp_server.agpair_get_task("T-BAD")


# ===================================================================
# agpair_wait_task tool
# ===================================================================

class TestWaitTask:
    def test_passes_correct_args(self, monkeypatch) -> None:
        json_calls: list[tuple] = []

        def fake_json(args, *, allow_nonzero=False):
            json_calls.append((args, allow_nonzero))
            return {"ok": True, "phase": "committed"}

        monkeypatch.setattr(mcp_server, "_run_cli_json", fake_json)

        result = mcp_server.agpair_wait_task("T-W1", interval_seconds=3.0, timeout_seconds=600.0)
        assert result == {"ok": True, "phase": "committed"}
        assert json_calls == [([
            "task", "wait", "T-W1", "--json",
            "--interval-seconds", "3.0",
            "--timeout-seconds", "600.0",
        ], True)]

    def test_uses_default_intervals(self, monkeypatch) -> None:
        json_calls: list[tuple] = []

        def fake_json(args, *, allow_nonzero=False):
            json_calls.append((args, allow_nonzero))
            return {"ok": True}

        monkeypatch.setattr(mcp_server, "_run_cli_json", fake_json)
        mcp_server.agpair_wait_task("T-W2")

        args = json_calls[0][0]
        assert "--interval-seconds" in args
        assert args[args.index("--interval-seconds") + 1] == "5.0"
        assert args[args.index("--timeout-seconds") + 1] == "3600.0"

    def test_timeout_result_propagates(self, monkeypatch) -> None:
        monkeypatch.setattr(
            mcp_server, "_run_cli_json",
            lambda args, *, allow_nonzero=False: {"ok": False, "error": "timeout"},
        )
        result = mcp_server.agpair_wait_task("T-W3")
        assert result["ok"] is False


# ===================================================================
# agpair_get_logs tool
# ===================================================================

class TestGetLogs:
    def test_default_limit(self, monkeypatch) -> None:
        json_calls: list[tuple] = []

        def fake_json(args, *, allow_nonzero=False):
            json_calls.append((args, allow_nonzero))
            return {"ok": True, "entries": []}

        monkeypatch.setattr(mcp_server, "_run_cli_json", fake_json)

        result = mcp_server.agpair_get_logs("T-L1")
        assert result == {"ok": True, "entries": []}
        assert json_calls == [
            (["task", "logs", "T-L1", "--json", "--limit", "20"], True),
        ]

    def test_custom_limit(self, monkeypatch) -> None:
        json_calls: list[tuple] = []

        def fake_json(args, *, allow_nonzero=False):
            json_calls.append((args, allow_nonzero))
            return {"ok": True, "entries": ["a", "b"]}

        monkeypatch.setattr(mcp_server, "_run_cli_json", fake_json)

        result = mcp_server.agpair_get_logs("T-L2", limit=5)
        args = json_calls[0][0]
        assert args[-1] == "5"

    def test_error_propagates(self, monkeypatch) -> None:
        monkeypatch.setattr(
            mcp_server, "_run_cli_json",
            lambda args, *, allow_nonzero=False: {"ok": False, "error": "no such task"},
        )
        result = mcp_server.agpair_get_logs("T-MISSING")
        assert result["ok"] is False


# ===================================================================
# agpair_continue_task tool
# ===================================================================

class TestContinueTask:
    def test_no_wait_no_force(self, monkeypatch) -> None:
        text_calls: list[list[str]] = []
        monkeypatch.setattr(
            mcp_server, "_run_cli_text",
            lambda args: text_calls.append(args) or "TASK-C1",
        )

        result = mcp_server.agpair_continue_task(
            task_id="TASK-C1", body="feedback here", wait=False,
        )

        assert result == {"ok": True, "task_id": "TASK-C1", "waited": False}
        assert text_calls == [[
            "task", "continue", "TASK-C1",
            "--body", "feedback here",
            "--no-wait",
        ]]

    def test_with_force_flag(self, monkeypatch) -> None:
        text_calls: list[list[str]] = []
        monkeypatch.setattr(
            mcp_server, "_run_cli_text",
            lambda args: text_calls.append(args) or "TASK-C2",
        )

        mcp_server.agpair_continue_task(
            task_id="TASK-C2", body="forced", force=True, wait=False,
        )

        assert "--force" in text_calls[0]

    def test_with_wait(self, monkeypatch) -> None:
        monkeypatch.setattr(mcp_server, "_run_cli_text", lambda args: "TASK-C3")
        monkeypatch.setattr(
            mcp_server, "_run_cli_json",
            lambda args, *, allow_nonzero=False: {"ok": True, "phase": "evidence_ready"},
        )

        result = mcp_server.agpair_continue_task(
            task_id="TASK-C3", body="review", wait=True,
            interval_seconds=2.0, timeout_seconds=90.0,
        )
        assert result["waited"] is True
        assert result["ok"] is True


# ===================================================================
# agpair_approve_task tool
# ===================================================================

class TestApproveTask:
    def test_default_body(self, monkeypatch) -> None:
        text_calls: list[list[str]] = []
        monkeypatch.setattr(
            mcp_server, "_run_cli_text",
            lambda args: text_calls.append(args) or "TASK-A1",
        )

        result = mcp_server.agpair_approve_task(task_id="TASK-A1", wait=False)

        assert result == {"ok": True, "task_id": "TASK-A1", "waited": False}
        args = text_calls[0]
        assert args[:4] == ["task", "approve", "TASK-A1", "--body"]
        assert args[4] == "Approved"  # default body

    def test_custom_body_and_force(self, monkeypatch) -> None:
        text_calls: list[list[str]] = []
        monkeypatch.setattr(
            mcp_server, "_run_cli_text",
            lambda args: text_calls.append(args) or "TASK-A2",
        )

        mcp_server.agpair_approve_task(
            task_id="TASK-A2", body="LGTM", force=True, wait=False,
        )

        args = text_calls[0]
        assert args[args.index("--body") + 1] == "LGTM"
        assert "--force" in args

    def test_approve_with_wait(self, monkeypatch) -> None:
        monkeypatch.setattr(mcp_server, "_run_cli_text", lambda args: "TASK-A3")
        monkeypatch.setattr(
            mcp_server, "_run_cli_json",
            lambda args, *, allow_nonzero=False: {"ok": True, "phase": "committed"},
        )

        result = mcp_server.agpair_approve_task(
            task_id="TASK-A3", wait=True,
        )
        assert result["waited"] is True
        assert result["result"]["phase"] == "committed"


# ===================================================================
# agpair_retry_task tool
# ===================================================================

class TestRetryTask:
    def test_minimal_args(self, monkeypatch) -> None:
        text_calls: list[list[str]] = []
        monkeypatch.setattr(
            mcp_server, "_run_cli_text",
            lambda args: text_calls.append(args) or "TASK-R1",
        )

        result = mcp_server.agpair_retry_task(task_id="TASK-R1", wait=False)

        assert result == {"ok": True, "task_id": "TASK-R1", "waited": False}
        args = text_calls[0]
        assert args[:3] == ["task", "retry", "TASK-R1"]
        assert "--body" not in args
        assert "--force" not in args

    def test_with_body_and_force(self, monkeypatch) -> None:
        text_calls: list[list[str]] = []
        monkeypatch.setattr(
            mcp_server, "_run_cli_text",
            lambda args: text_calls.append(args) or "TASK-R2",
        )

        mcp_server.agpair_retry_task(
            task_id="TASK-R2", body="retry reason", force=True, wait=False,
        )

        args = text_calls[0]
        assert "--body" in args
        assert args[args.index("--body") + 1] == "retry reason"
        assert "--force" in args

    def test_retry_with_wait(self, monkeypatch) -> None:
        monkeypatch.setattr(mcp_server, "_run_cli_text", lambda args: "TASK-R3")
        monkeypatch.setattr(
            mcp_server, "_run_cli_json",
            lambda args, *, allow_nonzero=False: {"ok": True, "phase": "running"},
        )

        result = mcp_server.agpair_retry_task(
            task_id="TASK-R3", body="try again", wait=True,
            interval_seconds=1.0, timeout_seconds=60.0,
        )
        assert result["waited"] is True
        assert result["ok"] is True

    def test_retry_without_body_does_not_add_flag(self, monkeypatch) -> None:
        """Explicitly pass body=None to verify no --body flag appears."""
        text_calls: list[list[str]] = []
        monkeypatch.setattr(
            mcp_server, "_run_cli_text",
            lambda args: text_calls.append(args) or "TASK-R4",
        )

        mcp_server.agpair_retry_task(task_id="TASK-R4", body=None, wait=False)
        assert "--body" not in text_calls[0]


# ===================================================================
# agpair_list_tasks / inspect / doctor tools
# ===================================================================

class TestListInspectDoctorTools:
    def test_list_tasks_supports_repo_filter_and_json(self, monkeypatch, tmp_path) -> None:
        json_calls: list[tuple[list[str], bool]] = []

        def fake_json(args, *, allow_nonzero=False):
            json_calls.append((args, allow_nonzero))
            return {"ok": True, "tasks": []}

        monkeypatch.setattr(mcp_server, "_run_cli_json", fake_json)

        result = mcp_server.agpair_list_tasks(repo_path=str(tmp_path), phase="acked", limit=5)

        assert result == {"ok": True, "tasks": []}
        assert json_calls == [([
            "task", "list", "--json", "--limit", "5",
            "--repo-path", str(tmp_path),
            "--phase", "acked",
        ], False)]

    def test_list_tasks_supports_target_alias(self, monkeypatch) -> None:
        captured: list[tuple[list[str], bool]] = []

        def fake_json(args, *, allow_nonzero=False):
            captured.append((args, allow_nonzero))
            return {"ok": True, "tasks": []}

        monkeypatch.setattr(mcp_server, "_run_cli_json", fake_json)
        mcp_server.agpair_list_tasks(target="my-repo")

        assert captured == [([
            "task", "list", "--json", "--limit", "20", "--target", "my-repo",
        ], False)]

    def test_inspect_repo_requires_locator(self) -> None:
        with pytest.raises(RuntimeError, match="Either repo_path or target must be provided"):
            mcp_server.agpair_inspect_repo()

    def test_inspect_repo_supports_task_id(self, monkeypatch, tmp_path) -> None:
        captured: list[tuple[list[str], bool]] = []

        def fake_json(args, *, allow_nonzero=False):
            captured.append((args, allow_nonzero))
            return {"repo_path": str(tmp_path), "task": None}

        monkeypatch.setattr(mcp_server, "_run_cli_json", fake_json)
        result = mcp_server.agpair_inspect_repo(repo_path=str(tmp_path), task_id="TASK-1")

        assert result["repo_path"] == str(tmp_path)
        assert captured == [([
            "inspect", "--json", "--repo-path", str(tmp_path), "--task-id", "TASK-1",
        ], False)]

    def test_doctor_supports_optional_repo_locator_and_fresh(self, monkeypatch, tmp_path) -> None:
        captured: list[tuple[list[str], bool]] = []

        def fake_json(args, *, allow_nonzero=False):
            captured.append((args, allow_nonzero))
            return {"ok": True}

        monkeypatch.setattr(mcp_server, "_run_cli_json", fake_json)
        result = mcp_server.agpair_doctor(repo_path=str(tmp_path), fresh=True)

        assert result == {"ok": True}
        assert captured == [([
            "doctor", "--repo-path", str(tmp_path), "--fresh",
        ], False)]


# ===================================================================
# Edge cases: _run_cli_json with tricky payloads
# ===================================================================

class TestRunCliJsonEdgeCases:
    def test_none_stdout_treated_as_empty_dict(self, monkeypatch) -> None:
        """subprocess may set stdout to None in rare cases; fallback to '{}'."""
        proc = subprocess.CompletedProcess(args=[], returncode=0, stdout=None, stderr="")
        # The code does `proc.stdout or "{}"` so None → "{}" → {}
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: proc)
        result = mcp_server._run_cli_json(["task", "status", "T-1"])
        assert result == {}

    def test_json_with_nested_objects(self, monkeypatch) -> None:
        """Verify deeply nested JSON round-trips correctly."""
        payload = {
            "ok": True,
            "result": {
                "evidence": {"diff_stat": "2 files changed"},
                "tags": ["test", "mcp"],
            },
        }
        monkeypatch.setattr(
            subprocess, "run",
            lambda *a, **kw: _ok_proc(stdout=json.dumps(payload)),
        )
        result = mcp_server._run_cli_json(["task", "status", "T-1"])
        assert result == payload

    def test_nonzero_with_list_payload_raises(self, monkeypatch) -> None:
        """Non-zero exit with a JSON list should raise (non-object)."""
        monkeypatch.setattr(
            subprocess, "run",
            lambda *a, **kw: _err_proc(stdout=json.dumps(["error"])),
        )
        with pytest.raises(RuntimeError, match="non-object JSON payload"):
            mcp_server._run_cli_json(["task", "status", "T-1"], allow_nonzero=True)

    def test_boolean_json_payload_raises(self, monkeypatch) -> None:
        """JSON true/false is valid JSON but not an object."""
        monkeypatch.setattr(
            subprocess, "run",
            lambda *a, **kw: _ok_proc(stdout="true"),
        )
        with pytest.raises(RuntimeError, match="non-object JSON payload"):
            mcp_server._run_cli_json(["task", "status", "T-1"])

    def test_string_json_payload_raises(self, monkeypatch) -> None:
        """A bare JSON string is not an object."""
        monkeypatch.setattr(
            subprocess, "run",
            lambda *a, **kw: _ok_proc(stdout='"just a string"'),
        )
        with pytest.raises(RuntimeError, match="non-object JSON payload"):
            mcp_server._run_cli_json(["task", "status", "T-1"])

# ===================================================================
# ProtectedFastMCP and Precedence Rules
# ===================================================================

class TestProtectedFastMCP:
    def test_allows_unsealed_registration(self) -> None:
        server = mcp_server.ProtectedFastMCP("test")
        
        @server.tool()
        def tool_one(): pass
        
        assert "tool_one" in server._builtin_tools

    def test_allows_unconflicting_sealed_registration(self) -> None:
        server = mcp_server.ProtectedFastMCP("test")
        
        @server.tool()
        def tool_one(): pass
        
        server.seal_builtins()
        
        @server.tool()
        def tool_two(): pass
        
        assert "tool_one" in server._builtin_tools
        assert "tool_two" not in server._builtin_tools

    def test_rejects_shadowing_after_sealed(self) -> None:
        server = mcp_server.ProtectedFastMCP("test")
        
        @server.tool()
        def tool_one(): pass
        
        server.seal_builtins()
        
        with pytest.raises(ValueError, match="Cannot override built-in MCP tool: tool_one"):
            @server.tool()
            def tool_one(): pass
            
        with pytest.raises(ValueError, match="Cannot override built-in MCP tool: tool_one"):
            @server.tool(name="tool_one")
            def another_name(): pass

    def test_global_mcp_is_sealed_and_protected(self) -> None:
        assert mcp_server.mcp._sealed is True
        assert "agpair_start_task" in mcp_server.mcp._builtin_tools
        
        with pytest.raises(ValueError, match="Cannot override built-in MCP tool: agpair_start_task"):
            @mcp_server.mcp.tool()
            def agpair_start_task(): pass
