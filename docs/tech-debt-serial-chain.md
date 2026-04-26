# Serial Task Chain — Tech Debt

## Open Items

### R4: Concurrent dispatch guard (low risk, deferred)

**Context**: `auto_advance_dependent_tasks` re-reads the task phase before dispatch
to narrow the race window, but does not use a true CAS (compare-and-swap) UPDATE.
The daemon is single-threaded, so the realistic race is with concurrent CLI `task retry`
calls. The `mark_acked` transition check prevents DB state corruption, but the executor
process may already have been forked.

**Decision (2026-04-26)**: Accepted as low risk given single-threaded daemon and the
re-read guard. No concurrent dispatch test written.

**Recommended fix**: Add a `claim_for_dispatch` method that does
`UPDATE tasks SET phase='acked' WHERE task_id=? AND phase='new'` atomically,
returning rowcount. Only proceed with `exec_instance.dispatch()` if rowcount == 1.

---

### S2: Store task body in tasks table instead of journal

**Context**: `auto_advance_dependent_tasks` retrieves the original task body from
the journal via `_get_task_body_from_journal(journal, task_id, limit=200)`. This has
two durability risks:

1. Journal auto-cleanup (default 30 days) could delete the `created` entry for
   long-deferred tasks.
2. If a task accumulates >200 journal entries before being advanced, the body
   lookup silently fails.

**Short-term mitigation (2026-04-26)**: Increased `limit` from 50 to 200.

**Recommended fix**: Add `task_body TEXT` column to the `tasks` table schema.
Populate it in `create_task()`. Read it directly in `auto_advance_dependent_tasks()`
instead of scanning the journal. This requires:
- Schema migration 14 in `db.py`
- `schema.sql` column addition
- `TaskRecord` dataclass field
- `create_task()` parameter
- `auto_advance_dependent_tasks()` body retrieval path
