import pytest
import subprocess
from pathlib import Path

from agpair.executors.gemini import GeminiExecutor, GeminiTaskRef, GeminiTaskState
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
        task_ref = executor.dispatch(task_id="test_gemini", body="do some work", repo_path=str(tmp_path))
        
        assert isinstance(task_ref, GeminiTaskRef)
        assert task_ref.task_id == "test_gemini"
        
        mock_popen.assert_called_once()
        args = mock_popen.call_args[0][0]
        
        # args should be ["sh", "-c", "..."]
        assert args[0] == "sh"
        assert args[1] == "-c"
        
        wrapper_script = args[2]
        # Check flags we pulled from gemini --help
        assert "gemini -y --output-format json -w " in wrapper_script
        
        # Check that prompt (-p) is included
        assert "-p 'do some work'" in wrapper_script


def test_gemini_executor_poll(tmp_path):
    executor = GeminiExecutor()
    
    rc_file = tmp_path / "rc.txt"
    stdout_file = tmp_path / "stdout.log"
    stderr_file = tmp_path / "stderr.log"
    
    rc_file.write_text("0", encoding="utf-8")
    stdout_file.write_text("line1\nline2\n", encoding="utf-8")
    
    task_ref = GeminiTaskRef(
        task_id="test1",
        process=None,
        stdout_file=stdout_file,
        stderr_file=stderr_file,
        rc_file=rc_file,
        temp_dir=tmp_path,
    )
    
    state = executor.poll(task_ref)
    assert isinstance(state, GeminiTaskState)
    assert state.is_done is True
    assert state.returncode == 0
    assert state.events_count == 2
    
    
def test_gemini_synthesize_receipt():
    state = GeminiTaskState(is_done=True, returncode=0, events_count=5)
    receipt = state.synthesize_receipt("t1")
    assert receipt["status"] == "EVIDENCE_PACK"
    assert receipt["payload"]["returncode"] == 0
    assert receipt["payload"]["events_count"] == 5

    state_err = GeminiTaskState(is_done=True, returncode=1, events_count=0)
    receipt_err = state_err.synthesize_receipt("t2")
    assert receipt_err["status"] == "BLOCKED"
    assert receipt_err["payload"]["returncode"] == 1
    assert receipt_err["payload"]["blocker_type"] == "execution_error"
