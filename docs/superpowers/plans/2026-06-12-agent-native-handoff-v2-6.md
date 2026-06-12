# Agent-Native Handoff V2.6 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make AGPair external executors feel practically indistinguishable from native subagents: controllers can hand off natural tasks, external agents can use their normal capabilities, and AGPair only enforces safety, evidence, and adoption boundaries instead of blocking useful agent work on protocol friction.

**Architecture:** Keep the V2.5 model: `managed-natural`, inherited skills/MCP/provider config, pluggable executor policy, lease-based waiting, isolated worktree adoption, and controller-side verification. Refactor admission, completion, and adoption into a "useful result first" pipeline: normalize the task brief, run the executor, collect artifacts, classify whether there is usable work, then expose a simple controller action. Protocol shape is metadata, not the main success condition. Safety, scope, authorization, apply-check, and evidence existence remain hard gates.

**Tech Stack:** Python 3.12, Typer, SQLite, AGPair local CLI executors, terminal receipts, git worktrees, git diff/apply, pytest, real executor smoke harness, Codex / Claude Code skills.

---

## 0. Plan Contract

This plan follows `2026-06-11-native-feel-external-agent-v2-5.md`. V2.5 proved that the active external executors can run real report and implementation tasks, but it also exposed the last product gap: AGPair still sometimes treats imperfect protocol output as a failed task even when there is useful work.

This plan must not add a heavy orchestration layer. It must remove friction and simplify the model.

Hard requirements:

- External executors remain first-class and equal. Do not add executor-specific special rules except adapter-level command syntax and health probes.
- Default executor environment stays `managed-natural`: inherit normal skills, MCP, memory, plugins, and provider config.
- Do not reintroduce `fast`, `managed-restricted`, `isolated-bare`, capability bundles, or hidden degraded launch modes.
- Do not require project-specific targets. Repo/path autodiscovery stays the default.
- Do not make structured receipt perfection a success requirement.
- Do not make `validation` text mandatory when `diff + scope + apply-check` already prove a bounded implementation result is usable.
- Do not let unsafe or unbounded work pass: authorization, scope, forbidden files, broad repo guardrails, isolated worktree boundaries, and apply-check remain real gates.
- Do not call raw model thoughts, bootstrap logs, or cancellation metadata a completed report.
- Do not silently abandon long-running complex work just because it does not produce a quick final answer.

The product test for this plan is:

```text
Would Codex or Claude Code prefer using AGPair for ordinary delegated work?
```

The answer is only "yes" if AGPair returns a clear, low-effort controller action:

```text
use_result
review_then_apply
wait_background
retry_same_executor
switch_executor
native_fallback
```

## 1. Current Evidence And Remaining Gaps

### 1.1 What Already Works

The current V2.5 branch already has these useful foundations:

- `task start` auto-structures normal short briefs instead of refusing them.
- Report-only policy no longer requires commits, changed files, or validation fields.
- Terminal receipt parsing tolerates mixed text + JSON and wrapped JSON.
- External executor environments default to `managed-natural`.
- `watch` / `wait` can avoid token-burning controller polling.
- Isolated implementation smoke can produce `ready_for_review` with `apply_check_ok=true`.
- `task diff` / `task apply --check` make isolated worker changes reviewable from the controller repo.

### 1.2 What Still Feels Unlike Native Subagents

Remaining blockers:

1. Implementation results with low-risk protocol warnings such as `mixed_text_json` or `wrapped_text_json` still become `adoptable_result=partial` even when `apply_check_ok=true`.
2. Non-zero executor exit with useful human output or diff is treated too much like total failure.
3. Raw stdout that contains only model thoughts, startup text, or `stopReason=Cancelled` can be copied into `report.md` and counted as `has_report=true`.
4. Missing `validation` is still over-weighted. If the worker produced a diff, scope is OK, and `git apply --check` passes, missing validation should be a warning, not an adoption blocker.
5. If a worker edits files but fails to print a perfect receipt, AGPair should infer changed files from git rather than require the worker to declare them.
6. `blocked` still mixes three very different meanings: unsafe, unavailable, and imperfect-but-salvageable.
7. Smoke reports currently allow executor identity to be missing in summary rows, making policy statistics harder than necessary.
8. `agpair/cli/task.py` and `agpair/executors/local_cli.py` still carry too much mixed responsibility, which makes admission and terminal arbitration harder to reason about.

## 2. Target Mental Model

### 2.1 AGPair's Job

AGPair should do only mechanism work:

- choose an executor according to policy;
- launch it in the right repo/worktree with the right authorization profile;
- capture stdout, stderr, receipt, report, diff, and metadata;
- enforce authorization and scope boundaries;
- check whether code changes can be applied;
- summarize the result into one controller action.

AGPair should not do agent work:

- do not judge whether an analysis is intellectually brilliant;
- do not require every agent to speak AGPair's preferred receipt dialect perfectly;
- do not force every task into commit-shaped or validation-shaped output;
- do not downgrade an otherwise usable result because JSON was wrapped in text.

### 2.2 Hard Gates vs Soft Signals

Hard gates block adoption:

```text
empty or placeholder task
executor unavailable
executor auth required
approval required / authorization violation
broad unsafe repo path without override
scope violation or forbidden file change
missing report for report-only tasks
missing diff/report/evidence for evidence tasks
apply-check failure for isolated implementation
process crash with no usable artifact
```

Soft signals never block by themselves:

```text
mixed text + JSON
wrapped JSON
schema version alias
status alias
missing validation text when apply-check passes
missing changed_files when git diff can infer them
non-zero exit with usable report or usable diff
stderr plugin/bootstrap noise when useful output exists
```

### 2.3 Result Vocabulary

Expose a native-agent-like result in status JSON while keeping backward-compatible fields:

```json
{
  "agent_result": {
    "state": "usable",
    "controller_action": "review_then_apply",
    "summary": "Worker produced an isolated diff that applies cleanly.",
    "hard_blockers": [],
    "soft_warnings": ["wrapped_text_json", "validation_missing"],
    "evidence_paths": {
      "report": ".../report.md",
      "diff": ".../diff.patch",
      "receipt": ".../receipt.json",
      "stdout": ".../stdout.log",
      "stderr": ".../stderr.log"
    }
  },
  "adoption_result": {
    "adoptable_result": "yes"
  }
}
```

Canonical states:

| `agent_result.state` | Backward-compatible `adoptable_result` | Meaning |
| --- | --- | --- |
| `usable` | `yes` | Controller can directly use or review/apply the result through normal verification. |
| `needs_review` | `partial` | Useful artifact exists, but controller must inspect a warning, missing validation, or salvage path. |
| `blocked` | `no` | No useful result, or a hard gate failed. |

The controller should mostly look at `agent_result.controller_action`, not internal protocol details.

## 3. File And Responsibility Plan

### Keep And Refactor

- `agpair/cli/task.py`
  - Keep CLI commands.
  - Move task brief normalization and status action derivation into focused modules.
- `agpair/executors/local_cli.py`
  - Keep executor launch and process polling.
  - Move terminal arbitration and useful artifact classification out of the executor adapter.
- `agpair/completion.py`
  - Keep policy completion gates.
  - Stop treating protocol shape as the same thing as useful result quality.
- `agpair/adoption.py`
  - Become the main "can the controller use this?" model.
  - Add warning severity and `agent_result` derivation.
- `agpair/task_terminal.py`
  - Remain the glue that writes terminal artifacts and task state.
  - Call the new useful-result classifier before deciding terminal phase.
- `agpair/terminal_receipts.py`
  - Keep receipt normalization.
  - Keep low-risk warning metadata.
- `agpair/worktree_adoption.py`
  - Remain the source of truth for isolated diff and apply-check.

### Add Focused Modules

- Create `agpair/task_brief.py`
  - Normalize natural controller briefs.
  - Reject only empty/trivial/unsafe admission cases.
- Create `agpair/agent_result.py`
  - Convert completion/adoption/protocol/scope/apply evidence into `agent_result`.
  - Classify hard blockers vs soft warnings.
- Create `agpair/terminal_arbitration.py`
  - Convert process exit, receipt, stdout/stderr, report extraction, and git evidence into a terminal artifact candidate.

These modules should be small. If a helper does not make the code easier to reason about, do not add it.

## 4. Task 1: Lock The Current Hidden Bugs Into Tests

**Files:**

- Create: `tests/fixtures/executor_outputs/grok_max_turns_thought_only_stdout.json`
- Create: `tests/fixtures/executor_outputs/report_after_nonzero_exit_stdout.txt`
- Create: `tests/fixtures/executor_outputs/implementation_wrapped_json_ready.txt`
- Modify: `tests/unit/test_adoption_result.py`
- Modify: `tests/unit/test_completion_policy.py`
- Modify: `tests/unit/test_receipt_validation.py`
- Modify: `tests/unit/test_local_cli_executor_isolated.py`
- Modify: `tests/integration/test_real_executor_smoke_harness.py`

- [ ] **Step 1: Add a thought-only Grok output fixture**

Create `tests/fixtures/executor_outputs/grok_max_turns_thought_only_stdout.json`:

```json
{
  "text": "Reviewing admission, acceptance, and adoption flows in the scoped files to identify UX friction for external CLI agents.\n",
  "stopReason": "Cancelled",
  "sessionId": "SESSION",
  "requestId": "REQUEST",
  "thought": "The user wants me to review the current AGPair admission and acceptance/adoption mechanisms. I need to continue reading more files."
}
```

Expected classification:

```text
has_raw_output=true
has_report=false
hard_blocker=report_output_missing
agent_result.state=blocked
controller_action=retry_same_executor or switch_executor
```

Rationale: raw model thought is evidence, not a completed report.

- [ ] **Step 2: Add a non-zero exit with real report fixture**

Create `tests/fixtures/executor_outputs/report_after_nonzero_exit_stdout.txt`:

```text
Here is the requested review.

Findings:
- Admission should reject only empty or unsafe tasks.
- Completion should treat low-risk receipt variants as warnings.

Evidence:
- agpair/adoption.py
- agpair/completion.py
```

Expected classification:

```text
has_report=true
exit_code=1
hard_blockers=[]
soft_warnings=["executor_nonzero_exit"]
agent_result.state=needs_review
controller_action=use_result
```

- [ ] **Step 3: Add an implementation wrapped JSON fixture**

Create `tests/fixtures/executor_outputs/implementation_wrapped_json_ready.txt`:

```text
I made the requested bounded change.

{"schema_version":"1.0.0","task_id":"TASK-WRAPPED","attempt_no":1,"review_round":0,"status":"ready_for_review","summary":"Implemented bounded change","payload":{"report":"Implemented the bounded change.","changed_files":["agpair/example.py"],"validation_not_run":"not run in fixture","scope_violations":[],"raw_log_path":"stdout.log","receipt_path":"receipt.json"}}
```

Expected classification:

```text
protocol_warnings include mixed_text_json or schema_version_alias
agent_result.state=usable when scope and apply-check pass
adoptable_result=yes
```

- [ ] **Step 4: Add adoption warning severity tests**

In `tests/unit/test_adoption_result.py`, add tests with exact expectations:

```python
def test_low_risk_protocol_warning_does_not_demote_applyable_implementation() -> None:
    decision = derive_adoption_decision(
        effective_policy=_policy("evidence"),
        receipt={
            "status": "EVIDENCE_PACK",
            "payload": {
                "changed_files": ["agpair/example.py"],
                "validation_not_run": "fixture",
                "scope_violations": [],
            },
        },
        stdout_path=None,
        report_path=None,
        receipt_path="receipt.json",
        changed_files_present=True,
        git_status_summary=" M agpair/example.py",
        scope_validation={"ok": True},
        protocol_warnings=("wrapped_text_json",),
        protocol_errors=(),
        controller_rework="none",
    )

    assert decision.adoptable_result == "yes"
    assert decision.blockers == ()
    assert decision.warnings == ("wrapped_text_json",)
```

```python
def test_missing_validation_is_needs_review_not_blocked_when_diff_is_applyable() -> None:
    decision = derive_adoption_decision(
        effective_policy=_policy("evidence"),
        receipt={
            "status": "EVIDENCE_PACK",
            "payload": {
                "changed_files": ["agpair/example.py"],
                "scope_violations": [],
            },
        },
        stdout_path=None,
        report_path=None,
        receipt_path="receipt.json",
        changed_files_present=True,
        git_status_summary=" M agpair/example.py",
        scope_validation={"ok": True},
        protocol_warnings=(),
        protocol_errors=(),
        controller_rework="none",
    )

    assert decision.adoptable_result == "partial"
    assert decision.blockers == ("validation_missing",)
```

The second test intentionally remains `partial` until Task 5 wires apply-check evidence into `agent_result`; after Task 5, `agent_result.state` should become `needs_review`, not `blocked`.

- [ ] **Step 5: Add smoke harness assertions for executor identity**

In `tests/integration/test_real_executor_smoke_harness.py`, add an assertion that every result row includes the resolved executor id:

```python
assert result["executor"] in {"antigravity-cli", "grok-cli", "claude-code", "codex"}
```

Expected current failure: existing JSON summaries may contain `executor: null`.

- [ ] **Step 6: Run focused tests and capture failures**

Run:

```bash
PYTHONPATH=. pytest \
  tests/unit/test_adoption_result.py \
  tests/unit/test_completion_policy.py \
  tests/unit/test_receipt_validation.py \
  tests/unit/test_local_cli_executor_isolated.py \
  tests/integration/test_real_executor_smoke_harness.py -q
```

Expected before implementation: at least the new low-risk warning and executor identity tests fail.

## 5. Task 2: Extract Natural Task Brief Admission

**Files:**

- Create: `agpair/task_brief.py`
- Modify: `agpair/cli/task.py`
- Modify: `tests/integration/test_task_start_and_status.py`
- Modify: `skills/Codex/SKILL.md`
- Modify: `skills/Claude/SKILL.md`

- [ ] **Step 1: Create `TaskBrief` and `normalize_task_brief`**

Create `agpair/task_brief.py`:

```python
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


@dataclass(frozen=True)
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

    lower_body = trimmed.lower()
    missing = [section for section in REQUIRED_SECTIONS if section not in lower_body]
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
    first_line = next((line.strip(" \t#:-") for line in trimmed.splitlines() if line.strip(" \t#:-")), trimmed)
    normalized = "\n\n".join(
        [
            "Goal:\n" + first_line,
            "Scope:\nUse the requested repository path and the files, commands, or evidence boundaries named in the original brief. Do not expand beyond that scope without saying so.",
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
```

- [ ] **Step 2: Wire `task start` to the extracted normalizer**

In `agpair/cli/task.py`, replace `_auto_structure_task_body`, `_missing_task_body_sections`, `_is_report_only_brief`, and `_task_goal_from_freeform` with imports from `agpair.task_brief`.

Expected behavior:

```text
empty body -> refused
placeholder -> refused
normal short Chinese or English brief -> accepted and normalized
missing sections -> warning metadata, not rejection
```

- [ ] **Step 3: Add admission tests for natural briefs**

In `tests/integration/test_task_start_and_status.py`, add:

```python
def test_task_start_accepts_natural_readonly_brief(cli_runner, tmp_path):
    result = cli_runner.invoke(
        app,
        [
            "task",
            "start",
            "--repo-path",
            str(tmp_path),
            "--executor",
            "grok-cli",
            "--authorization-profile",
            "local_readonly",
            "--completion-policy",
            "report",
            "--body",
            "帮我审查这个目录里的实现风险，不要修改文件。",
            "--no-wait",
        ],
    )

    assert result.exit_code == 0
    assert "Refused" not in result.output
```

```python
def test_task_start_still_refuses_placeholder_body(cli_runner, tmp_path):
    result = cli_runner.invoke(
        app,
        [
            "task",
            "start",
            "--repo-path",
            str(tmp_path),
            "--executor",
            "grok-cli",
            "--authorization-profile",
            "local_readonly",
            "--completion-policy",
            "report",
            "--body",
            "todo",
            "--no-wait",
        ],
    )

    assert result.exit_code != 0
    assert "placeholder" in result.output.lower()
```

- [ ] **Step 4: Update controller skills**

In `skills/Codex/SKILL.md` and `skills/Claude/SKILL.md`, replace strict wording that implies controllers must always manually build a perfect canonical brief.

Use this wording:

```text
Controllers should provide a clear natural brief. Structured Goal/Scope/Required changes/Exit criteria sections are recommended for complex mutating work, but AGPair normalizes ordinary briefs and should not reject useful tasks merely because a section heading is missing.
```

- [ ] **Step 5: Run admission tests**

Run:

```bash
PYTHONPATH=. pytest tests/integration/test_task_start_and_status.py -q
```

Expected: all tests pass.

## 6. Task 3: Add `agent_result` As The Controller-Facing Outcome

**Files:**

- Create: `agpair/agent_result.py`
- Modify: `agpair/adoption.py`
- Modify: `agpair/cli/task.py`
- Modify: `agpair/task_terminal.py`
- Modify: `tests/unit/test_adoption_result.py`
- Modify: `tests/integration/test_task_start_and_status.py`

- [ ] **Step 1: Create the controller-facing result model**

Create `agpair/agent_result.py`:

```python
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal


AgentResultState = Literal["usable", "needs_review", "blocked"]
ControllerAction = Literal[
    "use_result",
    "review_then_apply",
    "wait_background",
    "retry_same_executor",
    "switch_executor",
    "native_fallback",
    "inspect_logs",
]

LOW_RISK_PROTOCOL_WARNINGS = {
    "mixed_text_json",
    "wrapped_text_json",
    "schema_version_alias",
    "status_alias",
    "artifact_path_missing",
}

HARD_BLOCKERS = {
    "approval_required",
    "executor_unavailable",
    "executor_auth_required",
    "authorization_policy_mismatch",
    "scope_violations",
    "forbidden_changed_files",
    "apply_conflict",
    "report_output_missing",
    "evidence_missing",
    "output_missing",
    "no_progress_budget_exceeded",
}


@dataclass(frozen=True)
class AgentResult:
    state: AgentResultState
    controller_action: ControllerAction
    summary: str
    hard_blockers: tuple[str, ...] = ()
    soft_warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return asdict(self)


def warning_is_low_risk(warning: str) -> bool:
    return warning in LOW_RISK_PROTOCOL_WARNINGS


def blocker_is_hard(blocker: str) -> bool:
    return blocker in HARD_BLOCKERS
```

- [ ] **Step 2: Derive `agent_result` from adoption evidence**

In `agpair/adoption.py`, add a method or helper that converts the existing `AdoptionDecision` into `AgentResult`.

Required rules:

```text
hard blocker present -> blocked
report policy + has_report + no hard blocker -> usable
evidence policy + scope ok + changed files or diff + no hard blocker -> usable
evidence policy + useful diff/report but missing validation -> needs_review
only low-risk protocol warnings -> stay usable
unknown executor error + no usable artifact -> blocked
```

Backward compatibility:

```text
usable       -> adoptable_result=yes
needs_review -> adoptable_result=partial
blocked      -> adoptable_result=no
```

- [ ] **Step 3: Store `agent_result` inside existing adoption evidence JSON**

Do not add a new table or column for V2.6. Existing `task_attempts.adoption_evidence_json` is the right persistence surface because `agent_result` is the controller-facing interpretation of adoption evidence, not a separate lifecycle record.

Where `task_terminal.py` currently calls `update_attempt_adoption(... adoption_evidence_json=...)`, include:

```python
adoption_payload = adoption_decision.to_dict()
adoption_payload["agent_result"] = agent_result.to_dict()
```

This keeps the storage model simple and avoids another migration for a derived field.

- [ ] **Step 4: Surface `agent_result` in `task status --json`**

In `agpair/cli/task.py`, include:

```json
"agent_result": {
  "state": "usable",
  "controller_action": "review_then_apply",
  "summary": "...",
  "hard_blockers": [],
  "soft_warnings": ["wrapped_text_json"]
}
```

Keep existing `adoption_result` fields so older skills continue to work.

- [ ] **Step 5: Add status tests**

In `tests/integration/test_task_start_and_status.py`, assert:

```python
payload = json.loads(result.output)
assert payload["agent_result"]["state"] in {"usable", "needs_review", "blocked"}
assert payload["agent_result"]["controller_action"]
assert "adoption_result" in payload
```

- [ ] **Step 6: Run outcome tests**

Run:

```bash
PYTHONPATH=. pytest tests/unit/test_adoption_result.py tests/integration/test_task_start_and_status.py -q
```

Expected: all tests pass.

## 7. Task 4: Stop Letting Low-Risk Protocol Warnings Demote Usable Results

**Files:**

- Modify: `agpair/adoption.py`
- Modify: `agpair/terminal_receipts.py`
- Modify: `tests/unit/test_adoption_result.py`
- Modify: `tests/unit/test_receipt_validation.py`
- Modify: `scripts/smoke_real_executors.py`

- [ ] **Step 1: Classify protocol warnings by severity**

In `agpair/adoption.py`, treat warnings from `LOW_RISK_PROTOCOL_WARNINGS` as metadata only.

Implementation rule:

```python
low_risk_warnings = tuple(w for w in warnings if warning_is_low_risk(w))
blocking_warnings = tuple(w for w in warnings if not warning_is_low_risk(w))
```

Only `blocking_warnings` may demote `usable` to `needs_review`.

- [ ] **Step 2: Update evidence policy adoption**

Change evidence-policy logic from:

```python
return AdoptionDecision("partial" if warnings else "yes", (), warnings, evidence, controller_rework)
```

to:

```python
result = "partial" if blocking_warnings else "yes"
return AdoptionDecision(result, (), warnings, evidence, controller_rework)
```

- [ ] **Step 3: Preserve warning visibility**

Warnings must still appear in:

```text
protocol_result.warnings
adoption_result.warnings
agent_result.soft_warnings
smoke report rows
```

Do not hide them; just stop treating them as adoption blockers.

- [ ] **Step 4: Update smoke success criteria**

In `scripts/smoke_real_executors.py`, implementation smoke should fail if:

```text
phase != ready_for_review
apply_check_ok != true
agent_result.state != usable
adoptable_result != yes
```

Low-risk protocol warnings are allowed and should be counted separately.

- [ ] **Step 5: Run focused smoke harness tests**

Run:

```bash
PYTHONPATH=. pytest tests/integration/test_real_executor_smoke_harness.py tests/unit/test_adoption_result.py -q
```

Expected: implementation smoke fixtures no longer treat wrapped/mixed JSON as partial.

## 8. Task 5: Make Completion Salvage Useful Work Without Pretending Logs Are Reports

**Files:**

- Create: `agpair/terminal_arbitration.py`
- Modify: `agpair/executors/local_cli.py`
- Modify: `agpair/task_terminal.py`
- Modify: `agpair/completion.py`
- Modify: `tests/unit/test_completion_policy.py`
- Modify: `tests/unit/test_local_cli_executor_isolated.py`

- [ ] **Step 1: Create artifact classification helpers**

Create `agpair/terminal_arbitration.py`:

```python
from __future__ import annotations

import json


THOUGHT_ONLY_KEYS = {"thought", "stopReason", "sessionId", "requestId"}
NON_REPORT_STOP_REASONS = {"Cancelled", "MaxTurns", "max_turns_reached"}


def looks_like_completed_report(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, dict):
        if "report" in parsed and isinstance(parsed["report"], str) and parsed["report"].strip():
            return True
        if "text" in parsed and isinstance(parsed["text"], str):
            stop_reason = str(parsed.get("stopReason") or "")
            thought = parsed.get("thought")
            if stop_reason in NON_REPORT_STOP_REASONS and thought:
                return False
            return len(parsed["text"].strip()) >= 80 and "reviewing " not in parsed["text"].strip().lower()
        if THOUGHT_ONLY_KEYS.intersection(parsed) and "report" not in parsed:
            return False
    lower = stripped.lower()
    if "stopreason" in lower and "thought" in lower and "findings:" not in lower:
        return False
    report_markers = ("findings:", "summary:", "conclusion:", "结论", "发现", "建议", "evidence:")
    return any(marker in lower for marker in report_markers) or len(stripped) >= 200
```

This helper is intentionally conservative: raw output is always preserved, but only completed answer-like text counts as a report.

- [ ] **Step 2: Use helper before writing `report.md`**

In `agpair/task_terminal.py`, when building `report.md` from stdout/stderr fallback:

```text
if looks_like_completed_report(stdout_excerpt):
    write report.md
else:
    keep stdout/stderr as evidence only
```

Expected behavior for `grok_max_turns_thought_only_stdout.json`:

```text
stdout_path exists
report_path may be absent or empty
evidence records raw output
completion blocks with report_output_missing for report policy
```

- [ ] **Step 3: Salvage non-zero exits with real reports**

In `agpair/executors/local_cli.py`, when `exit_code != 0`, do not automatically create a `BLOCKED` receipt if a completed report or applyable diff exists.

Required behavior:

```text
exit_code != 0 + completed report -> EVIDENCE_PACK with soft warning executor_nonzero_exit
exit_code != 0 + isolated diff exists -> EVIDENCE_PACK with soft warning executor_nonzero_exit
exit_code != 0 + only thought/log/bootstrap output -> BLOCKED execution_error
```

The receipt payload should include:

```json
{
  "exit_code": 1,
  "soft_warnings": ["executor_nonzero_exit"],
  "raw_log_path": "...",
  "stderr_log_path": "...",
  "receipt_path": "..."
}
```

- [ ] **Step 4: Keep hard blocker classification for true failures**

These remain blocked:

```text
auth failure
binary missing
approval required
scope violation
apply conflict
no report/output/diff
only bootstrap noise
```

- [ ] **Step 5: Add focused tests**

Add tests:

```python
def test_thought_only_stdout_is_not_report() -> None:
    text = Path("tests/fixtures/executor_outputs/grok_max_turns_thought_only_stdout.json").read_text()
    assert looks_like_completed_report(text) is False
```

```python
def test_nonzero_exit_with_real_report_is_salvageable() -> None:
    text = Path("tests/fixtures/executor_outputs/report_after_nonzero_exit_stdout.txt").read_text()
    assert looks_like_completed_report(text) is True
```

- [ ] **Step 6: Run terminal arbitration tests**

Run:

```bash
PYTHONPATH=. pytest tests/unit/test_completion_policy.py tests/unit/test_local_cli_executor_isolated.py -q
```

Expected: thought-only output does not satisfy report policy; real report after non-zero exit becomes usable or needs_review.

## 9. Task 6: Infer Evidence From Git Instead Of Demanding Perfect Worker Declarations

**Files:**

- Modify: `agpair/worktree_adoption.py`
- Modify: `agpair/task_terminal.py`
- Modify: `agpair/adoption.py`
- Modify: `tests/unit/test_worktree_adoption.py`
- Modify: `tests/unit/test_local_cli_executor_isolated.py`

- [ ] **Step 1: Treat git diff as source of truth for isolated implementation**

When `task.isolated_worktree=true`, AGPair should infer:

```text
changed_files
has_diff
apply_check_ok
apply_check_reason
```

from `build_worktree_diff()` and `check_apply_to_controller_repo()` even if the worker receipt omits `changed_files`.

- [ ] **Step 2: Merge inferred evidence into receipt payload**

In `agpair/task_terminal.py`, before adoption derivation:

```python
if task.isolated_worktree and effective_policy.effective_completion_policy in {"evidence", "commit"}:
    worktree_diff = build_worktree_diff(task=task, session_state=session_state)
    apply_check = check_apply_to_controller_repo(repo_path=task.repo_path, patch=worktree_diff.patch)
    payload.setdefault("changed_files", list(worktree_diff.changed_files))
    payload["worktree_diff"] = {
        "has_patch": bool(worktree_diff.patch.strip()),
        "changed_files": list(worktree_diff.changed_files),
        "apply_check_ok": apply_check.ok,
        "apply_check_reason": apply_check.reason,
    }
```

- [ ] **Step 3: Make missing validation a soft warning when apply-check passes**

Adoption rule:

```text
scope ok + diff exists + apply-check ok + validation missing
  -> agent_result.state=needs_review
  -> adoptable_result=partial
  -> controller_action=review_then_apply
```

If validation exists:

```text
scope ok + diff exists + apply-check ok + validation exists
  -> agent_result.state=usable
  -> adoptable_result=yes
  -> controller_action=review_then_apply
```

- [ ] **Step 4: Add tests for omitted `changed_files`**

In `tests/unit/test_worktree_adoption.py`, create a fixture worktree with an edited file and no receipt-declared `changed_files`. Assert:

```python
assert diff.changed_files == ("agpair/example.py",)
assert apply_check.ok is True
```

In `tests/unit/test_local_cli_executor_isolated.py`, assert the terminal payload contains inferred `changed_files`.

- [ ] **Step 5: Run worktree tests**

Run:

```bash
PYTHONPATH=. pytest tests/unit/test_worktree_adoption.py tests/unit/test_local_cli_executor_isolated.py tests/unit/test_adoption_result.py -q
```

Expected: inferred diff makes imperfect worker receipts adoptable.

## 10. Task 7: Simplify Controller Actions In Status, Wait, And Watch

**Files:**

- Modify: `agpair/cli/task.py`
- Modify: `agpair/cli/wait.py`
- Modify: `agpair/watch.py`
- Modify: `tests/integration/test_task_wait.py`
- Modify: `tests/integration/test_task_watch_events.py`
- Modify: `docs/usage.md`
- Modify: `docs/usage.zh-CN.md`

- [ ] **Step 1: Make `controller_action` come from `agent_result` when terminal**

For terminal tasks:

```text
usable report -> use_result
usable implementation diff -> review_then_apply
needs_review report -> use_result
needs_review diff -> review_then_apply
blocked auth/unavailable -> switch_executor or native_fallback
blocked report missing -> retry_same_executor or switch_executor
blocked apply conflict -> inspect_logs
```

For non-terminal tasks, keep live signal actions:

```text
active and lease valid -> continue_waiting
active and lease expired -> wait_background
soft no progress but budget remains -> wait_background or retry_same_executor
budget expired -> switch_executor or native_fallback
```

- [ ] **Step 2: Update `wait --json`**

`agpair task wait --json` should always include:

```json
{
  "outcome": "terminal|controller_lease_expired|soft_no_progress|timeout",
  "agent_result": {},
  "recommended_action": "review_then_apply",
  "status_command": "agpair task status TASK --json",
  "evidence_commands": [
    "agpair task logs TASK --include-executor-output",
    "agpair task diff TASK",
    "agpair task apply TASK --check"
  ]
}
```

- [ ] **Step 3: Update `watch --json`**

`watch --json` should emit compact state changes:

```json
{
  "event": "agent_result_changed",
  "task_id": "TASK",
  "agent_result": {
    "state": "usable",
    "controller_action": "review_then_apply"
  }
}
```

Do not stream full logs by default.

- [ ] **Step 4: Add wait/watch tests**

In `tests/integration/test_task_wait.py`, assert terminal payload includes `recommended_action`.

In `tests/integration/test_task_watch_events.py`, assert watch emits `agent_result_changed` when a terminal adoption result appears.

- [ ] **Step 5: Run wait/watch tests**

Run:

```bash
PYTHONPATH=. pytest tests/integration/test_task_wait.py tests/integration/test_task_watch_events.py -q
```

Expected: JSON surfaces tell the controller what to do next without requiring manual receipt interpretation.

## 11. Task 8: Reduce Large-File Coupling Without A Rewrite

**Files:**

- Modify: `agpair/cli/task.py`
- Modify: `agpair/executors/local_cli.py`
- Create or update: `agpair/task_brief.py`
- Create or update: `agpair/agent_result.py`
- Create or update: `agpair/terminal_arbitration.py`
- Modify: `tests/integration/test_task_start_and_status.py`
- Modify: `tests/unit/test_local_cli_executor_isolated.py`

- [ ] **Step 1: Move only cohesive units**

Extract these responsibilities:

```text
task brief normalization -> agpair/task_brief.py
agent result derivation -> agpair/agent_result.py
report-vs-log classification -> agpair/terminal_arbitration.py
```

Do not split files for aesthetics. Stop once the admission and terminal decision paths are understandable.

- [ ] **Step 2: Keep CLI behavior stable**

After extraction, these commands must still work:

```bash
PYTHONPATH=. agpair task start --help
PYTHONPATH=. agpair task status --help
PYTHONPATH=. agpair task wait --help
PYTHONPATH=. agpair task diff --help
PYTHONPATH=. agpair task apply --help
```

- [ ] **Step 3: Run import and focused CLI tests**

Run:

```bash
python -m compileall agpair
PYTHONPATH=. pytest tests/integration/test_task_start_and_status.py tests/unit/test_local_cli_executor_isolated.py -q
```

Expected: no import regressions.

## 12. Task 9: Update Controller Skills To Encourage Real External Use

**Files:**

- Modify: `skills/Codex/SKILL.md`
- Modify: `skills/Claude/SKILL.md`
- Modify: `skills/claw.json`
- Modify: `README.md`
- Modify: `README.zh-CN.md`
- Modify: `docs/getting-started.en.md`
- Modify: `docs/getting-started-zh.md`
- Modify: `docs/usage.md`
- Modify: `docs/usage.zh-CN.md`

- [ ] **Step 1: Replace rigid brief language**

Use this controller guidance:

```text
For ordinary tasks, send a clear natural brief. For mutating work, include known allowed files, forbidden areas, and validation commands when you have them. AGPair will normalize the brief and should not reject useful work just because a section heading is missing.
```

- [ ] **Step 2: Encourage implementation delegation, not only review**

Add this guidance:

```text
For non-trivial implementation, refactor, or test-fix work, prefer one bounded AGPair implementation slice before native subagents unless the work is tiny, sensitive, or tightly coupled to the current controller context. Use `--isolated-worktree`, then inspect `agent_result`, `task diff`, and `task apply --check`.
```

- [ ] **Step 3: Make native fallback acceptable**

Add:

```text
External-first does not mean external-only. Use native subagents when AGPair is unavailable, when an external result is low quality, or when a native reviewer is the fastest verification lane.
```

- [ ] **Step 4: Document the new controller action vocabulary**

Docs must explain:

```text
use_result
review_then_apply
wait_background
retry_same_executor
switch_executor
native_fallback
inspect_logs
```

- [ ] **Step 5: Remove stale strictness**

Delete or rewrite wording that says:

```text
missing canonical sections are refused
implementation always requires perfect validation text
protocol warning implies partial adoption
controller should abandon external work after no quick useful signal
fast/restricted/bare launch profiles are recommended paths
```

- [ ] **Step 6: Run documentation sanity checks**

Run:

```bash
rg -n "managed-restricted|isolated-bare|fast startup|missing key structural sections|must include Goal/Scope" README.md README.zh-CN.md docs skills
rg -n "agent_result|review_then_apply|wait_background" README.md README.zh-CN.md docs skills
```

Expected:

```text
first command returns no stale recommendation hits
second command returns current behavior documentation
```

## 13. Task 10: Real Executor Verification Must Prove Native-Like Adoption

**Files:**

- Modify: `scripts/smoke_real_executors.py`
- Modify: `tests/integration/test_real_executor_smoke_harness.py`
- Update generated report only locally: `.agpair/smoke/reports/*.json`

- [ ] **Step 1: Require report smoke to prove usable result**

For each active executor in the controller policy:

```text
phase=ready_for_review
agent_result.state=usable
adoptable_result=yes
time_to_first_useful_signal_seconds recorded
executor id recorded
```

- [ ] **Step 2: Require implementation smoke to prove review/apply flow**

For implementation smoke:

```text
phase=ready_for_review
agent_result.state=usable
controller_action=review_then_apply
adoptable_result=yes
diff_available=true
apply_check_ok=true
executor id recorded
low-risk protocol warnings allowed
```

- [ ] **Step 3: Add salvage smoke fixture**

Add a fake executor scenario:

```text
exit_code=1
stdout contains completed report
no scope violation
```

Expected:

```text
agent_result.state=needs_review
controller_action=use_result
adoptable_result=partial
```

- [ ] **Step 4: Add thought-only failure fixture**

Add a fake executor scenario:

```text
exit_code=1
stdout contains only Grok thought JSON with stopReason=Cancelled
```

Expected:

```text
agent_result.state=blocked
controller_action=retry_same_executor or switch_executor
blocker_type=report_output_missing
```

- [ ] **Step 5: Run fake smoke tests**

Run:

```bash
PYTHONPATH=. pytest tests/integration/test_real_executor_smoke_harness.py -q
```

Expected: all fake smoke tests pass.

- [ ] **Step 6: Run real report smoke**

Run:

```bash
PYTHONPATH=. python scripts/smoke_real_executors.py \
  --repo-path <repo> \
  --controller codex \
  --scenario report_smoke \
  --executors antigravity-cli,grok-cli,claude-code \
  --timeout-seconds 900 \
  --no-progress-seconds 180
```

Expected:

```text
all_success=true
all selected executors attempted
each result has executor id
each result has agent_result.state=usable
each result has adoptable_result=yes
```

- [ ] **Step 7: Run real implementation smoke**

Run:

```bash
PYTHONPATH=. python scripts/smoke_real_executors.py \
  --repo-path <repo> \
  --controller codex \
  --scenario implementation_smoke \
  --executors antigravity-cli,grok-cli,claude-code \
  --timeout-seconds 1200 \
  --no-progress-seconds 240
```

Expected:

```text
all_success=true
all selected executors attempted
each result has executor id
each result has agent_result.state=usable
each result has controller_action=review_then_apply
each result has adoptable_result=yes
each result has apply_check_ok=true
```

If any real executor fails, classify the failure:

```text
executor unavailable/auth -> environment issue, not AGPair adoption failure
no useful output -> executor quality issue
protocol warning only -> AGPair bug
apply conflict -> adoption boundary issue
```

Do not hide failed real smoke; report exact task ids and artifact paths.

## 14. Task 11: Full Verification And Privacy-Safe Finish

**Files:**

- All modified source, tests, skills, and docs.

- [ ] **Step 1: Run focused tests**

Run:

```bash
PYTHONPATH=. pytest \
  tests/unit/test_adoption_result.py \
  tests/unit/test_completion_policy.py \
  tests/unit/test_receipt_validation.py \
  tests/unit/test_worktree_adoption.py \
  tests/unit/test_local_cli_executor_isolated.py \
  tests/integration/test_task_start_and_status.py \
  tests/integration/test_task_wait.py \
  tests/integration/test_task_watch_events.py \
  tests/integration/test_real_executor_smoke_harness.py -q
```

Expected: pass.

- [ ] **Step 2: Run full tests**

Run:

```bash
PYTHONPATH=. pytest -q
```

Expected: pass.

- [ ] **Step 3: Run compile and whitespace checks**

Run:

```bash
python -m compileall agpair
git diff --check
```

Expected: pass.

- [ ] **Step 4: Run real executor smoke**

Run both smoke commands from Task 10.

Expected:

```text
report_smoke all_success=true
implementation_smoke all_success=true
no executor process left running
no uncommitted smoke artifact intended for commit
```

- [ ] **Step 5: Inspect final diff**

Run:

```bash
git status -sb
git diff --stat
git diff -- README.md README.zh-CN.md docs skills | sed -n '1,240p'
```

Expected:

```text
diff is limited to AGPair source, tests, skills, docs, and this plan
docs describe current behavior only
no stale fast/restricted/bare recommendations
```

- [ ] **Step 6: Privacy scan before commit**

Run:

```bash
git diff --cached --name-only
git diff --cached | rg -n "sk-[A-Za-z0-9]|AKIA|BEGIN (RSA|OPENSSH|EC|PRIVATE) KEY|ANTHROPIC_API_KEY|OPENAI_API_KEY|XAI_API_KEY|MOONSHOT|Kimi|oauth|refresh_token|access_token|~/.claude|~/.codex"
```

Expected:

```text
no secrets
local paths appear only where documentation intentionally references local smoke examples, or are removed before commit
```

- [ ] **Step 7: Commit**

Use a scoped commit:

```bash
git add agpair tests scripts skills README.md README.zh-CN.md docs
git commit -m "feat: make external agent handoff native-like"
```

Do not commit `.agpair/smoke/reports/*.json`, executor temp dirs, raw logs, or local credentials.

## 15. Final Acceptance Criteria

The V2.6 implementation is complete only when all of these are true:

- Natural useful briefs are accepted and normalized; only empty, placeholder, unsafe, or unavailable-executor cases are refused.
- Report-only tasks require a real report, not a commit and not raw thought logs.
- Implementation tasks can be adopted from isolated git diff even when the worker receipt is imperfect.
- Low-risk protocol warnings do not demote otherwise usable results.
- Missing validation becomes `needs_review`, not `blocked`, when diff/scope/apply-check are good.
- Non-zero executor exit with real useful report or diff becomes salvageable `needs_review`, not total failure.
- Thought-only / cancelled / max-turn raw output is not mistaken for a completed report.
- `task status --json`, `wait --json`, and `watch --json` expose `agent_result` and a simple controller action.
- Real report and implementation smoke pass for `antigravity-cli`, `grok-cli`, and `claude-code` as Codex-controlled external executors.
- Controller skills make external implementation delegation feel normal, while preserving native subagents as fallback/review.
- The codebase has fewer mixed-responsibility paths for admission and terminal arbitration than before.

## 16. Design Decision Record

Accepted:

- Keep `managed-natural` as the default.
- Keep external-first, not external-only.
- Keep structured receipts as a fast path and evidence surface.
- Treat protocol variants as metadata unless they hide a real safety/evidence problem.
- Add `agent_result` as the controller-facing outcome while keeping `adoption_result` for compatibility.
- Infer implementation evidence from git and apply-check instead of trusting only worker-declared fields.

Rejected:

- Rejecting tasks because the body lacks specific section headings.
- Requiring every external agent to emit perfect JSON.
- Treating low-risk receipt formatting as a reason to downgrade adoption.
- Calling raw thoughts or cancellation metadata a report.
- Hiding native subagents or making external routing unconditional.
- Adding a new MCP/server/orchestrator layer.
- Adding more environment restriction modes to solve usability problems.

## 17. Execution Handoff

Implement this plan task-by-task. Prefer small commits after green focused tests. Use AGPair itself for bounded external review or fake-executor test generation where practical, but the main controller must verify every result before applying it.

Recommended execution order:

```text
Task 1 -> Task 2 -> Task 3 -> Task 4 -> Task 5 -> Task 6 -> Task 7 -> Task 8 -> Task 9 -> Task 10 -> Task 11
```

Do not skip Task 10. The purpose of V2.6 is not to make tests pass in isolation; it is to prove that real external agents feel close enough to native subagents that controllers naturally want to use them.
