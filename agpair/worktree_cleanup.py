from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
import pathlib
import subprocess
from typing import Protocol


@dataclass(frozen=True, slots=True)
class AgpairWorktreeCleanup:
    worktrees: int = 0
    failures: int = 0
    dry_run: bool = False


class AgpairWorktreeTask(Protocol):
    @property
    def task_id(self) -> str: ...

    @property
    def repo_path(self) -> str: ...

    @property
    def execution_repo_path(self) -> str | None: ...

    @property
    def isolated_worktree(self) -> bool: ...

    @property
    def worktree_boundary(self) -> str | None: ...


def sweep_agpair_orphan_worktrees(
    tasks: Iterable[AgpairWorktreeTask],
    *,
    dry_run: bool = False,
) -> AgpairWorktreeCleanup:
    task_records = tuple(tasks)
    protected = _referenced_worktree_paths(task_records)
    candidates = _candidate_worktrees(task_records, protected_paths=protected)
    if dry_run:
        return AgpairWorktreeCleanup(worktrees=len(candidates), dry_run=True)

    removed = 0
    failures = 0
    for candidate in candidates:
        if _remove_git_worktree(candidate):
            removed += 1
        else:
            failures += 1
    return AgpairWorktreeCleanup(worktrees=removed, failures=failures)


def _referenced_worktree_paths(tasks: Iterable[AgpairWorktreeTask]) -> frozenset[pathlib.Path]:
    paths: set[pathlib.Path] = set()
    for task in tasks:
        if task.execution_repo_path is not None:
            worktree_path = _agpair_worktree_path(
                pathlib.Path(task.execution_repo_path).expanduser().resolve(strict=False)
            )
            if worktree_path is not None:
                paths.add(worktree_path)
        if task.worktree_boundary is not None:
            boundary_path = _agpair_worktree_path(
                pathlib.Path(task.worktree_boundary).expanduser().resolve(strict=False)
            )
            if boundary_path is not None:
                paths.add(boundary_path)
        if task.isolated_worktree:
            repo_path = pathlib.Path(task.repo_path).expanduser().resolve(strict=False)
            paths.add((repo_path / ".agpair" / "worktrees" / task.task_id).resolve(strict=False))
    return frozenset(paths)


def _candidate_worktrees(
    tasks: Iterable[AgpairWorktreeTask],
    *,
    protected_paths: frozenset[pathlib.Path],
) -> tuple[pathlib.Path, ...]:
    roots = _worktree_roots(tasks)
    candidates: list[pathlib.Path] = []
    for root in roots:
        if not root.is_dir():
            continue
        try:
            children = tuple(root.iterdir())
        except OSError:
            continue
        for child in children:
            path = child.resolve(strict=False)
            if path in protected_paths:
                continue
            if (
                child.name.startswith("TASK-")
                and not child.is_symlink()
                and child.is_dir()
                and (child / ".git").exists()
            ):
                candidates.append(path)
    return tuple(sorted(set(candidates)))


def _worktree_roots(tasks: Iterable[AgpairWorktreeTask]) -> frozenset[pathlib.Path]:
    roots: set[pathlib.Path] = set()
    for task in tasks:
        repo_path = pathlib.Path(task.repo_path).expanduser().resolve(strict=False)
        roots.add(repo_path / ".agpair" / "worktrees")
        for path_text in (task.execution_repo_path, task.worktree_boundary):
            if path_text is None:
                continue
            root = _agpair_worktrees_root(pathlib.Path(path_text).expanduser().resolve(strict=False))
            if root is not None:
                roots.add(root)
    return frozenset(roots)


def _agpair_worktree_path(path: pathlib.Path) -> pathlib.Path | None:
    root = _agpair_worktrees_root(path)
    if root is None:
        return None
    relative = path.relative_to(root)
    if not relative.parts:
        return None
    return root / relative.parts[0]


def _agpair_worktrees_root(path: pathlib.Path) -> pathlib.Path | None:
    parts = path.parts
    for index, part in enumerate(parts[:-1]):
        if part == ".agpair" and parts[index + 1] == "worktrees":
            return pathlib.Path(*parts[: index + 2])
    return None


def _remove_git_worktree(path: pathlib.Path) -> bool:
    root = _agpair_worktrees_root(path)
    if root is None:
        return False
    base_repo = root.parent.parent
    try:
        subprocess.run(
            ["git", "-C", str(base_repo), "worktree", "remove", "--force", str(path)],
            check=True,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, OSError, subprocess.CalledProcessError):
        return False
    return True
