---
name: agpair
description: "Use agpair from Claude Code to delegate coding work to supported executors (currently Antigravity, Codex, and Gemini), check doctor/status/watch, and drive retry flows."
---

# agpair

## Purpose

Use `agpair` as the task lifecycle layer.

It handles:

- health checks
- dispatch
- watch / status / logs
- structured task state
- semantic follow-up (`retry`)

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

### 3. Phase handling

| Phase | Action |
|-------|--------|
| `acked` | Keep watching — not done yet |
| `committed` | Verify with `git log --oneline -3`, announce completion |
| `blocked` | `agpair task retry <TASK_ID> --no-wait`, then watch again |
| `stuck` | Wait for auto-recovery; if it transitions to `blocked`, retry |
| `abandoned` | Start fresh with `agpair task start --no-wait` if work still needed |

## Session Rule

Default rule: **new work = new task**.

Prefer fresh task when:

- this is a new independent unit of work
- the next step changes concern, layer, or language
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

## Antigravity Brief Header

When the executor is Antigravity, prepend this block:

```text
## Execution Rules (highest priority)
1. Wrap every shell command with timeout: timeout 15 <command>
2. Syntax checks only — never import project modules or start services:
   Python:  timeout 10 python3 -m py_compile <file>
   Node.js: timeout 10 node --check <file>
   Go:      timeout 10 go vet ./...
   Other:   skip syntax checks entirely — do not improvise
3. Do not run integration tests or start services
4. If timeout fires (exit code 124), skip that step and continue — do not retry
5. After all work is done, git commit directly — no external approval needed
6. Do NOT use shell/terminal for read-only operations (grep, find, cat, ls) OR file/directory
   creation (mkdir, touch, echo >). Use IDE built-in tools instead (grep_search, file_search,
   view_file, create_file, edit_file). Shell commands trigger approval prompts that block
   automated execution indefinitely. Only use shell for: syntax checks (Rule 2), git operations,
   and explicitly required build commands.
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
- Do not ask Antigravity to run integration tests, start services, or import project modules — these can block indefinitely
- Do not omit the execution rules block from the task body

## Completion Gate

Before telling the user a delegated task is done, confirm:

- dispatch health was good
- the task reached a real terminal or committed state in repo reality
- the evidence was actually reviewed
- no pending task is left hanging for the same worktree
