---
name: agpair
description: "Delegate coding work to Antigravity through agpair CLI: dispatch a task, wait for EVIDENCE_PACK or COMMITTED, inspect doctor/daemon health, review logs, or send continue/approve/reject/retry. Triggers on: 'send to Antigravity', 'use agpair', 'dispatch task', 'delegate to Antigravity', '交给 Antigravity', '派任务'."
---

# agpair

## Overview

Use this skill when your AI coding agent is the reviewer/controller and Antigravity is the executor.

`agpair` is the control surface for:

- preflight health checks
- task dispatch
- terminal-phase waiting
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

### 2. Inspect task truth

Use:

- `agpair task status <TASK_ID>`
- `agpair task logs <TASK_ID> --limit 20`

Do not choose `continue`, `approve`, `reject`, or `retry` until status and logs were read.

### 3. Blocking wait discipline

If you enter a blocking wait path (`task start` default `--wait`, `task wait`, or semantic commands with default `--wait`):

- treat the wait as an active operation until it exits
- keep consuming the same long-running command session, or keep checking:
  - `agpair task active-waits`
  - `agpair task status <TASK_ID>`
- do not tell the user the task is done while an active waiter still exists

`ACK` means accepted, not completed.

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

## Required gates

Before claiming completion:

- health was checked
- current task status was checked
- latest logs were checked
- if blocking wait was started, polling continued until terminal exit
- no same-task semantic action was sent while an active waiter existed

## Anti-patterns

- Do not start a blocking wait and then stop polling while the conversation is still alive.
- Do not treat `ACK` as proof of progress.
- Do not jump straight to `continue` because the user said “继续”.
- Do not hide `desktop_reader_conflict` or `repo_bridge_session_ready=false`.
- Do not invent commands or transport paths outside the real `agpair` CLI.
