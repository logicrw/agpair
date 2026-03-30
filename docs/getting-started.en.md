# Getting Started with agpair

This guide walks you through going from zero to your first successful task dispatch.

> **Key insight**: In normal use, you talk to your AI coding agent (Codex, Claude Code, etc.) in natural language and it uses `agpair` as its tool belt. You only need the CLI directly when you want to inspect state, debug, or manually take over.

## Prerequisites

| Requirement | Notes |
|-------------|-------|
| **macOS** | Primary tested platform. Linux is untested but may work. |
| **Python 3.12+** | For the `agpair` CLI |
| **Node.js 18+** | For building the companion extension |
| **`agent-bus`** | Shared message bus CLI — must be on your `PATH` |
| **[Antigravity](https://antigravity.google/) IDE** | The companion extension runs inside it |

### What is `agent-bus`?

`agent-bus` is the shared local message bus that agpair uses to dispatch tasks and receive receipts between your AI coding agent (desktop side) and Antigravity (code executor). It is distributed as part of the Antigravity tooling environment. If you are using an Antigravity-managed setup, it should already be available. Otherwise, install the `agent-bus` binary provided by your Antigravity distribution and ensure it is on your `PATH`. There is currently no standalone public package for `agent-bus`.

### What is the Antigravity IDE?

The [Antigravity](https://antigravity.google/) IDE is a VS Code-compatible IDE that provides the execution environment for agpair tasks. The companion extension in this repo (`companion-extension/`) runs inside it, providing the HTTP bridge between `agpair` CLI and Antigravity's execution capabilities. The `antigravity --install-extension` command used below is the Antigravity IDE's CLI for sideloading `.vsix` extensions, analogous to `code --install-extension` in VS Code.

## Step 1: Install agpair

```bash
git clone https://github.com/logicrw/agpair.git agpair
cd agpair
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e '.[dev]'
```

To make `agpair` available globally (so any AI coding agent can use it):

```bash
ln -sf "$PWD/.venv/bin/agpair" ~/.local/bin/agpair
which agpair   # should print ~/.local/bin/agpair
```

### Step 1b: Install the companion extension

The companion extension provides the HTTP bridge between `agpair` CLI and the [Antigravity](https://antigravity.google/) IDE. It is bundled in this repo:

```bash
cd companion-extension
npm install
npm run build
npm run package
antigravity --install-extension antigravity-companion-extension-*.vsix
cd ..
```

Reload the Antigravity window after installing. The extension activates on startup.

> **Security note**: The bridge listens on `127.0.0.1` only. By default, it is secured with an auto-generated bearer token stored in VS Code's SecretStorage — no manual configuration is needed. Mutating endpoints (`/run_task`, `/continue_task`, `/write_receipt`, etc.) require a valid `Authorization: Bearer <token>` header; read-only endpoints (`/health`, `/task_status`) are accessible without auth so that `agpair doctor` works out of the box. For local debugging, you can disable auth by setting `antigravityCompanion.bridgeInsecure = true` in IDE settings — this is not recommended for normal use. Request bodies are limited to 1 MiB.

## Step 2: Confirm agent-bus is available

```bash
agent-bus --help
```

If this fails, install or configure `agent-bus` first. agpair cannot dispatch tasks without it. See the [Prerequisites](#what-is-agent-bus) section for details on where to obtain it.

## Step 3: Check target repo health

Before dispatching your first task, make sure the environment is ready:

```bash
agpair doctor --repo-path /path/to/your/project
```

The three fields that matter most:

| Field | What you want |
|-------|---------------|
| `agent_bus_available` | `true` |
| `desktop_reader_conflict` | `false` |
| `repo_bridge_session_ready` | `true` |

**Example `doctor` output (healthy):**

```
agpair doctor — target: /Users/you/projects/my-app

  agent_bus_available ............ true
  desktop_reader_conflict ........ false
  repo_bridge_session_ready ...... true
  bridge_url ..................... http://127.0.0.1:8765

All checks passed.
```

**If `desktop_reader_conflict=true`**: Another desktop watcher is consuming the same receipts. Stop it before continuing.

**If `repo_bridge_session_ready=false`**: The Antigravity window for this repo is not healthy. Confirm the correct repo is open, reload/restart the Antigravity window, and re-run `doctor`.

## Step 4: Start the daemon

```bash
agpair daemon start
agpair daemon status
```

The daemon is a lightweight background helper. It handles:

- Receipt ingestion (`ACK`, `EVIDENCE_PACK`, `BLOCKED`, `COMMITTED`)
- Task → session continuity
- Stuck detection (soft watchdog → hard timeout)

It is **not** a semantic reviewer — it does not interpret code or make decisions.

## Step 5: Dispatch your first task

```bash
agpair task start \
  --repo-path /path/to/your/project \
  --body "Goal: fix the failing test and return EVIDENCE_PACK."
```

This returns a `TASK_ID` and, by default, **waits** until the task reaches a terminal phase.

**Example output:**

```
Task created: TASK-MY-APP-SMOKE-FIX-20260324-01
Waiting for terminal phase ...
Phase changed: new → acked
Phase changed: acked → evidence_ready
Task reached terminal phase: evidence_ready
```

To fire-and-forget instead:

```bash
agpair task start \
  --repo-path /path/to/your/project \
  --body "Goal: ..." \
  --no-wait
```

## Step 6: Inspect the task

```bash
agpair task status <TASK_ID>
agpair task logs <TASK_ID>
```

**Example `task status` output:**

```
task_id:    TASK-MY-APP-SMOKE-FIX-20260324-01
phase:      evidence_ready
attempt_no: 1
session_id: sess-abc123
created_at: 2026-03-24T10:00:00Z
```

### Task phases

| Phase | Meaning |
|-------|---------|
| `new` | Task created locally, not yet acknowledged |
| `acked` | Antigravity accepted and started a session |
| `evidence_ready` | Antigravity returned `EVIDENCE_PACK` — review the logs |
| `blocked` | Execution failed, blocker reason available |
| `committed` | Task completed and committed |
| `stuck` | No progress for too long (daemon-detected) |

## Step 7: Choose the next action

After reviewing `task logs`, pick exactly one:

```bash
# Continue in the same session
agpair task continue <TASK_ID> --body "Please address the remaining issue."

# Approve and commit
agpair task approve <TASK_ID> --body "Approved. Commit and return COMMITTED."

# Reject but stay in the same session
agpair task reject <TASK_ID> --body "Not ready. Fix the evidence gap."

# Retry with a fresh executor session
agpair task retry <TASK_ID> --body "Retry with a fresh session."

# Stop tracking locally (does not notify Antigravity)
agpair task abandon <TASK_ID> --reason "No longer needed."
```

**When to `continue` vs `retry`:**

- **`continue`** — the session is still healthy, just needs another round
- **`retry`** — the session is broken, stuck, or not worth continuing

## Step 8: Using agpair with your AI coding agent (the normal workflow)

In daily use, the intended flow is:

1. You tell your AI agent (Codex, Claude Code, etc.) what you want in natural language
2. The agent calls `agpair doctor`, `task start`, `task status`, etc.
3. Antigravity executes the work
4. You review the results and give the next instruction

The CLI exists as a manual fallback for when you need to:

- Inspect a stuck task directly
- List all locally tracked tasks
- Manually retry or abandon a task
- Confirm the bridge is healthy
- Take over when the agent is unavailable

## Common Problems

### `desktop_reader_conflict=true`

Another desktop watcher is consuming the same receipts. Stop it, then start `agpair daemon`.

### `repo_bridge_session_ready=false`

The Antigravity window for this repo is not ready. Confirm the correct repo is open, reload/restart the window, and re-run `agpair doctor`.

### `BLOCKED`

The current attempt failed. Inspect with `agpair task logs <TASK_ID>`, then decide: `continue` the same session or `retry` with a fresh one.

## Optional: Auto-start daemon on login

```bash
# Install launchd agent
python3 -m agpair.tools.install_agpair_daemon_launchd install \
  --agpair-home ~/.agpair

# Check status
python3 -m agpair.tools.install_agpair_daemon_launchd status

# Remove
python3 -m agpair.tools.install_agpair_daemon_launchd uninstall
```

This is entirely optional — start with manual `agpair daemon start` until you are comfortable with the workflow.
