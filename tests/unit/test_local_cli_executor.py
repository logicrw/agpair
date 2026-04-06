import json
from unittest import mock

from agpair.executors.local_cli import LocalCLIExecutor
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
         mock.patch("agpair.executors.local_cli._git_status_porcelain", return_value=""), \
         mock.patch("agpair.executors.local_cli._seconds_since", return_value=31), \
         mock.patch.object(executor, "_ensure_process_dead", return_value=128 + 15) as ensure_dead, \
         mock.patch.object(executor, "_clean_git_locks") as clean_locks:
        state = executor.poll("TASK-LOCAL-HANG", str(tmp_path))

    assert state is not None
    assert state.is_done is True
    assert state.receipt["status"] == "COMMITTED"
    assert state.receipt["payload"]["arbitration"] == "post_commit_hang"
    ensure_dead.assert_called_once()
    clean_locks.assert_called_once_with("/fake/repo")

    persisted = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert persisted["has_committed"] is True
    assert persisted["current_head"] == "def456"
    assert persisted["is_process_alive"] is False
    assert persisted["arbitration_rc"] == 128 + 15
