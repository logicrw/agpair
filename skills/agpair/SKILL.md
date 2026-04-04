---
name: agpair
description: "Use agpair from Claude Code to delegate coding work to supported executors (currently Antigravity, Codex, and Gemini), check doctor/status/watch, and drive continue/approve/retry flows."
---

# agpair

## Purpose

Use `agpair` as the task lifecycle layer.

It handles:

- health checks
- dispatch
- watch / status / logs
- structured task state
- semantic follow-up (`continue`, `approve`, `reject`, `retry`)

It does **not** replace planning or code review.

## Default Flow

### 1. Preflight

Before dispatch:

```bash
agpair doctor --repo-path <absolute-repo-path>
agpair daemon status
```

Do not dispatch if:

- `desktop_reader_conflict=true`
- `repo_bridge_session_ready=false`

### 2. Dispatch

Always use `--no-wait`:

```bash
agpair task start --repo-path <path> --body "<task brief>" --no-wait
```

Default observation path:

```bash
agpair task watch <TASK_ID>
```

Use `status` / `logs` only when you need point-in-time inspection.

Treat terminal phase as truth. Do **not** treat `ACK` as completion.

### 3. Review only when needed

If a task reaches `evidence_ready`:

1. inspect `status` and `logs`
2. spot-check key files if needed
3. choose exactly one:
   - `continue`
   - `approve`
   - `reject`
   - `retry`

## Session Rule

Default rule: **new work = new task**.

Reuse only for follow-up on the same task:

- `evidence_ready`
- `continue`
- `approve`
- `reject`

Prefer fresh task / fresh resume when:

- this is a new independent unit of work
- the next step changes concern, layer, or language
- CLI suggests `--fresh-resume`
- the session is stale, dead, or unreliable

Executor continuation policy:

- `antigravity`: prefer same-session continuation
- `codex`: `fresh_resume_first`
- `gemini`: conservative / limited continuation support

## Parallelism Rule

Default rule: **parallelize across worktrees, not inside one worktree**.

Allowed:

- different tasks in different worktrees
- different executors in different worktrees
- multiple Codex or Gemini tasks in separate worktrees

Avoid:

- multiple active tasks in the same worktree
- multiple controllers acting on the same task/worktree
- overlapping write scopes unless the merge plan is explicit

## Orchestration Metadata

Controllers can define planning metadata. These are **metadata-only** hints (persisted and readable via `status`/`inspect`), not runtime-enforced behaviors:

- `depends_on`: prior task IDs required
- `isolated_worktree`: execution in a separate git worktree
- `worktree_boundary`: targeted directory scope
- `setup_commands` / `teardown_commands`: workspace preparation/cleanup hooks
- `env_vars`: config overrides (e.g., `PORT`) for isolation
- `spotlight_testing`: intent to run focused, localized tests

Always respect the Parallelism Rule when planning metadata.

## Antigravity Brief Header

When the executor is Antigravity, prepend this block:

```text
## Execution Rules (highest priority)
1. Wrap every shell command with timeout: timeout 15 <command>
2. Syntax checks only — never import project modules or start services
3. Do not run integration tests or start services
4. If timeout fires (exit code 124), skip that step and continue — do not retry
5. After all work is done, git commit directly — no external approval needed
6. Do NOT use shell/terminal for read-only operations; use built-in IDE tools instead
```

## Task Brief Template

Every delegated task should include:

- `Goal`
- `Non-goals`
- `Scope`
- `Invariants`
- `Required changes`
- `Forbidden shortcuts`
- `Required evidence`
- `Exit criteria`

Prefer one well-scoped card when the task is already single-language, single-focus, and mechanically implementable. Split only when boundaries are genuinely ambiguous or fragile.

## Anti-Patterns

- Do not use `--wait` as the default path
- Do not open a fresh task for every follow-up
- Do not force reuse for a new independent task
- Do not send multiple semantic actions while an active wait exists
- Do not over-split simple work into tiny cards
- Do not force same-session continuation when backend policy is `fresh_resume_first`

## Completion Gate

Before telling the user a delegated task is done, confirm:

- dispatch health was good
- the task reached a real terminal or committed state in repo reality
- the evidence was actually reviewed
- no pending task is left hanging for the same worktree
