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
    with patch("agpair.executors.gemini.subprocess.Popen") as mock_popen:
        mock_popen.return_value = Mock()
        
        executor = GeminiExecutor()
        dispatch_res = executor.dispatch(task_id="test_gemini", body="do some work", repo_path=str(tmp_path))
        
        assert isinstance(dispatch_res, DispatchResult)
        
        
        mock_popen.assert_called_once()
        args = mock_popen.call_args[0][0]
        
        # args should be ["sh", "-c", "..."]
        assert args[0] == "sh"
        assert args[1] == "-c"
        
        wrapper_script = args[2]
        # Check flags we pulled from gemini --help
        assert "gemini -y --output-format json -p " in wrapper_script
        
        # Check that prompt (-p) is included
        assert "-p 'do some work'" in wrapper_script


def test_gemini_executor_poll(tmp_path):
    executor = GeminiExecutor()
    rc_file = tmp_path / "rc.txt"
    stdout_file = tmp_path / "stdout.log"
    
    rc_file.write_text("0", encoding="utf-8")
    stdout_file.write_text("line1\nline2\n", encoding="utf-8")
    
    state = executor.poll("test1", str(tmp_path))
    assert isinstance(state, TaskState)
    assert state.is_done is True
    assert state.receipt["payload"]["returncode"] == 0
    assert state.receipt["payload"]["events_count"] == 2
    assert state.receipt["status"] == "EVIDENCE_PACK"

def test_gemini_poll_error(tmp_path):
    executor = GeminiExecutor()
    rc_file = tmp_path / "rc.txt"
    rc_file.write_text("1", encoding="utf-8")
    state = executor.poll("test2", str(tmp_path))
    assert state.is_done is True
    assert state.receipt["status"] == "BLOCKED"
