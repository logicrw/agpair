# agpair Usage

`agpair` is a durable task lifecycle layer for multiple executors.

Use it when:
- Your AI coding agent is the main controller
- You are using Antigravity, Codex CLI, or Gemini CLI as the executor
- You want light mechanical automation without turning the tool into a second brain

## Environment

`agpair` stores its local state under:

- default: `~/.agpair/`
- override for testing: `AGPAIR_HOME=/path/to/custom/root`

It expects an `agent-bus` executable:

- default lookup: `agent-bus`
- override: `AGPAIR_AGENT_BUS_BIN=/absolute/path/to/agent-bus`

## Core commands

### Check health

```bash
agpair doctor
agpair doctor --repo-path /absolute/path/to/repo
```

The report includes:
- config root
- DB existence
- `db_error` when the DB file exists but is unreadable/corrupt
- `agent-bus` availability
- daemon pid/status visibility
- latest known receipt id
- `desktop_reader_conflict` when another desktop-side watcher is already claiming the same `code -> desktop` receipts
- optional repo bridge preflight when `--repo-path` is provided:
  - repo bridge marker path / port
  - bridge `/health` reachability
  - `sdk_initialized`
  - `ls_bridge_ready`
  - `monitor_running`
  - `workspace_paths` match
  - `agent_bus_watch_running`
  - `agent_bus_delegation_enabled`
  - `receipt_watcher_running`
  - consolidated `repo_bridge_warning` when the Antigravity host/session looks degraded

If `repo_bridge_warning` mentions:
- `ls_bridge_ready=false`: treat it as a likely stale Antigravity session / missing CSRF state
- `workspace_paths missing repo`: you are pointed at the wrong Antigravity window
- `bridge health probe failed`: the companion bridge is not currently reachable on the discovered port

### Standalone mode matters

`agpair` v1 assumes it is the only desktop-side consumer for Antigravity receipts.

If another desktop-side receipt watcher is already running on the same machine, both tools will compete for the same `code -> desktop` messages. In that situation:

- `agpair doctor` will report `desktop_reader_conflict=true`
- `agpair daemon start` and `agpair daemon run` will refuse to start
- use `--force` only when you know the environment is otherwise isolated and you are intentionally taking over receipt consumption
- `--force` bypasses the preflight warning only; it does not bypass the live shared desktop-reader lock

### Start daemon

```bash
agpair daemon start
agpair daemon status
```

For login-time auto-start on macOS:

```bash
python3 -m agpair.tools.install_agpair_daemon_launchd install \
  --agpair-home ~/.agpair
python3 -m agpair.tools.install_agpair_daemon_launchd status
```

Remove it with:

```bash
python3 -m agpair.tools.install_agpair_daemon_launchd uninstall
```

For foreground debugging:

```bash
agpair daemon run --once
agpair daemon run --interval-ms 1000 --timeout-seconds 1800
```

Background daemon logs are written to:

- `~/.agpair/daemon.stdout.log`
- `~/.agpair/daemon.stderr.log`

Override the standalone guard only if you explicitly want `agpair` to own receipt ingestion in the current environment:

```bash
agpair daemon start --force
agpair daemon run --once --force
```

Stop it with:

```bash
agpair daemon stop
```

### Start a task

```bash
agpair task start \
  --repo-path /absolute/path/to/repo \
  --body "Goal: implement the smoke fix and show evidence."
```

To explicitly use the Codex backend:

```bash
agpair task start \
  --executor codex \
  --repo-path /absolute/path/to/repo \
  --body "Goal: ..."
```

To explicitly use the Gemini backend:

```bash
agpair task start \
  --executor gemini \
  --repo-path /absolute/path/to/repo \
  --body "Goal: ..."
```

Current backend policy summary:

- `antigravity`: interactive IDE executor
- `codex`: CLI executor
- `gemini`: CLI executor

Note: all executors use fresh sessions for retries.

By default, `task start` blocks until the task reaches a terminal phase.
To return immediately after dispatch:

```bash
agpair task start \
  --repo-path /absolute/path/to/repo \
  --body "Goal: ..." \
  --no-wait
```

You may also provide your own id:

```bash
agpair task start \
  --task-id TASK-SMOKE-001 \
  --repo-path /absolute/path/to/repo \
  --body "Goal: ..."
```

### Task Metadata (Orchestration Hints)

You can attach orchestration metadata to a task to help the controller plan parallel and isolated execution.
**Note:** These fields are currently **metadata-only**. They are persisted in the database and surfaced in `status` and `inspect` outputs, but they are *not* runtime-enforced or automatically executed by the `agpair` daemon.

- `depends_on`: List of previous task IDs that must complete before this one.
- `isolated_worktree`: Boolean indicating intent to execute the task in a separate git worktree.
- `worktree_boundary`: The intended root directory path for the task's execution boundary.
- `setup_commands`: Pre-run shell steps (e.g., creating a worktree or starting a service).
- `teardown_commands`: Post-run shell steps (e.g., cleaning up the worktree).
- `env_vars`: Per-task environment overrides (e.g., `PORT`, `AGPAIR_PORT_OFFSET`).
- `spotlight_testing`: Boolean intent to prioritize localized test runs over full-suite execution.

**Parallelism recommendation:** Always parallelize across worktrees, not inside one worktree.

All task-changing commands support the same wait controls:

| Option | Default | Meaning |
|--------|---------|---------|
| `--wait / --no-wait` | `--wait` | wait for a terminal phase after dispatch |
| `--interval-seconds` | `5` | local polling interval in seconds |
| `--timeout-seconds` | `3600` | max wait time; intentionally longer than daemon stuck timeout |

### Inspect a task

```bash
agpair task status TASK-SMOKE-001
agpair task logs TASK-SMOKE-001
```

### Fresh retry

```bash
agpair task retry TASK-SMOKE-001 --body "Retry with a fresh executor session."
```

`retry` is always explicit CLI control in v1. The daemon only marks `retry_recommended=true`; it does not auto-retry.
It also waits by default unless you pass `--no-wait`.

### List local tasks

```bash
agpair task list
agpair task list --phase acked
```

This is the fastest way to see what the local SQLite state still tracks. Output includes:

- `task_id`
- `phase`
- `attempt`
- `retry`
- `recommended`
- `repo`

### Abandon a local task

```bash
agpair task abandon TASK-SMOKE-001 --reason "manual cleanup"
```

This is a local bookkeeping command. It does **not** send anything to Antigravity.
Use it when you want to stop tracking a hanging local task without editing SQLite by hand.

### Wait for a task (standalone)

If you dispatched with `--no-wait`, you can attach later:

```bash
agpair task wait TASK-SMOKE-001
agpair task wait TASK-SMOKE-001 --timeout-seconds 600 --interval-seconds 10
```

Exit code `0` means success (`evidence_ready` / `committed`).
Exit code `1` means `blocked`, `stuck`, `abandoned`, timeout, or **watchdog** (the
daemon flagged `retry_recommended=true` while the task was still `acked`).

Some `evidence_ready` tasks can now auto-close when strong repo-side commit evidence exists but a final terminal receipt never arrived. In that case, inspect `task status --json` / `inspect --json` before manually abandoning the task.

When the watchdog triggers, the message will tell you to run
`agpair task retry <TASK_ID>`.

### Auto-wait options

All dispatching commands (`start`, `retry`) accept:

| Flag | Default | Notes |
|------|---------|-------|
| `--wait / --no-wait` | `--wait` | Wait for terminal phase after dispatch |
| `--interval-seconds` | `5` | Seconds between status polls |
| `--timeout-seconds` | `3600` | Maximum wait duration (intentionally > daemon stuck timeout of 1800s) |

`status`, `logs`, and `wait` do **not** have `--wait/--no-wait`.

## Failure posture

`agpair` is intentionally conservative.

- duplicate receipts are ignored
- stale receipts do not roll task state backward
- invalid continuation targets fail closed
- daemon does not send semantic messages
- daemon does not auto-create fresh retries
- daemon sets `retry_recommended=true` after a soft watchdog window before the hard stuck timeout
- `task wait` and default auto-wait exit early (code 1) when the watchdog flags `retry_recommended=true` on an acked task, rather than blind-waiting until the hard timeout

If transport dispatch fails:
- the CLI exits with code `1`
- a failure event is written to the local journal
- the task is not silently advanced

## Live troubleshooting note

In a real smoke on this machine, `agpair` successfully:
- dispatched a task
- owned the receipt path exclusively after the old desktop watcher was stopped
- persisted the returned terminal state into the local task journal

The live terminal result was still `BLOCKED`, because Antigravity failed to create a fresh executor session and returned:

- `LS StartCascade: 403 -- Invalid CSRF token`

Treat that as a host/runtime problem on the Antigravity side, not as an `agpair` CLI transport failure. The practical recovery path is:

1. reload or restart the Antigravity window
2. re-authenticate if the host session is stale
3. run a fresh `agpair task retry <TASK_ID> --body "..."`
