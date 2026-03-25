---
name: agpair
description: "Delegate coding work to Antigravity through agpair CLI: dispatch a task, poll for completion, inspect doctor/daemon health, review logs, or send continue/approve/reject/retry. Triggers on: 'send to Antigravity', 'use agpair', 'dispatch task', 'delegate to Antigravity', '交给 Antigravity', '派任务'."
---

# agpair

## Overview

Use this skill when your AI coding agent is the reviewer/controller and Antigravity is the executor.

`agpair` is the control surface for:

- preflight health checks
- task dispatch
- terminal-phase polling
- evidence review
- semantic follow-up (`continue`, `approve`, `reject`, `retry`)

It is not a second orchestrator and it is not the semantic decision-maker.

## Triggering

This skill is intended to trigger when the user asks their AI agent to:

- send or delegate work to Antigravity
- use `agpair`
- inspect `doctor`, `daemon`, `task status`, or `task logs`
- review an `EVIDENCE_PACK`
- approve, reject, continue, or retry a delegated task

For the strongest activation, the user can explicitly say `use agpair` or `send this to Antigravity via agpair`.

## Workflow

### 1. Preflight first

Before any semantic action, check:

- `agpair doctor --repo-path <absolute-repo-path>`
- `agpair daemon status`

Do not continue if the target repo is unhealthy:

- `desktop_reader_conflict=true`
- `repo_bridge_session_ready=false`

### 2. Dispatch with `--no-wait` and poll

**Always dispatch with `--no-wait`:**

```bash
agpair task start --repo-path <path> --body "<task body>" --no-wait
```

This prints the TASK_ID and returns immediately.

**Then poll in a loop** until a terminal phase is reached:

```bash
agpair task status <TASK_ID>
```

Poll every **exactly 60 seconds** using `run_in_background` so the agent can respond to the user or do other work while waiting. Use a fixed 60-second interval — do NOT increase the interval over time (no exponential backoff, no adaptive delays). Antigravity tasks typically take 5–15 minutes for medium tasks, longer for large ones. Terminal phases are:

| Phase | Meaning |
|-------|---------|
| `evidence_ready` | Antigravity produced an evidence pack — review it |
| `committed` | Work committed successfully |
| `blocked` | Antigravity could not proceed |
| `stuck` | Daemon watchdog flagged the task as stale |
| `abandoned` | Task was abandoned |

**While polling:**

- `acked` means accepted, NOT completed — keep polling patiently
- **Never stop polling before a terminal phase.** Even if the task looks stuck, keep polling every 60 seconds.
- After **10 minutes** of `liveness_state: silent` (no heartbeat, no workspace activity): check logs with `agpair task logs <TASK_ID> --limit 5`, briefly inform the user of the situation, then **continue polling**. Do NOT stop and wait for user input. Antigravity may still be loading context, planning, or executing early steps.
- If `retry_recommended=true` appears in task status (set by daemon after ~15 min), run `agpair task retry <TASK_ID> --no-wait` and resume polling the new attempt.
- Report phase transitions to the user as they happen

**Why not `--wait`?** The built-in `--wait` blocks for up to 60 minutes, but AI agent Bash tools typically have a 2-minute timeout. The command gets killed and the waiter becomes orphaned. Polling keeps the agent in control.

### 3. Review evidence when `evidence_ready`

When phase becomes `evidence_ready`:

1. **Read the evidence pack**: `agpair task logs <TASK_ID> --limit 50`
2. **Check what was changed**: look for diff stat, key files, test results in the logs
3. **Spot-check the code**: read 2-3 key files from the working tree to verify quality
4. **Decide**:
   - `approve` — evidence is solid, tests pass, code looks good
   - `reject` — specific issues need fixing in the same session
   - `continue` — need Antigravity to do more work (e.g. add tests, fix a bug)

Do not choose a semantic action until you have read both status and logs.

### 4. Guard against premature intervention

If `agpair task active-waits` shows the task, or `task status` shows `waiter_state=waiting`:

- do not send another semantic action on the same task
- do not abandon/retry the task
- only use `--force` if the waiter is clearly orphaned

### 5. Pick one semantic action

Choose exactly one:

- `continue` for same-session follow-up
- `approve` when evidence is good enough for finalization
- `reject` when work must continue in the same session
- `retry` only when the session is stale or not worth continuing

All semantic commands also default to `--wait`. **Use `--no-wait` and poll** for these too:

```bash
agpair task continue <TASK_ID> --body "<feedback>" --no-wait
agpair task approve <TASK_ID> --body "<message>" --no-wait
```

**What to write in `--body`:**

- `approve`: summarize what you reviewed, suggest commit message
- `reject`: be specific — which file, which issue, what to fix. Antigravity reads this in the same session and continues working.
- `continue`: describe the additional work needed (e.g. "add tests for X", "also implement Y")

Do NOT send empty `--body`. Antigravity uses the feedback to guide its next actions.

Then resume polling `task status` until the next terminal phase.

## Required gates

Before claiming completion:

- health was checked
- current task status was checked
- latest logs were checked
- polling continued until a terminal phase was observed
- no same-task semantic action was sent while an active waiter existed

## Recovery: stuck session

Antigravity has **built-in auto-recovery**: the `DelegationReceiptWatcher` detects stale tasks (no receipt after timeout), automatically creates a new background session, and retries up to 2 times before falling back to BLOCKED.

### When a task is stuck during execution (no evidence produced)

1. **Wait for auto-recovery.** The companion extension will detect the stale task and attempt session recovery automatically.
2. If `phase` changes to `blocked` after auto-recovery exhaustion, use `agpair task retry <TASK_ID> --no-wait` to start a fresh attempt.

### When approve/continue is not consumed (evidence_ready but session dead)

This happens when Antigravity produced evidence but the session died before consuming semantic actions. Auto-recovery does NOT help here — the session that should process the approve is gone.

1. **Commit locally** — the code is already in the working tree:
   ```bash
   git add <files>
   git commit -m "..."
   ```
2. **Abandon the stuck task:**
   ```bash
   agpair task abandon <TASK_ID>
   ```
3. **Dispatch the next task fresh** with `agpair task start --no-wait`. The companion extension will automatically terminate the old session and clear the lock for the new task — no manual window reload needed.

### Last resort: manual window reload

Only if `agpair doctor` shows `repo_bridge_session_ready=false` AND dispatching a new task fails, ask the user to reload the Antigravity desktop window.

## Multi-task: new task auto-clears old session

Since companion extension `949111b`, dispatching a new task to a workspace that already has an old/stuck task will **automatically terminate the old session and clear the lock**. You do NOT need to wait for the old task to finish or manually abandon it.

This means:
- After `evidence_ready` + local commit, just dispatch the next task immediately
- If a task is stuck, just dispatch the replacement — the old session gets killed automatically
- No need to call `agpair task abandon` before dispatching (though it's still safe to do so)

## Anti-patterns

- Do not use `--wait` (default) — always pass `--no-wait` and poll instead.
- Do not treat `ACK` as proof of progress.
- Do not stop polling before a terminal phase is reached.
- Do not jump straight to `continue` because the user said "继续".
- Do not hide `desktop_reader_conflict` or `repo_bridge_session_ready=false`.
- Do not invent commands or transport paths outside the real `agpair` CLI.
- Do not keep sending `approve`/`continue` to a dead session — abandon and reload instead.
