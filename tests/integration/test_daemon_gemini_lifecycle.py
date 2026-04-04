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

VALID_BRIEF = "Goal: test\nScope: test\nRequired changes: test\nExit criteria: test"

def write_fake_gemini_bin(tmp_path: pathlib.Path) -> pathlib.Path:
    bin_path = tmp_path / "fake-gemini"
    # A script that ignores arguments, simulates typing some json,
    # outputs a final message, and exits with 0.
    # Note: test_gemini_executor.py asserts that we pass "-o <last_msg_file>"
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
        echo "Fake Gemini Success!" > "$OUTPUT_FILE"
    fi
    exit 0
    """
    bin_path.write_text(script)
    bin_path.chmod(0o755)
    return bin_path

def test_gemini_lifecycle_success(tmp_path: pathlib.Path, monkeypatch) -> None:
    # Setup paths and environment
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    
    from agpair.config import AppPaths
    paths = AppPaths.default()
    ensure_database(paths.db_path)
    
    # We don't have a real gemini CLI, we'll patch GeminiExecutor.gemini_bin
    fake_gemini = write_fake_gemini_bin(tmp_path)
    
    # We'll use CliRunner to dispatch the task
    runner = CliRunner()
    
    # To use our fake gemini binary, we mock the gemini_bin in GeminiExecutor
    import agpair.executors.gemini
    original_init = agpair.executors.gemini.GeminiExecutor.__init__
    
    def mocked_init(self, gemini_bin="gemini"):
        original_init(self, str(fake_gemini))
        
    monkeypatch.setattr(agpair.executors.gemini.GeminiExecutor, "__init__", mocked_init)
    
    # 1. Start a Gemini-backed task
    result = runner.invoke(app, [
        "start",
        "--repo-path", str(tmp_path),
        "--task-id", "TASK-GEMINI-TEST",
        "--executor", "gemini",
        "--body", VALID_BRIEF,
        "--no-wait",
    ])
    assert result.exit_code == 0
    assert "TASK-GEMINI-TEST" in result.output
    
    # 2. Check that it is immediately 'acked' with the temp_dir as session_id
    tasks = TaskRepository(paths.db_path)
    task = tasks.get_task("TASK-GEMINI-TEST")
    assert task is not None
    assert task.phase == "acked"
    assert task.executor_backend == "gemini_cli"
    
    session_id = task.antigravity_session_id
    assert session_id is not None
    assert "agpair_gemini_TASK-GEMINI-TEST_" in session_id
    
    # Wait for the fake subprocess to finish writing its output
    temp_dir = pathlib.Path(session_id)
    rc_file = temp_dir / "rc.txt"
    import time
    for _ in range(50):
        if rc_file.exists():
            break
        time.sleep(0.1)
    
    assert rc_file.exists(), "fake gemini wrapper should have created rc.txt"
    with open(rc_file) as f:
        assert f.read().strip() == "0"
    
    assert temp_dir.exists(), "temp_dir must exist before daemon cleanup"
        
    # 3. Run daemon run_once (mocking the bus client as it shouldn't be used for gemini polling)
    # The daemon should poll the local temp_dir, find it done, and synthesize receipt
    mock_bus = mock.MagicMock()
    run_once(paths, now=datetime.now(UTC), bus=mock_bus)
    
    # 4. Check the task phase is now 'evidence_ready' and receipt matches
    task = tasks.get_task("TASK-GEMINI-TEST")
    assert task.phase == "evidence_ready"
    
    assert not temp_dir.exists(), "temp_dir must be cleaned up after terminal transition"
    
    # Verify the terminal receipt synthesis
    from agpair.storage.journal import JournalRepository
    journal = JournalRepository(paths.db_path)
    terminal_event = None
    for row in journal.tail("TASK-GEMINI-TEST", limit=10):
        if row.event == "evidence_ready":
            terminal_event = row
            break
            
    assert terminal_event is not None
    receipt = json.loads(terminal_event.body)
    assert receipt["status"] == "EVIDENCE_PACK"
    assert receipt["summary"] == "Task finished successfully via Gemini"
    assert receipt["payload"]["returncode"] == 0
    assert receipt["payload"]["events_count"] == 2

def write_failing_gemini_bin(tmp_path: pathlib.Path) -> pathlib.Path:
    bin_path = tmp_path / "fake-gemini-fail"
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
        echo "Fake Gemini Error: syntax error" > "$OUTPUT_FILE"
    fi
    exit 1
    """
    bin_path.write_text(script)
    bin_path.chmod(0o755)
    return bin_path

def test_gemini_lifecycle_failure(tmp_path: pathlib.Path, monkeypatch) -> None:
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    from agpair.config import AppPaths
    paths = AppPaths.default()
    ensure_database(paths.db_path)
    
    fake_gemini = write_failing_gemini_bin(tmp_path)
    import agpair.executors.gemini
    original_init = agpair.executors.gemini.GeminiExecutor.__init__
    def mocked_init(self, gemini_bin="gemini"):
        original_init(self, str(fake_gemini))
    monkeypatch.setattr(agpair.executors.gemini.GeminiExecutor, "__init__", mocked_init)
    
    runner = CliRunner()
    result = runner.invoke(app, [
        "start", "--repo-path", str(tmp_path), "--task-id", "TASK-GEMINI-FAIL",
        "--executor", "gemini", "--body", VALID_BRIEF, "--no-wait",
    ])
    assert result.exit_code == 0
    
    tasks = TaskRepository(paths.db_path)
    task = tasks.get_task("TASK-GEMINI-FAIL")
    assert task.phase == "acked"
    
    session_id = task.antigravity_session_id
    temp_dir = pathlib.Path(session_id)
    rc_file = temp_dir / "rc.txt"
    import time
    for _ in range(50):
        if rc_file.exists():
            break
        time.sleep(0.1)
    
    assert temp_dir.exists(), "temp_dir must exist before daemon cleanup"
    
    mock_bus = mock.MagicMock()
    run_once(paths, now=datetime.now(UTC), bus=mock_bus)
    
    task = tasks.get_task("TASK-GEMINI-FAIL")
    assert task.phase == "blocked"
    
    assert not temp_dir.exists(), "temp_dir must be cleaned up after failing task transitions to blocked"
    
    from agpair.storage.journal import JournalRepository
    journal = JournalRepository(paths.db_path)
    terminal_event = None
    for row in journal.tail("TASK-GEMINI-FAIL", limit=10):
        if row.event == "blocked":
            terminal_event = row
            break
            
    assert terminal_event is not None
    receipt = json.loads(terminal_event.body)
    assert receipt["status"] == "BLOCKED"
    assert receipt["summary"] == "Gemini executor failed with return code 1"
    assert receipt["payload"]["returncode"] == 1
    assert receipt["payload"]["blocker_type"] == "execution_error"


def test_gemini_evidence_ready_not_repolled(tmp_path: pathlib.Path, monkeypatch) -> None:
    """After a Gemini task reaches evidence_ready, the daemon must NOT re-poll it."""
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    from agpair.config import AppPaths
    paths = AppPaths.default()
    ensure_database(paths.db_path)

    fake_gemini = write_fake_gemini_bin(tmp_path)
    import agpair.executors.gemini
    original_init = agpair.executors.gemini.GeminiExecutor.__init__

    def mocked_init(self, gemini_bin="gemini"):
        original_init(self, str(fake_gemini))

    monkeypatch.setattr(agpair.executors.gemini.GeminiExecutor, "__init__", mocked_init)

    runner = CliRunner()
    result = runner.invoke(app, [
        "start", "--repo-path", str(tmp_path), "--task-id", "TASK-GEMINI-NR",
        "--executor", "gemini", "--body", VALID_BRIEF, "--no-wait",
    ])
    assert result.exit_code == 0

    tasks = TaskRepository(paths.db_path)
    task = tasks.get_task("TASK-GEMINI-NR")
    session_id = task.antigravity_session_id
    rc_file = pathlib.Path(session_id) / "rc.txt"

    import time
    for _ in range(50):
        if rc_file.exists():
            break
        time.sleep(0.1)

    mock_bus = mock.MagicMock()
    run_once(paths, now=datetime.now(UTC), bus=mock_bus)
    task = tasks.get_task("TASK-GEMINI-NR")
    assert task.phase == "evidence_ready"

    from agpair.storage.journal import JournalRepository
    journal = JournalRepository(paths.db_path)

    # Second tick should NOT produce new evidence_ready entries
    run_once(paths, now=datetime.now(UTC), bus=mock_bus)
    task = tasks.get_task("TASK-GEMINI-NR")
    assert task.phase == "evidence_ready"

    rows_after = journal.tail("TASK-GEMINI-NR", limit=100)
    evidence_events = [r for r in rows_after if r.event == "evidence_ready"]
    assert len(evidence_events) == 1, "evidence_ready must not be emitted twice"


def test_gemini_receipt_carries_real_attempt_no(tmp_path: pathlib.Path, monkeypatch) -> None:
    """Synthesized receipt must carry the real task attempt_no."""
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    from agpair.config import AppPaths
    paths = AppPaths.default()
    ensure_database(paths.db_path)

    fake_gemini = write_fake_gemini_bin(tmp_path)
    import agpair.executors.gemini
    original_init = agpair.executors.gemini.GeminiExecutor.__init__

    def mocked_init(self, gemini_bin="gemini"):
        original_init(self, str(fake_gemini))

    monkeypatch.setattr(agpair.executors.gemini.GeminiExecutor, "__init__", mocked_init)

    runner = CliRunner()
    result = runner.invoke(app, [
        "start", "--repo-path", str(tmp_path), "--task-id", "TASK-GEMINI-ATT",
        "--executor", "gemini", "--body", VALID_BRIEF, "--no-wait",
    ])
    assert result.exit_code == 0

    tasks = TaskRepository(paths.db_path)
    task = tasks.get_task("TASK-GEMINI-ATT")
    assert task.attempt_no == 1

    rc_file = pathlib.Path(task.antigravity_session_id) / "rc.txt"
    import time
    for _ in range(50):
        if rc_file.exists():
            break
        time.sleep(0.1)

    mock_bus = mock.MagicMock()
    run_once(paths, now=datetime.now(UTC), bus=mock_bus)
    task = tasks.get_task("TASK-GEMINI-ATT")
    assert task.phase == "evidence_ready"

    from agpair.storage.journal import JournalRepository
    journal = JournalRepository(paths.db_path)
    for row in journal.tail("TASK-GEMINI-ATT", limit=10):
        if row.event == "evidence_ready":
            receipt = json.loads(row.body)
            assert receipt["attempt_no"] == 1
            break
    else:
        raise AssertionError("evidence_ready journal entry not found")
