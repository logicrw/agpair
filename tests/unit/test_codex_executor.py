import json
import logging
import pathlib
import subprocess
from unittest import mock

import pytest

from agpair.executors.codex import CodexExecutor
from agpair.executors.base import DispatchResult, TaskState


def test_codex_executor_dispatch():
    executor = CodexExecutor(codex_bin="fake-codex")
    
    with mock.patch("agpair.executors.local_cli._git_head", return_value="fake-head"), \
         mock.patch("subprocess.Popen") as mock_popen:
        mock_process = mock.Mock()
        mock_process.pid = 12345
        mock_popen.return_value = mock_process
        
        # Track file handle open/close via mock
        mock_stdout_fh = mock.Mock()
        mock_stderr_fh = mock.Mock()
        original_open = pathlib.Path.open
        call_count = [0]
        
        def tracked_open(self, *args, **kwargs):
            fh = original_open(self, *args, **kwargs)
            if 'stdout' in str(self):
                call_count[0] += 1
                return mock_stdout_fh
            elif 'stderr' in str(self):
                call_count[0] += 1
                return mock_stderr_fh
            return fh

        with mock.patch.object(pathlib.Path, 'open', tracked_open):
            dispatch_res = executor.dispatch(
                task_id="task-123",
                body="Do something",
                repo_path="/fake/repo"
            )
        
        assert isinstance(dispatch_res, DispatchResult)
        
        
        
        # Verify parent-process FD handles are closed
        mock_stdout_fh.close.assert_called_once()
        mock_stderr_fh.close.assert_called_once()
        
        mock_popen.assert_called_once()
        args, kwargs = mock_popen.call_args
        
        cmd = args[0]
        assert len(cmd) == 1
        wrapper_script_path = pathlib.Path(cmd[0])
        assert wrapper_script_path.exists()
        wrapper_content = wrapper_script_path.read_text(encoding="utf-8")
        
        assert "fake-codex exec" in wrapper_content
        assert "--ephemeral" in wrapper_content
        assert "--json" in wrapper_content
        assert "--skip-git-repo-check" in wrapper_content
        assert "-C" in wrapper_content
        assert "last_msg.txt" in wrapper_content
        assert "Do something" in wrapper_content
        
        assert kwargs["cwd"] == "/fake/repo"
        assert kwargs["text"] is True


def test_codex_executor_dispatch_uses_bypass_all_by_default(monkeypatch):
    monkeypatch.delenv("AGPAIR_CODEX_APPROVAL_MODE", raising=False)
    executor = CodexExecutor(codex_bin="fake-codex")

    cmd = executor._build_codex_cmd("Do something", "/fake/repo", pathlib.Path("/tmp"))

    assert "--dangerously-bypass-approvals-and-sandbox" in cmd
    assert "--full-auto" not in cmd


def test_codex_executor_dispatch_honors_full_auto_mode(monkeypatch):
    monkeypatch.setenv("AGPAIR_CODEX_APPROVAL_MODE", "full_auto")
    executor = CodexExecutor(codex_bin="fake-codex")

    cmd = executor._build_codex_cmd("Do something", "/fake/repo", pathlib.Path("/tmp"))

    assert "--full-auto" in cmd
    assert "--dangerously-bypass-approvals-and-sandbox" not in cmd


def test_codex_executor_dispatch_honors_default_mode(monkeypatch):
    monkeypatch.setenv("AGPAIR_CODEX_APPROVAL_MODE", "default")
    executor = CodexExecutor(codex_bin="fake-codex")

    cmd = executor._build_codex_cmd("Do something", "/fake/repo", pathlib.Path("/tmp"))

    assert "--full-auto" not in cmd
    assert "--dangerously-bypass-approvals-and-sandbox" not in cmd


def test_codex_executor_poll(tmp_path: pathlib.Path):
    stdout_file = tmp_path / "stdout.jsonl"
    rc_file = tmp_path / "rc.txt"
    last_msg_file = tmp_path / "last_msg.txt"
    
    stdout_file.write_text('{"event": "start"}\n{"event": "end"}\n', encoding="utf-8")
    last_msg_file.write_text("Hello World!", encoding="utf-8")
    rc_file.write_text("0", encoding="utf-8")
    
    executor = CodexExecutor()
    state = executor.poll("task-123", str(tmp_path))
    
    assert isinstance(state, TaskState)
    assert state.is_done is True
    
    receipt = state.receipt
    assert receipt["status"] == "COMMITTED"
    assert receipt["summary"] == "Hello World!"
    assert receipt["attempt_no"] == 1  # default
    assert receipt["payload"]["exit_code"] == 0
    assert receipt["payload"]["events_count"] == 2

    state_attempt3 = executor.poll("task-123", str(tmp_path), attempt_no=3)
    assert state_attempt3.receipt["attempt_no"] == 3


def test_codex_executor_poll_failed(tmp_path: pathlib.Path):
    rc_file = tmp_path / "rc.txt"
    last_msg_file = tmp_path / "last_msg.txt"
    last_msg_file.write_text("Error occurred!", encoding="utf-8")
    rc_file.write_text("1", encoding="utf-8")
    
    executor = CodexExecutor()
    state = executor.poll("task-123", str(tmp_path))
    
    assert state.is_done is True
    
    receipt = state.receipt
    assert receipt["status"] == "BLOCKED"
    assert receipt["summary"] == "Error occurred!"
    assert receipt["payload"]["exit_code"] == 1
    assert receipt["payload"]["blocker_type"] == "execution_error"


def test_codex_executor_cancel(tmp_path):
    executor = CodexExecutor()
    pid_file = tmp_path / "pid.txt"
    pid_file.write_text("12345", encoding="utf-8")
    
    with mock.patch("agpair.executors.local_cli._is_process_alive", return_value=True), \
         mock.patch("os.killpg") as mock_killpg:
        executor.cancel("task-123", str(tmp_path))
        mock_killpg.assert_called_once()


def test_codex_executor_cancel_timeout():
    pass


def test_codex_executor_dispatch_closes_fds_on_popen_error():
    """Parent must close FDs even when Popen raises."""
    executor = CodexExecutor(codex_bin="fake-codex")

    mock_stdout_fh = mock.Mock()
    mock_stderr_fh = mock.Mock()
    original_open = pathlib.Path.open

    def tracked_open(self, *args, **kwargs):
        fh = original_open(self, *args, **kwargs)
        if "stdout" in str(self):
            return mock_stdout_fh
        elif "stderr" in str(self):
            return mock_stderr_fh
        return fh

    with mock.patch.object(pathlib.Path, "open", tracked_open), \
         mock.patch("subprocess.Popen", side_effect=OSError("cannot exec")):
        with pytest.raises(OSError, match="cannot exec"):
            executor.dispatch(task_id="task-err", body="boom", repo_path="/fake/repo")

    mock_stdout_fh.close.assert_called_once()
    mock_stderr_fh.close.assert_called_once()
