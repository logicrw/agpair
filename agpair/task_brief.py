from __future__ import annotations

from dataclasses import dataclass

READONLY_HINTS = (
    "read-only",
    "read only",
    "report-only",
    "report only",
    "do not edit",
    "do not modify",
    "no file edits",
    "no code changes",
    "只读",
    "不要修改",
    "不修改",
    "禁止写",
    "无代码改动",
    "无，禁止写入",
)
REQUIRED_SECTIONS = ("goal", "scope", "required changes", "exit criteria")
TRIVIAL_PLACEHOLDERS = {"bar", "foo", "todo", "fix this", "test"}


@dataclass(frozen=True, slots=True)
class TaskBrief:
    body: str
    normalized_body: str
    auto_structured: bool
    warnings: tuple[str, ...] = ()


class TaskBriefError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def is_report_only_brief(*, body: str, authorization_profile: str, completion_policy: str) -> bool:
    lower_body = body.lower()
    return (
        authorization_profile == "local_readonly"
        or completion_policy.strip().lower().replace("-", "_") == "report"
        or any(hint in lower_body for hint in READONLY_HINTS)
    )


def normalize_task_brief(*, body: str, authorization_profile: str, completion_policy: str) -> TaskBrief:
    trimmed = body.strip()
    if not trimmed:
        raise TaskBriefError("empty_body", "Task body is empty.")
    if trimmed.lower() in TRIVIAL_PLACEHOLDERS:
        raise TaskBriefError("placeholder_body", "Task body looks like a trivial placeholder.")

    missing = _missing_sections(trimmed)
    if not missing:
        return TaskBrief(body=trimmed, normalized_body=trimmed, auto_structured=False)

    report_only = is_report_only_brief(
        body=trimmed,
        authorization_profile=authorization_profile,
        completion_policy=completion_policy,
    )
    required_changes = (
        "None. This is report-only. Do not edit files."
        if report_only
        else "Make the smallest useful change needed for the original brief. Keep edits scoped to the requested repository and files."
    )
    exit_criteria = (
        "Return the requested answer or report with evidence paths when available. Confirm that no files were edited."
        if report_only
        else "Return a concise summary, changed files when known, validation or validation_not_run when available, and raw evidence paths."
    )
    normalized = "\n\n".join(
        [
            "Goal:\n" + _first_non_empty_line(trimmed),
            (
                "Scope:\nUse the requested repository path and the files, commands, "
                "or evidence boundaries named in the original brief. Do not expand beyond that scope without saying so."
            ),
            "Required changes:\n" + required_changes,
            "Exit criteria:\n" + exit_criteria,
            "Original brief:\n" + trimmed,
        ]
    )
    return TaskBrief(
        body=trimmed,
        normalized_body=normalized,
        auto_structured=True,
        warnings=tuple(f"auto_added_{section.replace(' ', '_')}" for section in missing),
    )


def _missing_sections(body: str) -> list[str]:
    lower_body = body.lower()
    return [section for section in REQUIRED_SECTIONS if section not in lower_body]


def _first_non_empty_line(body: str) -> str:
    for line in body.splitlines():
        stripped = line.strip(" \t#:-")
        if stripped:
            return stripped
    return body.strip()
