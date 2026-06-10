from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal, Mapping

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

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["blockers"] = list(self.blockers)
        payload["warnings"] = list(self.warnings)
        payload["evidence"] = self.evidence.to_dict()
        return payload


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

    changed_files = _changed_files(payload.get("changed_files"))
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
    has_report = bool(_string_value(payload.get("report"))) or _path_has_content(report_path)
    has_receipt = isinstance(receipt, Mapping) or _path_has_content(receipt_path)
    has_stdout = _path_has_content(stdout_path)
    has_validation = bool(payload.get("validation")) or bool(payload.get("validation_not_run"))
    has_commit = bool(_string_value(payload.get("commit_ref")) or _string_value(payload.get("commit")) or _string_value(payload.get("commit_sha")))
    has_diff = bool((git_status_summary or "").strip())
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
        controller_rework_required=controller_rework in {"minor", "major", "redone"},
    )
    warnings = tuple(dict.fromkeys(protocol_warnings))
    blockers: list[str] = []
    if protocol_errors:
        blockers.append("protocol_errors")

    policy = effective_policy.effective_completion_policy
    if policy == "report":
        if not has_report:
            blockers.append("report_missing")
            return AdoptionDecision("no", tuple(blockers), warnings, evidence, controller_rework)
        if blockers:
            return AdoptionDecision("partial", tuple(blockers), warnings, evidence, controller_rework)
        result: AdoptableResult = "partial" if warnings or not has_receipt else "yes"
        return AdoptionDecision(result, (), warnings, evidence, controller_rework)

    if policy == "commit":
        _append_scope_blockers(blockers, evidence)
        if has_commit:
            result: AdoptableResult = "partial" if blockers or warnings else "yes"
            return AdoptionDecision(result, tuple(blockers), warnings, evidence, controller_rework)
        if has_diff or changed_files:
            blockers.append("commit_missing")
            return AdoptionDecision("partial", tuple(blockers), warnings, evidence, controller_rework)
        blockers.append("commit_and_diff_missing")
        return AdoptionDecision("no", tuple(blockers), warnings, evidence, controller_rework)

    if not changed_files and not has_report and not has_diff:
        blockers.append("evidence_missing")
        return AdoptionDecision("no", tuple(blockers), warnings, evidence, controller_rework)
    if changed_files and not present_changed_files:
        blockers.append("changed_files_not_present")
    _append_scope_blockers(blockers, evidence)
    if not has_validation:
        blockers.append("validation_missing")
    if blockers:
        return AdoptionDecision("partial", tuple(blockers), warnings, evidence, controller_rework)
    return AdoptionDecision("partial" if warnings else "yes", (), warnings, evidence, controller_rework)


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
