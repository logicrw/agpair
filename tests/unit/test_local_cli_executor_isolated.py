from __future__ import annotations

import json
import pathlib
import subprocess
import sys
from unittest import mock

import pytest

from agpair.executors.local_cli import LocalCLIExecutor
from agpair.executors.local_cli import WorktreeProvisionError
from agpair.config import AppPaths
from agpair.models import ContinuationCapability
from agpair.storage.db import ensure_database
from agpair.storage.journal import JournalRepository
from agpair.storage.tasks import TaskRepository

FIXTURES = pathlib.Path(__file__).resolve().parents[1] / "fixtures" / "executor_outputs"


class DummyLocalCLIExecutor(LocalCLIExecutor):
    def __init__(self) -> None:
        super().__init__(
            bin_path="dummy-cli",
            backend_id="dummy_cli",
            build_cmd=self._build_dummy_cmd,
        )

    def _build_dummy_cmd(self, body: str, repo_path: str, temp_dir) -> list[str]:
        return [self.bin_path, "--repo", repo_path, body]

    @property
    def continuation_capability(self) -> ContinuationCapability:
        return ContinuationCapability.UNSUPPORTED


class NoopLocalCLIExecutor(LocalCLIExecutor):
    def __init__(self) -> None:
        super().__init__(
            bin_path=sys.executable,
            backend_id="noop_cli",
            build_cmd=self._build_noop_cmd,
        )

    def _build_noop_cmd(self, body: str, repo_path: str, temp_dir) -> list[str]:
        del body, repo_path, temp_dir
        return [self.bin_path, "-c", ""]

    @property
    def continuation_capability(self) -> ContinuationCapability:
        return ContinuationCapability.UNSUPPORTED


def _init_repo(repo_path: pathlib.Path) -> None:
    repo_path.mkdir()
    subprocess.run(["git", "init"], cwd=repo_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_path, check=True)
    subprocess.run(["git", "config", "user.name", "AGPair Test"], cwd=repo_path, check=True)
    (repo_path / "tracked.txt").write_text("original\n", encoding="utf-8")
    (repo_path / "staged.txt").write_text("original staged\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt", "staged.txt"], cwd=repo_path, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=repo_path, check=True, capture_output=True)


def _make_worktree(repo_path: pathlib.Path, worktree_path: pathlib.Path) -> None:
    subprocess.run(["git", "worktree", "add", "--detach", str(worktree_path)], cwd=repo_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=worktree_path, check=True)
    subprocess.run(["git", "config", "user.name", "AGPair Test"], cwd=worktree_path, check=True)


def _git_output(repo_path: pathlib.Path, args: list[str]) -> str:
    return subprocess.check_output(["git", "-C", str(repo_path), *args], text=True)


def test_dispatch_creates_default_isolated_worktree_and_records_execution_path(tmp_path) -> None:
    executor = DummyLocalCLIExecutor()
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    expected_worktree = repo_path / ".agpair" / "worktrees" / "TASK-ISO-1"

    with mock.patch("agpair.executors.local_cli._git_toplevel", return_value=repo_path.resolve()), \
         mock.patch("agpair.executors.local_cli._git_head", return_value="fake-head"), \
         mock.patch("agpair.executors.local_cli.subprocess.Popen") as mock_popen, \
         mock.patch("agpair.executors.local_cli.subprocess.run") as mock_run, \
         mock.patch("agpair.executors.local_cli.subprocess.check_output") as mock_check_output:
        process = mock.Mock()
        process.pid = 12345
        mock_popen.return_value = process
        mock_run.return_value = mock.Mock(returncode=0)
        mock_check_output.return_value = f"worktree {repo_path.resolve()}\nworktree {expected_worktree.resolve()}\n"

        dispatch = executor.dispatch(
            task_id="TASK-ISO-1",
            body="Goal: test\nScope: test\nRequired changes: test\nExit criteria: test",
            repo_path=str(repo_path),
            isolated_worktree=True,
        )

    mock_run.assert_any_call(
        ["git", "-C", str(repo_path.resolve()), "worktree", "add", "--detach", "--", str(expected_worktree.resolve())],
        check=True,
        capture_output=True,
        text=True,
    )

    assert dispatch.execution_repo_path == str(expected_worktree.resolve())

    state = json.loads((pathlib.Path(dispatch.session_id) / "state.json").read_text(encoding="utf-8"))
    assert state["repo_path"] == str(expected_worktree.resolve())

    cmd_json = json.loads((pathlib.Path(dispatch.session_id) / "cmd.json").read_text(encoding="utf-8"))
    assert cmd_json[cmd_json.index("--repo") + 1] == str(expected_worktree.resolve())
    prompt = cmd_json[-1]
    root_lines = [
        line for line in prompt.splitlines()
        if line.startswith("Execution repository root:")
    ]
    assert root_lines == [f"Execution repository root: {expected_worktree.resolve()}"]

    _, kwargs = mock_popen.call_args
    assert kwargs["cwd"] == str(expected_worktree.resolve())
    assert kwargs["env"]["AGPAIR_INTERNAL_ROLE"] == "executor"
    assert kwargs["env"]["AGPAIR_SUPPRESS_CLIENT_HOOKS"] == "1"


def test_dispatch_reuses_existing_isolated_worktree(tmp_path) -> None:
    executor = DummyLocalCLIExecutor()
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    worktree_dir = repo_path / ".agpair" / "worktrees" / "TASK-ISO-2"
    worktree_dir.mkdir(parents=True)

    with mock.patch("agpair.executors.local_cli._git_toplevel", return_value=repo_path.resolve()), \
         mock.patch("agpair.executors.local_cli._git_head", return_value="fake-head"), \
         mock.patch("agpair.executors.local_cli.subprocess.Popen") as mock_popen, \
         mock.patch("agpair.executors.local_cli.subprocess.run") as mock_run, \
         mock.patch("agpair.executors.local_cli.subprocess.check_output") as mock_check_output:
        process = mock.Mock()
        process.pid = 12345
        mock_popen.return_value = process
        mock_run.return_value = mock.Mock(returncode=0)
        mock_check_output.side_effect = [
            str(worktree_dir.resolve()),
            f"worktree {repo_path.resolve()}\nworktree {worktree_dir.resolve()}\n",
        ]

        dispatch = executor.dispatch(
            task_id="TASK-ISO-2",
            body="Goal: test\nScope: test\nRequired changes: test\nExit criteria: test",
            repo_path=str(repo_path),
            isolated_worktree=True,
        )

    assert not any(
        call.args
        and isinstance(call.args[0], list)
        and call.args[0][:4] == ["git", "-C", str(repo_path.resolve()), "worktree"]
        for call in mock_run.call_args_list
    )
    assert mock_check_output.call_args_list[0].args == (
        ["git", "rev-parse", "--show-toplevel"],
    )
    assert mock_check_output.call_args_list[0].kwargs["cwd"] == str(worktree_dir.resolve())
    assert dispatch.execution_repo_path == str(worktree_dir.resolve())


def test_dispatch_resolves_relative_worktree_boundary_against_repo_path(tmp_path) -> None:
    executor = DummyLocalCLIExecutor()
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    relative_boundary = ".agpair/custom-worktree"
    expected_worktree = (repo_path / relative_boundary).resolve()

    with mock.patch("agpair.executors.local_cli._git_toplevel", return_value=repo_path.resolve()), \
         mock.patch("agpair.executors.local_cli._git_head", return_value="fake-head"), \
         mock.patch("agpair.executors.local_cli.subprocess.Popen") as mock_popen, \
         mock.patch("agpair.executors.local_cli.subprocess.run") as mock_run, \
         mock.patch("agpair.executors.local_cli.subprocess.check_output") as mock_check_output:
        process = mock.Mock()
        process.pid = 12345
        mock_popen.return_value = process
        mock_run.return_value = mock.Mock(returncode=0)
        mock_check_output.return_value = f"worktree {repo_path.resolve()}\nworktree {expected_worktree}\n"

        dispatch = executor.dispatch(
            task_id="TASK-ISO-3",
            body="Goal: test\nScope: test\nRequired changes: test\nExit criteria: test",
            repo_path=str(repo_path),
            isolated_worktree=True,
            worktree_boundary=relative_boundary,
        )

    mock_run.assert_any_call(
        ["git", "-C", str(repo_path.resolve()), "worktree", "add", "--detach", "--", str(expected_worktree)],
        check=True,
        capture_output=True,
        text=True,
    )
    assert dispatch.execution_repo_path == str(expected_worktree)


def test_dispatch_rejects_base_repo_as_isolated_worktree(tmp_path) -> None:
    executor = DummyLocalCLIExecutor()
    repo_path = tmp_path / "repo"
    repo_path.mkdir()

    with mock.patch("agpair.executors.local_cli._git_toplevel", return_value=repo_path.resolve()):
        with pytest.raises(WorktreeProvisionError, match="base repository root"):
            executor.dispatch(
                task_id="TASK-ISO-BASE",
                body="Goal: test\nScope: test\nRequired changes: test\nExit criteria: test",
                repo_path=str(repo_path),
                isolated_worktree=True,
                worktree_boundary=str(repo_path),
            )


def test_dispatch_tracked_dirty_snapshot_applies_to_isolated_worktree(tmp_path) -> None:
    executor = NoopLocalCLIExecutor()
    repo_path = tmp_path / "repo"
    _init_repo(repo_path)
    (repo_path / "tracked.txt").write_text("dirty unstaged\n", encoding="utf-8")
    (repo_path / "staged.txt").write_text("dirty staged\n", encoding="utf-8")
    (repo_path / "untracked-secret.txt").write_text("do not copy\n", encoding="utf-8")
    subprocess.run(["git", "add", "staged.txt"], cwd=repo_path, check=True)

    dispatch = executor.dispatch(
        task_id="TASK-DIRTY-TRACKED",
        body="Goal: test\nScope: test\nRequired changes: test\nExit criteria: test",
        repo_path=str(repo_path),
        isolated_worktree=True,
        dirty_snapshot_mode="tracked",
    )

    worktree = pathlib.Path(dispatch.execution_repo_path)
    assert (worktree / "tracked.txt").read_text(encoding="utf-8") == "dirty unstaged\n"
    assert (worktree / "staged.txt").read_text(encoding="utf-8") == "dirty staged\n"
    assert not (worktree / "untracked-secret.txt").exists()

    state = json.loads((pathlib.Path(dispatch.session_id) / "state.json").read_text(encoding="utf-8"))
    assert state["dirty_snapshot_mode"] == "tracked"
    assert state["dirty_snapshot_applied"] is True
    assert state["dirty_snapshot_json"]["has_staged_diff"] is True
    assert state["dirty_snapshot_json"]["has_unstaged_diff"] is True
    assert state["dirty_snapshot_json"]["untracked_files"] == ["untracked-secret.txt"]
    assert sorted(state["start_dirty_files"]) == ["staged.txt", "tracked.txt"]
    assert state["worker_base_created"] is True
    assert state["worker_diff_base_reason"] == "dirty_snapshot_baseline"
    assert state["worker_base_head"] == state["start_head"]
    assert state["worker_base_head"] != state["original_start_head"]
    assert subprocess.check_output(
        ["git", "status", "--porcelain"], cwd=worktree, text=True
    ).strip() == ""


def test_dispatch_dirty_snapshot_off_leaves_isolated_worktree_clean(tmp_path) -> None:
    executor = NoopLocalCLIExecutor()
    repo_path = tmp_path / "repo"
    _init_repo(repo_path)
    (repo_path / "tracked.txt").write_text("dirty unstaged\n", encoding="utf-8")

    dispatch = executor.dispatch(
        task_id="TASK-DIRTY-OFF",
        body="Goal: test\nScope: test\nRequired changes: test\nExit criteria: test",
        repo_path=str(repo_path),
        isolated_worktree=True,
        dirty_snapshot_mode="off",
    )

    worktree = pathlib.Path(dispatch.execution_repo_path)
    assert (worktree / "tracked.txt").read_text(encoding="utf-8") == "original\n"
    state = json.loads((pathlib.Path(dispatch.session_id) / "state.json").read_text(encoding="utf-8"))
    assert state["worker_base_created"] is False
    assert state["worker_diff_base_reason"] == "start_head"
    assert state["worker_base_head"] == state["start_head"]
    state = json.loads((pathlib.Path(dispatch.session_id) / "state.json").read_text(encoding="utf-8"))
    assert state["dirty_snapshot_mode"] == "off"
    assert state["dirty_snapshot_applied"] is False
    assert state["start_dirty_files"] == []


def test_arbitrate_salvages_readonly_report_after_nonzero_exit(tmp_path) -> None:
    executor = NoopLocalCLIExecutor()
    temp_dir = tmp_path / "session"
    temp_dir.mkdir()
    (temp_dir / "stdout.log").write_text(
        (FIXTURES / "report_after_nonzero_exit_stdout.txt").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (temp_dir / "stderr.log").write_text("process ended after report\n", encoding="utf-8")
    state = {
        "exit_code": 1,
        "is_process_alive": False,
        "has_committed": False,
        "authorization_profile": "local_readonly",
    }

    is_done, receipt = executor._arbitrate(state, "TASK-REPORT-SALVAGE", 1, temp_dir)

    assert is_done is True
    assert receipt is not None
    assert receipt["status"] == "EVIDENCE_PACK"
    assert receipt["payload"]["arbitration"] == "report_salvage_after_nonzero_exit"
    assert "Findings:" in receipt["payload"]["report"]


def test_arbitrate_blocks_readonly_thought_only_after_nonzero_exit(tmp_path) -> None:
    executor = NoopLocalCLIExecutor()
    temp_dir = tmp_path / "session"
    temp_dir.mkdir()
    (temp_dir / "stdout.log").write_text(
        (FIXTURES / "grok_max_turns_thought_only_stdout.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (temp_dir / "stderr.log").write_text("cancelled after max turns\n", encoding="utf-8")
    state = {
        "exit_code": 1,
        "is_process_alive": False,
        "has_committed": False,
        "authorization_profile": "local_readonly",
    }

    is_done, receipt = executor._arbitrate(state, "TASK-THOUGHT-ONLY", 1, temp_dir)

    assert is_done is True
    assert receipt is not None
    assert receipt["status"] == "BLOCKED"
    assert receipt["payload"]["blocker_type"] == "executor_turn_budget_exhausted"
    assert receipt["payload"]["recoverable"] is True
    assert receipt["payload"]["recommended_next_action"] == "retry_same_executor_with_more_budget_or_switch_executor"


def test_finalize_infers_isolated_worktree_changed_files_and_apply_check(tmp_path) -> None:
    from agpair.task_terminal import finalize_executor_receipt

    repo_path = tmp_path / "repo"
    worktree_path = tmp_path / "worker"
    _init_repo(repo_path)
    _make_worktree(repo_path, worktree_path)
    base = _git_output(worktree_path, ["rev-parse", "HEAD"]).strip()
    (worktree_path / "b.txt").write_text("worker change\n", encoding="utf-8")
    session_dir = tmp_path / "session"
    session_dir.mkdir()
    (session_dir / "stdout.log").write_text("", encoding="utf-8")
    (session_dir / "stderr.log").write_text("", encoding="utf-8")
    (session_dir / "state.json").write_text(
        json.dumps(
            {
                "repo_path": str(worktree_path),
                "worker_base_head": base,
                "start_head": base,
                "start_dirty_files": [],
            }
        ),
        encoding="utf-8",
    )
    paths = AppPaths.from_root(tmp_path / ".agpair")
    ensure_database(paths.db_path)
    tasks = TaskRepository(paths.db_path)
    tasks.create_task(
        task_id="TASK-INFER-WORKTREE",
        repo_path=str(repo_path),
        isolated_worktree=True,
        completion_policy="evidence",
        authorization_profile="local_mutating",
    )
    tasks.mark_acked(task_id="TASK-INFER-WORKTREE", session_id=str(session_dir))
    tasks.set_execution_repo_path(task_id="TASK-INFER-WORKTREE", execution_repo_path=str(worktree_path))
    task = tasks.get_task("TASK-INFER-WORKTREE")
    assert task is not None

    decision = finalize_executor_receipt(
        state_root=paths.root,
        tasks=tasks,
        journal=JournalRepository(paths.db_path),
        task=task,
        raw_receipt={
            "schema_version": "1",
            "task_id": "TASK-INFER-WORKTREE",
            "attempt_no": 1,
            "review_round": 0,
            "status": "EVIDENCE_PACK",
            "summary": "implemented without declared changed_files",
            "payload": {
                "raw_log_path": str(session_dir / "stdout.log"),
                "stderr_log_path": str(session_dir / "stderr.log"),
                "scope_violations": [],
                "exit_code": 0,
            },
        },
        source="test",
        message_id="msg-infer-worktree",
    )

    assert decision.ok is True
    task_after = tasks.get_task("TASK-INFER-WORKTREE")
    assert task_after is not None
    receipt = json.loads(task_after.terminal_receipt_json or "{}")
    assert receipt["payload"]["changed_files"] == ["b.txt"]
    assert receipt["payload"]["worktree_diff"]["apply_check_ok"] is True
    attempt = tasks.current_attempt("TASK-INFER-WORKTREE")
    assert attempt is not None
    adoption = json.loads(attempt.adoption_evidence_json)
    assert adoption["agent_result"]["state"] == "needs_review"
    assert adoption["agent_result"]["controller_action"] == "review_then_apply"
