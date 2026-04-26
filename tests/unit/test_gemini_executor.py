import pytest
import subprocess
from pathlib import Path

from agpair.executors.gemini import GeminiExecutor
from agpair.executors.base import DispatchResult, TaskState
from agpair.models import ContinuationCapability


def test_gemini_executor_properties():
    executor = GeminiExecutor()
    assert executor.backend_id == "gemini_cli"
    assert executor.continuation_capability == ContinuationCapability.UNSUPPORTED


from unittest.mock import patch, Mock

def test_gemini_executor_dispatch_command_construction(tmp_path):
    with patch("agpair.executors.local_cli._git_head", return_value="fake-head"), \
         patch("agpair.executors.local_cli.subprocess.Popen") as mock_popen:
        mock_process = Mock()
        mock_process.pid = 12345
        mock_popen.return_value = mock_process
        
        executor = GeminiExecutor()
        dispatch_res = executor.dispatch(task_id="test_gemini", body="do some work", repo_path=str(tmp_path))
        
        assert isinstance(dispatch_res, DispatchResult)
        
        
        mock_popen.assert_called_once()
        args = mock_popen.call_args[0][0]
        
        assert len(args) == 1
        wrapper_script_path = Path(args[0])
        assert wrapper_script_path.exists()
        wrapper_content = wrapper_script_path.read_text(encoding="utf-8")
        
        # Check flags we pulled from gemini --help
        assert "gemini -y --output-format json -p" in wrapper_content
        
        # Check that prompt (-p) is included
        assert "do some work" in wrapper_content


def test_gemini_executor_dispatch_uses_isolated_worktree_for_cwd(tmp_path):
    worktree_path = tmp_path / "wt"

    with patch("agpair.executors.local_cli._git_toplevel", return_value=tmp_path.resolve()), \
         patch("agpair.executors.local_cli._git_head", return_value="fake-head"), \
         patch("agpair.executors.local_cli.subprocess.run") as mock_run, \
         patch("agpair.executors.local_cli.subprocess.check_output") as mock_check_output, \
         patch("agpair.executors.local_cli.subprocess.Popen") as mock_popen:
        mock_process = Mock()
        mock_process.pid = 12345
        mock_popen.return_value = mock_process
        mock_run.return_value = Mock(returncode=0)
        mock_check_output.return_value = f"worktree {tmp_path.resolve()}\nworktree {worktree_path.resolve()}\n"

        executor = GeminiExecutor()
        dispatch_res = executor.dispatch(
            task_id="test_gemini_iso",
            body="do isolated work",
            repo_path=str(tmp_path),
            isolated_worktree=True,
            worktree_boundary=str(worktree_path),
        )

    wrapper_script_path = Path(mock_popen.call_args[0][0][0])
    assert wrapper_script_path.exists()
    wrapper_content = wrapper_script_path.read_text(encoding="utf-8")
    assert "gemini" in wrapper_content
    assert "do isolated work" in wrapper_content
    assert dispatch_res.execution_repo_path == str(worktree_path.resolve())
    assert mock_popen.call_args.kwargs["cwd"] == str(worktree_path.resolve())


def test_gemini_executor_dispatch_uses_yolo_by_default(monkeypatch):
    monkeypatch.delenv("AGPAIR_GEMINI_APPROVAL_MODE", raising=False)
    executor = GeminiExecutor()

    cmd = executor._build_gemini_cmd("do some work", "/fake/repo", Path("/tmp"))

    assert "-y" in cmd
    assert "--approval-mode" not in cmd


def test_gemini_executor_dispatch_honors_default_mode(monkeypatch):
    monkeypatch.setenv("AGPAIR_GEMINI_APPROVAL_MODE", "default")
    executor = GeminiExecutor()

    cmd = executor._build_gemini_cmd("do some work", "/fake/repo", Path("/tmp"))

    assert "-y" not in cmd
    assert "--approval-mode" not in cmd


def test_gemini_executor_dispatch_honors_auto_edit_mode(monkeypatch):
    monkeypatch.setenv("AGPAIR_GEMINI_APPROVAL_MODE", "auto_edit")
    executor = GeminiExecutor()

    cmd = executor._build_gemini_cmd("do some work", "/fake/repo", Path("/tmp"))

    assert "--approval-mode" in cmd
    assert "auto_edit" in cmd
    assert "-y" not in cmd


def test_gemini_executor_poll(tmp_path):
    executor = GeminiExecutor()
    rc_file = tmp_path / "rc.txt"
    stdout_file = tmp_path / "stdout.log"
    
    rc_file.write_text("0", encoding="utf-8")
    stdout_file.write_text("line1\nline2\n", encoding="utf-8")
    
    state = executor.poll("test1", str(tmp_path))
    assert isinstance(state, TaskState)
    assert state.is_done is True
    assert state.receipt["payload"]["exit_code"] == 0
    assert state.receipt["payload"]["events_count"] == 2
    assert state.receipt["status"] == "COMMITTED"

def test_gemini_poll_error(tmp_path):
    executor = GeminiExecutor()
    rc_file = tmp_path / "rc.txt"
    rc_file.write_text("1", encoding="utf-8")
    state = executor.poll("test2", str(tmp_path))
    assert state.is_done is True
    assert state.receipt["status"] == "BLOCKED"
