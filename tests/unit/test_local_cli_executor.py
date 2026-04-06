import json
from unittest import mock

from agpair.executors.local_cli import LocalCLIExecutor, _is_process_alive, _get_process_start_time
from agpair.models import ContinuationCapability


class DummyLocalCLIExecutor(LocalCLIExecutor):
    def __init__(self) -> None:
        super().__init__(
            bin_path="dummy-cli",
            backend_id="dummy_cli",
            build_cmd=self._build_dummy_cmd,
        )

    def _build_dummy_cmd(self, body: str, repo_path: str, temp_dir) -> list[str]:
        return [self.bin_path, body]

    @property
    def continuation_capability(self) -> ContinuationCapability:
        return ContinuationCapability.UNSUPPORTED


def test_poll_persists_final_summary_to_state_json(tmp_path):
    executor = DummyLocalCLIExecutor()
    (tmp_path / "rc.txt").write_text("0", encoding="utf-8")
    (tmp_path / "stdout.log").write_text("line1\nline2\n", encoding="utf-8")
    (tmp_path / "last_msg.txt").write_text("All done.", encoding="utf-8")

    state = executor.poll("TASK-LOCAL-OK", str(tmp_path))

    assert state is not None
    assert state.is_done is True
    assert state.receipt["status"] == "COMMITTED"
    assert state.receipt["summary"] == "All done."

    persisted = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert persisted["exit_code"] == 0
    assert persisted["final_summary"] == "All done."
    assert persisted["error_summary"] is None
    assert state.receipt["payload"]["returncode"] == 0


def test_poll_persists_error_summary_to_state_json(tmp_path):
    executor = DummyLocalCLIExecutor()
    (tmp_path / "rc.txt").write_text("7", encoding="utf-8")
    (tmp_path / "stderr.log").write_text("\u001b[31mboom\u001b[0m\nmore detail\n", encoding="utf-8")

    state = executor.poll("TASK-LOCAL-ERR", str(tmp_path))

    assert state is not None
    assert state.is_done is True
    assert state.receipt["status"] == "BLOCKED"
    assert "boom" in state.receipt["summary"]

    persisted = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert persisted["exit_code"] == 7
    assert "boom" in persisted["error_summary"]
    assert persisted["final_summary"] is None
    assert state.receipt["payload"]["returncode"] == 7


def test_poll_blocks_success_exit_without_commit_when_commit_evidence_available(tmp_path):
    executor = DummyLocalCLIExecutor()
    (tmp_path / "state.json").write_text(
        json.dumps(
            {
                "version": 1,
                "pid": 1234,
                "pgid": 1234,
                "started_at": "2026-04-06T00:00:00Z",
                "repo_path": "/fake/repo",
                "start_head": "abc123",
                "current_head": None,
                "exit_code": None,
                "arbitration_rc": None,
                "is_process_alive": False,
                "has_committed": False,
                "commit_detected_at": None,
                "is_worktree_dirty": False,
                "final_summary": None,
                "error_summary": None,
                "updated_at": "2026-04-06T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "rc.txt").write_text("0", encoding="utf-8")
    (tmp_path / "last_msg.txt").write_text("No changes needed.", encoding="utf-8")

    with mock.patch("agpair.executors.local_cli._git_head", return_value="abc123"), \
         mock.patch("agpair.executors.local_cli._git_status_porcelain", return_value=""), \
         mock.patch.object(executor, "_ensure_process_dead", return_value=None), \
         mock.patch.object(executor, "_clean_git_locks"):
        state = executor.poll("TASK-LOCAL-NOCOMMIT", str(tmp_path))

    assert state is not None
    assert state.is_done is True
    assert state.receipt["status"] == "BLOCKED"
    assert state.receipt["summary"] == "Process exited successfully without committing"
    assert state.receipt["payload"]["blocker_type"] == "missing_commit"
    assert state.receipt["payload"]["exit_code"] == 0
    assert state.receipt["payload"]["returncode"] == 0

    persisted = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert persisted["final_summary"] is None
    assert persisted["error_summary"] == "Process exited successfully without committing"


def test_poll_marks_post_commit_hang_arbitration_in_state_json(tmp_path):
    executor = DummyLocalCLIExecutor()
    (tmp_path / "state.json").write_text(
        json.dumps(
            {
                "version": 1,
                "pid": 4242,
                "pgid": 4242,
                "started_at": "2026-04-06T00:00:00Z",
                "repo_path": "/fake/repo",
                "start_head": "abc123",
                "current_head": None,
                "exit_code": None,
                "arbitration_rc": None,
                "is_process_alive": True,
                "has_committed": False,
                "commit_detected_at": None,
                "is_worktree_dirty": False,
                "final_summary": None,
                "error_summary": None,
                "updated_at": "2026-04-06T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )

    with mock.patch("agpair.executors.local_cli._is_process_alive", return_value=True), \
         mock.patch("agpair.executors.local_cli._git_head", return_value="def456"), \
         mock.patch("agpair.executors.local_cli._git_diff_stat", return_value=" 1 file changed, 1 insertion(+)"), \
         mock.patch("agpair.executors.local_cli._git_log_grep_task_id", return_value=True), \
         mock.patch("agpair.executors.local_cli._git_status_porcelain", return_value=""), \
         mock.patch("agpair.executors.local_cli._seconds_since", return_value=31), \
         mock.patch.object(executor, "_ensure_process_dead", return_value=(False, 128 + 15)) as ensure_dead, \
         mock.patch.object(executor, "_clean_git_locks") as clean_locks:
        state = executor.poll("TASK-LOCAL-HANG", str(tmp_path))

    assert state is not None
    assert state.is_done is True
    assert state.receipt["status"] == "COMMITTED"
    assert state.receipt["payload"]["arbitration"] == "post_commit_hang"
    ensure_dead.assert_called_once()
    clean_locks.assert_not_called()

    persisted = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert persisted["has_committed"] is True
    assert persisted["current_head"] == "def456"
    assert persisted["is_process_alive"] is False
    assert persisted["arbitration_rc"] == 128 + 15


def test_poll_does_not_clean_git_locks_on_normal_success(tmp_path):
    executor = DummyLocalCLIExecutor()
    (tmp_path / "rc.txt").write_text("0", encoding="utf-8")
    (tmp_path / "last_msg.txt").write_text("All done.", encoding="utf-8")

    with mock.patch.object(executor, "_clean_git_locks") as clean_locks:
        state = executor.poll("TASK-LOCAL-SAFE", str(tmp_path))

    assert state is not None
    assert state.is_done is True
    clean_locks.assert_not_called()


def test_ensure_process_dead_requests_sigterm_without_sleeping(tmp_path):
    executor = DummyLocalCLIExecutor()
    state = {"pid": 1234, "pgid": 1234}

    with mock.patch("agpair.executors.local_cli._is_process_alive", return_value=True), \
         mock.patch("agpair.executors.local_cli.os.killpg") as killpg, \
         mock.patch("agpair.executors.local_cli.time.sleep") as sleep:
        alive_after, arbitration_rc = executor._ensure_process_dead(state, tmp_path)

    assert alive_after is True
    assert arbitration_rc == 128 + 15
    assert state["termination_signal"] == "SIGTERM"
    assert state["termination_requested_at"]
    killpg.assert_called_once()
    sleep.assert_not_called()


def test_ensure_process_dead_escalates_to_sigkill_after_grace(tmp_path):
    executor = DummyLocalCLIExecutor()
    state = {
        "pid": 1234,
        "pgid": 1234,
        "termination_signal": "SIGTERM",
        "termination_requested_at": "2026-04-06T00:00:00Z",
    }

    with mock.patch("agpair.executors.local_cli._is_process_alive", return_value=True), \
         mock.patch("agpair.executors.local_cli._seconds_since", return_value=6), \
         mock.patch("agpair.executors.local_cli.os.killpg") as killpg:
        alive_after, arbitration_rc = executor._ensure_process_dead(state, tmp_path)

    assert alive_after is True
    assert arbitration_rc == 128 + 9
    assert state["termination_signal"] == "SIGKILL"
    killpg.assert_called_once()


def test_is_process_alive_treats_zombie_as_dead():
    with mock.patch("agpair.executors.local_cli.os.killpg"), \
         mock.patch("agpair.executors.local_cli.subprocess.check_output", side_effect=[
             "Z+\n",  # ps -p: leader is zombie
             "1234\n",  # pgrep -g: only the leader itself
         ]):
        assert _is_process_alive(4321) is False


def test_is_process_alive_treats_live_child_in_same_group_as_alive():
    with mock.patch("agpair.executors.local_cli.os.killpg"), \
         mock.patch("agpair.executors.local_cli.subprocess.check_output", side_effect=[
             "Z+\n",  # ps -p: leader is zombie
             "4321\n5678\n",  # pgrep -g: leader + child
             "S\n",  # ps -p child: non-zombie
         ]):
        assert _is_process_alive(4321) is True


def test_cleanup_waits_for_exit_and_removes_temp_dir(tmp_path):
    executor = DummyLocalCLIExecutor()
    session_dir = tmp_path / "agpair_dummy_cleanup"
    session_dir.mkdir()
    (session_dir / "state.json").write_text(
        json.dumps(
            {
                "version": 1,
                "pid": 1234,
                "pgid": 1234,
                "termination_requested_at": None,
                "termination_signal": None,
                "arbitration_rc": None,
            }
        ),
        encoding="utf-8",
    )

    with mock.patch("agpair.executors.local_cli._is_process_alive", return_value=False), \
         mock.patch.object(executor, "_ensure_process_dead") as ensure_dead, \
         mock.patch.object(executor, "_clean_git_locks") as clean_locks:
        executor.cleanup(str(session_dir))

    assert not session_dir.exists()
    ensure_dead.assert_not_called()
    clean_locks.assert_not_called()


def test_cleanup_does_not_block_or_remove_dir_while_process_still_alive(tmp_path):
    executor = DummyLocalCLIExecutor()
    session_dir = tmp_path / "agpair_dummy_cleanup_running"
    session_dir.mkdir()
    (session_dir / "state.json").write_text(
        json.dumps(
            {
                "version": 1,
                "pid": 1234,
                "pgid": 1234,
                "started_at": "2026-04-06T00:00:00Z",
                "termination_requested_at": None,
                "termination_signal": None,
                "arbitration_rc": None,
            }
        ),
        encoding="utf-8",
    )

    with mock.patch("agpair.executors.local_cli._is_process_alive", return_value=True), \
         mock.patch.object(executor, "_ensure_process_dead", return_value=(True, 128 + 15)) as ensure_dead, \
         mock.patch("agpair.executors.local_cli.time.sleep", side_effect=AssertionError("cleanup must not sleep")):
        executor.cleanup(str(session_dir))

    assert session_dir.exists()
    ensure_dead.assert_called_once()
    persisted = json.loads((session_dir / "state.json").read_text(encoding="utf-8"))
    assert persisted["is_process_alive"] is True
    assert persisted["arbitration_rc"] == 128 + 15


def test_poll_skips_git_status_while_process_is_still_running(tmp_path):
    executor = DummyLocalCLIExecutor()
    (tmp_path / "state.json").write_text(
        json.dumps(
            {
                "version": 1,
                "pid": 1234,
                "pgid": 1234,
                "started_at": "2026-04-06T00:00:00Z",
                "repo_path": "/fake/repo",
                "start_head": "abc123",
                "current_head": None,
                "exit_code": None,
                "arbitration_rc": None,
                "is_process_alive": True,
                "has_committed": False,
                "commit_detected_at": None,
                "is_worktree_dirty": False,
                "final_summary": None,
                "error_summary": None,
                "updated_at": "2026-04-06T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )

    with mock.patch("agpair.executors.local_cli._is_process_alive", return_value=True), \
         mock.patch("agpair.executors.local_cli._git_head", return_value="abc123"), \
         mock.patch("agpair.executors.local_cli._git_status_porcelain", side_effect=AssertionError("running poll must not call git status")):
        state = executor.poll("TASK-LOCAL-RUNNING", str(tmp_path))

    assert state is not None
    assert state.is_done is False


def test_is_process_alive_batches_child_status_checks():
    with mock.patch("agpair.executors.local_cli.os.killpg"), \
         mock.patch("agpair.executors.local_cli.subprocess.check_output", side_effect=[
             "Z+\n",  # ps -p: leader is zombie
             "4321\n5678\n6789\n",  # pgrep -g: leader + children
             "Z+\nS\nZ\n",  # ps -p combined child statuses
         ]) as check_output:
        assert _is_process_alive(4321) is True

    assert check_output.call_args_list[2].args[0] == ["ps", "-o", "stat=", "-p", "4321,5678,6789"]


def test_is_process_alive_detects_pid_recycling():
    """When expected_start_time doesn't match actual, PID was recycled."""
    with mock.patch("agpair.executors.local_cli.os.killpg"), \
         mock.patch("agpair.executors.local_cli._get_process_start_time", return_value=9999999.0):
        # expected_start_time=1000.0, actual=9999999.0 → mismatch → dead
        assert _is_process_alive(4321, expected_start_time=1000.0) is False


def test_is_process_alive_allows_matching_start_time():
    """When expected_start_time roughly matches, treat as same process."""
    with mock.patch("agpair.executors.local_cli.os.killpg"), \
         mock.patch("agpair.executors.local_cli._get_process_start_time", return_value=1000.5), \
         mock.patch("agpair.executors.local_cli.subprocess.check_output", return_value="S\n"):
        # expected_start_time=1000.0, actual=1000.5 → within tolerance → alive
        assert _is_process_alive(4321, expected_start_time=1000.0) is True


def test_poll_ignores_commit_from_another_task(tmp_path):
    """When another task committed to the same repo, this task should NOT claim it."""
    executor = DummyLocalCLIExecutor()
    (tmp_path / "state.json").write_text(
        json.dumps(
            {
                "version": 1,
                "pid": 1234,
                "pgid": 1234,
                "started_at": "2026-04-06T00:00:00Z",
                "repo_path": "/fake/repo",
                "start_head": "abc123",
                "current_head": None,
                "exit_code": None,
                "arbitration_rc": None,
                "is_process_alive": True,
                "has_committed": False,
                "commit_detected_at": None,
                "is_worktree_dirty": False,
                "final_summary": None,
                "error_summary": None,
                "updated_at": "2026-04-06T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )

    with mock.patch("agpair.executors.local_cli._is_process_alive", return_value=True), \
         mock.patch("agpair.executors.local_cli._git_head", return_value="def456"), \
         mock.patch("agpair.executors.local_cli._git_diff_stat", return_value=" 1 file changed, 1 insertion(+)"), \
         mock.patch("agpair.executors.local_cli._git_log_grep_task_id", return_value=False):
        state = executor.poll("TASK-A", str(tmp_path))

    assert state is not None
    assert state.is_done is False  # Still running, commit belongs to another task
    persisted = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert persisted["has_committed"] is False


def test_poll_uses_cached_receipt_on_second_poll_during_process_death(tmp_path):
    """When arbitration result is cached from a previous poll where process was dying,
    the second poll should reuse the cached receipt without re-running arbitration."""
    executor = DummyLocalCLIExecutor()
    cached_receipt = {
        "schema_version": "1",
        "task_id": "TASK-CACHE",
        "attempt_no": 1,
        "review_round": 0,
        "status": "COMMITTED",
        "summary": "Committed via cache test",
        "payload": {"exit_code": 0, "arbitration": "post_commit_hang"},
    }
    (tmp_path / "state.json").write_text(
        json.dumps(
            {
                "version": 1,
                "pid": 1234,
                "pgid": 1234,
                "started_at": "2026-04-06T00:00:00Z",
                "repo_path": "/fake/repo",
                "start_head": "abc123",
                "current_head": "def456",
                "exit_code": None,
                "arbitration_rc": None,
                "is_process_alive": False,  # Process died since last poll
                "has_committed": True,
                "commit_detected_at": "2026-04-06T00:00:30Z",
                "is_worktree_dirty": False,
                "final_summary": None,
                "error_summary": None,
                "updated_at": "2026-04-06T00:00:35Z",
                "cached_receipt": cached_receipt,
                "cached_is_done": True,
            }
        ),
        encoding="utf-8",
    )

    with mock.patch("agpair.executors.local_cli._is_process_alive", return_value=False), \
         mock.patch("agpair.executors.local_cli._git_head", return_value="def456"), \
         mock.patch.object(executor, "_arbitrate", side_effect=AssertionError("should use cache")):
        state = executor.poll("TASK-CACHE", str(tmp_path))

    assert state is not None
    assert state.is_done is True
    assert state.receipt["status"] == "COMMITTED"
    assert state.receipt["summary"] == "Committed via cache test"

    # Verify cache is cleared after successful completion
    persisted = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert "cached_receipt" not in persisted
    assert "cached_is_done" not in persisted
