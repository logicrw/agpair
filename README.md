# agpair

![Python](https://img.shields.io/badge/python-≥3.12-blue)
![Platform](https://img.shields.io/badge/platform-macOS-lightgrey)
![License](https://img.shields.io/badge/license-MIT-green)

**agpair** is a lightweight CLI that connects your [Codex](https://openai.com/codex) chat window to an [Antigravity](https://antigravity.google/) executor — so you can dispatch coding tasks, track their progress, and review results without leaving the conversation.

## Why agpair?

When you use Codex + Antigravity together, there is a mechanical gap between "I told Codex what to do" and "Antigravity finished executing it." That gap includes:

- dispatching the task over `agent-bus`
- tracking which task maps to which executor session
- collecting receipts (`ACK`, `EVIDENCE_PACK`, `BLOCKED`, `COMMITTED`)
- detecting stuck tasks
- providing a clean continue / approve / reject / retry flow

**agpair fills that gap.** It is a tool belt for Codex — and a manual fallback for you when you need direct control.

### What agpair is *not*

- Not a second Supervisor — Codex stays in charge of semantic decisions.
- Not a fully autonomous reviewer — you (or Codex) choose the next action.
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

`agent-bus` is the shared local message bus that agpair uses to dispatch tasks and receive receipts between Codex (desktop) and Antigravity (code executor). It must be available on your `PATH`.

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
antigravity --install-extension agpair-companion-extension-*.vsix
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

For the full step-by-step walkthrough, see the detailed guides below.

## Architecture

```
┌───────────────┐     agpair CLI      ┌─────────────┐     agent-bus      ┌──────────────────┐
│               │  ─────────────────▶  │             │  ───────────────▶  │   Antigravity    │
│    Codex      │   task start/wait    │   agpair    │   dispatch/recv    │   (executor)     │
│  (chat UI)    │  ◀─────────────────  │   daemon    │  ◀───────────────  │                  │
│               │   status/receipts    │             │   receipts/ack     │   companion ext  │
└───────────────┘                      └──────┬──────┘                    └──────────────────┘
                                              │
                                         SQLite DB
                                     (tasks, receipts,
                                       journals)
```

**Data flow:** Codex → `agpair task start` → daemon dispatches via `agent-bus` → Antigravity executes → companion extension writes receipts → daemon ingests receipts → Codex reads status.

## How it Works in Practice

In normal use, **you do not need to manually type every `agpair` command**.

The intended workflow is:

1. You tell Codex what you want in natural language
2. Codex calls `agpair` commands behind the scenes
3. Antigravity executes the task
4. `agpair` keeps the mechanical path stable

The CLI is still valuable for manual inspection, debugging, retry, and recovery when Codex is not available.

## Optional Codex Skill

This repo also ships a reusable Codex skill at [skills/agpair/SKILL.md](skills/agpair/SKILL.md).

The skill is the public, portable way to teach Codex how to use `agpair` correctly:

- run preflight before semantic actions
- keep polling blocking waits until terminal exit
- avoid same-task intervention while an active waiter exists

This public distribution path is intentionally **skill-first**. It does not require downstream projects to copy repo-level `AGENTS.md`, `CLAUDE.md`, or `GEMINI.md` files.

Install it into your local Codex skills directory:

```bash
mkdir -p ~/.codex/skills
ln -sfn "$PWD/skills/agpair" ~/.codex/skills/agpair
```

Then restart Codex or open a new Codex window.

This improves automatic triggering for Antigravity delegation requests. For deterministic activation, explicitly say `use agpair` in your prompt.

## Status

agpair v1.0 is a focused tool for Codex → Antigravity task dispatch.

What already works:

- `agent-bus`-based task dispatch with auto-wait
- Local SQLite-backed task / receipt / journal state
- Continuation flow: `continue`, `approve`, `reject`, `retry`, `abandon`
- Standalone `task wait` with configurable timeout/interval
- Daemon with receipt ingestion, session continuity, and stuck detection
- `doctor` preflight checks (local health, desktop conflicts, bridge health)

What is explicitly *not* in scope:

- Replacing Codex as the semantic controller
- Hiding all operational boundaries

## Documentation

| Language | Document | Description |
|----------|----------|-------------|
| English | [Getting Started](docs/getting-started.en.md) | Step-by-step beginner guide |
| English | [Command Reference](docs/usage.md) | Full CLI reference |
| 中文 | [中文说明](README.zh-CN.md) | 中文入口 |
| 中文 | [新手教程](docs/getting-started-zh.md) | 详细入门指南 |
| 中文 | [命令参考](docs/usage.zh-CN.md) | 中文命令参考 |

## Repository Structure

```
agpair/
├── agpair/                 # Python CLI package
├── companion-extension/    # Bundled Antigravity companion (TypeScript)
│   ├── src/                # Extension source
│   ├── package.json
│   └── esbuild.js
├── skills/
│   └── agpair/             # Optional Codex skill package
├── tests/                  # Python integration tests
├── docs/                   # Documentation
└── pyproject.toml
```

This is a **single self-contained repo**. No external `codex-antigravity-supervisor` checkout is needed.

## Important Operating Notes

### Desktop receipt exclusivity

agpair consumes `code -> desktop` receipts. If another desktop-side watcher is already claiming the same receipts, `agpair doctor` will report `desktop_reader_conflict=true` and the daemon will refuse to start. Stop the other watcher first.

### One controller per task

You can open multiple Codex windows, but avoid having two windows send `continue / approve / reject / retry` for the **same** `TASK_ID`. Rule: one active task → one main Codex window.

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
