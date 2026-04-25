---
name: agpair-codex
description: "Use when Codex is the controller and the user requests one or more coding changes that should run in another executor (Codex, Gemini, or Antigravity). Triggers: 'delegate this', 'dispatch to gemini', 'run this in antigravity', multi-task turns with >=2 independent goals, parallel worktree execution, 'agpair', 'agpair-codex'."
---

# agpair (Codex controller)

Codex acts as the controller and dispatches work to another executor (Gemini / Antigravity / a second Codex). Same-repo, same-worktree concurrent editing is **not supported** — isolation boundary is a separate repo or a separate git worktree.

## Parallel Decision Tree

```
Q1: >= 2 distinct goals this turn?
    NO  -> single-task path
    YES -> Q2

Q2: Do any goals touch the SAME files / modules?
    YES -> serialize the overlapping ones, parallelize the rest
    NO  -> Q3

Q3: Does any task depend on another's output?
    YES -> chain via --depends-on, parallelize the independent ones
    NO  -> one worktree per task, dispatch in parallel (DEFAULT)
```

When the tree says parallel, dispatch all worktrees in the same controller turn. Do not start one task to "see how it goes" before launching the rest.

## When to Delegate (single-task path)

Delegate when the work is larger than a trivial local edit, or you want durable state, structured receipts, or background execution. Do **not** delegate when the fix is tiny and local, you still need exploratory reading, or the work needs constant interactive judgment mid-execution.

## Executor Selection

Resolution order: explicit `--executor` → target-level default → `AGPAIR_DEFAULT_EXECUTOR` → product fallback (`antigravity`).

From Codex as controller:

- **Current worktree, focused task:** `antigravity`
- **Parallel / isolated-worktree task:** `gemini` (preferred), then `codex` only if you explicitly want a second Codex worker
- **Never** dispatch a worktree task to `antigravity` — it cannot see dynamic worktrees

## Worktree workflow (default for >= 2 tasks)

### 1. Create one worktree per task (parallel)

```bash
REPO=/absolute/path/to/repo
SLUG=task-a              # short, filesystem-safe label
BRANCH=wt/${SLUG}
WT=${REPO}-wt/${SLUG}    # sibling directory; do NOT nest inside the main worktree

git -C "$REPO" worktree add -b "$BRANCH" "$WT" HEAD
```

### 2. Preflight (once per session)

```bash
agpair daemon status
agpair doctor --repo-path "$WT"
```

### 3. Dispatch in parallel

```bash
agpair task start \
  --repo-path "$WT" \
  --executor gemini \
  --body "<brief>" \
  --isolated-worktree \
  --worktree-boundary "$WT" \
  --no-wait
```

`--no-wait` is mandatory in parallel mode. For a single isolated task, the default `--wait` behavior is fine.

### 4. Watch each task

```bash
agpair task watch <TASK_ID> --json
```

Only emits on phase change, so token cost is minimal. Run one watch per `TASK_ID`.

### 5. Integrate back to main

```bash
git -C "$WT" log --oneline -5
git -C "$WT" diff HEAD~1            # review before merging anything

# pick ONE
git -C "$REPO" cherry-pick $(git -C "$WT" rev-parse HEAD)
git -C "$REPO" merge --no-ff "$BRANCH"
```

### 6. Cleanup

```bash
git -C "$REPO" worktree remove "$WT"
git -C "$REPO" branch -d "$BRANCH"   # -D only if work was abandoned
```

If the worktree is dirty, inspect first; do not blindly `--force` removal.

## Single-task dispatch

```bash
agpair task start --repo-path "$REPO" --executor antigravity --body "<brief>"
```

`task start` waits by default. Use `--no-wait` only when you want to continue Codex work immediately and the task is long enough to justify async monitoring.

## Brief Template

```text
Goal: [what must be true after the task]
Non-goals: [what must not change]
Scope: [exact files / modules / functions]
Invariants: [what must remain true]
Required changes:
  - [specific before/after behavior]
Forbidden shortcuts:
  - [what the executor must not do]
Required evidence:
  - [exact commands]
Exit criteria:
  - [binary done condition]
  - [suggested commit message]
```

## Failure handling

| Symptom | Action |
|---------|--------|
| `git worktree add` fails: branch exists | Pick a new `SLUG`; do not reuse a prior branch |
| `git worktree add` fails: path exists | `git worktree prune`, then re-add |
| `agpair daemon status` shows `running: false` | `agpair daemon start`, then re-dispatch |
| Task stuck in `acked` past expected runtime | Wait for daemon detection; on `blocked`, retry next executor |
| Task `blocked` | Stop watch. `agpair task retry <ID> --body "..."` with the next executor |
| Two parallel tasks edit the same file | Abandon the later one; redo serially after the first lands |
| Cherry-pick conflict in main | Resolve in main. Do **not** push the resolution back into the worktree |

## Completion Gate

Before trusting a completed task: check `agpair task status <TASK_ID> --json`, inspect `git -C <wt> log --oneline -1` and `git -C <wt> diff HEAD~1`, run the brief's evidence commands yourself, then cherry-pick / merge or hold for user review. Remove the worktree and prune the branch unless intentionally kept. Never treat "task completed" as sufficient proof.

## Anti-Patterns

| Thought | Reality |
|---------|---------|
| "User gave me 4 things, I'll dispatch them one at a time" | Default is parallel worktrees. Serial only when the Decision Tree forces it. |
| "I'll always use `--no-wait`" | Single-task control uses blocking wait. `--no-wait` is for parallel and long-running async only. |
| "Codex is controller, default executor should be codex" | Prefer `gemini` (worktree) or `antigravity` (single). Use `codex` only when you explicitly want a second Codex worker. |
| "Worktree task to Antigravity" | Antigravity cannot see dynamic worktrees. Use Codex / Gemini. |
| "Two parallel tasks editing the same file is fine" | It is not. Serialize them, or carve disjoint scopes per worktree. |
| "Completed means safe" | Verify git evidence and tests. |
