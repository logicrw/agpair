import json
import logging
import pathlib
import subprocess
from unittest import mock

import pytest

from agpair.executors.codex import CodexExecutor, CodexTaskRef, CodexTaskState


def test_codex_executor_dispatch():
    executor = CodexExecutor(codex_bin="fake-codex")
    
    with mock.patch("subprocess.Popen") as mock_popen:
        mock_process = mock.Mock()
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
            task_ref = executor.dispatch(
                task_id="task-123",
                body="Do something",
                repo_path="/fake/repo"
            )
        
        assert isinstance(task_ref, CodexTaskRef)
        assert task_ref.task_id == "task-123"
        assert task_ref.process is mock_process
        
        # Verify parent-process FD handles are closed
        mock_stdout_fh.close.assert_called_once()
        mock_stderr_fh.close.assert_called_once()
        
        mock_popen.assert_called_once()
        args, kwargs = mock_popen.call_args
        
        cmd = args[0]
        assert cmd[0] == "sh"
        assert cmd[1] == "-c"
        assert "fake-codex exec" in cmd[2]
        assert "--ephemeral" in cmd[2]
        assert "--json" in cmd[2]
        assert "--skip-git-repo-check" in cmd[2]
        assert "-C " in cmd[2]
        assert str(task_ref.last_msg_file) in cmd[2]
        assert "Do something" in cmd[2]
        
        assert kwargs["cwd"] == "/fake/repo"
        assert kwargs["text"] is True


def test_codex_executor_poll(tmp_path: pathlib.Path):
    task_ref = CodexTaskRef(
        task_id="task-123",
        process=mock.Mock(spec=subprocess.Popen),
        stdout_file=tmp_path / "stdout.jsonl",
        stderr_file=tmp_path / "stderr.log",
        last_msg_file=tmp_path / "last_msg.txt",
        temp_dir=tmp_path,
    )
    
    task_ref.stdout_file.write_text('{"event": "start"}\n{"event": "end"}\n', encoding="utf-8")
    task_ref.last_msg_file.write_text("Hello World!", encoding="utf-8")
    
    # Simulate process done
    task_ref.process.poll.return_value = 0
    
    executor = CodexExecutor()
    state = executor.poll(task_ref)
    
    assert isinstance(state, CodexTaskState)
    assert state.is_done is True
    assert state.returncode == 0
    assert state.events_count == 2
    assert state.last_message == "Hello World!"
    
    receipt = state.synthesize_receipt("task-123")
    assert receipt["status"] == "EVIDENCE_PACK"
    assert receipt["summary"] == "Hello World!"
    assert receipt["attempt_no"] == 1  # default
    assert receipt["payload"]["returncode"] == 0
    assert receipt["payload"]["events_count"] == 2

    # Verify explicit attempt_no is used
    receipt_attempt3 = state.synthesize_receipt("task-123", attempt_no=3)
    assert receipt_attempt3["attempt_no"] == 3


def test_codex_executor_poll_failed(tmp_path: pathlib.Path):
    task_ref = CodexTaskRef(
        task_id="task-123",
        process=mock.Mock(spec=subprocess.Popen),
        stdout_file=tmp_path / "stdout.jsonl",
        stderr_file=tmp_path / "stderr.log",
        last_msg_file=tmp_path / "last_msg.txt",
        temp_dir=tmp_path,
    )
    
    task_ref.last_msg_file.write_text("Error occurred!", encoding="utf-8")
    task_ref.process.poll.return_value = 1
    
    executor = CodexExecutor()
    state = executor.poll(task_ref)
    
    assert state.is_done is True
    assert state.returncode == 1
    
    receipt = state.synthesize_receipt("task-123")
    assert receipt["status"] == "BLOCKED"
    assert receipt["summary"] == "Error occurred!"
    assert receipt["payload"]["returncode"] == 1
    assert receipt["payload"]["blocker_type"] == "execution_error"


def test_codex_executor_cancel():
    executor = CodexExecutor()
    task_ref = CodexTaskRef(
        task_id="task-123",
        process=mock.Mock(spec=subprocess.Popen),
        stdout_file=mock.Mock(),
        stderr_file=mock.Mock(),
        last_msg_file=mock.Mock(),
        temp_dir=mock.Mock(),
    )
    
    # Process not done
    task_ref.process.poll.return_value = None
    
    executor.cancel(task_ref)
    task_ref.process.terminate.assert_called_once()
    task_ref.process.wait.assert_called_once_with(timeout=5.0)


def test_codex_executor_cancel_timeout():
    executor = CodexExecutor()
    task_ref = CodexTaskRef(
        task_id="task-123",
        process=mock.Mock(spec=subprocess.Popen),
        stdout_file=mock.Mock(),
        stderr_file=mock.Mock(),
        last_msg_file=mock.Mock(),
        temp_dir=mock.Mock(),
    )
    
    task_ref.process.poll.return_value = None
    task_ref.process.wait.side_effect = subprocess.TimeoutExpired(cmd="fake", timeout=5.0)
    
    executor.cancel(task_ref)
    task_ref.process.terminate.assert_called_once()
    task_ref.process.kill.assert_called_once()


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


def test_synthesize_receipt_attempt_no_propagation():
    """Receipt carries the real attempt_no for both success and failure paths."""
    success_state = CodexTaskState(is_done=True, returncode=0, last_message="ok", events_count=1)
    r_ok = success_state.synthesize_receipt("T1", attempt_no=5)
    assert r_ok["attempt_no"] == 5
    assert r_ok["status"] == "EVIDENCE_PACK"

    fail_state = CodexTaskState(is_done=True, returncode=1, last_message="err", events_count=0)
    r_fail = fail_state.synthesize_receipt("T2", attempt_no=3)
    assert r_fail["attempt_no"] == 3
    assert r_fail["status"] == "BLOCKED"

    # Default should be 1 for backward compat
    r_default = success_state.synthesize_receipt("T3")
    assert r_default["attempt_no"] == 1
