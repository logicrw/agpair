---
name: agpair-codex
description: "Use when Codex should delegate non-trivial coding, refactor, test-fix, research, or review work through AGPair external CLI executors before using Codex native subagents."
---

# AGPair for Codex

Default: external AGPair executor first for non-trivial work. Codex remains the controller and verifier.

Actively outsource low-value, repetitive, time-consuming, or easily verifiable work through AGPair: repo scans, alternative reviews, focused test-fix attempts, multi-file mechanical edits, smoke checks, and implementation slices with clear acceptance criteria.

Use direct Codex edits for tiny local fixes, sensitive judgment-heavy work, or when AGPair is unavailable. Use Codex native subagents only after external executors are unavailable, unsuitable, or not good enough, or for narrow controller-side review/helper work.

## Normal Task

For ordinary tasks, send a clear natural brief. AGPair normalizes useful
briefs and should not reject work merely because a section heading is missing.
Do not pass placeholders like `<brief>`, `todo`, or `fix this`.

For complex mutating work, prefer this structured shape because it gives the
external executor tighter scope and gives the controller better evidence:

```text
Goal:
State the concrete outcome.

Scope:
Allowed files/areas:
Forbidden files/areas:

Required changes:
Describe the expected edit, or say: None. This is report-only. Do not edit files.

Exit criteria:
List required verification, report format, and expected AGPair evidence.
```

```bash
agpair task start \
  --repo-path "$REPO" \
  --controller codex \
  --executor antigravity-cli \
  --task-kind quick_review \
  --wait-policy lease \
  --authorization-profile local_readonly \
  --completion-policy report \
  --body "$BRIEF"
```

`task start` waits cheaply according to the selected task kind. Lease-based tasks may return a structured background-running result while the executor continues. After completion, inspect status, diff, receipt, raw logs, and required evidence before reporting success.

Default executor environments are `managed-natural` for `antigravity-cli`, `grok-cli`, and healthy `claude-code`: AGPair manages state and evidence, while the external CLI keeps its normal skills, MCP, memory, plugins, and provider config. If an external attempt fails or is low quality, retry naturally, switch to another external executor, or use Codex native subagents as fallback/review.

AGPair external-first routing applies to controller sessions. AGPair-started executor, probe, smoke, and retry processes suppress AGPair client hooks to avoid recursive delegation, but external workers still inherit their normal CLI capabilities, skills, MCP, plugins, memory, and provider config unless an explicit diagnostic mode says otherwise.

Use `agpair policy list --controller codex --json` to inspect the effective executor order, suppression, and lifecycle state. Use `agpair policy disable/enable/priority/reset` for pluggable runtime changes instead of editing source.

## Bounded Implementation

For non-trivial implementation, refactor, or test-fix work, first dispatch one bounded slice unless the task is tiny, sensitive, external executors are unhealthy, or a prior external result was low quality:

```bash
agpair task start \
  --repo-path "$REPO" \
  --controller codex \
  --executor antigravity-cli \
  --task-kind implementation \
  --wait-policy lease \
  --authorization-profile local_mutating \
  --completion-policy evidence \
  --isolated-worktree \
  --body "$BRIEF"
```

Use a brief with explicit allowed files, forbidden files, required changes, validation command, and exit criteria. The external worker returns `changed_files`, `validation` or `validation_not_run`, `scope_violations`, report text, and raw evidence paths. Codex integrates or rejects the result in the main worktree after verification.

For isolated mutating evidence/commit tasks, AGPair defaults to `--dirty-snapshot tracked`: tracked staged/unstaged controller changes are copied into the executor worktree before launch. Ignored and untracked files are not copied; use `--dirty-snapshot off` when the worker should start from committed HEAD only.

When `--wait-policy lease` expires and the task is still alive, detach and continue or run a native reviewer in parallel. Do not abandon a complex external task solely because it has not produced a quick final report.

## Parallel Or Async

```bash
agpair task start --repo-path "$WT_A" --body "$BRIEF_A" --no-wait
agpair task start --repo-path "$WT_B" --body "$BRIEF_B" --no-wait
agpair task watch <TASK_ID> --json
agpair task wait <TASK_ID> --json
```

Each `BRIEF_*` must be clear enough to identify the goal, scope, allowed
changes, and expected evidence. Use the structured shape for mutating work
when those boundaries are known.

Do not use repeated Codex prompts as a polling loop. Use `agpair task watch <TASK_ID> --json` or `agpair task wait <TASK_ID>` for low-token waiting.

`watch --json` emits state changes and raw evidence paths; it does not stream full logs. Do not run raw executor output through lossy compression by default.

`wait --json` reports `outcome`, `agent_result`, `recommended_action`, and whether the controller wait lease expired. Treat `controller_lease_expired` and `soft_no_progress` as background-running outcomes; the controller action for those cases is effectively `wait_background` unless the task budget has expired. Inspect `task status --json` rather than burning model turns in a polling loop.

Use Codex App thread automation only for very long tasks that should wake the same thread later.

## Review And Adoption

Always inspect:

```bash
agpair task status TASK-123 --json
agpair task logs TASK-123 --include-executor-output
```

Use `agent_result` as the controller-facing outcome. Prefer `agent_result.controller_action`: `use_result` for reports, `review_then_apply` for isolated implementation diffs, `retry_or_switch_executor` for blocked attempts, and `inspect_evidence` when the evidence needs manual inspection. `protocol_result` and `adoption_result` remain compatibility/debug surfaces; do not make low-risk protocol warnings override useful evidence.

For isolated implementation or test-fix tasks, review and apply the executor diff explicitly:

```bash
agpair task diff TASK-123
agpair task apply TASK-123 --check
agpair task apply TASK-123
```

`task apply` leaves changes in the controller worktree for normal Codex review and verification. It does not auto-accept the AGPair task.

After verification, close the loop:

```bash
agpair task accept TASK-123 --adoptable-result yes --controller-rework none
```

If the protocol failed but the report/stdout is still useful, record explicit salvage instead of pretending the executor succeeded:

```bash
agpair task adopt TASK-123 --from-report --adoptable-result partial --controller-rework minor
```

## Workflows

Use `agpair workflow start` only for high-value multi-part, parallel, adversarial, or long-running work. Workflow manifests are declarative; AGPair rejects arbitrary script fields and creates normal V1.1 child tasks.

Workflow `ready_for_review` means AGPair has an evidence pack for Codex verification, not final user-facing success.

## Blocked Retry

```bash
agpair task retry TASK-123 --from-block --authorization-profile local_mutating
```

`blocked(approval_required)` is terminal in V1. Retry starts a new attempt with structured blocked context and a dispatch-time authorization profile.

## Executor Order

For Codex as controller, prefer `antigravity-cli`, then `grok-cli`, then `claude-code`.

`claude-code` is the AGPair-managed external Claude Code worker for Codex controllers. It is the cross-controller quality escalation lane, not a native Codex subagent. Its default Claude auth mode is `auto`: `agpair doctor --fresh` first verifies the local Claude Code OAuth/subscription login, then falls back to the current Claude provider selected in CC Switch. Update Claude login or the CC Switch provider if `doctor --fresh` reports `executor_auth_required` or `Invalid Authentication`. Probe timeout is not the same as auth failure; check `doctor --fresh` `last_failure_type` for `executor_probe_timeout` or `executor_hook_interference`. API-key worker mode is only an explicit fallback via `AGPAIR_CLAUDE_CODE_AUTH_MODE=api`.

Do not request the AGPair-managed external `codex` executor by default; it is the Codex CLI worker and is suppressed for Codex controllers unless `--allow-self-executor` is explicitly justified. Use Codex native subagents as the fallback/review lane after external executors are unavailable, unsuitable, or not good enough.

Only route new work to active registered executor ids. Historical task records may remain inspectable for compatibility, but they are not default dispatch targets.

`ready_for_review`, `evidence_ready`, and `committed` mean the external executor claims completion. Codex still verifies the diff, receipt, raw evidence paths, and tests.
