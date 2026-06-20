from __future__ import annotations

import pathlib

from agpair.models import authorization_profile_summary, validate_authorization_profile
from agpair.task_brief import is_report_only_brief


def body_with_task_contract(
    task_id: str,
    body: str,
    *,
    execution_repo_path: str | None = None,
    authorization_profile: str = "local_mutating",
    authorization_summary: str | None = None,
    completion_policy: str = "auto",
) -> str:
    normalized_profile = validate_authorization_profile(authorization_profile)
    summary = authorization_summary or authorization_profile_summary(normalized_profile)
    execution_context = ""
    if execution_repo_path:
        repo_root = str(pathlib.Path(execution_repo_path).expanduser().resolve())
        execution_context = (
            f"Execution repository root: {repo_root}\n"
            "Before reading, editing, testing, or running git commands, work from this exact repository root. "
            "If your CLI opens in a scratch directory, cd to this path or use absolute paths under it. "
            "Do not search or modify files outside this repository unless the task explicitly says so.\n\n"
        )
    contract = (
        f"Task ID: {task_id}\n"
        f"If you create a git commit for this task, the commit message must include `{task_id}` verbatim.\n\n"
        f"{execution_context}"
        f"Authorization profile: {normalized_profile}\n"
        f"{summary}\n\n"
        "Noninteractive execution requirements:\n"
        "- This is a background AGPair task; do not wait for human confirmation, editor interaction, or approval prompts.\n"
        "- Do not start another AGPair task from inside this executor unless the controller explicitly authorized nested delegation.\n"
        "- For file edits, use deterministic repository-local shell/file operations or your CLI's noninteractive edit tools.\n"
        "- Do not describe intended work as completed; only claim changed files, validation, or success after observing actual file state and command output.\n"
        "- If you cannot continue without interaction, return a structured BLOCKED receipt instead of waiting.\n\n"
        f"{_report_only_addendum(body, normalized_profile, completion_policy)}"
        "Structured terminal receipt JSON requirements:\n"
        "- Print the requested report or conclusion directly to stdout; do not only save it to an external file, local brain, or link.\n"
        "- The final output line must be one single-line JSON terminal receipt object with schema_version, task_id, attempt_no, review_round, status, summary, and payload.\n"
        "- For report-only tasks, include the report text in payload.report; changed_files, validation, and scope_violations are not required.\n"
        "- For implementation or test-fix tasks, claim `ready_for_review` only with changed_files, validation or validation_not_run, scope_violations, raw_log_path, and receipt_path.\n"
        "- When blocked by missing permission, return blocker_type `approval_required` with requested_authorization_profile, requested_actions, authorization_delta, request_reason, risk_assessment, safe_to_retry, and raw_log_path.\n\n"
    )
    return contract + body


def _report_only_addendum(body: str, authorization_profile: str, completion_policy: str) -> str:
    if not is_report_only_brief(
        body=body,
        authorization_profile=authorization_profile,
        completion_policy=completion_policy,
    ):
        return ""
    return (
        "Report-only outcome requirements:\n"
        "- Choose the inspection strategy yourself.\n"
        "- Optimize for a useful stdout report before exhaustive exploration.\n"
        "- If context, time, or turn budget is running low, emit the best available report plus the terminal receipt instead of continuing silently.\n"
        "- Clearly state what you verified and what you did not verify.\n\n"
    )
