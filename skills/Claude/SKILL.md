---
name: agpair
description: "Use when the user asks for one or more coding changes that should run in an external executor (Codex, Gemini, Antigravity), or when a turn lists >=2 independent code goals. Triggers: 'delegate this', 'dispatch to codex', 'run this in gemini', 'start a task', 'parallel execution', 'agpair', multi-file changes, mechanically clear work."
---

# agpair (Claude controller)

Task lifecycle layer for delegating coding work. Claude's main thread is the **coordinator**, not the editor.

## Role split

| Layer | Allowed work |
|-------|--------------|
| Main Claude thread | Plan, locate files, write briefs, dispatch, monitor, review diffs, decide cherry-pick / merge |
| Codex / Gemini in worktree | Multi-line code edits, refactors, tests, commits |
| Antigravity in current worktree | Single-worktree IDE-rich work (cannot see dynamic worktrees) |

Main thread should **not** use `Edit`/`Write` on source code unless: it's a single-line fix or comment / typo, it's a doc / config edit, or the user explicitly asked the main thread to do it. Otherwise: write a brief and dispatch.

Same-repo, same-worktree concurrent editing is **not supported** — isolation boundary is a separate repo or a separate git worktree.

## Parallel Decision Tree

```
Q1: >= 2 distinct goals listed this turn?
    NO  -> single-task path
    YES -> Q2

Q2: Do any goals touch the SAME files / modules / schema?
    YES -> serialize the overlapping ones, parallelize the rest
    NO  -> Q3

Q3: Does any task depend on another's output?
    YES -> chain via --depends-on, parallelize the independent ones
    NO  -> one worktree per task, dispatch in parallel (DEFAULT)
```

When the tree says parallel, dispatch all worktrees in the same assistant turn and open one Monitor per task immediately. Do not wait for the user to ask.

## When to Delegate (single-task path)

**Default: delegate.** Skip delegation only when the change is a single-line fix, you still need exploratory reading, or the task requires interactive judgment mid-execution.

## Executor Selection

- **Worktree / parallel tasks:** `codex` → `gemini` (fallback). *Never use Antigravity for dynamic worktrees.*
- **Single-worktree task on current repo:** `antigravity` → `codex` → `gemini`.

| Executor | Strengths | Use when |
|----------|-----------|----------|
| `antigravity` | IDE tools, rich context | Single task on current worktree |
| `codex` | Thorough, runs tests reliably | Worktree tasks; or Antigravity unavailable |
| `gemini` | Alternative perspective | Alongside Codex; or Codex unavailable |

## Worktree workflow (default for >= 2 tasks)

### 1. Create one worktree per task (parallel Bash calls)

```bash
REPO=/absolute/path/to/repo
SLUG=task-a              # short, filesystem-safe label
BRANCH=wt/${SLUG}
WT=${REPO}-wt/${SLUG}    # sibling directory, NOT inside main worktree

git -C "$REPO" worktree add -b "$BRANCH" "$WT" HEAD
```

### 2. Preflight (once per session)

```bash
agpair daemon status
agpair doctor --repo-path "$WT"
```

### 3. Dispatch in parallel (one call per task, same assistant turn)

```bash
agpair task start \
  --repo-path "$WT" \
  --executor codex \
  --body "<brief>" \
  --isolated-worktree \
  --worktree-boundary "$WT" \
  --no-wait
```

`--no-wait` is mandatory in parallel mode so the assistant turn does not block.

### 4. Monitor each task immediately

```
Monitor(
  description="Watch AGPair task <TASK_ID>",
  command="agpair task watch <TASK_ID> --json",
  timeout_ms=3600000
)
```

`watch --json` only emits on phase change, so token cost is minimal.

### 5. Cherry-pick / merge back to main

```bash
git -C "$WT" log --oneline -5
git -C "$WT" diff HEAD~1

# pick ONE
git -C "$REPO" cherry-pick $(git -C "$WT" rev-parse HEAD)
git -C "$REPO" merge --no-ff "$BRANCH"
```

### 6. Cleanup

```bash
git -C "$REPO" worktree remove "$WT"
git -C "$REPO" branch -d "$BRANCH"   # -D only if work was abandoned
```

If `worktree remove` complains about uncommitted state, inspect `$WT` before forcing.

## Failure handling

| Symptom | Action |
|---------|--------|
| `git worktree add` fails: branch exists | Pick a new `SLUG`; do not reuse a prior branch |
| `git worktree add` fails: path exists | `git worktree prune`, then re-add |
| `agpair daemon status` shows `running: false` | `agpair daemon start`, then re-dispatch |
| Task stuck in `acked` past expected runtime | Wait for daemon detection; on `blocked`, retry next executor |
| Task `blocked` | Stop monitor. `agpair task retry <ID> --body "..."` with the next executor |
| Two tasks accidentally edit the same file | Abandon the later one, redo it serially after the first lands |
| Cherry-pick conflict in main | Resolve in main. Do **not** push the resolution back into the worktree |

## Phase actions

| Phase | Action |
|-------|--------|
| `acked` | Keep monitoring |
| `evidence_ready` / `committed` | Completion Gate, then cherry-pick / merge |
| `blocked` | Stop monitor. Retry with next executor |
| `stuck` | Wait for auto-recovery; on `blocked`, retry |
| `abandoned` | Start fresh if the work is still needed |

If the repo has `agpair claude statusline` or the AGPair SessionStart hook configured, treat them as passive hints — they do **not** replace Monitor attachment after dispatch.

## Brief Template

```
Goal:         [what the code should do after this task]
Non-goals:    [what to explicitly NOT change]
Scope:        [exact file paths and function names]
Invariants:   [what must NOT break]
Required changes:
  - [exact file path -> specific before/after behavior]
Forbidden shortcuts:
  - [what the executor must NOT do]
Required evidence:
  - [validation command]
Exit criteria:
  - [concrete done condition]
  - [suggested commit message]
```

One task = maximum work for one logical goal. Do not split a single goal into many tasks; do split distinct goals into parallel worktree tasks.

## Executor-Specific Rules

Never include one executor's rules in another's brief.

### Antigravity

Prepend this exact block to the task body:

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

Do not include Antigravity rules. Just provide the brief block.

## Completion Gate

Per worktree:

- [ ] `git -C <wt> log --oneline -1` shows the commit
- [ ] `git -C <wt> diff HEAD~1` matches intent
- [ ] Tests pass (ran the brief's validation command)
- [ ] Cherry-picked / merged into main, or explicitly held for user review
- [ ] Worktree removed and branch cleaned up (unless intentionally kept)

If the receipt payload shows `verification: unverified`, run git log manually.

## Anti-Patterns

| Thought | Reality |
|---------|---------|
| "User gave me 4 tasks, I'll dispatch them one by one" | Default is parallel worktrees. Serial only when the Decision Tree forces it. |
| "I'll just `Edit` from main, faster than dispatching" | If it's more than a single-line / doc edit, the main thread is the wrong place. |
| "I'll wait for the user to suggest worktrees" | Don't. Apply the Default workflow. |
| "I'll split this single goal into 5 small tasks" | One task = maximum work for one logical goal. |
| "I'll use a bash polling loop" | Use Monitor + `watch --json` — event-driven, minimal tokens. |
| "Worktree task to Antigravity" | Antigravity can't see dynamic worktrees. Use Codex / Gemini. |
| "The executor will figure it out" | It can't ask questions. Brief must be explicit and self-sufficient. |
| "Two parallel tasks editing the same file is fine" | It is not. Serialize, or carve disjoint scopes. |
