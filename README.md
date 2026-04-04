# agpair

![Python](https://img.shields.io/badge/python-≥3.12-blue)
![Platform](https://img.shields.io/badge/platform-macOS-lightgrey)
![License](https://img.shields.io/badge/license-MIT-green)

[中文说明](README.zh-CN.md) | [新手教程](docs/getting-started-zh.md) | [中文命令参考](docs/usage.zh-CN.md)

**agpair** is a lightweight CLI that gives AI coding agents a unified task lifecycle layer for supported executors — currently [Antigravity](https://antigravity.google/) and the local Codex CLI — so you can dispatch coding tasks, track their progress, and review results without leaving the conversation.

Works with [Codex](https://openai.com/codex) (CLI & Desktop), [Claude Code](https://docs.anthropic.com/en/docs/claude-code), and any tool that can run shell commands.

## Why agpair?

When you use an AI coding agent + Antigravity together, there is a mechanical gap between "I told my agent what to do" and "Antigravity finished executing it." That gap includes:

- dispatching the task over `agent-bus`
- tracking which task maps to which executor session
- collecting receipts (`ACK`, `EVIDENCE_PACK`, `BLOCKED`, `COMMITTED`)
- detecting stuck tasks
- providing a clean continue / approve / reject / retry flow

**agpair fills that gap.** It is a tool belt for your AI agent — and a manual fallback for you when you need direct control.

### What agpair is *not*

- Not a semantic controller — your AI agent stays in charge of decisions.
- Not a fully autonomous reviewer — you (or your AI agent) choose the next action.
- Not a zero-dependency runtime — it still depends on `agent-bus`, Antigravity itself, and the bundled companion extension.

## Prerequisites

| Requirement | Notes |
|-------------|-------|
| **macOS** | Primary tested platform. Linux is untested but may work. |
| **Python 3.12+** | For the `agpair` CLI |
| **Node.js 18+** | For building the companion extension |
| **`agent-bus`** | Shared message bus CLI — see below |
| **[Antigravity](https://antigravity.google/) IDE** | The companion extension runs inside it |

### `agent-bus`

`agent-bus` is the shared local message bus that agpair uses to dispatch tasks and receive receipts between your AI agent (desktop side) and Antigravity (code executor). It must be available on your `PATH`.

> **Note:** `agent-bus` is a local CLI tool distributed as part of the Antigravity tooling environment. If you are using an Antigravity-managed setup, it should already be available. If not, install the `agent-bus` binary provided by your Antigravity distribution and ensure it is on your `PATH`. There is currently no standalone public package for `agent-bus` — it is expected to be present in environments where Antigravity is installed.

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
  --body "Goal: fix the failing smoke test and return EVIDENCE_PACK."
```

By default, `task start` **waits** until the task reaches a terminal phase. Add `--no-wait` for fire-and-forget.

If you use the same repo frequently, you can save it as a local target alias and reuse `--target`:

```bash
agpair target add --name my-project --repo-path /path/to/your/project
agpair doctor --target my-project
agpair inspect --target my-project --json
agpair task start --target my-project \
  --body "Goal: fix the failing smoke test and return EVIDENCE_PACK."
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

**Data flow:** AI Agent → `agpair task start` → daemon dispatches via `agent-bus` → Antigravity executes → companion extension writes receipts → daemon ingests receipts → AI Agent reads status.

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
- Internal `ExecutorAdapter` abstraction extended to expose a stable `backend_id` (`antigravity` / `codex_cli`), now visible in read-only info (e.g., `task status --json` and `doctor`) for transparency.
- `task start --executor codex` as a first-class entry point, with Codex tasks now flowing through dispatch / poll / canonical terminal receipt synthesis
- Added formal Continuation Capability Matrix to encode policy for backends (e.g., `same_session` for Antigravity vs. `fresh_resume_first` for Codex CLI), visible in `task status --json`.
- Implemented `fresh_resume_first` path for review/approval flows, allowing Codex-backed tasks to seamlessly carry over feedback via a fresh dispatch.
- Automatic closeout for eligible `evidence_ready` tasks when strong repo-side commit evidence exists but a final terminal receipt never arrived
- Background daemon stdout/stderr now persist to `~/.agpair/daemon.stdout.log` and `~/.agpair/daemon.stderr.log`

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

The current attempt did not complete. Run `agpair task logs <TASK_ID>` to inspect, then decide whether to `continue` the same session or `retry` with a fresh one.

## License

MIT
