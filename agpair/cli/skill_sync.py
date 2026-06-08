from __future__ import annotations

from dataclasses import dataclass
from difflib import unified_diff
from pathlib import Path


@dataclass(frozen=True)
class SkillSyncPlan:
    target_path: Path
    before: str
    after: str
    action: str

    @property
    def changed(self) -> bool:
        return self.before != self.after

    def diff(self) -> str:
        if not self.changed:
            return ""
        return "".join(
            unified_diff(
                self.before.splitlines(keepends=True),
                self.after.splitlines(keepends=True),
                fromfile=str(self.target_path),
                tofile=str(self.target_path),
            )
        )

    def apply(self) -> None:
        if not self.changed:
            return
        if self.action == "remove":
            self.target_path.unlink(missing_ok=True)
            return
        self.target_path.parent.mkdir(parents=True, exist_ok=True)
        self.target_path.write_text(self.after, encoding="utf-8")


def bundled_skill_path(client: str) -> Path:
    repo_root = Path(__file__).resolve().parents[2]
    source = repo_root / "skills" / client / "SKILL.md"
    if not source.is_file():
        raise RuntimeError(f"Bundled AGPair {client} skill not found at {source}")
    return source


def skill_target_path(*, client_home_dir: str, scope: str, repo_path: Path | None) -> Path:
    if scope == "user":
        base = Path.home() / client_home_dir
    else:
        base = (repo_path or Path.cwd()).expanduser().resolve() / client_home_dir
    return base / "skills" / "agpair" / "SKILL.md"


def plan_skill_sync(
    *,
    source_path: Path,
    target_path: Path,
    uninstall: bool = False,
) -> SkillSyncPlan:
    source_text = source_path.read_text(encoding="utf-8")
    before = target_path.read_text(encoding="utf-8") if target_path.exists() else ""
    if before and not _looks_like_agpair_skill(before):
        raise RuntimeError(
            f"Refusing to manage non-AGPair skill at {target_path}; move it aside or edit manually."
        )
    if uninstall:
        return SkillSyncPlan(
            target_path=target_path,
            before=before,
            after="",
            action="remove",
        )
    return SkillSyncPlan(
        target_path=target_path,
        before=before,
        after=source_text,
        action="write",
    )


def _looks_like_agpair_skill(text: str) -> bool:
    prefix = text[:500].lower()
    return "name: agpair" in prefix or "# agpair" in prefix
