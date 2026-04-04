---
name: agpair
description: "Use agpair as the unified task lifecycle/control plane for delegating coding work to supported executors (currently Antigravity, Codex, and Gemini), checking health/status/watch, and handling continue/approve/retry flows. Trigger when the user asks to send work out, use agpair, inspect doctor/task state, or when a mechanical, well-specified task should be delegated proactively."
---

# agpair

## Overview

Use this skill when your agent is the controller/reviewer and `agpair` is the lifecycle layer.

`agpair` is responsible for:

- preflight health checks
- task dispatch
- task watch / status / logs
- terminal receipts and task state
- semantic follow-up (`continue`, `approve`, `reject`, `retry`)

It is **not** the executor itself. Executors are pluggable.

Current executor policy:

- `antigravity`: primary interactive IDE executor, `same_session`
- `codex`: CLI executor, `fresh_resume_first`
- `gemini`: CLI executor, continuation support is conservative/limited

Important distinction:

- `antigravity` currently has real session semantics
- `codex` in the current agpair implementation is **process-based** (`codex exec` per task), not a manually reused interactive terminal session
- `gemini` in the current agpair implementation is also **process-based** (`gemini -p ...` per task), and current continuation support is intentionally conservative

## Default Flow

### 1. Preflight first

Before any semantic action, check:

```bash
agpair doctor --repo-path <absolute-repo-path>
agpair daemon status
```

Do not dispatch if:

- `desktop_reader_conflict=true`
- `repo_bridge_session_ready=false`

### 2. Dispatch with `--no-wait`

Always dispatch with `--no-wait`:

```bash
agpair task start --repo-path <path> --body "<task brief>" --no-wait
```

Use `agpair task watch <TASK_ID>` as the default observation path.
Use `task status` / `task logs` when you need point-in-time inspection.

Treat terminal phase as truth. Do **not** treat `ACK` as completion.

### 3. Prefer `watch` over manual polling

Default:

```bash
agpair task watch <TASK_ID>
```

Fallback:

- `agpair task status <TASK_ID>`
- `agpair task logs <TASK_ID> --limit <n>`

### 4. Review only when needed

If the task reaches `evidence_ready`:

1. Inspect `status` and `logs`
2. Spot-check key files if needed
3. Choose exactly one:
   - `continue`
   - `approve`
   - `reject`
   - `retry`

## Session Reuse Policy

Default rule: **open a fresh task for new work; reuse only for follow-up on the same task**.

Reuse the current execution chain only when:

- the current task is already in a review/follow-up stage (`evidence_ready`, `continue`, `approve`, `reject`)
- the follow-up is still about the same code slice and same acceptance target
- the executor/session still looks healthy

Prefer a fresh task or fresh resume when:

- this is a new independent unit of work
- the next step is a different concern, layer, or language
- CLI explicitly suggests `--fresh-resume`
- the task has gone through several review rounds and context is bloated
- the session is stale, dead, or clearly unreliable

Executor-specific continuation policy:

- `antigravity`: try same-session continuation first
- `codex`: treat continuation as `fresh_resume_first`; current agpair integration is process-based (`codex exec` per task), not a long-lived interactive session
- `gemini`: treat continuation conservatively; do not assume same-session continuation unless runtime behavior clearly supports it

If continuation fails and the product automatically switches to fresh resume, accept that path. Do not force same-session continuation just to preserve conversation continuity.

## Parallelism Boundary

Default rule: **parallelize across worktrees, not inside one worktree**.

Allowed:

- task A in worktree A
- task B in worktree B
- different executors on different worktrees
- multiple Codex-backed tasks in separate worktrees
- multiple Gemini-backed tasks in separate worktrees

Avoid:

- multiple active tasks in the same repo worktree
- multiple controllers issuing semantic actions against the same task/worktree
- overlapping write scopes across parallel tasks unless the merge plan is explicit

Operational guidance:

- use one main controller per worktree
- start with 2-way parallelism, then increase only after the flow is stable
- prefer Codex for larger fan-out parallel worker sets; use Antigravity more conservatively when opening many concurrent sessions

## Antigravity Task Body Rules

When the executor is Antigravity, prepend this execution block to the task body:

```text
## Execution Rules (highest priority)
1. Wrap every shell command with timeout: timeout 15 <command>
2. Syntax checks only — never import project modules or start services
3. Do not run integration tests or start services
4. If timeout fires (exit code 124), skip that step and continue — do not retry
5. After all work is done, git commit directly — no external approval needed
6. Do NOT use shell/terminal for read-only operations; use built-in IDE tools instead
```

Keep this block stable. Do not improvise alternate rules unless the project explicitly requires it.

## Task Brief Template

Every delegated implementation task should include:

- `Goal`
- `Non-goals`
- `Scope`
- `Invariants`
- `Required changes`
- `Forbidden shortcuts`
- `Required evidence`
- `Exit criteria`

Keep briefs precise. If the task is already single-language, single-focus, and mechanically implementable, prefer sending it as one card. Split only when cross-boundary coupling would make the brief fragile or ambiguous.

## Proactive Dispatch

Dispatch proactively when the task is:

- mechanical
- well-specified
- multi-file enough to benefit from executor throughput

Do **not** dispatch proactively when:

- user input or product decisions are still needed
- the task is trivial
- you have not inspected enough code to write a precise brief

## Anti-Patterns

- Do not use `--wait` as the default control path
- Do not treat every follow-up as a reason to open a fresh task
- Do not force reuse for a new independent task just to save explanation tokens
- Do not keep long historical failure playbooks in your head; follow current CLI/runtime signals
- Do not send multiple semantic actions while an active wait is in progress
- Do not over-split simple work into tiny cards
- Do not force same-session continuation when the executor policy is `fresh_resume_first`

## Completion Gate

Before telling the user a delegated task is done, confirm:

- `doctor` was healthy at dispatch time
- the task reached a real terminal or committed state in code/repo reality
- the relevant evidence or verification was actually reviewed
- no pending task is left hanging in the bridge for the same worktree
