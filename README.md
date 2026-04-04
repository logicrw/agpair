# agpair

![Python](https://img.shields.io/badge/python-≥3.12-blue)
![Platform](https://img.shields.io/badge/platform-macOS-lightgrey)
![License](https://img.shields.io/badge/license-MIT-green)

[中文说明](README.zh-CN.md) | [新手教程](docs/getting-started-zh.md) | [中文命令参考](docs/usage.zh-CN.md)

**agpair** is a durable orchestration layer for AI coding workflows: break work into tasks, dispatch them to supported executors, track structured results, recover from failures, and keep long-running projects moving without stuffing everything into chat context. It currently supports [Antigravity](https://antigravity.google/), the local Codex CLI, and the local Gemini CLI.

Works with [Codex](https://openai.com/codex) (CLI & Desktop), [Claude Code](https://docs.anthropic.com/en/docs/claude-code), and any tool that can run shell commands.

## Why agpair?

Many tools are great at **one-shot delegation**:

- send one prompt
- wait for one result
- maybe inspect or cancel it

That is enough for a quick rescue, quick review, or one-off patch. It is **not** enough for the workflow many serious codebases actually need:

1. write a plan or project spec
2. split it into multiple tasks
3. dispatch those tasks one by one, or in parallel across isolated worktrees
4. watch progress over time
5. decide what to do next based on structured results
6. recover when a task stalls, blocks, or needs a fresh resume

That is the gap `agpair` fills.

`agpair` is useful when you want:

- **persistent task state** instead of relying on chat context alone
- **structured receipts** (`ACK`, `EVIDENCE_PACK`, `BLOCKED`, `COMMITTED`) instead of guessing from free text
- **controller semantics** like `continue / approve / reject / retry`
- **watchdog and health checks** for long-running work
- **executor flexibility** so the same control plane can drive Antigravity, Codex CLI, and Gemini CLI without rewriting the workflow
- **lower token burn** in long workflows because state lives in SQLite/journal/receipts instead of being re-explained in every chat turn

### Why this matters in real usage

Without `agpair`, a controller agent has to keep a growing amount of workflow state in context:

- which task is currently active
- which tasks are already complete
- what the previous executor returned
- which tasks need retry / continue / approval
- whether the latest result was a true success, a block, or just partial evidence

That gets expensive and brittle fast.

`agpair` externalizes that state into:

- SQLite task records
- journals
- structured receipts
- `doctor` / `inspect` / `watch`

So the controller can query the current truth instead of carrying the whole project history inside the prompt window.

In other words:

- a plugin is often the best tool for **“send one task to Codex quickly”**
- `agpair` is the better tool for **“run a multi-step engineering workflow without losing the plot”**

**agpair does not replace your AI agent.** It gives your AI agent a durable control plane.

### Current best-practice controller role

`agpair` is controller-agnostic, but current practical experience suggests:

- **Claude Code** is often the best fit for long-running orchestration
  - split a large plan into tasks
  - keep dispatching / watching / deciding over time
  - manage parallel work across isolated worktrees
- **Codex** is extremely strong as an executor and short-chain reviewer, but is less natural as the long-running controller in the same workflow

This is a usage recommendation, not a product limitation: `agpair` itself stays neutral and works as the lifecycle layer either way.

### What agpair is *not*

- Not a semantic controller — your AI agent stays in charge of planning and decisions.
- Not a “just type one slash command” UX layer — it is closer to infrastructure than a thin plugin.
- Not a zero-dependency runtime — it still depends on `agent-bus`, supported executors, and the bundled companion extension where applicable.

## Prerequisites

| Requirement | Notes |
|-------------|-------|
| **macOS** | Primary tested platform. Linux is untested but may work. |
| **Python 3.12+** | For the `agpair` CLI |
| **Node.js 18+** | For building the companion extension |
| **`agent-bus`** | Shared message bus CLI — see below |
| **[Antigravity](https://antigravity.google/) IDE** | The companion extension runs inside it |

### `agent-bus`

`agent-bus` is the local message bus agpair uses for its Antigravity-backed execution path. If you use Antigravity as an executor, it must be available on your `PATH`.

> **Note:** `agent-bus` is distributed as part of the Antigravity tooling environment. If you are only using `codex` / `gemini` executors, agpair's lifecycle still works, but the Antigravity-specific transport path is unused. If you want Antigravity available as an executor, ensure the `agent-bus` binary is on your `PATH`.

### Antigravity IDE

The companion extension (`companion-extension/`) is a VS Code-compatible extension that runs inside the [Antigravity](https://antigravity.google/) IDE. The `antigravity --install-extension` command used below is the Antigravity IDE's CLI for sideloading `.vsix` extensions, analogous to `code --install-extension` in VS Code.

## Quick Start

### 1. Install agpair and the companion extension

```bash
git clone https://github.com/logicrw/agpair.git && cd agpair
python3 -m venv .venv && source .venv/bin/activate
python3 -m pip install -e '.[dev]'

# Build and install the companion extension
cd companion-extension && npm install && npm run package
antigravity --install-extension antigravity-companion-extension-*.vsix
cd ..
```

### 2. Verify the environment

```bash
agpair doctor --repo-path /path/to/your/project
```

You want `agent_bus_available=true`, `desktop_reader_conflict=false`, and `repo_bridge_session_ready=true`. See the [Getting Started guide](docs/getting-started.en.md) for details and troubleshooting.

### 3. Start working

```bash
agpair daemon start
agpair task start --repo-path /path/to/your/project \
  --body "Goal: fix the failing smoke test. Scope: smoke tests. Required changes: update assertion. Exit criteria: tests pass and return EVIDENCE_PACK."
```

By default, `task start` **waits** until the task reaches a terminal phase. Add `--no-wait` for fire-and-forget.

If you use the same repo frequently, you can save it as a local target alias and reuse `--target`:

```bash
agpair target add --name my-project --repo-path /path/to/your/project
agpair doctor --target my-project
agpair inspect --target my-project --json
agpair task start --target my-project \
  --body "Goal: fix the failing smoke test. Scope: smoke tests. Required changes: update assertion. Exit criteria: tests pass and return EVIDENCE_PACK."
```

For the full step-by-step walkthrough, see the detailed guides below.

## Architecture

```
┌───────────────┐     agpair CLI      ┌─────────────┐     agent-bus      ┌──────────────────┐
│               │  ─────────────────▶  │             │  ───────────────▶  │   Antigravity    │
│   AI Agent    │   task start/wait    │   agpair    │   dispatch/recv    │   (executor)     │
│  (chat UI)    │  ◀─────────────────  │   daemon    │  ◀───────────────  │                  │
│               │   status/receipts    │             │   receipts/ack     │   companion ext  │
└───────────────┘                      └──────┬──────┘                    └──────────────────┘
                                              │
                                         SQLite DB
                                     (tasks, receipts,
                                       journals)
```

**Data flow:** Controller agent → `agpair task start` → agpair dispatches to the selected executor → executor returns structured progress / terminal state → agpair ingests and persists state → controller reads status/watch/inspect.

## How it Works in Practice

In normal use, **you do not need to manually type every `agpair` command**.

The intended workflow is:

1. You tell your AI agent what you want in natural language
2. Your AI agent calls `agpair` commands behind the scenes
3. Antigravity executes the task
4. `agpair` keeps the mechanical path stable

The CLI is still valuable for manual inspection, debugging, retry, and recovery when your AI agent is not available.

## Skill Integration

This repo ships a reusable skill at [skills/agpair/SKILL.md](skills/agpair/SKILL.md) that teaches your AI tool how to use `agpair` correctly — preflight checks, blocking wait discipline, and semantic action flow.

Install for your tool of choice:

```bash
# Codex
mkdir -p ~/.codex/skills
ln -sfn "$PWD/skills/agpair" ~/.codex/skills/agpair

# Claude Code
mkdir -p ~/.claude/skills
ln -sfn "$PWD/skills/agpair" ~/.claude/skills/agpair
```

After installing, restart or open a new window. Say `use agpair` in your prompt to trigger it explicitly.

> **Other tools** (Cursor, Aider, OpenCode, etc.): copy the content of `skills/agpair/SKILL.md` into your tool's instruction file (e.g. `.cursorrules`, `AGENTS.md`).

## Status

agpair v1.0 started as an Antigravity bridge and now exposes a growing multi-executor control plane.

What already works:

- `agent-bus`-based task dispatch with auto-wait
- Local SQLite-backed task / receipt / journal state
- Continuation flow: `continue`, `approve`, `reject`, `retry`, `abandon` (with explicit ACK/NACK hardening)
- Standalone `task wait` with configurable timeout/interval
- Streaming `task watch` for continuous progress observation until terminal phase
- Daemon with receipt ingestion, session continuity, and stuck detection
- `inspect` command for unified local repo/task overview, integrating `doctor` and task context
- Local `target` aliases so high-frequency commands can use `--target <alias>` instead of a full repo path
- `doctor` preflight checks (local health, desktop conflicts, bridge health, concurrency policy/pending tasks)
- Structured terminal receipts (v1) and JSON CLI output with A2A state hints
- Task start idempotency keys and structured committed result/failure context
- Minimal persistent task dependency and concurrency metadata for controller execution planning
- Internal `ExecutorAdapter` abstraction extended to expose a stable `backend_id` (`antigravity` / `codex_cli` / `gemini_cli`), now visible in read-only info (e.g., `task status --json` and `doctor`) for transparency.
- `task start --executor codex` and `task start --executor gemini` as first-class entry points, with both CLI-backed executors now flowing through dispatch / poll / canonical terminal receipt synthesis
- Added formal Continuation Capability Matrix to encode policy for backends (e.g., `same_session` for Antigravity, `fresh_resume_first` for Codex CLI, and conservative/limited continuation for Gemini), visible in `task status --json`.
- Added formal Executor Safety Metadata to encode fail-closed execution postures (e.g., `is_mutating`, `is_concurrency_safe`, `requires_human_interaction`), enforcing explicit capability signals from backend adapters.
- Implemented `fresh_resume_first` path for review/approval flows, allowing Codex-backed tasks to seamlessly carry over feedback via a fresh dispatch.
- Automatic closeout for eligible `evidence_ready` tasks when strong repo-side commit evidence exists but a final terminal receipt never arrived
- Background daemon stdout/stderr now persist to `~/.agpair/daemon.stdout.log` and `~/.agpair/daemon.stderr.log`
- Gemini CLI executor support is now wired into the lifecycle, while continuation remains conservative by design.

### Why teams end up liking it

The practical value of `agpair` is not just “delegation”.

It gives you:

- a **durable control plane** instead of a one-shot bridge
- **machine-readable results** instead of free-form completion prose
- **recovery paths** when sessions die or tasks block
- **multi-executor flexibility** without rebuilding your workflow around each tool
- a way to keep long-running work moving **without stuffing every intermediate state into token context**

What is explicitly *not* in scope:

- Replacing your AI agent as the semantic controller
- Hiding all operational boundaries

## Documentation

| Document | Description |
|----------|-------------|
| [Getting Started](docs/getting-started.en.md) | Step-by-step beginner guide |
| [Command Reference](docs/usage.md) | Full CLI reference |

## Repository Structure

```
agpair/
├── agpair/                 # Python CLI package
├── companion-extension/    # Bundled Antigravity companion (TypeScript)
│   ├── src/                # Extension source
│   ├── package.json
│   └── esbuild.js
├── skills/
│   └── agpair/             # Optional agent skill package
├── tests/                  # Python integration tests
├── docs/                   # Documentation
└── pyproject.toml
```

This is a **single self-contained repo**. No external checkout is needed.

## Important Operating Notes

### A2A State Hints

The CLI JSON outputs (`task status`, `task wait`, and `task watch`) include an `a2a_state_hint` field mapping internal phases to approximate A2A `TaskState` values (e.g., mapping blocked auth tasks to `auth-required`). This is purely a semantic hint-level alignment for AI consumers—**agpair does not implement a full A2A server or the complete A2A protocol**. Its primary goal remains to be a robust local execution bridge.

### Concurrency rule (one task per worktree)

Same-repo, same-worktree concurrent editing is not supported. You must limit execution to **one active delegated task per repo worktree**. For parallel work, use a separate `git worktree` or clone a separate repo. `agpair doctor` now explicitly exposes this policy and shows the current pending task count and IDs so tooling can isolate tasks correctly.

### Desktop receipt exclusivity

agpair consumes `code -> desktop` receipts. If another desktop-side watcher is already claiming the same receipts, `agpair doctor` will report `desktop_reader_conflict=true` and the daemon will refuse to start. Stop the other watcher first.

### One controller per task

You can open multiple agent windows, but avoid having two windows send `continue / approve / reject / retry` for the **same** `TASK_ID`. Rule: one active task → one main agent window.

### The daemon is not a second brain

The daemon only handles mechanical work (receipts, continuity, stuck detection). It does not review code or make semantic decisions.

### `doctor` is a preflight, not a ritual

Run `agpair doctor` when starting a new task, switching repos, restarting the daemon, or investigating a stuck task. You do not need it before every `status` or `logs` check.

### Bridge security

The companion extension's HTTP bridge listens on `127.0.0.1` only. **By default, the bridge is secured with an auto-generated bearer token** stored in VS Code's SecretStorage. Mutating endpoints (`/run_task`, `/continue_task`, `/write_receipt`, etc.) require a valid `Authorization: Bearer <token>` header; read-only endpoints (`/health`, `/task_status`) remain accessible without authentication so that `agpair doctor` works out of the box.

The token is generated automatically on first activation and persisted securely — no manual configuration is needed for normal use. You can override the token via the `antigravityCompanion.bridgeToken` IDE setting. For local debugging only, you can disable auth entirely by setting `antigravityCompanion.bridgeInsecure = true` — **this is not recommended for normal use** as it allows any local process to call mutating bridge endpoints. Request bodies are limited to 1 MiB.

## macOS Auto-Start (Optional)

```bash
# Install
python3 -m agpair.tools.install_agpair_daemon_launchd install \
  --agpair-home ~/.agpair

# Check
python3 -m agpair.tools.install_agpair_daemon_launchd status

# Uninstall
python3 -m agpair.tools.install_agpair_daemon_launchd uninstall
```

## Troubleshooting

### `desktop_reader_conflict=true`

Another desktop watcher is consuming the same receipts. Stop it, then start `agpair daemon`.

### `repo_bridge_session_ready=false`

The Antigravity window for this repo is not ready. Confirm the correct repo is open in Antigravity, reload/restart the window, then re-run `agpair doctor --repo-path ...`.

### `BLOCKED`

The current attempt did not complete. Run `agpair task logs <TASK_ID>` to inspect, then decide whether to `continue` the same session or `retry` with a fresh one. By default, logs filter out transient operational chatter; use `--all` to view the full history.

## License

MIT
