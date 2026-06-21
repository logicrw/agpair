from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal, Mapping

from agpair.terminal_arbitration import completed_report_text

ArtifactKind = Literal[
    "report",
    "stdout_salvage",
    "diff",
    "patch_or_commit",
    "evidence",
    "blocker",
    "nothing_useful",
]
ArtifactState = Literal["usable", "needs_review", "blocked", "absent"]
ArtifactResultState = Literal["usable", "needs_review", "blocked"]

GLOBAL_HARD_BLOCKERS = frozenset(
    {
        "executor_unavailable",
        "executor_auth_required",
        "executor_auth_failed",
        "executor_probe_failed",
        "executor_quota_exhausted",
        "approval_required",
        "authorization_violation",
        "authorization_profile_insufficient",
        "secret_or_token_exposure_detected",
        "uninspectable_artifacts",
        "process_crash_with_no_usable_artifact",
    }
)


@dataclass(frozen=True, slots=True)
class Artifact:
    kind: ArtifactKind
    state: ArtifactState
    summary: str
    paths: dict[str, str]
    hard_blockers: tuple[str, ...] = ()
    soft_warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["hard_blockers"] = list(self.hard_blockers)
        payload["soft_warnings"] = list(self.soft_warnings)
        return payload


@dataclass(frozen=True, slots=True)
class ArtifactResult:
    state: ArtifactResultState
    primary_artifact: ArtifactKind
    artifacts: tuple[Artifact, ...]
    global_hard_blockers: tuple[str, ...] = ()
    soft_warnings: tuple[str, ...] = ()

    def by_kind(self, kind: ArtifactKind) -> Artifact | None:
        return next((artifact for artifact in self.artifacts if artifact.kind == kind), None)

    def to_dict(self) -> dict[str, object]:
        return {
            "state": self.state,
            "primary_artifact": self.primary_artifact,
            "artifacts": [artifact.to_dict() for artifact in self.artifacts],
            "global_hard_blockers": list(self.global_hard_blockers),
            "soft_warnings": list(self.soft_warnings),
        }


def classify_artifacts(
    *,
    receipt: Mapping[str, Any] | None,
    report_path: str | None,
    stdout_path: str | None,
    receipt_path: str | None,
    git_status_summary: str | None,
    scope_validation: Mapping[str, Any] | None,
    protocol_warnings: tuple[str, ...],
    protocol_errors: tuple[str, ...],
) -> ArtifactResult:
    payload = receipt.get("payload") if isinstance(receipt, Mapping) else None
    if not isinstance(payload, Mapping):
        payload = {}

    warnings = _unique((*tuple(protocol_warnings), *tuple(protocol_errors)))
    global_blockers = _global_blockers(receipt=receipt, payload=payload)
    artifacts = _artifact_tuple(
        payload=payload,
        report_path=report_path,
        stdout_path=stdout_path,
        receipt_path=receipt_path,
        git_status_summary=git_status_summary,
        scope_validation=scope_validation,
        warnings=warnings,
    )

    if global_blockers:
        primary = _best_artifact(artifacts) or _nothing_useful_artifact("Global hard blocker prevents adoption.")
        return ArtifactResult(
            state="blocked",
            primary_artifact=primary.kind,
            artifacts=artifacts or (primary,),
            global_hard_blockers=global_blockers,
            soft_warnings=warnings,
        )

    primary = _best_artifact(artifacts)
    if primary is None:
        stdout_text = _read_text(stdout_path)
        blocker = "thought_only_output" if stdout_text else "no_useful_artifact"
        nothing = _nothing_useful_artifact(
            "No safe useful report, stdout salvage, diff, patch, commit, or evidence artifact exists."
        )
        return ArtifactResult(
            state="blocked",
            primary_artifact="nothing_useful",
            artifacts=(*artifacts, nothing),
            global_hard_blockers=(blocker,),
            soft_warnings=warnings,
        )

    state: ArtifactResultState = (
        "usable" if primary.state == "usable" and not _has_non_usable_artifact(artifacts) else "needs_review"
    )
    return ArtifactResult(
        state=state,
        primary_artifact=primary.kind,
        artifacts=artifacts,
        global_hard_blockers=(),
        soft_warnings=warnings,
    )


def _artifact_tuple(
    *,
    payload: Mapping[str, Any],
    report_path: str | None,
    stdout_path: str | None,
    receipt_path: str | None,
    git_status_summary: str | None,
    scope_validation: Mapping[str, Any] | None,
    warnings: tuple[str, ...],
) -> tuple[Artifact, ...]:
    artifacts: list[Artifact] = []
    report = _string_value(payload.get("report"))
    if report or _path_has_content(report_path):
        artifacts.append(
            Artifact(
                kind="report",
                state="usable",
                summary="Receipt payload or report path contains a completed report.",
                paths=_paths(report=report_path, receipt=receipt_path),
                soft_warnings=warnings,
            )
        )

    stdout_candidate = _string_value(payload.get("stdout_report_candidate"))
    stdout_report = completed_report_text(stdout_candidate or _read_text(stdout_path))
    if stdout_report and not report and not _path_has_content(report_path):
        artifacts.append(
            Artifact(
                kind="stdout_salvage",
                state="needs_review",
                summary="Stdout contains a completed report but terminal receipt/report artifact is missing.",
                paths=_paths(stdout=stdout_path),
                soft_warnings=_unique(("terminal_receipt_missing", *warnings)),
            )
        )

    worktree_diff = payload.get("worktree_diff")
    if isinstance(worktree_diff, Mapping):
        diff_artifact = _diff_artifact(
            worktree_diff,
            git_status_summary=git_status_summary,
            scope_validation=scope_validation,
        )
        if diff_artifact is not None:
            artifacts.append(diff_artifact)
    elif (git_status_summary or "").strip() or _changed_files(payload.get("changed_files")):
        artifacts.append(
            Artifact(
                kind="diff",
                state="needs_review",
                summary="Git status indicates changed files, but apply-check evidence is absent.",
                paths={},
                soft_warnings=("apply_check_missing",),
            )
        )

    if (
        _string_value(payload.get("commit_ref"))
        or _string_value(payload.get("commit"))
        or _string_value(payload.get("commit_sha"))
    ):
        artifacts.append(
            Artifact(
                kind="patch_or_commit",
                state="needs_review",
                summary="Receipt declares a commit artifact, but the ref must be verified before it is usable.",
                paths={},
                soft_warnings=("commit_ref_unverified",),
            )
        )

    if payload.get("validation") or payload.get("validation_not_run"):
        artifacts.append(
            Artifact(
                kind="evidence",
                state="needs_review" if payload.get("validation_not_run") else "usable",
                summary="Receipt contains validation evidence or an explicit validation-not-run reason.",
                paths={},
                soft_warnings=("validation_not_run",) if payload.get("validation_not_run") else (),
            )
        )
    return tuple(artifacts)


def _diff_artifact(
    worktree_diff: Mapping[str, Any],
    *,
    git_status_summary: str | None,
    scope_validation: Mapping[str, Any] | None,
) -> Artifact | None:
    has_patch = bool(worktree_diff.get("has_patch")) or bool((git_status_summary or "").strip())
    changed_files = _changed_files(worktree_diff.get("changed_files"))
    if not has_patch and not changed_files:
        return None

    blockers: list[str] = []
    warnings: list[str] = []
    if worktree_diff.get("apply_check_ok") is False:
        blockers.append(_string_value(worktree_diff.get("apply_check_reason")) or "apply_check_failed")
    elif "apply_check_ok" not in worktree_diff:
        warnings.append("apply_check_missing")

    if isinstance(scope_validation, Mapping) and scope_validation.get("ok") is False:
        if scope_validation.get("forbidden_changed_files"):
            blockers.append("forbidden_changes")
        if scope_validation.get("undeclared_changed_files"):
            blockers.append("undeclared_changes")
        if scope_validation.get("missing_declared_files"):
            blockers.append("missing_declared_changes")
        if not blockers:
            blockers.append("scope_violations")

    state: ArtifactState = "blocked" if blockers else "usable" if worktree_diff.get("apply_check_ok") is True else "needs_review"
    return Artifact(
        kind="diff",
        state=state,
        summary="Worktree diff artifact was classified from receipt patch metadata.",
        paths={},
        hard_blockers=tuple(blockers),
        soft_warnings=tuple(warnings),
    )


def _global_blockers(*, receipt: Mapping[str, Any] | None, payload: Mapping[str, Any]) -> tuple[str, ...]:
    blockers: list[str] = []
    blocker_type = _string_value(payload.get("blocker_type"))
    receipt_status = _string_value(receipt.get("status") if isinstance(receipt, Mapping) else None)
    if blocker_type in GLOBAL_HARD_BLOCKERS:
        blockers.append(blocker_type)
    if receipt_status and receipt_status.upper() == "BLOCKED" and blocker_type in GLOBAL_HARD_BLOCKERS:
        blockers.append(blocker_type)
    return _unique(blockers)


def _best_artifact(artifacts: tuple[Artifact, ...]) -> Artifact | None:
    priority = {
        "diff": 0,
        "patch_or_commit": 1,
        "report": 2,
        "stdout_salvage": 3,
        "evidence": 4,
        "blocker": 5,
        "nothing_useful": 6,
    }
    candidates = [artifact for artifact in artifacts if artifact.state in {"usable", "needs_review"}]
    if not candidates:
        return None
    return sorted(candidates, key=lambda item: (0 if item.state == "usable" else 1, priority[item.kind]))[0]


def _has_non_usable_artifact(artifacts: tuple[Artifact, ...]) -> bool:
    return any(artifact.state in {"needs_review", "blocked"} for artifact in artifacts)


def _nothing_useful_artifact(summary: str) -> Artifact:
    return Artifact(
        kind="nothing_useful",
        state="blocked",
        summary=summary,
        paths={},
    )


def _paths(**paths: str | None) -> dict[str, str]:
    return {key: value for key, value in paths.items() if value}


def _path_has_content(path: str | None) -> bool:
    if not path:
        return False
    try:
        value = Path(path)
        return value.exists() and value.is_file() and value.stat().st_size > 0
    except OSError:
        return False


def _read_text(path: str | None) -> str | None:
    if not path:
        return None
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _string_value(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _changed_files(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        stripped = value.strip()
        return (stripped,) if stripped else ()
    if isinstance(value, list):
        return tuple(str(item) for item in value if isinstance(item, str) and item.strip())
    return ()


def _unique(values: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))
