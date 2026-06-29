from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal, Mapping

from agpair.agent_result import AgentResult, ControllerAction, blocking_protocol_warnings, unique
from agpair.artifact_classification import ArtifactResult, classify_artifacts
from agpair.completion import EffectiveTaskPolicy

AdoptableResult = Literal["yes", "partial", "no", "unknown"]
ControllerRework = Literal["none", "minor", "major", "redone", "unknown"]


@dataclass(frozen=True)
class AdoptionEvidence:
    has_report: bool = False
    has_receipt: bool = False
    has_changed_files: bool = False
    changed_files_present: bool = False
    scope_validation_passed: bool = False
    has_undeclared_changes: bool = False
    has_missing_declared_changes: bool = False
    has_forbidden_changes: bool = False
    has_validation: bool = False
    has_scope_violations: bool = False
    has_commit: bool = False
    has_diff: bool = False
    has_apply_check: bool = False
    apply_check_passed: bool = False
    controller_rework_required: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AdoptionDecision:
    adoptable_result: AdoptableResult
    blockers: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    evidence: AdoptionEvidence = AdoptionEvidence()
    controller_rework: ControllerRework = "unknown"
    agent_result: AgentResult | None = None
    artifact_result: ArtifactResult | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "adoptable_result": self.adoptable_result,
            "blockers": list(self.blockers),
            "warnings": list(self.warnings),
            "evidence": self.evidence.to_dict(),
            "controller_rework": self.controller_rework,
        }
        if self.agent_result is not None:
            payload["agent_result"] = self.agent_result.to_dict()
        if self.artifact_result is not None:
            payload["artifact_result"] = self.artifact_result.to_dict()
        return payload


def _agent_result_from_artifacts(
    *,
    adoptable_result: AdoptableResult,
    artifact_result: ArtifactResult,
    blockers: tuple[str, ...],
    warnings: tuple[str, ...],
    controller_rework: ControllerRework,
    policy: str,
) -> AgentResult:
    action = _controller_action_from_artifacts(artifact_result, policy=policy)
    hard_blockers = unique((*artifact_result.global_hard_blockers, *blockers))
    soft_warnings = unique((*artifact_result.soft_warnings, *warnings))
    if artifact_result.state == "blocked" or adoptable_result == "no":
        return AgentResult(
            state="blocked",
            controller_action=action,
            summary="Executor result has no safe useful artifact or hit a global hard blocker.",
            hard_blockers=hard_blockers,
            soft_warnings=soft_warnings,
        )
    if artifact_result.state == "needs_review" or adoptable_result == "partial" or controller_rework in {"major", "redone"}:
        return AgentResult(
            state="needs_review",
            controller_action=action,
            summary="Executor result contains useful artifacts but needs controller review before adoption.",
            hard_blockers=hard_blockers,
            soft_warnings=soft_warnings,
        )
    return AgentResult(
        state="usable",
        controller_action=action,
        summary="Executor result is usable with normal controller verification.",
        hard_blockers=(),
        soft_warnings=soft_warnings,
    )


def _controller_action_from_artifacts(artifact_result: ArtifactResult, *, policy: str) -> ControllerAction:
    if artifact_result.state == "blocked":
        has_fatal_global = any(
            blocker not in {"no_useful_artifact", "thought_only_output"}
            for blocker in artifact_result.global_hard_blockers
        )
        return "inspect_evidence" if has_fatal_global else "retry_or_switch_executor"
    has_reviewable_code = any(
        item.kind in {"diff", "patch_or_commit"} and item.state in {"usable", "needs_review"}
        for item in artifact_result.artifacts
    )
    if policy in {"commit", "evidence"} and has_reviewable_code:
        return "review_then_apply"
    if artifact_result.primary_artifact in {"report", "stdout_salvage"}:
        return "use_result"
    if artifact_result.primary_artifact in {"diff", "patch_or_commit"}:
        return "review_then_apply" if policy in {"commit", "evidence"} else "inspect_evidence"
    return "inspect_evidence"


def _agent_result_for_decision(
    *,
    adoptable_result: AdoptableResult,
    blockers: tuple[str, ...],
    warnings: tuple[str, ...],
    evidence: AdoptionEvidence,
    controller_rework: ControllerRework,
    policy: str,
) -> AgentResult:
    hard_blockers = unique(blockers)
    soft_warnings = unique(warnings)
    has_code_evidence = evidence.has_changed_files or evidence.has_diff or evidence.has_commit
    if adoptable_result == "no":
        return AgentResult(
            state="blocked",
            controller_action="retry_or_switch_executor",
            summary="Executor result is missing required usable evidence or reported a blocker.",
            hard_blockers=hard_blockers,
            soft_warnings=soft_warnings,
        )
    if adoptable_result == "partial" or controller_rework in {"major", "redone"}:
        action = "review_then_apply" if has_code_evidence else "use_result" if evidence.has_report else "inspect_evidence"
        return AgentResult(
            state="needs_review",
            controller_action=action,
            summary="Executor result contains useful evidence but needs controller review before adoption.",
            hard_blockers=hard_blockers,
            soft_warnings=soft_warnings,
        )
    action = "use_result" if policy == "report" or (evidence.has_report and not has_code_evidence) else "review_then_apply"
    return AgentResult(
        state="usable",
        controller_action=action,
        summary="Executor result is usable with normal controller verification.",
        hard_blockers=(),
        soft_warnings=soft_warnings,
    )


def _make_decision(
    adoptable_result: AdoptableResult,
    blockers: tuple[str, ...],
    warnings: tuple[str, ...],
    evidence: AdoptionEvidence,
    controller_rework: ControllerRework,
    policy: str,
    artifact_result: ArtifactResult | None = None,
) -> AdoptionDecision:
    normalized_blockers = unique(blockers)
    normalized_warnings = unique(warnings)
    agent_result = (
        _agent_result_from_artifacts(
            adoptable_result=adoptable_result,
            artifact_result=artifact_result,
            blockers=normalized_blockers,
            warnings=normalized_warnings,
            controller_rework=controller_rework,
            policy=policy,
        )
        if artifact_result is not None
        else _agent_result_for_decision(
            adoptable_result=adoptable_result,
            blockers=normalized_blockers,
            warnings=normalized_warnings,
            evidence=evidence,
            controller_rework=controller_rework,
            policy=policy,
        )
    )
    return AdoptionDecision(
        adoptable_result,
        normalized_blockers,
        normalized_warnings,
        evidence,
        controller_rework,
        agent_result,
        artifact_result,
    )


def derive_adoption_decision(
    *,
    effective_policy: EffectiveTaskPolicy,
    receipt: Mapping[str, Any] | None,
    report_path: str | None = None,
    stdout_path: str | None = None,
    receipt_path: str | None = None,
    changed_files_present: bool | None = None,
    git_status_summary: str | None = None,
    scope_validation: Mapping[str, Any] | None = None,
    protocol_warnings: tuple[str, ...] = (),
    protocol_errors: tuple[str, ...] = (),
    controller_rework: ControllerRework = "unknown",
) -> AdoptionDecision:
    payload = receipt.get("payload") if isinstance(receipt, Mapping) else None
    if not isinstance(payload, Mapping):
        payload = {}

    artifact_result = classify_artifacts(
        receipt=receipt,
        report_path=report_path,
        stdout_path=stdout_path,
        receipt_path=receipt_path,
        git_status_summary=git_status_summary,
        scope_validation=scope_validation,
        protocol_warnings=protocol_warnings,
        protocol_errors=protocol_errors,
    )
    worktree_diff = payload.get("worktree_diff")
    changed_files = _changed_files(payload.get("changed_files"))
    if not changed_files and isinstance(worktree_diff, Mapping):
        changed_files = _changed_files(worktree_diff.get("changed_files"))
    scope_violations = payload.get("scope_violations")
    scope_payload = scope_validation if isinstance(scope_validation, Mapping) else None
    has_undeclared_changes = _non_empty_list(scope_payload, "undeclared_changed_files")
    has_missing_declared_changes = _non_empty_list(scope_payload, "missing_declared_files")
    has_forbidden_changes = _non_empty_list(scope_payload, "forbidden_changed_files")
    scope_validation_passed = bool(scope_payload and scope_payload.get("ok") is True)
    has_scope_violations = (
        isinstance(scope_violations, list)
        and bool(scope_violations)
        or has_undeclared_changes
        or has_missing_declared_changes
        or has_forbidden_changes
    )
    has_report = any(
        item.kind in {"report", "stdout_salvage"} and item.state in {"usable", "needs_review"}
        for item in artifact_result.artifacts
    )
    has_receipt = isinstance(receipt, Mapping) or _path_has_content(receipt_path)
    has_validation = bool(payload.get("validation")) or bool(payload.get("validation_not_run"))
    has_commit = any(
        item.kind == "patch_or_commit" and item.state in {"usable", "needs_review"}
        for item in artifact_result.artifacts
    )
    has_safe_code_artifact = any(
        item.kind in {"diff", "patch_or_commit"} and item.state in {"usable", "needs_review"}
        for item in artifact_result.artifacts
    )
    has_diff = any(item.kind == "diff" for item in artifact_result.artifacts) or bool((git_status_summary or "").strip())
    has_apply_check = isinstance(worktree_diff, Mapping) and "apply_check_ok" in worktree_diff
    apply_check_passed = bool(worktree_diff.get("apply_check_ok")) if isinstance(worktree_diff, Mapping) else False
    apply_check_reason = _string_value(worktree_diff.get("apply_check_reason")) if isinstance(worktree_diff, Mapping) else None
    has_worktree_patch = bool(worktree_diff.get("has_patch")) if isinstance(worktree_diff, Mapping) else False
    if has_worktree_patch:
        has_diff = True
    report_only_evidence = has_report and not changed_files and not has_safe_code_artifact and not has_commit and not has_diff
    if changed_files_present is not None:
        present_changed_files = bool(changed_files_present)
    elif scope_payload is not None:
        present_changed_files = bool(changed_files) and not has_missing_declared_changes
    else:
        present_changed_files = bool(changed_files)

    evidence = AdoptionEvidence(
        has_report=has_report,
        has_receipt=has_receipt,
        has_changed_files=bool(changed_files),
        changed_files_present=present_changed_files,
        scope_validation_passed=scope_validation_passed,
        has_undeclared_changes=has_undeclared_changes,
        has_missing_declared_changes=has_missing_declared_changes,
        has_forbidden_changes=has_forbidden_changes,
        has_validation=has_validation,
        has_scope_violations=has_scope_violations,
        has_commit=has_commit,
        has_diff=has_diff,
        has_apply_check=has_apply_check,
        apply_check_passed=apply_check_passed,
        controller_rework_required=controller_rework in {"minor", "major", "redone"},
    )
    warnings = unique((*tuple(protocol_warnings), *tuple(protocol_errors)))
    blocking_warnings = blocking_protocol_warnings(warnings)
    blockers: list[str] = []
    for artifact in artifact_result.artifacts:
        blockers.extend(artifact.hard_blockers)
    policy = effective_policy.effective_completion_policy
    receipt_status = (_string_value(receipt.get("status") if isinstance(receipt, Mapping) else None) or "").upper()
    blocker_type = _string_value(payload.get("blocker_type"))
    fatal_global_blockers = tuple(
        blocker
        for blocker in artifact_result.global_hard_blockers
        if blocker not in {"no_useful_artifact", "thought_only_output"}
    )
    if fatal_global_blockers:
        return _make_decision(
            "no",
            fatal_global_blockers,
            warnings,
            evidence,
            controller_rework,
            policy,
            artifact_result,
        )
    if blocker_type:
        blockers.append(blocker_type)
    elif receipt_status == "BLOCKED":
        blockers.append("blocked_terminal_result")

    if policy == "report":
        if not has_report:
            blockers.append("report_missing")
            if artifact_result.state in {"usable", "needs_review"}:
                return _make_decision(
                    "partial",
                    tuple(blockers),
                    warnings,
                    evidence,
                    controller_rework,
                    policy,
                    artifact_result,
                )
            return _make_decision("no", tuple(blockers), warnings, evidence, controller_rework, policy, artifact_result)
        if blockers:
            return _make_decision("partial", tuple(blockers), warnings, evidence, controller_rework, policy, artifact_result)
        result: AdoptableResult = "yes" if has_receipt and artifact_result.state == "usable" else "partial"
        return _make_decision(result, (), warnings, evidence, controller_rework, policy, artifact_result)

    if policy == "commit":
        _append_scope_blockers(blockers, evidence)
        if has_commit and artifact_result.state == "usable":
            result = "partial" if blockers or blocking_warnings else "yes"
            return _make_decision(result, tuple(blockers), warnings, evidence, controller_rework, policy, artifact_result)
        if artifact_result.state in {"usable", "needs_review"}:
            blockers.append("commit_missing")
            return _make_decision("partial", tuple(blockers), warnings, evidence, controller_rework, policy, artifact_result)
        blockers.append("commit_and_diff_missing")
        return _make_decision("no", tuple(blockers), warnings, evidence, controller_rework, policy, artifact_result)

    if artifact_result.state == "blocked" or (not has_report and not has_safe_code_artifact):
        blockers.extend(artifact_result.global_hard_blockers)
        blockers.append("evidence_missing")
        return _make_decision("no", tuple(blockers), warnings, evidence, controller_rework, policy, artifact_result)
    if changed_files and not present_changed_files:
        blockers.append("changed_files_not_present")
    _append_scope_blockers(blockers, evidence)
    if has_apply_check and not apply_check_passed:
        blockers.append(apply_check_reason or "apply_check_failed")
    if not has_validation and not report_only_evidence:
        blockers.append("validation_missing")
    if blockers:
        return _make_decision("partial", tuple(blockers), warnings, evidence, controller_rework, policy, artifact_result)
    result = "partial" if artifact_result.state == "needs_review" or blocking_warnings else "yes"
    return _make_decision(result, (), warnings, evidence, controller_rework, policy, artifact_result)


def _changed_files(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        stripped = value.strip()
        return (stripped,) if stripped else ()
    if isinstance(value, list):
        return tuple(str(item) for item in value if isinstance(item, str) and item.strip())
    return ()


def _path_has_content(path: str | None) -> bool:
    if not path:
        return False
    try:
        p = Path(path)
        return p.exists() and p.is_file() and p.stat().st_size > 0
    except OSError:
        return False


def _string_value(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _non_empty_list(payload: Mapping[str, Any] | None, key: str) -> bool:
    if not payload:
        return False
    value = payload.get(key)
    return isinstance(value, (list, tuple)) and bool(value)


def _append_scope_blockers(blockers: list[str], evidence: AdoptionEvidence) -> None:
    if evidence.has_forbidden_changes:
        blockers.append("forbidden_changes")
    if evidence.has_undeclared_changes:
        blockers.append("undeclared_changes")
    if evidence.has_missing_declared_changes:
        blockers.append("missing_declared_changes")
    if evidence.has_scope_violations and not any(
        item in blockers for item in ("forbidden_changes", "undeclared_changes", "missing_declared_changes")
    ):
        blockers.append("scope_violations")
