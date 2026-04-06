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
