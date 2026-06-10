from __future__ import annotations

import subprocess
from pathlib import Path

from agpair.scope_validation import (
    changed_files_from_git_diff,
    changed_files_from_git_status,
    validate_changed_files,
)


def test_validate_changed_files_accepts_matching_declared_changes() -> None:
    result = validate_changed_files(
        declared_changed_files=["src/app.py"],
        actual_changed_files=["src/app.py"],
    )

    assert result.ok is True
    assert result.to_dict()["ok"] is True
    assert result.undeclared_changed_files == ()
    assert result.missing_declared_files == ()


def test_validate_changed_files_detects_undeclared_missing_and_forbidden_changes() -> None:
    result = validate_changed_files(
        declared_changed_files=["src/app.py", "tests/test_app.py"],
        actual_changed_files=["src/app.py", ".agpair/tasks/TASK-1.json", "docs/extra.md"],
    )

    assert result.ok is False
    assert result.undeclared_changed_files == (".agpair/tasks/TASK-1.json", "docs/extra.md")
    assert result.missing_declared_files == ("tests/test_app.py",)
    assert result.forbidden_changed_files == (".agpair/tasks/TASK-1.json",)


def test_validate_changed_files_excludes_baseline_dirty_snapshot() -> None:
    result = validate_changed_files(
        declared_changed_files=["src/new.py"],
        actual_changed_files=["README.md", "src/new.py"],
        baseline_changed_files=["README.md"],
    )

    assert result.ok is True
    assert result.effective_changed_files == ("src/new.py",)


def test_changed_files_helpers_read_git_status_and_commit_diff(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "tests@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "AGPair Tests"], cwd=repo, check=True)
    (repo / "tracked.txt").write_text("baseline\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "baseline"], cwd=repo, check=True, capture_output=True)
    start = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()

    (repo / "tracked.txt").write_text("changed\n", encoding="utf-8")
    (repo / "untracked.txt").write_text("new\n", encoding="utf-8")

    assert changed_files_from_git_status(repo) == ("tracked.txt", "untracked.txt")

    subprocess.run(["git", "add", "tracked.txt", "untracked.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "TASK-SCOPE commit"], cwd=repo, check=True, capture_output=True)
    end = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()

    assert changed_files_from_git_diff(repo, start_ref=start, end_ref=end) == ("tracked.txt", "untracked.txt")
