---
name: agpair-codex
description: "Use when Codex should delegate non-trivial coding, refactor, test-fix, research, or review work through AGPair external CLI executors before using Codex native subagents."
---

# AGPair for Codex

Default: external AGPair executor first for non-trivial work. Codex remains the controller and verifier.

Use direct Codex edits for tiny local fixes, sensitive judgment-heavy work, or when AGPair is unavailable. Use Codex native subagents as fallback/review resources, not the default worker pool.

## Normal Task

```bash
agpair task start \
  --repo-path "$REPO" \
  --executor antigravity-cli \
  --authorization-profile local_mutating \
  --body "<brief>"
```

`task start` waits by default. After completion, inspect status, diff, receipt, raw logs, and required evidence before reporting success.

## Parallel Or Async

```bash
agpair task start --repo-path "$WT_A" --body "<brief A>" --no-wait
agpair task start --repo-path "$WT_B" --body "<brief B>" --no-wait
agpair task watch <TASK_ID> --json
```

Do not use repeated Codex prompts as a polling loop. Use `agpair task watch <TASK_ID> --json` or `agpair task wait <TASK_ID>` for low-token waiting.

`watch --json` emits state changes and raw evidence paths; it does not stream full logs. Do not run raw executor output through lossy compression by default.

Use Codex App thread automation only for very long tasks that should wake the same thread later.

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

`claude-code` is the AGPair-managed external Claude Code worker for Codex controllers. It is the cross-controller quality escalation lane, not a native Codex subagent. Its default Claude auth mode is `auto`: `agpair doctor --fresh` first verifies the local Claude Code OAuth/subscription login, then falls back to the current Claude provider selected in CC Switch. Update Claude login or the CC Switch provider if `doctor --fresh` reports `executor_auth_required` or `Invalid Authentication`. API-key bare mode is only an explicit fallback via `AGPAIR_CLAUDE_CODE_AUTH_MODE=api`.

Do not request the AGPair-managed external `codex` executor by default; it is the Codex CLI worker and is suppressed for Codex controllers unless `--allow-self-executor` is explicitly justified. Use Codex native subagents as the fallback/review lane after external executors are unavailable, unsuitable, or not good enough.

Do not use Gemini for new work.

`ready_for_review`, `evidence_ready`, and `committed` mean the external executor claims completion. Codex still verifies the diff, receipt, raw evidence paths, and tests.
