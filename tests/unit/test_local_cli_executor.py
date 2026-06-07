import json
from pathlib import Path
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


class EnvDummyLocalCLIExecutor(LocalCLIExecutor):
    def __init__(self) -> None:
        super().__init__(
            bin_path="dummy-cli",
            backend_id="dummy_cli",
            build_cmd=self._build_dummy_cmd,
            build_env=self._build_dummy_env,
        )

    def _build_dummy_cmd(self, body: str, repo_path: str, temp_dir) -> list[str]:
        return [self.bin_path, body]

    def _build_dummy_env(self, body: str, repo_path: str, temp_dir) -> dict[str, str]:
        return {"AGPAIR_TEST_SECRET": "secret-value"}

    @property
    def continuation_capability(self) -> ContinuationCapability:
        return ContinuationCapability.UNSUPPORTED


def test_dispatch_injects_executor_env_without_writing_secret_to_cmd_json(tmp_path):
    executor = EnvDummyLocalCLIExecutor()

    with mock.patch("agpair.executors.local_cli._git_head", return_value="fake-head"), \
         mock.patch("agpair.executors.local_cli.subprocess.Popen") as mock_popen:
        process = mock.Mock()
        process.pid = 12345
        mock_popen.return_value = process

        dispatch = executor.dispatch(
            task_id="TASK-ENV-1",
            body="Goal: test\nScope: test\nRequired changes: test\nExit criteria: test",
            repo_path=str(tmp_path),
        )

    cmd = json.loads((Path(dispatch.session_id) / "cmd.json").read_text(encoding="utf-8"))
    assert "secret-value" not in json.dumps(cmd)
    assert mock_popen.call_args.kwargs["env"]["AGPAIR_TEST_SECRET"] == "secret-value"


def test_dispatch_injects_task_id_commit_requirement(tmp_path):
    executor = DummyLocalCLIExecutor()

    with mock.patch("agpair.executors.local_cli._git_head", return_value="fake-head"), \
         mock.patch("agpair.executors.local_cli.subprocess.Popen") as mock_popen:
        process = mock.Mock()
        process.pid = 12345
        mock_popen.return_value = process

        dispatch = executor.dispatch(
            task_id="TASK-HINT-1",
            body="Goal: test\nScope: test\nRequired changes: test\nExit criteria: test",
            repo_path=str(tmp_path),
        )

    wrapper = Path(dispatch.session_id) / "wrapper.sh"
    wrapper_content = wrapper.read_text(encoding="utf-8")
    cmd = json.loads((Path(dispatch.session_id) / "cmd.json").read_text(encoding="utf-8"))
    prompt = cmd[1]
    assert "commit message" not in wrapper_content
    assert "TASK-HINT-1" in prompt
    assert "commit message" in prompt
    assert "must include" in prompt


def test_dispatch_injects_authorization_and_structured_receipt_contract(tmp_path):
    executor = DummyLocalCLIExecutor()

    with mock.patch("agpair.executors.local_cli._git_head", return_value="fake-head"), \
         mock.patch("agpair.executors.local_cli.subprocess.Popen") as mock_popen:
        process = mock.Mock()
        process.pid = 12345
        mock_popen.return_value = process

        dispatch = executor.dispatch(
            task_id="TASK-AUTH-CONTRACT",
            body="Goal: test\nScope: test\nRequired changes: test\nExit criteria: test",
            repo_path=str(tmp_path),
            authorization_profile="local_readonly",
            authorization_summary="Allowed actions: inspect files. Denied actions: edit files.",
        )

    wrapper = Path(dispatch.session_id) / "wrapper.sh"
    wrapper_content = wrapper.read_text(encoding="utf-8")
    cmd = json.loads((Path(dispatch.session_id) / "cmd.json").read_text(encoding="utf-8"))
    prompt = cmd[1]
    assert "Authorization profile: local_readonly" not in wrapper_content
    assert "Authorization profile: local_readonly" in prompt
    assert "Allowed actions: inspect files." in prompt
    assert "Denied actions: edit files." in prompt
    assert "Structured terminal receipt JSON requirements" in prompt
    assert "ready_for_review" in prompt
    assert "Print the requested report or conclusion directly to stdout" in prompt
    assert "final output line must be one single-line JSON terminal receipt object" in prompt
    assert "payload.report" in prompt


def test_structured_receipt_from_logs_parses_pretty_wrapped_text(tmp_path):
    executor = DummyLocalCLIExecutor()
    receipt = {
        "schema_version": "1",
        "task_id": "TASK-WRAPPED-LOG",
        "attempt_no": 1,
        "review_round": 0,
        "status": "EVIDENCE_PACK",
        "summary": "Smoke complete",
        "payload": {
            "claimed_state": "ready_for_review",
            "changed_files": ["tests/fixtures/external_executor_smoke/grok-cli.txt"],
            "validation_not_run": "smoke",
            "scope_violations": [],
            "raw_log_path": "stdout.log",
            "receipt_path": "receipt.json",
        },
    }
    wrapper = {
        "text": json.dumps(receipt, sort_keys=True),
        "stopReason": "EndTurn",
    }
    (tmp_path / "stdout.log").write_text(json.dumps(wrapper, indent=2), encoding="utf-8")

    parsed = executor._structured_receipt_from_logs(tmp_path, "TASK-WRAPPED-LOG")

    assert parsed is not None
    assert parsed.status == "EVIDENCE_PACK"
    assert parsed.payload["changed_files"] == ["tests/fixtures/external_executor_smoke/grok-cli.txt"]


def test_structured_receipt_from_logs_parses_claude_result_envelope(tmp_path):
    executor = DummyLocalCLIExecutor()
    receipt = {
        "schema_version": "1.0",
        "task_id": "TASK-CLAUDE-RESULT",
        "attempt_no": 1,
        "review_round": 1,
        "status": "success",
        "summary": "Claude worker completed through CC Switch",
        "payload": {
            "claimed_state": "ready_for_review",
            "changed_files": [],
            "validation_not_run": "read-only smoke",
            "scope_violations": [],
            "report": "Smoke check passed",
            "raw_log_path": "stdout.log",
            "receipt_path": "receipt.json",
        },
    }
    envelope = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": "中文结论：Smoke check passed.\n\n" + json.dumps(receipt, ensure_ascii=False),
    }
    (tmp_path / "stdout.log").write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")

    parsed = executor._structured_receipt_from_logs(tmp_path, "TASK-CLAUDE-RESULT")

    assert parsed is not None
    assert parsed.status == "EVIDENCE_PACK"
    assert parsed.payload["report"] == "Smoke check passed"


def test_poll_persists_final_summary_to_state_json(tmp_path):
    executor = DummyLocalCLIExecutor()
    (tmp_path / "rc.txt").write_text("0", encoding="utf-8")
    (tmp_path / "stdout.log").write_text("line1\nline2\n", encoding="utf-8")
    (tmp_path / "last_msg.txt").write_text("All done.", encoding="utf-8")

    state = executor.poll("TASK-LOCAL-OK", str(tmp_path))

    assert state is not None
    assert state.is_done is True
    assert state.receipt["status"] == "EVIDENCE_PACK"
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


def test_poll_classifies_executor_quota_exhaustion(tmp_path):
    executor = DummyLocalCLIExecutor()
    (tmp_path / "rc.txt").write_text("1", encoding="utf-8")
    (tmp_path / "stdout.log").write_text(
        "You've hit your usage limit. Visit settings to purchase more credits.\n",
        encoding="utf-8",
    )

    state = executor.poll("TASK-LOCAL-QUOTA", str(tmp_path))

    assert state is not None
    assert state.is_done is True
    assert state.receipt["status"] == "BLOCKED"
    assert state.receipt["payload"]["blocker_type"] == "executor_quota_exhausted"
    assert state.receipt["payload"]["recoverable"] is True
    assert state.receipt["payload"]["recommended_next_action"] == "wait_or_switch_executor"


def test_poll_classifies_executor_auth_failure(tmp_path):
    executor = DummyLocalCLIExecutor()
    (tmp_path / "rc.txt").write_text("1", encoding="utf-8")
    (tmp_path / "stderr.log").write_text("TokenRefreshFailed invalid_grant\n", encoding="utf-8")

    state = executor.poll("TASK-LOCAL-AUTH", str(tmp_path))

    assert state is not None
    assert state.is_done is True
    assert state.receipt["status"] == "BLOCKED"
    assert state.receipt["payload"]["blocker_type"] == "executor_auth_failed"
    assert state.receipt["payload"]["recoverable"] is False
    assert state.receipt["payload"]["recommended_next_action"] == "repair_executor_auth"


def test_poll_report_only_process_crash_reports_missing_report_not_commit(tmp_path):
    executor = DummyLocalCLIExecutor()
    (tmp_path / "state.json").write_text(
        json.dumps(
            {
                "version": 1,
                "pid": None,
                "repo_path": None,
                "exit_code": None,
                "is_process_alive": False,
                "has_committed": False,
                "authorization_profile": "local_readonly",
                "updated_at": "2026-04-06T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )

    state = executor.poll("TASK-LOCAL-REPORT-CRASH", str(tmp_path))

    assert state is not None
    assert state.is_done is True
    assert state.receipt["status"] == "BLOCKED"
    assert "commit" not in state.receipt["summary"].lower()
    assert state.receipt["payload"]["blocker_type"] == "report_output_missing"
    assert state.receipt["payload"]["authorization_profile"] == "local_readonly"


def test_poll_mutating_process_crash_mentions_commit_evidence(tmp_path):
    executor = DummyLocalCLIExecutor()
    (tmp_path / "state.json").write_text(
        json.dumps(
            {
                "version": 1,
                "pid": None,
                "repo_path": None,
                "exit_code": None,
                "is_process_alive": False,
                "has_committed": False,
                "authorization_profile": "local_mutating",
                "updated_at": "2026-04-06T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )

    state = executor.poll("TASK-LOCAL-MUTATING-CRASH", str(tmp_path))

    assert state is not None
    assert state.is_done is True
    assert state.receipt["status"] == "BLOCKED"
    assert "commit evidence" in state.receipt["summary"]
    assert state.receipt["payload"]["blocker_type"] == "terminal_receipt_missing"
    assert state.receipt["payload"]["authorization_profile"] == "local_mutating"


def test_poll_returns_evidence_pack_for_success_exit_without_commit_when_commit_evidence_available(tmp_path):
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

    with mock.patch("agpair.executors.local_cli._is_process_alive", return_value=False), \
         mock.patch("agpair.executors.local_cli._git_head", return_value="abc123"), \
         mock.patch("agpair.executors.local_cli._git_status_porcelain", return_value=""), \
         mock.patch.object(executor, "_clean_git_locks"):
        state = executor.poll("TASK-LOCAL-NOCOMMIT", str(tmp_path))

    assert state is not None
    assert state.is_done is True
    assert state.receipt["status"] == "EVIDENCE_PACK"
    assert state.receipt["summary"] == "No changes needed."
    assert state.receipt["payload"]["exit_code"] == 0
    assert state.receipt["payload"]["returncode"] == 0

    persisted = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert persisted["final_summary"] == "No changes needed."
    assert persisted["error_summary"] is None


def test_poll_returns_evidence_pack_for_success_exit_without_commit_in_repo_without_baseline_head(tmp_path):
    executor = DummyLocalCLIExecutor()
    (tmp_path / "state.json").write_text(
        json.dumps(
            {
                "version": 1,
                "pid": 1234,
                "pgid": 1234,
                "started_at": "2026-04-06T00:00:00Z",
                "repo_path": "/fake/repo",
                "start_head": None,
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

    with mock.patch("agpair.executors.local_cli._is_process_alive", return_value=False), \
         mock.patch("agpair.executors.local_cli._git_head", return_value=None), \
         mock.patch("agpair.executors.local_cli._git_status_porcelain", return_value=""):
        state = executor.poll("TASK-LOCAL-EMPTY-REPO", str(tmp_path))

    assert state is not None
    assert state.is_done is True
    assert state.receipt["status"] == "EVIDENCE_PACK"
    assert state.receipt["summary"] == "No changes needed."


def test_poll_accepts_first_commit_in_repo_without_baseline_head_when_task_id_matches(tmp_path):
    executor = DummyLocalCLIExecutor()
    (tmp_path / "state.json").write_text(
        json.dumps(
            {
                "version": 1,
                "pid": 1234,
                "pgid": 1234,
                "started_at": "2026-04-06T00:00:00Z",
                "repo_path": "/fake/repo",
                "start_head": None,
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
    (tmp_path / "last_msg.txt").write_text("Committed first change.", encoding="utf-8")

    with mock.patch("agpair.executors.local_cli._is_process_alive", return_value=False), \
         mock.patch("agpair.executors.local_cli._git_head", return_value="def456"), \
         mock.patch("agpair.executors.local_cli._git_log_grep_task_id", return_value=True):
        state = executor.poll("TASK-FIRST-COMMIT", str(tmp_path))

    assert state is not None
    assert state.is_done is True
    assert state.receipt["status"] == "COMMITTED"
    assert state.receipt["summary"] == "Committed first change."

    persisted = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert persisted["has_committed"] is True
    assert persisted["current_head"] == "def456"


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


def test_git_log_grep_task_id_handles_multiline_commit_body():
    """Commit messages with blank lines (conventional commits) must not break parsing."""
    from agpair.executors.local_cli import _git_log_grep_task_id

    # Simulate git log output with multi-paragraph body containing blank lines
    multi_para_output = (
        "abc123\x00feat: implement feature [TASK-XYZ]\n\n"
        "This is the body paragraph.\n\n"
        "Signed-off-by: someone\x01"
    )
    with mock.patch("agpair.executors.local_cli.subprocess.check_output", return_value=multi_para_output):
        assert _git_log_grep_task_id("/repo", "start", "end", "TASK-XYZ") is True


def test_git_log_grep_task_id_rejects_wrong_task_in_multiline_body():
    """Even with multi-paragraph body, wrong task_id should not match."""
    from agpair.executors.local_cli import _git_log_grep_task_id

    multi_para_output = (
        "abc123\x00feat: implement feature [TASK-OTHER]\n\n"
        "This body mentions nothing else.\x01"
    )
    with mock.patch("agpair.executors.local_cli.subprocess.check_output", return_value=multi_para_output):
        assert _git_log_grep_task_id("/repo", "start", "end", "TASK-XYZ") is False


def test_ensure_process_dead_forces_dead_after_sigkill_timeout(tmp_path):
    """After SIGKILL + 2s, treat zombie processes as dead to prevent infinite loop."""
    executor = DummyLocalCLIExecutor()
    state = {
        "pid": 1234,
        "pgid": 1234,
        "termination_signal": "SIGKILL",
        "termination_requested_at": "2026-04-06T00:00:00Z",
    }

    with mock.patch("agpair.executors.local_cli._is_process_alive", return_value=True), \
         mock.patch("agpair.executors.local_cli._seconds_since", return_value=3), \
         mock.patch("agpair.executors.local_cli._reap_child_process") as reap:
        alive_after, arbitration_rc = executor._ensure_process_dead(state, tmp_path)

    assert alive_after is False  # Force-treated as dead
    assert arbitration_rc == 128 + 9  # 128 + SIGKILL
    reap.assert_called_once_with(1234)


def test_is_process_alive_treats_permission_error_as_alive():
    """PermissionError means process exists but we can't signal — should be treated as alive."""
    with mock.patch("agpair.executors.local_cli.os.killpg", side_effect=PermissionError("Operation not permitted")):
        assert _is_process_alive(4321) is True
