import json
import pathlib
import subprocess
from unittest import mock
from datetime import UTC, datetime

from agpair.cli.task import app
from agpair.daemon.loop import run_once
from agpair.storage.db import ensure_database
from agpair.storage.tasks import TaskRepository
from typer.testing import CliRunner

def write_fake_codex_bin(tmp_path: pathlib.Path) -> pathlib.Path:
    bin_path = tmp_path / "fake-codex"
    # A script that ignores arguments, simulates typing some json,
    # outputs a final message, and exits with 0.
    # Note: test_codex_executor.py asserts that we pass "-o <last_msg_file>"
    # so we need a minimal sh wrapper that writes something to the requested file.
    script = """#!/bin/bash
    OUTPUT_FILE=""
    while [[ $# -gt 0 ]]; do
      case $1 in
        -o)
          OUTPUT_FILE="$2"
          shift 2
          ;;
        *)
          shift
          ;;
      esac
    done

    echo '{"event": "start"}'
    echo '{"event": "end"}'

    if [ -n "$OUTPUT_FILE" ]; then
        echo "Fake Codex Success!" > "$OUTPUT_FILE"
    fi
    exit 0
    """
    bin_path.write_text(script)
    bin_path.chmod(0o755)
    return bin_path

def test_codex_lifecycle_success(tmp_path: pathlib.Path, monkeypatch) -> None:
    # Setup paths and environment
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    
    from agpair.config import AppPaths
    paths = AppPaths.default()
    ensure_database(paths.db_path)
    
    # We don't have a real codex CLI, we'll patch CodexExecutor.codex_bin
    fake_codex = write_fake_codex_bin(tmp_path)
    
    # We'll use CliRunner to dispatch the task
    runner = CliRunner()
    
    # To use our fake codex binary, we mock the codex_bin in CodexExecutor
    import agpair.executors.codex
    original_init = agpair.executors.codex.CodexExecutor.__init__
    
    def mocked_init(self, codex_bin="codex"):
        original_init(self, str(fake_codex))
        
    monkeypatch.setattr(agpair.executors.codex.CodexExecutor, "__init__", mocked_init)
    
    # 1. Start a Codex-backed task
    result = runner.invoke(app, [
        "start",
        "--repo-path", str(tmp_path),
        "--task-id", "TASK-CODEX-TEST",
        "--executor", "codex",
        "--body", "test body",
        "--no-wait",
    ])
    assert result.exit_code == 0
    assert "TASK-CODEX-TEST" in result.output
    
    # 2. Check that it is immediately 'acked' with the temp_dir as session_id
    tasks = TaskRepository(paths.db_path)
    task = tasks.get_task("TASK-CODEX-TEST")
    assert task is not None
    assert task.phase == "acked"
    assert task.executor_backend == "codex_cli"
    
    session_id = task.antigravity_session_id
    assert session_id is not None
    assert "agpair_codex_TASK-CODEX-TEST_" in session_id
    
    # Wait for the fake subprocess to finish writing its output
    temp_dir = pathlib.Path(session_id)
    rc_file = temp_dir / "rc.txt"
    import time
    for _ in range(50):
        if rc_file.exists():
            break
        time.sleep(0.1)
    
    assert rc_file.exists(), "fake codex wrapper should have created rc.txt"
    with open(rc_file) as f:
        assert f.read().strip() == "0"
        
    # 3. Run daemon run_once (mocking the bus client as it shouldn't be used for codex polling)
    # The daemon should poll the local temp_dir, find it done, and synthesize receipt
    mock_bus = mock.MagicMock()
    run_once(paths, now=datetime.now(UTC), bus=mock_bus)
    
    # 4. Check the task phase is now 'evidence_ready' and receipt matches
    task = tasks.get_task("TASK-CODEX-TEST")
    assert task.phase == "evidence_ready"
    
    # Verify the terminal receipt synthesis
    from agpair.storage.journal import JournalRepository
    journal = JournalRepository(paths.db_path)
    terminal_event = None
    for row in journal.tail("TASK-CODEX-TEST", limit=10):
        if row.event == "evidence_ready":
            terminal_event = row
            break
            
    assert terminal_event is not None
    receipt = json.loads(terminal_event.body)
    assert receipt["status"] == "EVIDENCE_PACK"
    assert receipt["summary"] == "Fake Codex Success!"
    assert receipt["payload"]["returncode"] == 0
    assert receipt["payload"]["events_count"] == 2

def write_failing_codex_bin(tmp_path: pathlib.Path) -> pathlib.Path:
    bin_path = tmp_path / "fake-codex-fail"
    script = """#!/bin/bash
    OUTPUT_FILE=""
    while [[ $# -gt 0 ]]; do
      case $1 in
        -o)
          OUTPUT_FILE="$2"
          shift 2
          ;;
        *)
          shift
          ;;
      esac
    done
    echo '{"event": "start"}'
    if [ -n "$OUTPUT_FILE" ]; then
        echo "Fake Codex Error: syntax error" > "$OUTPUT_FILE"
    fi
    exit 1
    """
    bin_path.write_text(script)
    bin_path.chmod(0o755)
    return bin_path

def test_codex_lifecycle_failure(tmp_path: pathlib.Path, monkeypatch) -> None:
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    from agpair.config import AppPaths
    paths = AppPaths.default()
    ensure_database(paths.db_path)
    
    fake_codex = write_failing_codex_bin(tmp_path)
    import agpair.executors.codex
    original_init = agpair.executors.codex.CodexExecutor.__init__
    def mocked_init(self, codex_bin="codex"):
        original_init(self, str(fake_codex))
    monkeypatch.setattr(agpair.executors.codex.CodexExecutor, "__init__", mocked_init)
    
    runner = CliRunner()
    result = runner.invoke(app, [
        "start", "--repo-path", str(tmp_path), "--task-id", "TASK-CODEX-FAIL",
        "--executor", "codex", "--body", "test bad", "--no-wait",
    ])
    assert result.exit_code == 0
    
    tasks = TaskRepository(paths.db_path)
    task = tasks.get_task("TASK-CODEX-FAIL")
    assert task.phase == "acked"
    
    session_id = task.antigravity_session_id
    rc_file = pathlib.Path(session_id) / "rc.txt"
    import time
    for _ in range(50):
        if rc_file.exists():
            break
        time.sleep(0.1)
    
    mock_bus = mock.MagicMock()
    run_once(paths, now=datetime.now(UTC), bus=mock_bus)
    
    task = tasks.get_task("TASK-CODEX-FAIL")
    assert task.phase == "blocked"
    
    from agpair.storage.journal import JournalRepository
    journal = JournalRepository(paths.db_path)
    terminal_event = None
    for row in journal.tail("TASK-CODEX-FAIL", limit=10):
        if row.event == "blocked":
            terminal_event = row
            break
            
    assert terminal_event is not None
    receipt = json.loads(terminal_event.body)
    assert receipt["status"] == "BLOCKED"
    assert receipt["summary"] == "Fake Codex Error: syntax error"
    assert receipt["payload"]["returncode"] == 1
    assert receipt["payload"]["blocker_type"] == "execution_error"

