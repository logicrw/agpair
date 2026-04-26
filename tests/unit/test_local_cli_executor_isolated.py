from __future__ import annotations

import json
import pathlib
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

    wrapper = pathlib.Path(dispatch.session_id) / "wrapper.sh"
    content = wrapper.read_text(encoding="utf-8")
    assert f"--repo {shlex_quote(str(expected_worktree.resolve()))}" in content

    _, kwargs = mock_popen.call_args
    assert kwargs["cwd"] == str(expected_worktree.resolve())


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

    assert mock_run.call_count == 0
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


def shlex_quote(text: str) -> str:
    import shlex

    return shlex.quote(text)
