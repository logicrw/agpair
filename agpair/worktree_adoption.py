from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping
import subprocess

from agpair.models import TaskRecord


class WorktreeAdoptionError(RuntimeError):
    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class WorktreeDiff:
    task_id: str
    execution_repo_path: str
    base_ref: str
    patch: str
    stat: str
    changed_files: tuple[str, ...]


@dataclass(frozen=True)
class ApplyCheck:
    ok: bool
    reason: str | None = None
    stderr: str = ""


def _git_output(repo_path: str, args: list[str], *, input_text: str | None = None) -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", repo_path, *args],
            input=input_text,
            text=True,
            stderr=subprocess.STDOUT,
        )
    except subprocess.CalledProcessError as exc:
        output = str(exc.output or "").strip()
        raise WorktreeAdoptionError("git_failed", output or str(exc)) from exc


def _git_diff_output_allowing_differences(repo_path: str, args: list[str]) -> str:
    process = subprocess.run(
        ["git", "-C", repo_path, *args],
        check=False,
        capture_output=True,
        text=True,
    )
    if process.returncode not in {0, 1}:
        output = (process.stderr or process.stdout or "").strip()
        raise WorktreeAdoptionError("git_failed", output or f"git exited {process.returncode}")
    return process.stdout


def _session_value(session_state: Mapping[str, object], key: str) -> str | None:
    value = session_state.get(key)
    return str(value) if value else None


def build_worktree_diff(*, task: TaskRecord, session_state: Mapping[str, object]) -> WorktreeDiff:
    """Return the patch between worker_base_head and the executor worktree."""
    if not task.isolated_worktree:
        raise WorktreeAdoptionError("not_isolated_worktree", "Task was not dispatched with an isolated worktree.")
    execution_repo_path = _session_value(session_state, "repo_path") or task.execution_repo_path
    if not execution_repo_path:
        raise WorktreeAdoptionError("execution_repo_missing", "Task has no execution repository path.")
    if not Path(execution_repo_path).is_dir():
        raise WorktreeAdoptionError("worktree_missing", f"Execution worktree is missing: {execution_repo_path}")
    base_ref = _session_value(session_state, "worker_base_head") or _session_value(session_state, "start_head")
    if not base_ref:
        raise WorktreeAdoptionError("baseline_missing", "Worker diff baseline is missing.")

    patch_parts = [_git_output(execution_repo_path, ["diff", "--binary", base_ref])]
    stat_parts = [_git_output(execution_repo_path, ["diff", "--stat", base_ref])]
    changed = _git_output(execution_repo_path, ["diff", "--name-only", base_ref])
    changed_files = [line.strip() for line in changed.splitlines() if line.strip()]
    untracked = _git_output(execution_repo_path, ["ls-files", "--others", "--exclude-standard"])
    for rel_path in [line.strip() for line in untracked.splitlines() if line.strip()]:
        patch_parts.append(
            _git_diff_output_allowing_differences(
                execution_repo_path,
                ["diff", "--binary", "--no-index", "--", "/dev/null", rel_path],
            )
        )
        stat_parts.append(
            _git_diff_output_allowing_differences(
                execution_repo_path,
                ["diff", "--stat", "--no-index", "--", "/dev/null", rel_path],
            )
        )
        if rel_path not in changed_files:
            changed_files.append(rel_path)
    patch = "".join(part for part in patch_parts if part)
    stat = "".join(part for part in stat_parts if part)
    return WorktreeDiff(
        task_id=task.task_id,
        execution_repo_path=execution_repo_path,
        base_ref=base_ref,
        patch=patch,
        stat=stat,
        changed_files=tuple(changed_files),
    )


def check_apply_to_controller_repo(*, repo_path: str, patch: str) -> ApplyCheck:
    """Run git apply --check against the controller repo without modifying files."""
    if not patch.strip():
        return ApplyCheck(ok=False, reason="diff_missing")
    try:
        subprocess.run(
            ["git", "-C", repo_path, "apply", "--check", "--whitespace=nowarn", "-"],
            input=patch,
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or exc.stdout or "").strip()
        return ApplyCheck(ok=False, reason="apply_conflict", stderr=stderr)
    return ApplyCheck(ok=True)


def apply_to_controller_repo(*, repo_path: str, patch: str) -> ApplyCheck:
    """Apply the worker patch to the controller repo and report conflicts."""
    check = check_apply_to_controller_repo(repo_path=repo_path, patch=patch)
    if not check.ok:
        return check
    try:
        subprocess.run(
            ["git", "-C", repo_path, "apply", "--whitespace=nowarn", "-"],
            input=patch,
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        first_error = (exc.stderr or exc.stdout or "").strip()
        try:
            subprocess.run(
                ["git", "-C", repo_path, "apply", "--3way", "--whitespace=nowarn", "-"],
                input=patch,
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as plain_exc:
            stderr = (plain_exc.stderr or plain_exc.stdout or first_error).strip()
            return ApplyCheck(ok=False, reason="apply_conflict", stderr=stderr)
    return ApplyCheck(ok=True)
