---
name: agpair
description: "Delegate coding tasks to external AI executors (Codex, Gemini, Antigravity). Use for: multi-file changes, mechanically clear work, or parallel worktree execution. Triggers: 'delegate this', 'dispatch to codex', 'run this in gemini', 'start a task', 'parallel execution', 'agpair'."
---

# agpair

Task lifecycle layer for delegating coding work to executors. Handles dispatch, monitoring, and result handling. Does **not** replace planning or code review.

## When to Delegate

**Default: delegate.** If a task involves writing or modifying code, delegate it unless:
- The change is a single-line fix or trivial edit.
- You need to explore code before knowing what to change.
- The task requires interactive judgment mid-execution.

## Executor Selection

- **Worktree/parallel tasks:** `codex` → `gemini` (Fallback order). *Never use Antigravity for dynamic worktrees.*
- **Single-worktree tasks:** `antigravity` → `codex` → `gemini`.

| Executor | Strengths | Use when |
|----------|-----------|----------|
| `antigravity` | IDE tools, rich context | Single task on current worktree |
| `codex` | Thorough, runs tests reliably | Worktree tasks; or Antigravity unavailable |
| `gemini` | Alternative perspective | Alongside Codex; or Codex unavailable |

## Workflow Checklist

Copy and check off these items in your scratchpad during execution:

- [ ] **1. Preflight:** Run `agpair doctor --repo-path <path>` and `agpair daemon status`.
- [ ] **2. Write Brief:** Create a self-contained brief using the template. The executor cannot ask clarifying questions.
- [ ] **3. Dispatch:** Run `agpair task start --repo-path <path> --executor <name> --body "<brief>" --no-wait`.
- [ ] **4. Monitor:** Start a `Monitor` with `agpair task watch <TASK_ID> --json` immediately (do not wait for the user).
- [ ] **5. Completion Gate:** Verify physical git evidence and tests before reporting success.

## Monitoring

Use Claude Code's `Monitor` tool for all executors. The `watch --json` command only outputs when state changes, so token cost is minimal.

```
Monitor(
  description="Watch AGPair task <TASK_ID>",
  command="agpair task watch <TASK_ID> --json",
  timeout_ms=3600000
)
```

| Phase | Action |
|-------|--------|
| `acked` | Keep monitoring |
| `evidence_ready` / `committed` | Proceed to Completion Gate |
| `blocked` | Stop monitoring. Retry with next executor. |
| `stuck` | Wait for auto-recovery. If it transitions to `blocked`, retry. |
| `abandoned` | Start fresh if work is still needed |

## Task Scoping & Briefs

A brief must be completely self-sufficient. **One task = maximum work for one logical goal.** Do not split unless forced to (e.g. parallel worktrees or hard data dependency).

### Brief Template

```
Goal:         [What the code should do after this task]
Non-goals:    [What to explicitly NOT change]
Scope:        [Exact file paths and function names]
Invariants:   [What must NOT break]
Required changes:
  - [Exact file path → specific change description including before/after behavior]
Forbidden shortcuts:
  - [What the executor must NOT do]
Required evidence:
  - [Validation command: e.g., cd project && npm test]
Exit criteria:
  - [Concrete condition for "done"]
  - [Suggested commit message]
```

### Example Brief

```
Goal: Fix _is_process_alive() to work on macOS where ps -g has different semantics.
Non-goals: Do not change the termination protocol.
Scope: agpair/executors/local_cli.py — function _is_process_alive()
Invariants: All 28 tests in test_local_cli_executor.py must still pass.
Required changes:
  - L60: Replace os.kill() with os.killpg()
  - L64-65: Replace ps -g with ps -p for leader, then pgrep -g for group
Forbidden shortcuts:
  - Do not use platform.system() branching — find a cross-platform approach
Required evidence:
  - python -m pytest tests/unit/test_local_cli_executor.py -v
Exit criteria:
  - All tests pass. Commit: "fix: cross-platform process group liveness check"
```

## Executor-Specific Rules

Never include one executor's rules in another's brief.

### Antigravity

When dispatching to `antigravity`, prepend this exact block to the task body:

```text
## Execution Rules
1. Wrap every shell command with timeout: timeout 15 <command>
2. Syntax checks only — never import project modules or start services
3. Do not run integration tests or start services
4. If timeout fires (exit code 124), skip that step and continue — do not retry
5. After all work is done, git commit directly — no external approval needed
6. Use IDE built-in tools (grep_search, view_file, etc.) for read-only filesystem operations. Only use shell for syntax checks and git/build commands.
```

### Codex / Gemini

Do not include Antigravity rules. They run as CLI processes. Just provide the brief block.

## Completion Gate

Before reporting a task as done, verify:
- [ ] Terminal state confirmed: `git -C <repo-path> log --oneline -1` shows the commit.
- [ ] Diff reviewed: `git -C <repo-path> diff HEAD~1` matches intent.
- [ ] Tests pass: ran the validation command from the brief.
- [ ] Orphan tasks cleared: daemon handles cleanup automatically.
*(Note: If receipt payload shows `verification: unverified`, you must run git log manually).*

## Anti-Patterns

| Thought | Reality |
|---------|---------|
| "I'll split this into 5 small tasks" | Over-splitting wastes sessions. One task = maximum work for one logical goal. |
| "I'll use a bash polling loop" | Use `Monitor` tool with `watch --json` instead — event-driven, minimal tokens. |
| "Worktree task to Antigravity" | Antigravity can't see dynamic worktrees. Use Codex/Gemini. |
| "The executor will figure it out" | It can't ask questions. Brief must be explicit and self-sufficient. |
