# AgPair — Tech Debt & Roadmap

Items deferred by explicit decision. Each has a trigger condition — when the trigger fires, the item is worth implementing.

## R4: Concurrent dispatch CAS guard

**Risk**: `auto_advance_dependent_tasks` re-reads task phase before dispatch but doesn't use atomic CAS. A concurrent `task retry` could cause double-dispatch.

**Current mitigation**: Single-threaded daemon + `mark_acked` transition check prevents DB corruption. Re-read narrows the race window.

**Recommended fix**: `UPDATE tasks SET phase='acked' WHERE task_id=? AND phase='new'` (check rowcount) before calling `dispatch()`.

**Trigger**: Any report of duplicate executor processes on the same worktree.

---

## S2: Store task body in tasks table

**Risk**: `auto_advance` retrieves body from journal via `tail(limit=200)`. Journal auto-cleanup (30 days) or >200 entries could lose the body.

**Current mitigation**: limit raised from 50 to 200.

**Recommended fix**: Add `task_body TEXT` column — migration 14 in `db.py`, populate in `create_task()`, read in `auto_advance`.

**Trigger**: Any deferred task that fails with "no task body found in journal".

---

## Level 3: `--branch-from` daemon worktree creation

**Context**: Currently, serial chains use a shared worktree (Level 1). For cases where A and B need isolation but B must inherit A's commits, daemon-level worktree creation from A's branch tip would be needed.

**Why deferred**: Level 1 (shared worktree) covers most sequential scenarios. Adding worktree operations to the daemon increases the failure surface (worktree pruned, branch force-pushed, disk full). Better to observe real usage first.

**Recommended design**: `--branch-from TASK-A` flag. Daemon resolves A's `execution_repo_path`, runs `git worktree add -b wt/<B> <new-path> <A-branch-tip>`, dispatches B in the new worktree.

**Trigger**: ≥3 real cases where users need "inherit A's commits + isolated worktree for B" within a month.
