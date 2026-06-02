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

## Blocked Retry

```bash
agpair task retry TASK-123 --from-block --authorization-profile local_mutating
```

`blocked(approval_required)` is terminal in V1. Retry starts a new attempt with structured blocked context and a dispatch-time authorization profile.

## Executor Order

Prefer `antigravity-cli`, then `grok-cli`, then `claude-code`, then `codex` as fallback. Do not use Gemini for new work.

`ready_for_review`, `evidence_ready`, and `committed` mean the external executor claims completion. Codex still verifies the diff, receipt, raw evidence paths, and tests.
