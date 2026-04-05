---
name: agpair
description: "Use agpair to delegate coding work to supported executors (currently Antigravity, Codex, and Gemini), check doctor/status/watch, and drive retry flows."
---

# agpair

## Purpose

Use `agpair` as the task lifecycle layer for delegating coding work to executors.

It handles:

- health checks
- dispatch
- watch / status / logs
- structured task state
- semantic follow-up (`retry`)

It does **not** replace planning or code review.

## When to Delegate

Proactively delegate when any of these apply — do not wait for the user to ask:

- the task involves multi-file changes across modules
- the change is mechanically clear (scope, invariants, and exit criteria are unambiguous)
- tests need to run as part of validation
- the work is independent enough to hand off without mid-task clarification

Do **not** delegate when:

- the change is a single-line fix you can make directly
- the task requires interactive judgment calls mid-execution
- you need to explore or understand code before knowing what to change

## Executor Preference

Default order: **Antigravity → Codex → Gemini**.

| Executor | Strengths | Use when |
|----------|-----------|----------|
| `antigravity` | IDE tools, fast file ops | Default choice; single-worktree tasks |
| `codex` | Thorough, good at cross-module refactors, runs tests reliably | Antigravity unavailable or task needs heavy test validation |
| `gemini` | Alternative perspective | Antigravity and Codex both unavailable, or parallel diversity needed |

Fallback: if the preferred executor is blocked or unhealthy, try the next in order. Do not retry the same executor more than once for the same failure.

## Default Flow

### 1. Preflight

Before dispatch:

```bash
agpair doctor --repo-path <absolute-repo-path>
agpair daemon status
```

Do not dispatch if:

- `desktop_reader_conflict=true`
- `repo_bridge_session_ready=false` (Antigravity only)

### 2. Dispatch

Always use `--no-wait`:

```bash
agpair task start --repo-path <path> --executor <executor> --body "<task brief>" --no-wait
```

Observation differs by executor:

| Executor | Observation method |
|----------|-------------------|
| `antigravity` | `agpair task watch <TASK_ID>` (run in background; Antigravity sessions are not directly observable) |
| `codex` / `gemini` | Local CLI processes — use `agpair task status`, `ps`, `tail` on the working dir, or read stdout directly. `watch` in background for terminal notification only. |

For Codex/Gemini, avoid running `watch` in foreground — heartbeat lines waste tokens. Use `task status` for point-in-time checks, or directly inspect the process and its output.

Treat terminal phase as truth. Do **not** treat `ACK` as completion.

### 3. Phase handling

| Phase | Action |
|-------|--------|
| `acked` | Keep watching — not done yet |
| `evidence_ready` | Executor finished and committed — verify with `git log --oneline -3`, announce completion |
| `committed` | Same as `evidence_ready` — verify and announce |
| `blocked` | Evaluate: retry same executor, or fallback to next executor |
| `stuck` | Wait for auto-recovery; if it transitions to `blocked`, retry or fallback |
| `abandoned` | Start fresh with next executor if work still needed |

## Task Scoping

The goal is **one task = one session = one commit**. Every dispatched task should be completable by the executor without mid-task clarification.

### Sizing rules

- **Maximize per-session work**: pack as much related work as possible into one task. Do not split unless there is a concrete reason (different worktrees, hard dependency ordering, or genuinely unrelated concerns).
- **Clear mechanical steps**: the executor should never have to guess intent
- **Self-contained context**: include all file paths, function names, and behavioral expectations in the brief — the executor cannot ask follow-up questions

### When to split (and when not to)

**Do not split** when:

- the changes touch multiple files but serve one logical goal in the same language/runtime
- the task is large but all steps are clearly specified in the brief

**Split** when:

- the work spans different languages (e.g., Python + TypeScript) — failure in one shouldn't waste progress in the other
- changes must land in separate worktrees for parallel execution
- step B literally cannot be written until step A is committed (hard data dependency)
- the concerns are genuinely unrelated and would produce a confusing commit

Exception: tightly coupled cross-language changes (e.g., a protocol change that must update both sides atomically) can stay in one task to avoid inconsistent intermediate state.

When splitting, order by dependency and wait for commit before dispatching the next. Each sub-task gets its own full brief.

## Writing Self-Sufficient Briefs

A good brief eliminates round-trips. The executor should be able to complete the task by reading only the brief.

### Must include

- **Exact file paths** — not "the config file" but `companion-extension/src/sdk/sessionController.ts`
- **Specific line references or function names** when targeting existing code
- **Before/after behavior** — what the code does now vs what it should do
- **Validation commands** — exact commands to run (e.g., `cd companion-extension && npm test`)
- **Commit message suggestion** — saves the executor from guessing intent

### Must avoid

- Ambiguous scope — "clean up the code" without specifying which code
- Implicit context — referencing conversations or decisions the executor can't see
- Over-constraining implementation — specify what and why, not how (unless the how matters)
- Multiple unrelated goals in one brief

## Executor-Specific Rules

Task body content MUST match the target executor. Never include executor-specific headers or instructions for a different executor — doing so causes the executor to misinterpret the task (e.g., Codex re-dispatching to Antigravity).

### Antigravity only

When `--executor antigravity` (or default), prepend this block to the task body:

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

### Codex / Gemini

Do **not** include the Antigravity execution rules block. Do **not** include agent-bus send commands, receipt paths, or any Antigravity session management instructions. These executors run as local CLI processes — they commit directly and produce receipts through the CLI executor adapter, not through agent-bus.

Task body for Codex/Gemini should contain only the task brief template sections below.

## Task Brief Template

Every delegated task should include these sections:

`Goal` · `Non-goals` · `Scope` · `Invariants` · `Required changes` · `Forbidden shortcuts` · `Required evidence` · `Exit criteria`

## Session Rule

Default rule: **one task = one session**. Each task runs in a fresh session and commits when done. If blocked, retry or start fresh — do not attempt to continue within the same session.

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

## Anti-Patterns

- Do not use `--wait` as the default path
- Do not over-split work into tiny tasks — maximize what one session can accomplish
- Do not attempt same-session continuation — always use fresh sessions
- Do not ask Antigravity to run integration tests, start services, or import project modules — these can block indefinitely
- Do not omit the execution rules block from Antigravity task bodies
- Do not include Antigravity execution rules, agent-bus commands, or receipt paths in Codex/Gemini task bodies — this causes re-dispatch confusion
- Do not run `watch` in foreground for Codex/Gemini tasks — heartbeat lines waste tokens; use background + direct observation
- Do not write ambiguous briefs that require the executor to ask clarifying questions — the executor cannot ask
- Do not wait for the user to request delegation — proactively delegate when the task matches delegation criteria

## Completion Gate

Before telling the user a delegated task is done, confirm:

- dispatch health was good
- the task reached a real terminal or committed state in repo reality
- the evidence was actually reviewed (git log, test results)
- no pending task is left hanging for the same worktree