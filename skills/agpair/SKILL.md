---
name: agpair
description: "Use when delegating coding tasks to external executors (Antigravity, Codex, Gemini) — multi-file changes, mechanically clear work, or parallel worktree execution."
---

# agpair

Task lifecycle layer for delegating coding work to executors. Handles health checks, dispatch, monitoring, retry. Does **not** replace planning or code review.

## When to Delegate

**Default: delegate.** If a task involves writing or modifying code, delegate it unless there's a specific reason not to. Do not wait for the user to ask.

Only work directly when:

- the change is a single-line fix or trivial edit
- you need to explore code before knowing what to change
- the task requires interactive judgment mid-execution

## Executor Selection

Worktree/parallel task → Codex → Gemini (never Antigravity — it can't see dynamic worktrees).
Single-worktree task → Antigravity → Codex → Gemini.

| Executor | Strengths | Use when |
|----------|-----------|----------|
| `antigravity` | IDE tools, rich context | Single task on current worktree |
| `codex` | Thorough, runs tests reliably | Worktree tasks; or Antigravity unavailable |
| `gemini` | Alternative perspective | Alongside Codex; or Codex unavailable |

Do not retry the same executor twice for the same failure.

## Flow

### 1. Preflight

```bash
agpair doctor --repo-path <absolute-repo-path>
agpair daemon status
```

Do not dispatch if `desktop_reader_conflict=true` or `repo_bridge_session_ready=false` (Antigravity only).

### 2. Dispatch

```bash
agpair task start --repo-path <path> --executor <executor> --body "<task brief>" --no-wait
```

### 3. Monitoring

Immediately after dispatch, set up background monitoring. Do not wait for the user to ask.

**Antigravity:** `agpair task watch <TASK_ID>` with `run_in_background=true`. Restart if it times out while task is still `acked`.

**Codex / Gemini:** Do not use `watch` (wastes tokens). Use a polling loop:

```bash
task_id="<TASK_ID>"
while true; do
  task_phase=$(agpair task status "$task_id" 2>/dev/null | grep '^phase:' | awk '{print $2}')
  # zsh: do NOT use `status` as variable name — it's read-only
  if [[ "$task_phase" == "evidence_ready" || "$task_phase" == "committed" || "$task_phase" == "blocked" || "$task_phase" == "abandoned" ]]; then
    echo "AGPAIR_TERMINAL: task=$task_id phase=$task_phase"
    break
  fi
  sleep 60
done
```

Run with `run_in_background=true`. One loop per task for parallel tasks.

### 4. Phase handling

| Phase | Action |
|-------|--------|
| `acked` | Keep monitoring |
| `evidence_ready` / `committed` | Proceed to Completion Gate |
| `blocked` | Stop monitoring. Retry with next executor. |
| `stuck` | Wait for auto-recovery; if → `blocked`, retry |
| `abandoned` | Start fresh if work still needed |

## Task Scoping

**Core principle: one task should contain as much work as possible.** Do not split unless forced to.

Each task = one session = one commit. The executor cannot ask follow-up questions, so the brief must be completely self-contained.

### Do not split when:

- changes touch multiple files but serve one logical goal
- the task is large but all steps are clearly specified

### Split only when:

- different languages where failure in one wastes the other
- hard data dependency (step B needs step A committed first)
- separate worktrees for parallel execution

## Writing Briefs

### Must include

- **Exact file paths** — not "the config file" but `src/sdk/sessionController.ts`
- **Specific functions or line references** when targeting existing code
- **Before/after behavior** — what the code does now vs what it should do
- **Validation commands** — e.g., `cd project && npm test`
- **Commit message suggestion**

### Must avoid

- Ambiguous scope ("clean up the code")
- Implicit context the executor can't see
- Multiple unrelated goals

### Template

Every brief should include: `Goal` · `Non-goals` · `Scope` · `Invariants` · `Required changes` · `Forbidden shortcuts` · `Required evidence` · `Exit criteria`

## Executor-Specific Rules

Never include one executor's rules in another's brief.

### Antigravity only

Prepend this block to the task body:

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
   view_file, create_file, edit_file). Only use shell for: syntax checks, git operations,
   and explicitly required build commands.
```

### Codex / Gemini

Do not include Antigravity execution rules or agent-bus commands. Task body should contain only the brief template sections.

## Parallelism

Parallelize across worktrees, not inside one. Avoid multiple active tasks in the same worktree.

## Anti-Patterns

| Thought | Reality |
|---------|---------|
| "I'll split this into 5 small tasks for safety" | Over-splitting wastes sessions. One task = maximum work for one logical goal. |
| "I'll use `watch` for Codex" | `watch` wastes tokens for CLI executors. Use polling loop. |
| "Worktree task can go to Antigravity" | Antigravity can't see dynamic worktrees. Use Codex/Gemini. |
| "The executor will figure out what I mean" | It can't ask questions. Brief must be self-sufficient. |

## Completion Gate

Before reporting a task as done:

- task reached terminal state in repo reality (`git log`)
- evidence reviewed (test results, diff)
- no pending tasks hanging for the same worktree
- process cleanup is automatic (daemon handles cancel→cleanup)
