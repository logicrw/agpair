from __future__ import annotations

from dataclasses import asdict, dataclass
import subprocess
from pathlib import Path
from typing import Any

DEFAULT_FORBIDDEN_PATH_PREFIXES: tuple[str, ...] = (
    ".agpair/",
    ".git/",
    "../",
    "/",
)


@dataclass(frozen=True)
class ScopeValidationResult:
    declared_changed_files: tuple[str, ...]
    actual_changed_files: tuple[str, ...]
    baseline_changed_files: tuple[str, ...] = ()
    effective_changed_files: tuple[str, ...] = ()
    undeclared_changed_files: tuple[str, ...] = ()
    missing_declared_files: tuple[str, ...] = ()
    forbidden_changed_files: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not (
            self.undeclared_changed_files
            or self.missing_declared_files
            or self.forbidden_changed_files
        )

    @property
    def has_actual_changes(self) -> bool:
        return bool(self.effective_changed_files)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["ok"] = self.ok
        payload["has_actual_changes"] = self.has_actual_changes
        return payload


def changed_files_from_git_status(repo_path: str | Path) -> tuple[str, ...]:
    """Return git dirty/untracked paths relative to repo_path.

    This is intentionally narrow: AGPair needs a controller-side fact about the
    executor worktree, not a replacement for git's own diff machinery.
    """
    repo = Path(repo_path)
    if not repo.is_dir():
        return ()
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            capture_output=True,
            text=True,
            cwd=repo,
            timeout=10,
        )
    except (subprocess.SubprocessError, FileNotFoundError, OSError, TypeError):
        return ()
    if result.returncode != 0:
        return ()
    stdout = result.stdout if isinstance(result.stdout, str) else ""
    return normalize_changed_files(_parse_porcelain_status(stdout))


def changed_files_from_git_diff(
    repo_path: str | Path,
    *,
    start_ref: str | None,
    end_ref: str,
) -> tuple[str, ...]:
    repo = Path(repo_path)
    if not repo.is_dir() or not end_ref:
        return ()
    cmd = ["git", "diff", "--name-only"]
    cmd.append(f"{start_ref}..{end_ref}" if start_ref else f"{end_ref}^..{end_ref}")
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=repo,
            timeout=10,
        )
    except (subprocess.SubprocessError, FileNotFoundError, OSError, TypeError):
        return ()
    if result.returncode != 0:
        return ()
    stdout = result.stdout if isinstance(result.stdout, str) else ""
    return normalize_changed_files(stdout.splitlines())


def normalize_changed_files(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        candidates = [value]
    elif isinstance(value, (list, tuple, set)):
        candidates = [str(item) for item in value if isinstance(item, (str, Path))]
    else:
        candidates = []
    normalized: list[str] = []
    for item in candidates:
        path = _normalize_path(item)
        if path and path not in normalized:
            normalized.append(path)
    return tuple(sorted(normalized))


def validate_changed_files(
    *,
    declared_changed_files: Any,
    actual_changed_files: Any,
    baseline_changed_files: Any = (),
    forbidden_prefixes: tuple[str, ...] = DEFAULT_FORBIDDEN_PATH_PREFIXES,
) -> ScopeValidationResult:
    declared = normalize_changed_files(declared_changed_files)
    actual = normalize_changed_files(actual_changed_files)
    baseline = normalize_changed_files(baseline_changed_files)
    baseline_set = set(baseline)
    effective = tuple(path for path in actual if path not in baseline_set)
    effective_set = set(effective)
    declared_set = set(declared)

    forbidden = tuple(path for path in effective if _is_forbidden(path, forbidden_prefixes))
    undeclared = tuple(path for path in effective if path not in declared_set)
    missing = tuple(path for path in declared if path not in effective_set)
    return ScopeValidationResult(
        declared_changed_files=declared,
        actual_changed_files=actual,
        baseline_changed_files=baseline,
        effective_changed_files=effective,
        undeclared_changed_files=undeclared,
        missing_declared_files=missing,
        forbidden_changed_files=forbidden,
    )


def _parse_porcelain_status(body: str) -> tuple[str, ...]:
    paths: list[str] = []
    for line in body.splitlines():
        if len(line) < 4:
            continue
        path = line[3:].split(" -> ")[-1].strip()
        if path:
            paths.append(path)
    return tuple(paths)


def _normalize_path(value: str | Path) -> str | None:
    text = str(value).strip().replace("\\", "/")
    if not text:
        return None
    while text.startswith("./"):
        text = text[2:]
    text = text.rstrip("/")
    return text or None


def _is_forbidden(path: str, prefixes: tuple[str, ...]) -> bool:
    return any(path == prefix.rstrip("/") or path.startswith(prefix) for prefix in prefixes)
