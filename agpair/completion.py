from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping

from agpair.transport import messages

CANONICAL_COMPLETION_POLICIES = frozenset({"auto", "evidence", "report", "commit"})
_POLICY_ALIASES = {
    "direct_commit": "commit",
    "evidence_ready": "evidence",
    "ready_for_review": "evidence",
}

_READONLY_HINTS = (
    "required changes: none",
    "required changes: 无",
    "禁止写入",
    "read-only",
    "read only",
    "readonly",
    "只读",
    "无需修改",
    "不修改",
)
_COMMIT_HINTS = (
    "must commit",
    "commit required",
    "requires commit",
    "required commit",
    "提交 commit",
    "必须提交",
    "需要提交",
)


@dataclass(frozen=True)
class EffectiveTaskPolicy:
    requested_completion_policy: str
    effective_completion_policy: str
    authorization_profile: str
    allows_file_edits: bool
    allows_commit: bool
    requires_commit: bool
    requires_report: bool
    requires_machine_evidence: bool
    report_only: bool
    source: str
    controller: str = "generic"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class EffectiveTaskSafety:
    is_mutating: bool
    is_concurrency_safe: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ExecutionEvidence:
    stdout_path: str | None = None
    stderr_path: str | None = None
    receipt_path: str | None = None
    report_path: str | None = None
    evidence_path: str | None = None
    output_excerpt: str | None = None
    report_text: str | None = None
    commit_ref: str | None = None
    changed_files: tuple[str, ...] = ()
    validation_present: bool = False
    receipt_valid: bool = False
    structured_status: str | None = None

    @property
    def has_output(self) -> bool:
        return bool((self.output_excerpt or "").strip()) or bool((self.report_text or "").strip()) or any(
            _path_has_content(path) for path in (self.stdout_path, self.stderr_path, self.report_path)
        )

    @property
    def has_report(self) -> bool:
        return _path_has_content(self.report_path) or bool((self.report_text or "").strip())

    @property
    def has_commit(self) -> bool:
        return bool((self.commit_ref or "").strip())

    @property
    def has_machine_evidence(self) -> bool:
        return bool(
            self.receipt_valid
            or self.changed_files
            or self.validation_present
            or _path_has_content(self.evidence_path)
            or _path_has_content(self.receipt_path)
            or self.has_report
            or self.has_commit
        )


@dataclass(frozen=True)
class CompletionDecision:
    phase: str
    ok: bool
    summary: str
    blocker_type: str | None = None
    terminal_status: str | None = None
    receipt: dict[str, Any] | None = None
    effective_policy: EffectiveTaskPolicy | None = None
    reason_code: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        if self.effective_policy is not None:
            payload["effective_policy"] = self.effective_policy.to_dict()
        return payload


def normalize_completion_policy(value: str | None) -> str:
    normalized = (value or "auto").strip().lower().replace("-", "_")
    normalized = _POLICY_ALIASES.get(normalized, normalized)
    if normalized not in CANONICAL_COMPLETION_POLICIES:
        allowed = ", ".join(sorted(CANONICAL_COMPLETION_POLICIES | frozenset(_POLICY_ALIASES)))
        raise ValueError(f"completion policy must be one of: {allowed}")
    return normalized


def resolve_effective_task_policy(
    *,
    requested_completion_policy: str | None,
    authorization_profile: str,
    body: str | None = None,
    controller: str | None = None,
) -> EffectiveTaskPolicy:
    requested = normalize_completion_policy(requested_completion_policy)
    normalized_authorization = (authorization_profile or "local_mutating").strip().lower()
    lower_body = (body or "").lower()
    controller_id = (controller or "generic").strip().lower() or "generic"

    source = "explicit"
    effective = requested
    if requested == "auto":
        if normalized_authorization == "local_readonly":
            effective = "report"
            source = "authorization_profile"
        elif any(hint in lower_body for hint in _READONLY_HINTS):
            effective = "report"
            source = "brief_hint"
        elif any(hint in lower_body for hint in _COMMIT_HINTS):
            effective = "commit"
            source = "brief_hint"
        else:
            effective = "evidence"
            source = "default"

    allows_file_edits = normalized_authorization != "local_readonly"
    allows_commit = normalized_authorization in {"local_mutating", "local_test_heavy", "external_network"}
    requires_commit = effective == "commit"
    requires_report = effective == "report"
    report_only = effective == "report" and not allows_file_edits

    return EffectiveTaskPolicy(
        requested_completion_policy=requested,
        effective_completion_policy=effective,
        authorization_profile=normalized_authorization,
        allows_file_edits=allows_file_edits,
        allows_commit=allows_commit,
        requires_commit=requires_commit,
        requires_report=requires_report,
        requires_machine_evidence=True,
        report_only=report_only,
        source=source,
        controller=controller_id,
    )


def derive_effective_task_safety(policy: EffectiveTaskPolicy) -> EffectiveTaskSafety:
    return EffectiveTaskSafety(
        is_mutating=policy.allows_file_edits,
        is_concurrency_safe=not policy.allows_file_edits,
    )


def normalize_success_phase(phase: str) -> str:
    if phase in {"evidence_ready", "committed", "ready_for_review"}:
        return "ready_for_review"
    return phase


def normalize_receipt_status(status: str | None) -> str:
    value = (status or "").strip().upper()
    if value == "READY_FOR_REVIEW":
        return messages.EVIDENCE_PACK
    return value


def evidence_from_receipt_and_paths(
    receipt: Mapping[str, Any] | None,
    *,
    stdout_path: str | None = None,
    stderr_path: str | None = None,
    receipt_path: str | None = None,
    report_path: str | None = None,
    evidence_path: str | None = None,
    output_excerpt: str | None = None,
    receipt_valid: bool = False,
) -> ExecutionEvidence:
    payload = receipt.get("payload") if isinstance(receipt, Mapping) else None
    if not isinstance(payload, Mapping):
        payload = {}
    changed = payload.get("changed_files")
    if isinstance(changed, str):
        changed_files = (changed,) if changed.strip() else ()
    elif isinstance(changed, list):
        changed_files = tuple(str(item) for item in changed if isinstance(item, str) and item.strip())
    else:
        changed_files = ()
    validation_present = bool(payload.get("validation")) or bool(payload.get("validation_not_run"))
    commit_ref = payload.get("commit_ref") or payload.get("commit") or payload.get("commit_sha")
    if commit_ref is not None and not isinstance(commit_ref, str):
        commit_ref = str(commit_ref)
    report_text = _string_value(payload.get("report"))
    status = receipt.get("status") if isinstance(receipt, Mapping) else None
    return ExecutionEvidence(
        stdout_path=stdout_path or _string_value(payload.get("raw_log_path")),
        stderr_path=stderr_path or _string_value(payload.get("stderr_log_path")),
        receipt_path=receipt_path or _string_value(payload.get("receipt_path")),
        report_path=report_path or _string_value(payload.get("report_path")),
        evidence_path=evidence_path,
        output_excerpt=output_excerpt,
        report_text=report_text,
        commit_ref=commit_ref,
        changed_files=changed_files,
        validation_present=validation_present,
        receipt_valid=receipt_valid,
        structured_status=_string_value(status),
    )


def evaluate_completion(
    *,
    effective_policy: EffectiveTaskPolicy,
    receipt: Mapping[str, Any] | None,
    evidence: ExecutionEvidence,
    process_returncode: int | None = None,
    structured_receipt_ok: bool = True,
) -> CompletionDecision:
    status = normalize_receipt_status(_string_value(receipt.get("status")) if isinstance(receipt, Mapping) else None)
    payload = receipt.get("payload") if isinstance(receipt, Mapping) else None
    if not isinstance(payload, Mapping):
        payload = {}
    summary = _string_value(receipt.get("summary")) if isinstance(receipt, Mapping) else None
    summary = summary or _string_value(payload.get("message")) or "terminal decision"

    if status == messages.BLOCKED:
        blocker_type = _string_value(payload.get("blocker_type")) or "unknown"
        return CompletionDecision(
            phase="blocked",
            ok=False,
            summary=summary,
            blocker_type=blocker_type,
            terminal_status=status,
            receipt=dict(receipt) if isinstance(receipt, Mapping) else None,
            effective_policy=effective_policy,
            reason_code=blocker_type,
        )

    if not status and not evidence.has_machine_evidence and not evidence.has_output and process_returncode is not None:
        if effective_policy.requires_report:
            return CompletionDecision(
                phase="blocked",
                ok=False,
                summary="executor exited without report or terminal receipt",
                blocker_type="report_output_missing",
                terminal_status=status or None,
                receipt=dict(receipt) if isinstance(receipt, Mapping) else None,
                effective_policy=effective_policy,
                reason_code="report_output_missing",
            )
        return CompletionDecision(
            phase="blocked",
            ok=False,
            summary="executor output missing",
            blocker_type="output_missing",
            terminal_status=status or None,
            receipt=dict(receipt) if isinstance(receipt, Mapping) else None,
            effective_policy=effective_policy,
            reason_code="output_missing",
        )

    scope_violations = payload.get("scope_violations")
    if isinstance(scope_violations, list) and scope_violations:
        return CompletionDecision(
            phase="blocked",
            ok=False,
            summary="structured receipt reported scope violations",
            blocker_type="validation_failure",
            terminal_status=status or None,
            receipt=dict(receipt) if isinstance(receipt, Mapping) else None,
            effective_policy=effective_policy,
            reason_code="scope_violations",
        )

    if not structured_receipt_ok:
        return CompletionDecision(
            phase="blocked",
            ok=False,
            summary="structured receipt failed validation",
            blocker_type="validation_failure",
            terminal_status=status or None,
            receipt=dict(receipt) if isinstance(receipt, Mapping) else None,
            effective_policy=effective_policy,
            reason_code="receipt_validation_failed",
        )

    if effective_policy.requires_commit and not evidence.has_commit:
        return CompletionDecision(
            phase="blocked",
            ok=False,
            summary="commit policy requires a task-specific commit",
            blocker_type="missing_commit_for_commit_policy",
            terminal_status=status or None,
            receipt=dict(receipt) if isinstance(receipt, Mapping) else None,
            effective_policy=effective_policy,
            reason_code="missing_commit_for_commit_policy",
        )

    if effective_policy.requires_report and not evidence.has_report:
        return CompletionDecision(
            phase="blocked",
            ok=False,
            summary="report policy requires captured report output",
            blocker_type="report_output_missing",
            terminal_status=status or None,
            receipt=dict(receipt) if isinstance(receipt, Mapping) else None,
            effective_policy=effective_policy,
            reason_code="report_output_missing",
        )

    if effective_policy.requires_machine_evidence and not evidence.has_machine_evidence:
        return CompletionDecision(
            phase="blocked",
            ok=False,
            summary="machine-readable evidence missing",
            blocker_type="evidence_missing",
            terminal_status=status or None,
            receipt=dict(receipt) if isinstance(receipt, Mapping) else None,
            effective_policy=effective_policy,
            reason_code="evidence_missing",
        )

    if process_returncode not in (None, 0) and status not in {messages.COMMITTED, messages.EVIDENCE_PACK}:
        return CompletionDecision(
            phase="blocked",
            ok=False,
            summary=summary or f"executor exited with code {process_returncode}",
            blocker_type="execution_error",
            terminal_status=status or None,
            receipt=dict(receipt) if isinstance(receipt, Mapping) else None,
            effective_policy=effective_policy,
            reason_code="execution_error",
        )

    normalized_receipt = dict(receipt) if isinstance(receipt, Mapping) else {}
    normalized_receipt["status"] = status if status in {messages.COMMITTED, messages.EVIDENCE_PACK} else messages.EVIDENCE_PACK
    return CompletionDecision(
        phase="ready_for_review",
        ok=True,
        summary=summary,
        terminal_status=normalized_receipt["status"],
        receipt=normalized_receipt,
        effective_policy=effective_policy,
        reason_code="ready_for_review",
    )


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
