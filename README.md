# AGPair 1.0

![Python](https://img.shields.io/badge/python-≥3.12-blue)
![Platform](https://img.shields.io/badge/platform-macOS-lightgrey)
![License](https://img.shields.io/badge/license-MIT-green)
![Version](https://img.shields.io/badge/version-1.0.0-blueviolet)

[中文说明](README.zh-CN.md) | [Getting Started](docs/getting-started.en.md) | [Command Reference](docs/usage.md)

AGPair 1.0 is a local task lifecycle and evidence layer that lets Codex or Claude Code delegate bounded work to external CLI agents, then wait, verify, adopt, retry, or fall back with structured evidence.

Controllers plan and verify. AGPair dispatches external CLI executors, persists task state, waits cheaply, validates structured receipts, and supports state-aware retry when an executor blocks.

## AGPair 1.0 Model

- Common first external executors: `grok-cli` and `antigravity-cli`; multiple
  external lanes may run when their prompts, scopes, or acceptance criteria are distinct
- Quality escalation: `claude-code`
- Fallback external Codex CLI worker: `codex`
- Native Codex / Claude Code subagents: review/helper/fallback lanes when they
  materially improve verification or recovery

Controller-aware routing suppresses self-executors by default: Codex controllers do not choose AGPair-managed external `codex`, and Claude Code controllers do not choose AGPair-managed external `claude-code`, unless `--allow-self-executor` is explicitly used.

Operationally, `codex` is the external Codex CLI worker for Claude Code controllers. Codex controllers should use native Codex subagents as their fallback/review lane. `claude-code` is the external Claude Code worker for Codex controllers. Claude Code controllers should use native Claude Code subagents as their fallback/review lane.

Historical executor records remain inspectable for compatibility. New dispatch uses the active executor ids above.

For non-trivial work, the controller should prefer useful breadth over a single
default lane: use `grok-cli` and `antigravity-cli` as peer first lanes, add a
second `grok-cli` or `claude-code` when their output can improve confidence, and use native
subagents as narrow reviewers/helpers when they add controller-side value.
Mutating parallel lanes must use isolated worktrees or disjoint scopes.

Default executor environments are managed-natural for every active external CLI executor: AGPair manages task boundaries, receipts, logs, status, retry, and verification evidence, while the external CLI keeps its normal skills, MCP, memory, plugins, and provider config. Controller suppression handles self-executor avoidance; executor launch configuration is not special-cased.

AGPair-started executor, probe, smoke, and retry processes are marked internal, so installed Codex/Claude hooks no-op for those processes. Normal controller sessions still receive external-first guidance.

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
agpair doctor --fresh --repo-path /path/to/repo
```

If Antigravity CLI print mode times out with the current default model, select
a known-good Antigravity model for AGPair-launched workers. The value below is
an Antigravity model label, not the retired Gemini CLI executor:

```bash
export AGPAIR_ANTIGRAVITY_MODEL="Gemini 3.1 Pro (Low)"
```

Start a task. `task start` waits by default:

```bash
agpair task start \
  --executor grok-cli \
  --task-kind quick_review \
  --wait-policy lease \
  --authorization-profile local_readonly \
  --completion-policy report \
  --repo-path /path/to/repo \
  --body "Goal: review the target area. Scope: named files only. Required changes: None. This is report-only. Do not edit files. Exit criteria: return findings with evidence."
```

For async or parallel work, dispatch and watch state changes:

```bash
agpair task start \
  --executor grok-cli \
  --task-kind quick_review \
  --wait-policy lease \
  --authorization-profile local_readonly \
  --completion-policy report \
  --repo-path /path/to/repo \
  --body "Goal: review the target area. Scope: named files only. Required changes: None. This is report-only. Do not edit files. Exit criteria: return findings with evidence." \
  --no-wait

agpair task watch TASK-123 --json
```

`watch --json` emits state-change events and paths to raw logs/receipts. It does not stream full executor logs into the controller context.

For high-value review, research, design, or competing implementation choices,
use Fusion-style fanout so the controller receives lane cards plus one
synthesis/gate evidence pack:

```bash
agpair workflow fanout \
  --controller codex \
  --mode review \
  --topic "Review external-agent routing risks" \
  --lane grok-cli:primary \
  --lane grok-cli:adversarial \
  --lane antigravity-cli:second-opinion \
  --repo-path /path/to/repo \
  --wait --json
```

Inspect `panel_result`, `lane_cards`, `synthesis_result`, and `evidence_path`
before answering. Synthesis is evidence for controller verification, not an
automatic final answer.

For implementation/refactor/test-fix slices, use an isolated worktree with evidence completion:

```bash
agpair task start \
  --executor antigravity-cli \
  --task-kind implementation \
  --wait-policy lease \
  --authorization-profile local_mutating \
  --completion-policy evidence \
  --isolated-worktree \
  --repo-path /path/to/repo \
  --body "Goal: make the bounded change. Scope: name allowed files. Required changes: describe edits. Exit criteria: focused validation."
```

`--wait-policy lease` lets the controller wait cheaply for a bounded window. Waits use adaptive polling: the initial completion window is checked faster, and long tasks fall back to the requested `--interval-seconds` maximum. If the executor is still running, AGPair returns a structured background-running result instead of forcing the controller to burn model turns polling or killing the task early.

Review and adopt isolated code changes explicitly:

```bash
agpair task diff TASK-123
agpair task apply TASK-123 --check
agpair task apply TASK-123
```

Canonical examples use `--repo-path`, `--body`, and full profile names such as
`local_readonly`. `task start` also accepts compatibility aliases like `--repo`,
`--prompt`, and `readonly`, and auto-structures short bodies as a safety net.
Do not rely on aliases or one-line bodies in controller skills.

For isolated mutating evidence/commit tasks, AGPair snapshots tracked staged/unstaged changes into the executor worktree by default. It does not copy ignored or untracked files; pass `--dirty-snapshot off` to require a clean committed baseline.

If an executor returns `blocked(approval_required)`, retry with structured blocked context:

```bash
agpair task retry TASK-123 \
  --from-block \
  --authorization-profile local_mutating
```

If a retry is not the right recovery path, switch to another external executor or let the controller use its native subagents as the fallback/review lane.

## Controller Setup

Codex:

```bash
agpair codex config
agpair codex config --install --scope project --repo-path /path/to/repo --sync-skill
```

Claude Code:

```bash
agpair claude config
agpair claude config --install --scope project --repo-path /path/to/repo --sync-skill
```

To let Codex use the external `claude-code` worker, AGPair uses Claude auth
mode `auto` by default. It first tries a valid local Claude Code
OAuth/subscription login; if that is absent or the live probe fails, it reuses
the current Anthropic-compatible Claude provider selected in CC Switch. AGPair
does not need a separate Claude API key
configuration.

```bash
claude auth status
agpair doctor --fresh --repo-path /path/to/repo
```

`doctor --fresh` runs a tiny live auth probe and reports the selected
`auth_mode` as `oauth` or `ccswitch`. If OAuth fails, refresh the local Claude
Code login with `claude auth login`; if CC Switch fails, update the current
Claude provider inside CC Switch. `executor_probe_timeout` and
`executor_hook_interference` in `last_failure_type` are runtime/probe boundary
failures, not credential failures; `executor_auth_required` is the credential
case.

Use API-key worker mode only when you explicitly want a separate worker credential
outside OAuth and CC Switch:

```bash
mkdir -p ~/.agpair
agpair claude worker-settings > ~/.agpair/claude-worker-settings.json
export AGPAIR_CLAUDE_CODE_AUTH_MODE=api
export AGPAIR_CLAUDE_CODE_SETTINGS="$HOME/.agpair/claude-worker-settings.json"
export ANTHROPIC_API_KEY="..."
```

The managed hooks are advisory and fail open when AGPair state is unavailable. They preserve unrelated local settings and remove only AGPair-managed entries on uninstall.

## Authorization Profiles

Use the narrowest dispatch-time budget that can finish the work:

- `local_readonly`: inspect-only.
- `local_mutating`: normal repo-local edits and focused tests.
- `local_test_heavy`: broad local builds/tests.
- `external_network`: external network access required by the task.

AGPair 1.0 does not pause a running executor for live approval. Out-of-scope work should return `blocked(approval_required)`, and the controller starts a new retry attempt.

## Review Gate

`ready_for_review`, `evidence_ready`, and `committed` are not automatic success. The controller must inspect `recovery_decision`, `agent_result`, git diff/commit evidence, receipts, raw log paths when needed, and run the relevant verification before reporting completion. Real executor smoke also requires `all_success=true`, a usable `agent_result.state`, and `recovery_decision.action` such as `use_result` or `review_then_apply` for each attempted executor; dispatch or phase success alone is not enough.

Useful value metrics are completion rate, usable `agent_result` rate, time to first useful signal, fallback recommendation rate, controller rework rate, and abandoned/no-progress rate. Use `task status --json`, `task list --json`, and `scripts/smoke_real_executors.py` to inspect `summary_metrics` and per-executor `recovery_decision`.

After the controller accepts the evidence, mark the task accepted so Stop hooks do not keep blocking on the same receipt:

```bash
agpair task accept TASK-123 --adoptable-result yes --controller-rework none
```

When AGPair protocol parsing failed but the report/stdout is useful, record explicit salvage instead:

```bash
agpair task adopt TASK-123 --from-report --adoptable-result partial --controller-rework minor
```

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

- `~/.codex/skills/agpair-codex/SKILL.md`
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
grok-cli / antigravity-cli / claude-code / codex
```

AGPair is not a semantic controller. The AI controller still owns planning, scope decisions, review, and final verification.

## Documentation

| Document | Description |
| --- | --- |
| [Getting Started](docs/getting-started.en.md) | Minimal setup and first task |
| [Command Reference](docs/usage.md) | Full CLI reference |
| [Executor Lifecycle](docs/executor-lifecycle.md) | Add, disable, deprecate, or remove external executors |
| [Workflows](docs/workflows.md) | Declarative multi-task workflow orchestration |
| [Claude Code Integration](docs/claude-code-integration.zh-CN.md) | Claude Code setup and routing rules |
| [中文说明](README.zh-CN.md) | Chinese README |
| [中文命令参考](docs/usage.zh-CN.md) | Chinese command reference |

## Compatibility

The repository keeps legacy companion and bridge diagnostics for existing installations. Current task dispatch uses the registered CLI executors listed in the AGPair 1.0 model.

## License

MIT
