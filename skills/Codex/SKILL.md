---
name: agpair-codex
description: "Use agpair to dispatch coding tasks from Codex to external executors. Prefer blocking waits for normal tasks, use watch for background or parallel tasks, and prefer Antigravity or Gemini as executors."
---

# agpair

Use `agpair` when Codex is acting as the controller and you want to dispatch work to another executor.

## When to Use

Use `agpair` when:

- the task is larger than a trivial local edit
- you want durable state outside prompt context
- you want background / parallel / isolated-worktree execution
- you want structured receipts and retry / inspect / watch semantics

Do not use `agpair` when:

- the fix is tiny and local
- you still need exploratory reading before you know the task
- the work requires constant interactive judgment mid-execution

## Executor Selection

Executor resolution order:

1. explicit `--executor`
2. target-level default executor
3. `AGPAIR_DEFAULT_EXECUTOR`
4. product fallback (`antigravity`)

Recommended from Codex:

- **Current worktree, focused task:** `antigravity`
- **Parallel / isolated-worktree task:** `gemini`
- **Use `codex` as executor only when you explicitly want another Codex worker**

## Default Codex Workflow

- [ ] Preflight with `agpair doctor --repo-path <path>` or `--target <alias>`
- [ ] Write a self-contained brief
- [ ] Dispatch with `agpair task start ... --wait` for normal tasks
- [ ] Use `task watch` only when you intentionally go background / parallel
- [ ] Verify git evidence and tests yourself before trusting success

## Dispatch Patterns

### Normal task

```bash
agpair task start \
  --repo-path <path> \
  --executor antigravity \
  --body "<brief>"
```

`task start` waits by default, so there is no need to force `--no-wait` for ordinary Codex control flow.

### Parallel / isolated task

```bash
agpair task start \
  --repo-path <path> \
  --executor gemini \
  --body "<brief>" \
  --no-wait

agpair task watch <TASK_ID> --json
```

Use this only when:

- you want to continue other work immediately
- you have multiple independent tasks
- the expected runtime is long enough to justify async monitoring

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

## Completion Gate

Before you trust a completed task:

- check `agpair task status <TASK_ID> --json`
- inspect the latest commit / diff in the executor workspace
- run the required evidence commands yourself if correctness matters

Never treat “task completed” as sufficient proof.

## Anti-Patterns

| Thought | Reality |
|---------|---------|
| "I should always use `--no-wait`" | In Codex, blocking wait is the normal path. |
| "I need Claude Monitor semantics" | Codex can use `task watch` directly from shell. |
| "Since I am Codex, default executor should be codex" | If Codex is the controller, prefer another executor unless you explicitly want a second Codex worker. |
| "No need to care about executor defaults" | Codex control works best when executor choice is explicit or configured. |
| "Completed means safe" | Always verify git evidence and tests. |
