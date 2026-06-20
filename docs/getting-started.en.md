# Getting Started with AGPair 1.0

This guide gets AGPair 1.0 installed and dispatching external CLI agents from Codex or Claude Code.

## 1. Install

```bash
git clone https://github.com/logicrw/agpair.git
cd agpair
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e '.[dev]'
```

Optional global CLI link:

```bash
mkdir -p ~/.local/bin
ln -sf "$PWD/.venv/bin/agpair" ~/.local/bin/agpair
which agpair
```

## 2. Check Health

```bash
agpair doctor
agpair doctor --repo-path /path/to/repo
agpair doctor --fresh --repo-path /path/to/repo
```

Important fields:

- `supported_executor_backends`: `grok-cli`, `antigravity-cli`, `claude-code`, `codex`.
- `default_executor_backend`: `grok-cli`.
- `executor_cli_health`: whether each CLI binary is available.
- `authorization_profiles`: dispatch-time permission budgets.
- `client_hook_install_status`: Codex/Claude hook status when a repo path is provided.

Missing non-default executor binaries are warnings. They do not prevent AGPair from managing other executors.

## 3. Configure Your Controller

Codex:

```bash
agpair codex config --install --scope project --repo-path /path/to/repo --sync-skill
```

Codex and Claude Code avoid post-answer Stop hooks by default. Add
`--include-stop-hook` only if you want that hard guardrail.

Claude Code:

```bash
agpair claude config --install --scope project --repo-path /path/to/repo --sync-skill
```

Hooks fail open if AGPair is unavailable. They are routing hints; optional Stop hooks are completion guardrails, not a replacement for controller verification.

## 4. Dispatch A Task

`task start` waits by default:

```bash
agpair task start \
  --repo-path /path/to/repo \
  --executor antigravity-cli \
  --task-kind quick_review \
  --wait-policy lease \
  --authorization-profile local_readonly \
  --completion-policy report \
  --body "Goal: review the target area. Scope: named files only. Required changes: None. This is report-only. Do not edit files. Exit criteria: return findings with evidence."
```

For bounded implementation, refactor, or test-fix work, use an isolated
worktree and evidence completion:

```bash
agpair task start \
  --repo-path /path/to/repo \
  --executor antigravity-cli \
  --task-kind implementation \
  --wait-policy lease \
  --authorization-profile local_mutating \
  --completion-policy evidence \
  --isolated-worktree \
  --body "Goal: make the bounded change. Scope: name allowed files. Required changes: describe edits. Exit criteria: run focused validation and report evidence."
```

`--wait-policy lease` lets the controller wait cheaply for a bounded window. If
the executor is still running, AGPair returns a structured background-running
result instead of forcing the controller to burn model turns polling or killing
the task early.

For async or parallel work:

```bash
agpair task start \
  --repo-path /path/to/repo \
  --executor antigravity-cli \
  --task-kind quick_review \
  --wait-policy lease \
  --authorization-profile local_readonly \
  --completion-policy report \
  --body "Goal: review the target area. Scope: named files only. Required changes: None. This is report-only. Do not edit files. Exit criteria: return findings with evidence." \
  --no-wait

agpair task watch TASK-123 --json
```

`watch --json` emits state changes and paths to raw logs/receipts. It does not stream full logs.

## 5. Review The Result

```bash
agpair task status TASK-123 --json
agpair task logs TASK-123
git -C /path/to/repo status --short
git -C /path/to/repo diff
```

Treat `ready_for_review`, `evidence_ready`, and `committed` as review gates. The external executor has claimed progress; Codex or Claude Code still verifies diff, receipt, raw evidence paths, and tests before reporting success.

In `status --json` and `wait --json`, read `agent_result.controller_action` first: reports usually say `use_result`, isolated implementation diffs usually say `review_then_apply`, and blocked attempts tell the controller to inspect, retry, or switch executor.

`commit_ref` is optional unless the brief or authorization profile required a commit.

For isolated code tasks, inspect and apply the worker diff explicitly:

```bash
agpair task diff TASK-123
agpair task apply TASK-123 --check
agpair task apply TASK-123
```

After controller verification, mark the receipt handled:

```bash
agpair task accept TASK-123 --adoptable-result yes --controller-rework none
```

## 6. Retry An Approval Block

If the task returns `blocked(approval_required)`, stop polling and retry from the structured block context:

```bash
agpair task retry TASK-123 \
  --from-block \
  --authorization-profile local_mutating
```

The retry includes the original brief, blocked reason, terminal receipt, journal tail, current git status, diff/commits, and the new authorization profile.

## 7. Use Workflows For Multi-Part Work

Use normal `agpair task start` for ordinary work. Use `agpair workflow start` for high-value multi-part, parallel, adversarial, or long-running work:

```bash
agpair workflow validate --file templates/workflows/fanout-synthesize.json
agpair workflow start --file templates/workflows/fanout-synthesize.json --controller codex --repo-path /path/to/repo --json
agpair workflow watch WF-ABC123DEF456 --json
```

Workflow manifests are declarative and are not a script runner. Workflow `ready_for_review` means AGPair has an evidence pack for controller verification, not final user-facing success.

## 8. Executor Selection

Use this order unless the task gives a better reason:

1. `grok-cli`: default fast external executor.
2. `antigravity-cli`: strong implementation / second-opinion executor.

For Codex controllers, next use `claude-code`; external `codex` is suppressed by default because it is the AGPair-managed Codex CLI worker. For Claude Code controllers, next use `codex`; external `claude-code` is suppressed by default because Claude Code already has native subagents. Override self-executor suppression only with `--allow-self-executor`.

Historical executor records remain inspectable for compatibility. New dispatch uses active registered executor ids only.

If `antigravity-cli` is healthy but `--print` tasks time out with the current
Antigravity default model, set `AGPAIR_ANTIGRAVITY_MODEL` to a model that works
in your CLI, for example `Gemini 3.1 Pro (Low)`. This is an Antigravity model
label, not the retired Gemini CLI executor.

Executor onboarding, disabling, deprecation, and removal use the shared registry
profile contract. See [Executor Lifecycle](executor-lifecycle.md).

## 9. Local Files

Do not commit local runtime or personal config:

- `~/.agpair`
- `~/.codex/*`
- `~/.claude/settings.json`
- `.claude/settings.local.json`
- raw AGPair logs
- session transcripts
- generated hook debug output

Project-level `.claude/settings.json` or Codex hook config should be committed only when sanitized and intentionally shared.

## Compatibility Note

Legacy companion and bridge diagnostics remain for existing installations. Current task dispatch uses the registered CLI executors listed above.
