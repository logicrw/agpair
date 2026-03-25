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

Poll every **60 seconds**. Do not poll more frequently — Antigravity tasks typically take minutes to hours. Terminal phases are:

| Phase | Meaning |
|-------|---------|
| `evidence_ready` | Antigravity produced an evidence pack — review it |
| `committed` | Work committed successfully |
| `blocked` | Antigravity could not proceed |
| `stuck` | Daemon watchdog flagged the task as stale |
| `abandoned` | Task was abandoned |

**While polling:**

- `acked` means accepted, NOT completed — keep polling patiently
- Only escalate after **10 minutes** of `liveness_state: silent` (no heartbeat, no workspace activity). Before that, Antigravity may still be loading context, planning, or executing early steps
- Report phase transitions to the user as they happen
- Use `agpair task logs <TASK_ID> --limit 5` to check for progress details when escalating

**Why not `--wait`?** The built-in `--wait` blocks for up to 60 minutes, but AI agent Bash tools typically have a 2-minute timeout. The command gets killed and the waiter becomes orphaned. Polling keeps the agent in control.

### 3. Inspect task truth

Before any semantic follow-up, always read:

- `agpair task status <TASK_ID>`
- `agpair task logs <TASK_ID> --limit 20`

Do not choose `continue`, `approve`, `reject`, or `retry` until status and logs were read.

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

Then resume polling `task status` until the next terminal phase.

## Required gates

Before claiming completion:

- health was checked
- current task status was checked
- latest logs were checked
- polling continued until a terminal phase was observed
- no same-task semantic action was sent while an active waiter existed

## Anti-patterns

- Do not use `--wait` (default) — always pass `--no-wait` and poll instead.
- Do not treat `ACK` as proof of progress.
- Do not stop polling before a terminal phase is reached.
- Do not jump straight to `continue` because the user said "继续".
- Do not hide `desktop_reader_conflict` or `repo_bridge_session_ready=false`.
- Do not invent commands or transport paths outside the real `agpair` CLI.
