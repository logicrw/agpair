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
        
        task_ref = executor.dispatch(
            task_id="task-123",
            body="Do something",
            repo_path="/fake/repo"
        )
        
        assert isinstance(task_ref, CodexTaskRef)
        assert task_ref.task_id == "task-123"
        assert task_ref.process is mock_process
        
        mock_popen.assert_called_once()
        args, kwargs = mock_popen.call_args
        
        cmd = args[0]
        assert cmd[0] == "fake-codex"
        assert cmd[1] == "exec"
        assert "--ephemeral" in cmd
        assert "--json" in cmd
        assert "--skip-git-repo-check" in cmd
        assert "-C" in cmd
        assert str(task_ref.last_msg_file) in cmd
        assert cmd[-1] == "Do something"
        
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
    assert len(state.events) == 2
    assert state.events[0]["event"] == "start"
    assert state.events[1]["event"] == "end"
    assert state.last_message == "Hello World!"
    
    receipt = state.synthesize_receipt("task-123")
    assert receipt["status"] == "EVIDENCE_PACK"
    assert receipt["summary"] == "Hello World!"
    assert receipt["payload"]["returncode"] == 0
    assert receipt["payload"]["events_count"] == 2


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
