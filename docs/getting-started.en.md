# Getting Started with AGPair

This guide gets AGPair installed and dispatching external CLI agents from Codex or Claude Code.

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
```

Important fields:

- `supported_executor_backends`: `antigravity-cli`, `grok-cli`, `claude-code`, `codex`.
- `default_executor_backend`: `antigravity-cli`.
- `executor_cli_health`: whether each CLI binary is available.
- `authorization_profiles`: dispatch-time permission budgets.
- `client_hook_install_status`: Codex/Claude hook status when a repo path is provided.

Missing non-default executor binaries are warnings. They do not prevent AGPair from managing other executors.

## 3. Configure Your Controller

Codex:

```bash
mkdir -p ~/.codex/skills/agpair
cp "$PWD/skills/Codex/SKILL.md" ~/.codex/skills/agpair/SKILL.md
agpair codex config --install --scope project --repo-path /path/to/repo
```

Claude Code:

```bash
mkdir -p ~/.claude/skills/agpair
cp "$PWD/skills/Claude/SKILL.md" ~/.claude/skills/agpair/SKILL.md
claude mcp add --transport stdio agpair -- agpair-mcp
agpair claude config --install --scope project --repo-path /path/to/repo
```

Hooks fail open if AGPair is unavailable. They are routing hints and completion guardrails, not a replacement for controller verification.

## 4. Dispatch A Task

`task start` waits by default:

```bash
agpair task start \
  --repo-path /path/to/repo \
  --executor antigravity-cli \
  --authorization-profile local_mutating \
  --body "Goal: fix the failing smoke test. Required evidence: run the focused test."
```

For async or parallel work:

```bash
agpair task start \
  --repo-path /path/to/repo \
  --executor antigravity-cli \
  --authorization-profile local_mutating \
  --body "Goal: ..." \
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

`commit_ref` is optional unless the brief or authorization profile required a commit.

## 6. Retry An Approval Block

If the task returns `blocked(approval_required)`, stop polling and retry from the structured block context:

```bash
agpair task retry TASK-123 \
  --from-block \
  --authorization-profile local_mutating
```

The retry includes the original brief, blocked reason, terminal receipt, journal tail, current git status, diff/commits, and the new authorization profile.

## 7. Executor Selection

Use this order unless the task gives a better reason:

1. `antigravity-cli`: default external implementation executor.
2. `grok-cli`: cheap challenger / backup.
3. `claude-code`: quality escalation or Claude-specific external run.
4. `codex`: fallback external Codex worker.

Do not use Gemini for new work. Legacy `gemini_cli` records may be inspected or cleaned up only.

## 8. Local Files

Do not commit local runtime or personal config:

- `~/.agpair`
- `~/.codex/*`
- `~/.claude/settings.json`
- `.claude/settings.local.json`
- raw AGPair logs
- session transcripts
- generated hook debug output

Project-level `.claude/settings.json` or Codex hook config should be committed only when sanitized and intentionally shared.

## Legacy Note

The Antigravity desktop companion extension and bridge diagnostics remain for older installations. New tasks should use `antigravity-cli`.
