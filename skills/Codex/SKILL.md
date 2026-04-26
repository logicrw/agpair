---
name: agpair-codex
description: "Use when Codex is the controller and the user requests one or more coding changes that should run in another executor (Gemini or Antigravity). Triggers: 'delegate this', 'dispatch to gemini', 'run this in antigravity', multi-task turns with >=2 independent goals, parallel worktree execution, 'agpair', 'agpair-codex'."
---

# agpair (Codex controller)

Codex is the controller and dispatches work to **Gemini** (worktree-friendly CLI executor) or **Antigravity** (current-worktree IDE executor). Codex already has built-in subagent capability for in-process delegation; do **not** dispatch agpair tasks back to a `codex` executor — use Gemini for parallel worktree work and Antigravity for single-worktree work. Same-repo, same-worktree concurrent editing is **not supported** — isolation boundary is a separate repo or a separate git worktree.

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

## Serial Chain (auto-advance)

When tasks have strict ordering (B cannot start until A's commit has landed), use a **shared worktree** with `--depends-on`. The daemon auto-dispatches each step after its dependencies reach `committed`.

**This is the recommended serial pattern for Codex** — Codex is one-shot and cannot maintain a Monitor loop across tasks.

### Key pattern: shared worktree

All tasks share **one worktree**. Auto-advance guarantees no concurrent editing.

```bash
# Create ONE worktree for the chain
WT=${REPO}-wt/chain
git -C "$REPO" worktree add -b wt/chain "$WT" HEAD

# Step A: dispatch immediately
agpair task start --repo-path "$WT" --executor codex \
  --body "<brief-A>" --task-id TASK-A --no-wait

# Step B: same worktree, deferred until A commits
agpair task start --repo-path "$WT" --executor codex \
  --body "<brief-B>" --task-id TASK-B \
  --depends-on '["TASK-A"]' --no-wait

# Monitor the final task
agpair task watch TASK-B --json
```

Why shared worktree: **B sees A's commits.** Separate worktrees would start from the old HEAD and miss A's changes.

### Key pattern: goal-oriented briefs

Briefs must reference the **current repo state**, not the previous task's specific changes. The executor AI reads the code.

❌ Bad — requires knowing what A changed:
```
Add user_role field to the API endpoint response
```

✅ Good — works regardless of A's specific output:
```
Goal:         Make API layer consistent with current schema definitions
Non-goals:    Do not modify schema files — updated by prior task
Scope:        api/routes.py, api/models.py
Required changes:
  - Read models/schema.py for current field definitions
  - Align all API endpoints with schema
Required evidence: python -m pytest tests/api/ -v
Exit criteria: API layer reflects current schema; all tests pass
```

### Mixed parallel + serial

```bash
# A and B in parallel (separate worktrees)
agpair task start --repo-path "$WT_A" ... --task-id TASK-A --no-wait
agpair task start --repo-path "$WT_B" ... --task-id TASK-B --no-wait

# C waits for both
agpair task start --repo-path "$REPO" ... --task-id TASK-C \
  --depends-on '["TASK-A", "TASK-B"]' --no-wait
```


## When to Delegate (single-task path)

Delegate when the work is larger than a trivial local edit, or you want durable state, structured receipts, or background execution. Do **not** delegate when the fix is tiny and local, you still need exploratory reading, or the work needs constant interactive judgment mid-execution.

## Executor Selection

Resolution order: explicit `--executor` → target-level default → `AGPAIR_DEFAULT_EXECUTOR` → product fallback (`antigravity`).

From Codex as controller:

- **Current worktree, focused task:** `antigravity`
- **Parallel / isolated-worktree task:** `gemini`
- **Never** dispatch to `--executor codex` from this controller — for in-process work, use Codex's built-in subagent instead of round-tripping through agpair
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
| Serial chain dep blocked/abandoned | Downstream tasks are auto-blocked. After retrying the upstream dep to `committed`, you must also `task retry` each downstream task — they do not auto-unblock. |
| Two parallel tasks edit the same file | Abandon the later one; redo serially after the first lands |
| Cherry-pick conflict in main | Resolve in main. Do **not** push the resolution back into the worktree |

## Completion Gate

Before trusting a completed task: check `agpair task status <TASK_ID> --json`, inspect `git -C <wt> log --oneline -1` and `git -C <wt> diff HEAD~1`, run the brief's evidence commands yourself, then cherry-pick / merge or hold for user review. Remove the worktree and prune the branch unless intentionally kept. Never treat "task completed" as sufficient proof.

## Anti-Patterns

| Thought | Reality |
|---------|---------|
| "User gave me 4 things, I'll dispatch them one at a time" | Default is parallel worktrees. Serial only when the Decision Tree forces it. |
| "I'll always use `--no-wait`" | Single-task control uses blocking wait. `--no-wait` is for parallel and long-running async only. |
| "Codex is controller, default executor should be codex" | No. Use Gemini for worktree work, Antigravity for single-worktree work. For in-process delegation, use Codex's built-in subagent — not `agpair task start --executor codex`. |
| "Worktree task to Antigravity" | Antigravity cannot see dynamic worktrees. Use Gemini. |
| "Two parallel tasks editing the same file is fine" | It is not. Serialize them, or carve disjoint scopes per worktree. |
| "Completed means safe" | Verify git evidence and tests. |
