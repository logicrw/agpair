"""Regression tests for 4 high-risk paths identified in 2026-04-06 audit.

1. Retry dedup: second terminal receipt after retry must NOT be silently dropped.
2. Repo evidence false auto-close: historical commit must NOT close a new task.
3. Commit body parsing: task_id in footer/body must be detected.
4. SIGKILL zombie reaping: cleanup must complete after SIGKILL.
"""
from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

from agpair.config import AppPaths
from agpair.executors.base import TaskState
from agpair.storage.db import connect, ensure_database
from agpair.storage.journal import JournalRepository
from agpair.storage.receipts import ReceiptRepository
from agpair.storage.tasks import TaskRepository


class FakePullBus:
    def __init__(self, receipts: list[dict] | None = None) -> None:
        self._receipts = receipts or []

    def pull_receipts(self, *, task_id: str | None = None, limit: int = 20) -> list[dict]:
        return list(self._receipts)

    def send_task(self, *args, **kwargs):
        pass


def make_paths(tmp_path: Path) -> AppPaths:
    return AppPaths.from_root(tmp_path / ".agpair")


def seed_task(tmp_path: Path, task_id: str = "TASK-1", repo_path: str = "/tmp/repo") -> AppPaths:
    paths = make_paths(tmp_path)
    ensure_database(paths.db_path)
    TaskRepository(paths.db_path).create_task(
        task_id=task_id, repo_path=repo_path
    )
    return paths


# --------------------------------------------------------------------------
# Test 1: Retry receipt dedup — second terminal receipt must NOT be dropped
# --------------------------------------------------------------------------
def test_retry_terminal_receipt_not_deduped(tmp_path: Path, monkeypatch) -> None:
    """After retry, the second terminal receipt must be ingested, not silently dropped.

    Reproduces: synthetic msg_id was fixed as '{backend}-{task_id}-done' with no
    attempt_no, so ReceiptRepository.record() rejected the retry's terminal receipt
    as a duplicate, leaving the task stuck in 'acked' forever.
    """
    from agpair.daemon.loop import run_once

    paths = seed_task(tmp_path, "TASK-RETRY")
    tasks = TaskRepository(paths.db_path)
    receipts = ReceiptRepository(paths.db_path)

    # --- Attempt 1: ack + terminal COMMITTED ---
    with connect(paths.db_path) as conn:
        conn.execute(
            "UPDATE tasks SET executor_backend=?, phase='acked', antigravity_session_id=?, attempt_no=1 "
            "WHERE task_id=?",
            ("codex_cli", "session-attempt-1", "TASK-RETRY"),
        )
        conn.commit()

    class FakeExecutorAttempt1:
        def poll(self, task_id, session_id, attempt_no=1):
            return TaskState(
                is_done=True,
                receipt={
                    "schema_version": "1", "task_id": task_id, "attempt_no": attempt_no,
                    "review_round": 0, "status": "COMMITTED", "summary": "Attempt 1 done",
                    "payload": {"exit_code": 0},
                },
            )
        def cleanup(self, session_id):
            pass

    monkeypatch.setattr("agpair.executors.get_executor", lambda bid, **kw: FakeExecutorAttempt1())
    run_once(paths, now=datetime(2026, 4, 6, 12, 0, tzinfo=UTC), bus=FakePullBus())

    task = tasks.get_task("TASK-RETRY")
    assert task.phase == "committed", "Attempt 1 should land as committed"

    # --- Retry: reset to new → ack attempt 2 ---
    tasks.apply_retry_dispatch(task_id="TASK-RETRY")
    task = tasks.get_task("TASK-RETRY")
    assert task.phase == "new"
    assert task.attempt_no == 2

    with connect(paths.db_path) as conn:
        conn.execute(
            "UPDATE tasks SET executor_backend=?, phase='acked', antigravity_session_id=? "
            "WHERE task_id=?",
            ("codex_cli", "session-attempt-2", "TASK-RETRY"),
        )
        conn.commit()

    # --- Attempt 2: terminal COMMITTED with different session ---
    class FakeExecutorAttempt2:
        def poll(self, task_id, session_id, attempt_no=1):
            return TaskState(
                is_done=True,
                receipt={
                    "schema_version": "1", "task_id": task_id, "attempt_no": attempt_no,
                    "review_round": 0, "status": "COMMITTED", "summary": "Attempt 2 done",
                    "payload": {"exit_code": 0},
                },
            )
        def cleanup(self, session_id):
            pass

    monkeypatch.setattr("agpair.executors.get_executor", lambda bid, **kw: FakeExecutorAttempt2())
    run_once(paths, now=datetime(2026, 4, 6, 12, 5, tzinfo=UTC), bus=FakePullBus())

    task = tasks.get_task("TASK-RETRY")
    assert task.phase == "committed", \
        "Attempt 2 terminal receipt must NOT be deduped — task should reach committed again"


# --------------------------------------------------------------------------
# Test 2: Repo evidence false auto-close — historical commit must NOT close new task
# --------------------------------------------------------------------------
def test_repo_evidence_rejects_historical_commits(tmp_path: Path) -> None:
    """A task_id that appeared in a commit BEFORE the task was created must not auto-close it.

    Reproduces: detect_committed_task_in_repo() used `git log --all` with no time bound,
    so any historical commit with the task_id would trigger a false auto-close.
    """
    from agpair.daemon.loop import auto_close_evidence_ready_tasks

    # Set up a git repo with one historical commit containing a task_id
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True, capture_output=True)
    (repo / "old.txt").write_text("old")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "feat: old work TASK-HISTORY-1"],
        cwd=repo, check=True, capture_output=True,
    )

    # Create a new task with the SAME task_id, AFTER the historical commit
    import time
    time.sleep(1.1)  # Ensure created_at is after the commit timestamp

    paths = make_paths(tmp_path)
    ensure_database(paths.db_path)
    tasks_repo = TaskRepository(paths.db_path)
    tasks_repo.create_task(
        task_id="TASK-HISTORY-1", repo_path=str(repo)
    )
    tasks_repo.mark_acked(task_id="TASK-HISTORY-1", session_id="session-new")

    count = auto_close_evidence_ready_tasks(paths)
    assert count == 0, (
        "Historical commit from BEFORE task creation must NOT trigger auto-close. "
        "detect_committed_task_in_repo must use --after=<task.created_at> to bound the search."
    )

    task = tasks_repo.get_task("TASK-HISTORY-1")
    assert task.phase == "acked", "Task must remain acked, not falsely auto-closed"


# --------------------------------------------------------------------------
# Test 3: Commit body parsing — task_id in footer or multi-paragraph body
# --------------------------------------------------------------------------
def test_git_log_grep_task_id_finds_id_in_footer():
    """task_id appearing only in the commit footer (after blank lines) must be found."""
    from agpair.executors.local_cli import _git_log_grep_task_id

    # Simulate conventional commit with task_id in footer, separated by blank lines
    git_output = (
        "abc123\x00feat: implement new API\n\n"
        "This is the detailed description of the change.\n"
        "It spans multiple lines.\n\n"
        "Refs: TASK-FOOTER-1\n"
        "Signed-off-by: dev <dev@test.com>\x01"
    )
    with mock.patch(
        "agpair.executors.local_cli.subprocess.check_output",
        return_value=git_output,
    ):
        assert _git_log_grep_task_id("/repo", "start", "end", "TASK-FOOTER-1") is True


def test_git_log_grep_task_id_handles_multiple_commits_with_blank_lines():
    """Multiple commits each with multi-paragraph bodies must be parsed correctly."""
    from agpair.executors.local_cli import _git_log_grep_task_id

    git_output = (
        "aaa111\x00feat: unrelated\n\nBody of unrelated commit\x01\n"
        "bbb222\x00fix: the real fix\n\nDetailed explanation\n\nRefs: TASK-MULTI-1\x01"
    )
    with mock.patch(
        "agpair.executors.local_cli.subprocess.check_output",
        return_value=git_output,
    ):
        assert _git_log_grep_task_id("/repo", "start", "end", "TASK-MULTI-1") is True


# --------------------------------------------------------------------------
# Test 4: SIGKILL zombie reaping — cleanup must NOT loop forever
# --------------------------------------------------------------------------
def test_cleanup_completes_after_sigkill_zombie(tmp_path: Path) -> None:
    """After SIGKILL + 2s timeout, cleanup must force-reap and remove temp dir."""
    from agpair.executors.local_cli import LocalCLIExecutor
    from agpair.models import ContinuationCapability

    class DummyExec(LocalCLIExecutor):
        def __init__(self):
            super().__init__("dummy", "dummy_cli", lambda b, r, t: ["echo"])
        @property
        def continuation_capability(self):
            return ContinuationCapability.UNSUPPORTED

    executor = DummyExec()
    session_dir = tmp_path / "agpair_dummy_zombie_test"
    session_dir.mkdir()
    (session_dir / "state.json").write_text(
        json.dumps({
            "version": 1,
            "pid": 9999,
            "pgid": 9999,
            "started_at": "2026-04-06T00:00:00Z",
            "termination_requested_at": "2026-04-06T00:00:00Z",
            "termination_signal": "SIGKILL",
            "arbitration_rc": None,
            "repo_path": None,
        }),
        encoding="utf-8",
    )

    # Simulate: _is_process_alive returns True (zombie), but after 2s timeout it's force-treated as dead
    call_count = [0]
    def fake_alive(pgid, **kw):
        call_count[0] += 1
        if call_count[0] == 1:
            return True  # First check: looks alive (zombie)
        return False  # After force-reap in _ensure_process_dead

    with mock.patch("agpair.executors.local_cli._is_process_alive", side_effect=fake_alive), \
         mock.patch("agpair.executors.local_cli._seconds_since", return_value=3), \
         mock.patch("agpair.executors.local_cli._reap_child_process") as reap:
        executor.cleanup(str(session_dir))

    assert not session_dir.exists(), \
        "Temp dir must be removed after SIGKILL+timeout zombie — cleanup must not infinite-loop"
    reap.assert_called()


# --------------------------------------------------------------------------
# Test 5: Retry + stale commit — attempt 1's commit must NOT close attempt 2
# --------------------------------------------------------------------------
def test_retry_stale_commit_does_not_close_new_attempt(tmp_path: Path) -> None:
    """A commit from attempt 1 must NOT auto-close attempt 2.

    Reproduces: auto_close used task.created_at as time boundary, but created_at
    doesn't change on retry. Attempt 1's commit (after created_at) falsely closes
    attempt 2 immediately upon ack.
    """
    from agpair.daemon.loop import auto_close_evidence_ready_tasks

    # Set up a git repo and create the commit from "attempt 1"
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True, capture_output=True)
    (repo / "init.txt").write_text("init")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)

    # Create task and simulate attempt 1 committed
    paths = make_paths(tmp_path)
    ensure_database(paths.db_path)
    tasks_repo = TaskRepository(paths.db_path)
    tasks_repo.create_task(
        task_id="TASK-RETRY-STALE", repo_path=str(repo)
    )
    tasks_repo.mark_acked(task_id="TASK-RETRY-STALE", session_id="session-1")

    # Attempt 1 makes a commit
    import time
    time.sleep(0.1)
    (repo / "attempt1.txt").write_text("attempt 1 work")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "feat: done TASK-RETRY-STALE"],
        cwd=repo, check=True, capture_output=True,
    )

    # Mark attempt 1 as committed, then retry
    tasks_repo.mark_committed(task_id="TASK-RETRY-STALE", terminal_source="receipt")
    tasks_repo.apply_retry_dispatch(task_id="TASK-RETRY-STALE")
    time.sleep(1.1)  # Ensure ack timestamp is AFTER the stale commit
    tasks_repo.mark_acked(task_id="TASK-RETRY-STALE", session_id="session-2")

    task = tasks_repo.get_task("TASK-RETRY-STALE")
    assert task.phase == "acked"
    assert task.attempt_no == 2

    # auto_close should NOT find attempt 1's commit for attempt 2
    count = auto_close_evidence_ready_tasks(paths)
    assert count == 0, (
        "Attempt 1's stale commit must NOT auto-close attempt 2. "
        "auto_close must use last_activity_at (attempt-level anchor), not created_at."
    )

    task = tasks_repo.get_task("TASK-RETRY-STALE")
    assert task.phase == "acked", "Task must remain acked after retry, not falsely closed by stale commit"


# --------------------------------------------------------------------------
# Test 6: Side branch commit must NOT auto-close task on main
# --------------------------------------------------------------------------
def test_side_branch_commit_does_not_close_main_task(tmp_path: Path) -> None:
    """A commit on a different branch must NOT auto-close a task on the current branch.

    Reproduces: detect_committed_task_in_repo used `git log --all` which searches
    all branches, so a commit on any branch would falsely close the task even though
    the current working tree doesn't have that code.
    """
    from agpair.daemon.loop import auto_close_evidence_ready_tasks

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True, capture_output=True)
    (repo / "init.txt").write_text("init")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)

    # Create a side branch with a commit containing the task_id
    subprocess.run(["git", "checkout", "-b", "side"], cwd=repo, check=True, capture_output=True)
    (repo / "side.txt").write_text("side branch work")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "feat: side branch TASK-SIDE-1"],
        cwd=repo, check=True, capture_output=True,
    )

    # Switch back to main — this branch does NOT have the side commit
    subprocess.run(["git", "checkout", "main"], cwd=repo, capture_output=True)
    # Fallback: try 'master' if 'main' doesn't exist
    result = subprocess.run(["git", "branch", "--show-current"], cwd=repo, capture_output=True, text=True)
    if result.stdout.strip() != "main":
        subprocess.run(["git", "checkout", "master"], cwd=repo, capture_output=True)

    paths = make_paths(tmp_path)
    ensure_database(paths.db_path)
    tasks_repo = TaskRepository(paths.db_path)
    tasks_repo.create_task(
        task_id="TASK-SIDE-1", repo_path=str(repo)
    )
    tasks_repo.mark_acked(task_id="TASK-SIDE-1", session_id="session-main")

    count = auto_close_evidence_ready_tasks(paths)
    assert count == 0, (
        "Commit on side branch must NOT auto-close task on main. "
        "detect_committed_task_in_repo must NOT use --all."
    )

    task = tasks_repo.get_task("TASK-SIDE-1")
    assert task.phase == "acked", "Task on main must remain acked when commit is only on side branch"


# --------------------------------------------------------------------------
# Test 7: Cleanup with PermissionError must NOT delete session dir
# --------------------------------------------------------------------------
def test_cleanup_permission_denied_preserves_session_dir(tmp_path: Path) -> None:
    """When SIGTERM/SIGKILL fails with PermissionError, cleanup must NOT delete
    the session directory. The process is still alive but we can't kill it.

    Reproduces: _ensure_process_dead returned (False, None) on PermissionError,
    causing cleanup to proceed to shutil.rmtree while the process was still running.
    """
    from agpair.executors.local_cli import LocalCLIExecutor
    from agpair.models import ContinuationCapability

    class DummyExec(LocalCLIExecutor):
        def __init__(self):
            super().__init__("dummy", "dummy_cli", lambda b, r, t: ["echo"])
        @property
        def continuation_capability(self):
            return ContinuationCapability.UNSUPPORTED

    executor = DummyExec()
    session_dir = tmp_path / "agpair_dummy_permerror_test"
    session_dir.mkdir()
    (session_dir / "state.json").write_text(
        json.dumps({
            "version": 1,
            "pid": 8888,
            "pgid": 8888,
            "started_at": "2026-04-06T00:00:00Z",
            "arbitration_rc": None,
            "repo_path": None,
        }),
        encoding="utf-8",
    )

    # _is_process_alive returns True, killpg raises PermissionError
    with mock.patch("agpair.executors.local_cli._is_process_alive", return_value=True), \
         mock.patch("agpair.executors.local_cli.os.killpg", side_effect=PermissionError("Operation not permitted")):
        executor.cleanup(str(session_dir))

    assert session_dir.exists(), (
        "Session dir must NOT be deleted when kill fails with PermissionError. "
        "Process is still alive — deleting session dir loses tracking state."
    )

    # Verify state.json still marks process as alive
    state = json.loads((session_dir / "state.json").read_text(encoding="utf-8"))
    assert state["is_process_alive"] is True, "Process must remain marked as alive after PermissionError"

