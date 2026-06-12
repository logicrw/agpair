from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import subprocess

from agpair.models import TaskRecord
from agpair.worktree_adoption import (
    apply_to_controller_repo,
    build_worktree_diff,
    check_apply_to_controller_repo,
)


def _run(repo: Path, args: list[str]) -> str:
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True)


def _init_repo(repo: Path) -> None:
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "AGPair Test"], cwd=repo, check=True)
    (repo / "a.txt").write_text("base\n", encoding="utf-8")
    (repo / "b.txt").write_text("base\n", encoding="utf-8")
    (repo / "target.py").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=repo, check=True, capture_output=True)


def _make_task(*, repo: Path, worktree: Path, task_id: str = "TASK-WT") -> TaskRecord:
    now = datetime(2026, 6, 11, tzinfo=UTC).isoformat()
    return TaskRecord(
        task_id=task_id,
        repo_path=str(repo),
        execution_repo_path=str(worktree),
        phase="ready_for_review",
        antigravity_session_id=None,
        attempt_no=1,
        retry_count=0,
        last_receipt_id=None,
        stuck_reason=None,
        retry_recommended=False,
        last_activity_at=now,
        created_at=now,
        updated_at=now,
        isolated_worktree=True,
        executor_session_id=str(worktree.parent / "session"),
    )


def _make_worktree(repo: Path, worktree: Path) -> None:
    subprocess.run(["git", "worktree", "add", "--detach", str(worktree)], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=worktree, check=True)
    subprocess.run(["git", "config", "user.name", "AGPair Test"], cwd=worktree, check=True)


def _baseline_dirty_snapshot(worktree: Path, task_id: str = "TASK-WT") -> str:
    (worktree / "a.txt").write_text("controller dirty\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=worktree, check=True)
    subprocess.run(
        ["git", "commit", "-m", f"AGPair baseline snapshot {task_id}"],
        cwd=worktree,
        check=True,
        capture_output=True,
    )
    return _run(worktree, ["rev-parse", "HEAD"]).strip()


def test_worker_diff_excludes_controller_dirty_snapshot(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    worktree = tmp_path / "worker"
    _init_repo(repo)
    (repo / "a.txt").write_text("controller dirty\n", encoding="utf-8")
    _make_worktree(repo, worktree)
    baseline = _baseline_dirty_snapshot(worktree)
    (worktree / "b.txt").write_text("worker change\n", encoding="utf-8")
    task = _make_task(repo=repo, worktree=worktree)

    diff = build_worktree_diff(
        task=task,
        session_state={"repo_path": str(worktree), "worker_base_head": baseline},
    )

    assert diff.changed_files == ("b.txt",)
    assert "b.txt" in diff.patch
    assert "worker change" in diff.patch
    assert "controller dirty" not in diff.patch
    assert "a.txt" not in diff.stat


def test_apply_check_refuses_conflict_without_modifying_controller_repo(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    worktree = tmp_path / "worker"
    _init_repo(repo)
    _make_worktree(repo, worktree)
    base = _run(worktree, ["rev-parse", "HEAD"]).strip()
    (worktree / "target.py").write_text("worker change\n", encoding="utf-8")
    (repo / "target.py").write_text("controller change\n", encoding="utf-8")
    task = _make_task(repo=repo, worktree=worktree)
    diff = build_worktree_diff(task=task, session_state={"repo_path": str(worktree), "worker_base_head": base})

    result = check_apply_to_controller_repo(repo_path=str(repo), patch=diff.patch)

    assert result.ok is False
    assert result.reason == "apply_conflict"
    assert (repo / "target.py").read_text(encoding="utf-8") == "controller change\n"


def test_apply_applies_worker_patch_to_controller_repo(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    worktree = tmp_path / "worker"
    _init_repo(repo)
    _make_worktree(repo, worktree)
    base = _run(worktree, ["rev-parse", "HEAD"]).strip()
    (worktree / "b.txt").write_text("worker change\n", encoding="utf-8")
    task = _make_task(repo=repo, worktree=worktree)
    diff = build_worktree_diff(task=task, session_state={"repo_path": str(worktree), "worker_base_head": base})

    result = apply_to_controller_repo(repo_path=str(repo), patch=diff.patch)

    assert result.ok is True
    assert (repo / "b.txt").read_text(encoding="utf-8") == "worker change\n"


def test_worker_diff_includes_untracked_new_files(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    worktree = tmp_path / "worker"
    _init_repo(repo)
    _make_worktree(repo, worktree)
    base = _run(worktree, ["rev-parse", "HEAD"]).strip()
    (worktree / "new_dir").mkdir()
    (worktree / "new_dir" / "created.txt").write_text("worker new file\n", encoding="utf-8")
    task = _make_task(repo=repo, worktree=worktree)

    diff = build_worktree_diff(task=task, session_state={"repo_path": str(worktree), "worker_base_head": base})

    assert diff.changed_files == ("new_dir/created.txt",)
    assert "new file mode" in diff.patch
    assert "worker new file" in diff.patch
    check = check_apply_to_controller_repo(repo_path=str(repo), patch=diff.patch)
    assert check.ok is True
    assert not (repo / "new_dir" / "created.txt").exists()
    result = apply_to_controller_repo(repo_path=str(repo), patch=diff.patch)
    assert result.ok is True
    assert (repo / "new_dir" / "created.txt").read_text(encoding="utf-8") == "worker new file\n"
