from __future__ import annotations

import json
import pathlib
import subprocess
import sys
from unittest import mock

import pytest

from agpair.executors.local_cli import LocalCLIExecutor
from agpair.executors.local_cli import WorktreeProvisionError
from agpair.models import ContinuationCapability


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
    assert state["dirty_snapshot_mode"] == "off"
    assert state["dirty_snapshot_applied"] is False
    assert state["start_dirty_files"] == []
