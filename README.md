# agpair

![Python](https://img.shields.io/badge/python-≥3.12-blue)
![Platform](https://img.shields.io/badge/platform-macOS-lightgrey)
![License](https://img.shields.io/badge/license-MIT-green)

[中文说明](README.zh-CN.md) | [Getting Started](docs/getting-started.en.md) | [Command Reference](docs/usage.md)

AGPair is an external-agent-first control plane for Codex and Claude Code.

Controllers plan and verify. AGPair dispatches external CLI executors, persists task state, waits cheaply, validates structured receipts, and supports state-aware retry when an executor blocks.

## Current Model

- Default external executor: `antigravity-cli`
- Cheap challenger / backup: `grok-cli`
- Quality escalation: `claude-code`
- Fallback external Codex CLI worker: `codex`
- Native Codex / Claude Code subagents: fallback or review only

Controller-aware routing suppresses self-executors by default: Codex controllers do not choose AGPair-managed external `codex`, and Claude Code controllers do not choose AGPair-managed external `claude-code`, unless `--allow-self-executor` is explicitly used.

Gemini is not used for new work. Legacy `gemini_cli` records can still be inspected or cleaned up.

## Quick Start

```bash
git clone https://github.com/logicrw/agpair.git
cd agpair
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e '.[dev]'
```

Make the CLI available to your controller:

```bash
ln -sf "$PWD/.venv/bin/agpair" ~/.local/bin/agpair
agpair doctor
```

Start a task. `task start` waits by default:

```bash
agpair task start \
  --executor antigravity-cli \
  --authorization-profile local_mutating \
  --repo-path /path/to/repo \
  --body "Goal: fix the failing smoke test. Required evidence: run the focused test."
```

For async or parallel work, dispatch and watch state changes:

```bash
agpair task start \
  --executor antigravity-cli \
  --authorization-profile local_mutating \
  --repo-path /path/to/repo \
  --body "Goal: ..." \
  --no-wait

agpair task watch TASK-123 --json
```

`watch --json` emits state-change events and paths to raw logs/receipts. It does not stream full executor logs into the controller context.

If an executor returns `blocked(approval_required)`, retry with structured blocked context:

```bash
agpair task retry TASK-123 \
  --from-block \
  --authorization-profile local_mutating
```

## Controller Setup

Codex:

```bash
mkdir -p ~/.codex/skills/agpair
cp "$PWD/skills/Codex/SKILL.md" ~/.codex/skills/agpair/SKILL.md
agpair codex config
agpair codex config --install --scope project --repo-path /path/to/repo
```

Claude Code:

```bash
mkdir -p ~/.claude/skills/agpair
cp "$PWD/skills/Claude/SKILL.md" ~/.claude/skills/agpair/SKILL.md
agpair claude config
agpair claude config --install --scope project --repo-path /path/to/repo
```

The managed hooks are advisory and fail open when AGPair state is unavailable. They preserve unrelated local settings and remove only AGPair-managed entries on uninstall.

## Authorization Profiles

Use the narrowest dispatch-time budget that can finish the work:

- `local_readonly`: inspect-only.
- `local_mutating`: normal repo-local edits and focused tests.
- `local_test_heavy`: broad local builds/tests.
- `external_network`: external network access required by the task.

V1 does not pause a running executor for live approval. Out-of-scope work should return `blocked(approval_required)`, and the controller starts a new retry attempt.

## Review Gate

`ready_for_review`, `evidence_ready`, and `committed` are not automatic success. The controller must inspect the AGPair status, git diff/commit evidence, receipts, raw log paths when needed, and run the relevant verification before reporting completion.

`commit_ref` is optional unless the brief or authorization profile explicitly requires a commit.

## Local State

AGPair stores local runtime state under `~/.agpair` by default. Override for tests with:

```bash
export AGPAIR_HOME=/path/to/agpair-state
```

Do not commit local runtime state, raw logs, session transcripts, generated hook debug output, or personal Codex/Claude settings.

Repository source files:

- `skills/Codex/SKILL.md`
- `skills/Claude/SKILL.md`
- `agpair/cli/codex.py`
- `agpair/cli/claude.py`

Local installed copies:

- `~/.codex/skills/agpair/SKILL.md`
- `~/.claude/skills/agpair/SKILL.md`
- Codex hook config
- `~/.claude/settings.json`

Project config such as `.claude/settings.json` or Codex project hooks should be committed only when sanitized and intentionally shared.

## Architecture

```
Controller (Codex / Claude Code)
        |
        | agpair task start / watch / retry
        v
AGPair CLI + SQLite state + journal + receipts
        |
        | external CLI executor
        v
antigravity-cli / grok-cli / claude-code / codex
```

AGPair is not a semantic controller. The AI controller still owns planning, scope decisions, review, and final verification.

## Documentation

| Document | Description |
| --- | --- |
| [Getting Started](docs/getting-started.en.md) | Minimal setup and first task |
| [Command Reference](docs/usage.md) | Full CLI reference |
| [Workflows V2](docs/workflows.md) | Declarative multi-task workflow orchestration |
| [Claude Code Integration](docs/claude-code-integration.zh-CN.md) | Claude Code setup and routing rules |
| [中文说明](README.zh-CN.md) | Chinese README |
| [中文命令参考](docs/usage.zh-CN.md) | Chinese command reference |

## Legacy Surfaces

The repository still contains the Antigravity desktop companion extension and legacy bridge diagnostics for existing installations. The current recommended path for new work is the `antigravity-cli` executor, not the IDE bridge.

## License

MIT
