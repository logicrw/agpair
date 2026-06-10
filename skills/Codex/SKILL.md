---
name: agpair-codex
description: "Use when Codex should delegate non-trivial coding, refactor, test-fix, research, or review work through AGPair external CLI executors before using Codex native subagents."
---

# AGPair for Codex

Default: external AGPair executor first for non-trivial work. Codex remains the controller and verifier.

Actively outsource low-value, repetitive, time-consuming, or easily verifiable work through AGPair: repo scans, alternative reviews, focused test-fix attempts, multi-file mechanical edits, smoke checks, and implementation slices with clear acceptance criteria.

Use direct Codex edits for tiny local fixes, sensitive judgment-heavy work, or when AGPair is unavailable. Use Codex native subagents only after external executors are unavailable, unsuitable, or not good enough, or for narrow controller-side review/helper work.

## Normal Task

```bash
agpair task start \
  --repo-path "$REPO" \
  --executor antigravity-cli \
  --authorization-profile local_mutating \
  --body "<brief>"
```

`task start` waits by default. After completion, inspect status, diff, receipt, raw logs, and required evidence before reporting success.

Default executor environments are `managed-natural` for `antigravity-cli`, `grok-cli`, and healthy `claude-code`: AGPair manages state and evidence, while the external CLI keeps its normal skills, MCP, memory, plugins, and provider config. If an external attempt fails or is low quality, retry naturally, switch to another external executor, or use Codex native subagents as fallback/review.

## Bounded Implementation

For non-trivial implementation, refactor, or test-fix work, first dispatch one bounded slice unless the task is tiny, sensitive, external executors are unhealthy, or a prior external result was low quality:

```bash
agpair task start \
  --repo-path "$REPO" \
  --controller codex \
  --executor antigravity-cli \
  --authorization-profile local_mutating \
  --completion-policy evidence \
  --isolated-worktree \
  --body "$BRIEF"
```

Use a brief with explicit allowed files, forbidden files, required changes, validation command, and exit criteria. The external worker returns `changed_files`, `validation` or `validation_not_run`, `scope_violations`, report text, and raw evidence paths. Codex integrates or rejects the result in the main worktree after verification.

For isolated mutating evidence/commit tasks, AGPair defaults to `--dirty-snapshot tracked`: tracked staged/unstaged controller changes are copied into the executor worktree before launch. Ignored and untracked files are not copied; use `--dirty-snapshot off` when the worker should start from committed HEAD only.

## Parallel Or Async

```bash
agpair task start --repo-path "$WT_A" --body "<brief A>" --no-wait
agpair task start --repo-path "$WT_B" --body "<brief B>" --no-wait
agpair task watch <TASK_ID> --json
```

Do not use repeated Codex prompts as a polling loop. Use `agpair task watch <TASK_ID> --json` or `agpair task wait <TASK_ID>` for low-token waiting.

`watch --json` emits state changes and raw evidence paths; it does not stream full logs. Do not run raw executor output through lossy compression by default.

Use Codex App thread automation only for very long tasks that should wake the same thread later.

## Review And Adoption

Always inspect:

```bash
agpair task status TASK-123 --json
agpair task logs TASK-123 --include-executor-output
```

Use `protocol_result` to judge AGPair receipt quality and `adoption_result` to judge whether Codex can use the result. `adoptable_result=yes` means directly adoptable after normal verification; `partial` means usable with bounded controller review or minor rework; `no` means retry, switch executor, or use native subagents.

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

`claude-code` is the AGPair-managed external Claude Code worker for Codex controllers. It is the cross-controller quality escalation lane, not a native Codex subagent. Its default Claude auth mode is `auto`: `agpair doctor --fresh` first verifies the local Claude Code OAuth/subscription login, then falls back to the current Claude provider selected in CC Switch. Update Claude login or the CC Switch provider if `doctor --fresh` reports `executor_auth_required` or `Invalid Authentication`. API-key worker mode is only an explicit fallback via `AGPAIR_CLAUDE_CODE_AUTH_MODE=api`.

Do not request the AGPair-managed external `codex` executor by default; it is the Codex CLI worker and is suppressed for Codex controllers unless `--allow-self-executor` is explicitly justified. Use Codex native subagents as the fallback/review lane after external executors are unavailable, unsuitable, or not good enough.

Do not use Gemini for new work.

`ready_for_review`, `evidence_ready`, and `committed` mean the external executor claims completion. Codex still verifies the diff, receipt, raw evidence paths, and tests.
