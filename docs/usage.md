# agpair Usage

`agpair` is a durable task lifecycle layer for external CLI executors.

Use it when:
- Your AI coding agent is the main controller
- You are using Antigravity CLI, Grok CLI, Claude Code, or Codex CLI as the executor
- You want light mechanical automation without turning the tool into a second brain

## Environment

`agpair` stores its local state under:

- default: `~/.agpair/`
- override for testing: `AGPAIR_HOME=/path/to/custom/root`

Local CLI executors do not require the legacy desktop bridge. `agent-bus` is
only needed for older companion/bridge installations and their receipt-ingestion
diagnostics:

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
- daemon pid/status visibility
- latest known receipt id
- registered executor health, including binary, launch, receipt capability, lifecycle status, and routing eligibility
- controller hook install status when `--repo-path` is provided
- legacy companion bridge diagnostics when a repo still uses that path

Legacy bridge diagnostics can include `agent-bus`, `desktop_reader_conflict`,
repo bridge marker/port, bridge `/health`, `ls_bridge_ready`,
`workspace_paths`, `receipt_watcher_running`, and `repo_bridge_warning`.

If `repo_bridge_warning` mentions:
- `ls_bridge_ready=false`: treat it as a likely stale Antigravity session / missing CSRF state
- `workspace_paths missing repo`: you are pointed at the wrong Antigravity window
- `bridge health probe failed`: the companion bridge is not currently reachable on the discovered port

### Legacy companion mode

Local CLI executors do not compete for desktop receipts. The standalone
desktop-reader guard applies only to the legacy Antigravity companion bridge.

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

To explicitly use the default external CLI backend:

```bash
agpair task start \
  --executor antigravity-cli \
  --repo-path /absolute/path/to/repo \
  --authorization-profile local_mutating \
  --body "Goal: ..."
```

Use a focused project directory for `--repo-path`. AGPair refuses filesystem
roots, the user home directory, and paths above the user home by default because
external executors can otherwise scan private logs, caches, and unrelated
projects. If a broad path is intentional, pass `--allow-broad-repo-path`; that
override is stored on the task and visible in `task status`.

Other new-task executor ids are:

- `antigravity-cli`: default external implementation executor
- `grok-cli`: cheap alternate external executor
- `claude-code`: AGPair-managed external Claude Code CLI executor
- `codex`: AGPair-managed external Codex CLI executor

`gemini_cli` is legacy-read-only for historical tasks. New `task start` and `task retry` dispatches reject Gemini.

Executor resolution order when `--executor` is omitted:

1. target-level `default_executor`
2. `AGPAIR_DEFAULT_EXECUTOR`
3. fallback `antigravity-cli`

Recommended controller-side defaults:

- Codex and Claude Code should prefer external AGPair executors first.
- Codex controllers suppress AGPair-managed external `codex` by default; use `claude-code` before Codex native subagents.
- Claude Code controllers suppress AGPair-managed external `claude-code` by default; use `codex` before Claude Code native subagents.
- Native Codex or Claude subagents are fallback/review resources.
- Review `ready_for_review` receipts, diffs, and tests before reporting success, then run `agpair task accept TASK_ID` to mark that receipt handled.

This keeps cross-controller workers explicit: `codex` is for Claude Code controllers, and `claude-code` is for Codex controllers. Each controller should use its native subagents only as its own fallback/review lane.

Local CLI approval modes can be adjusted with environment variables:

- `AGPAIR_ANTIGRAVITY_CLI_BIN=/absolute/path/to/agy`
  Legacy alias: `AGPAIR_ANTIGRAVITY_CLI`
- `AGPAIR_ANTIGRAVITY_APPROVAL_MODE=default|yolo`
  Default: `yolo`
- `AGPAIR_ANTIGRAVITY_PRINT_TIMEOUT=30m0s`
- `AGPAIR_GROK_CLI_BIN=/absolute/path/to/grok`
  Legacy alias: `AGPAIR_GROK_CLI`
- `AGPAIR_GROK_OUTPUT_FORMAT=json|streaming-json`
  Default: `json`
- `AGPAIR_GROK_MAX_TURNS=24`
- `AGPAIR_CLAUDE_CODE_BIN=/absolute/path/to/claude`
  Legacy alias: `AGPAIR_CLAUDE_CODE_CLI`
- `AGPAIR_CLAUDE_CODE_AUTH_MODE=oauth|api`
  Default: `oauth`. OAuth mode reuses the local Claude Code subscription/OAuth
  login reported by `claude auth status` and does not pass `--bare`.
  `doctor --fresh` and dispatch preflight also run a tiny live auth probe so
  stale OAuth tokens fail before a delegated task is launched.
- `AGPAIR_CLAUDE_CODE_MAX_RETRIES=<integer>`
  Default: `0`. AGPair sets `CLAUDE_CODE_MAX_RETRIES` for worker launches so
  invalid OAuth/API credentials fail quickly instead of silently retrying.
- `AGPAIR_CLAUDE_CODE_BARE=1|0`
  Legacy compatibility switch. Setting it to `1` selects API/bare mode when
  `AGPAIR_CLAUDE_CODE_AUTH_MODE` is unset.
- `AGPAIR_CLAUDE_CODE_SETTINGS=/absolute/path/to/settings.json`
  Optional Claude Code settings JSON or path for API/bare mode.
  Generate a safe template with:
  `agpair claude worker-settings > ~/.agpair/claude-worker-settings.json`
  then set `AGPAIR_CLAUDE_CODE_SETTINGS` to that path and make the helper return
  a valid API key, usually via `ANTHROPIC_API_KEY`.
- `AGPAIR_CLAUDE_CODE_PERMISSION_MODE=<claude --permission-mode value>`
  Default: `bypassPermissions`
- `AGPAIR_CODEX_BIN=/absolute/path/to/codex`
  Legacy alias: `AGPAIR_CODEX_CLI`
- `AGPAIR_CODEX_IGNORE_USER_CONFIG=1|0`
  Default: `1`. Keep this on for external-worker isolation; set `0` only for diagnostics.
- `AGPAIR_CODEX_APPROVAL_MODE=default|full_auto|bypass_all`
  Default: `bypass_all`

These knobs are adapter-local escape hatches. The registry profile remains the
shared contract for every executor, and tests require declared noninteractive
and isolation flags to match each adapter's default command.

Note: all executors use fresh sessions for retries.

### Completion policy

Not every task needs a commit. Completion policy owns terminal semantics:

- `report`: succeeds with a captured report, stdout report output, or valid structured receipt carrying report evidence.
- `evidence`: succeeds with verifiable evidence such as receipt payloads, artifacts, changed files, or test output.
- `commit`: requires a verifiable commit.
- `auto`: resolves from the authorization profile and task brief.

`local_readonly` and briefs that explicitly say `Required changes: none`, `no changes`, or `禁止写入` should use report/evidence semantics. They must not be blocked just because no commit was created.

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

### Task metadata and worktree isolation

You can attach orchestration metadata to a task to help the controller plan
parallel and isolated execution.

- `depends_on`: List of previous task IDs that must complete before this one.
- `isolated_worktree`: Runs local CLI executors from a separate git worktree when AGPair can create or resolve one for the task.
- `worktree_boundary`: The intended execution boundary for the worktree or task.
- `setup_commands`: Persisted pre-run hints for the controller; AGPair does not run arbitrary setup scripts.
- `teardown_commands`: Persisted post-run hints for the controller; AGPair does not run arbitrary teardown scripts.
- `env_vars`: Persisted per-task environment hints; only explicitly supported executor env is applied automatically.
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
agpair task status TASK-SMOKE-001 --json
agpair task logs TASK-SMOKE-001
agpair task logs TASK-SMOKE-001 --raw stdout
agpair task logs TASK-SMOKE-001 --raw stderr
```

`status --json` exposes the active attempt, executor id, actual binary name,
pid when available, stdout/stderr paths, log sizes, last output time, small tail
excerpts, liveness state, effective completion policy, and precise blocker
metadata. Full raw logs stay on disk unless explicitly requested.

### Fresh retry

```bash
agpair task retry TASK-SMOKE-001 --body "Retry with a fresh executor session."
```

`retry` is always explicit CLI control in v1. The daemon only marks `retry_recommended=true`; it does not auto-retry.
It also waits by default unless you pass `--no-wait`.

For an approval block, retry from the structured terminal context:

```bash
agpair task retry TASK-SMOKE-001 \
  --from-block \
  --authorization-profile local_mutating
```

### List local tasks

```bash
agpair task list
agpair task list --phase acked
agpair task list --repo-path /absolute/path/to/repo --json
```

This is the fastest way to see what the local SQLite state still tracks. Output includes:

- `task_id`
- `phase`
- `attempt`
- `retry`
- `recommended`
- `repo`

`task list` now also supports:

- `--repo-path` / `--target` to scope the listing to one repository
- `--json` to emit machine-readable task payloads, suitable for status lines, hooks, or controller-side filtering

### Claude Code helpers

`agpair` also ships a small Claude Code integration surface:

```bash
agpair claude config
agpair claude statusline
agpair claude hook session-start
agpair claude hook precompact
agpair claude hook user-prompt-submit
agpair claude hook stop
agpair claude hook subagent-start
```

`agpair claude config` prints a ready-to-paste Claude Code settings snippet wiring:

- `statusLine.command` → `agpair claude statusline`
- `SessionStart` hook → `agpair claude hook session-start`
- `PreCompact` hook → `agpair claude hook precompact`
- `UserPromptSubmit` hook → `agpair claude hook user-prompt-submit`
- `Stop` hook → `agpair claude hook stop`
- `SubagentStart` hook → `agpair claude hook subagent-start`
- `SubagentStop` / `TaskCreated` / `TaskCompleted` observability hooks

Config management flags:

- default: print the managed JSON snippet only
- `--install` / `--merge`: write the AGPair-managed fragment into Claude Code settings
- `--scope project|user`: choose `.claude/settings.json` in the current repo or `~/.claude/settings.json`; default is `project`
- `--dry-run`: print a unified diff without writing
- `--uninstall`: remove only AGPair-managed entries
- `--force`: replace a conflicting non-AGPair `statusLine`

Safety rules:

- AGPair never overwrites a foreign `statusLine` unless `--force` is passed.
- AGPair preserves unrelated hook entries and only de-duplicates by AGPair command identity.
- Uninstall removes only AGPair-managed entries and leaves unrelated settings untouched.

Notes:

- `statusline` reads the Claude Code JSON payload on stdin, resolves the current repo/worktree, and prints a compact AGPair summary.
- `session-start` injects a short reminder that AGPair external-first routing is available in the current repo.
- `precompact` blocks compaction only while an AGPair task is `acked` or `evidence_ready`; other visible states may still appear in the status line without blocking compaction.
- `user-prompt-submit` injects external-first routing context.
- `stop` blocks only actionable terminal states such as unaccepted `ready_for_review` and `approval_required`.
- `subagent-start` is advisory; Claude Code native subagents remain fallback/review resources.
- AGPair intentionally does **not** provide a default `InstructionsLoaded` reminder hook because Claude Code documents that event as observability-only.
- AGPair intentionally does **not** provide a default `WorktreeCreate` hook because that hook replaces Claude Code’s built-in git-worktree behavior entirely.

### Codex helpers

AGPair can emit Codex hook config so Codex prefers external CLI executors for non-trivial work and avoids model-token polling loops:

```bash
agpair codex config
agpair codex config --install --scope project --repo-path "$REPO"
```

Managed hooks:

- `UserPromptSubmit`: adds short external-first context.
- `Stop`: blocks only actionable AGPair terminal states such as unaccepted `ready_for_review` and `approval_required`.
- `SubagentStart`: advisory context only; Codex native subagents remain fallback/review resources.

For async tasks, attach with:

```bash
agpair task watch TASK-123 --json
```

### Abandon a local task

```bash
agpair task abandon TASK-SMOKE-001 --reason "manual cleanup"
```

This is a local bookkeeping command. It does **not** contact the executor.
Use it when you want to stop tracking a hanging local task without editing SQLite by hand.

### Wait for a task (standalone)

If you dispatched with `--no-wait`, you can attach later:

```bash
agpair task wait TASK-SMOKE-001
agpair task wait TASK-SMOKE-001 --timeout-seconds 600 --interval-seconds 10
```

Exit code `0` means success (`ready_for_review` / `evidence_ready` / `committed`).
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

## Workflows

Use `agpair task start` for ordinary work. Use `agpair workflow start` for high-value multi-part, parallel, adversarial, or long-running work.

```bash
agpair workflow validate --file templates/workflows/fanout-synthesize.json
agpair workflow start --file templates/workflows/fanout-synthesize.json --controller codex --repo-path /absolute/path/to/repo --json
agpair workflow status WF-ABC123DEF456 --json
agpair workflow watch WF-ABC123DEF456 --json --cursor '<cursor>'
agpair workflow retry-node WF-ABC123DEF456 scan-routing --authorization-profile local_mutating
agpair workflow cancel WF-ABC123DEF456 --reason 'operator requested'
```

Workflow manifests are declarative. AGPair rejects arbitrary script fields and dispatches normal V1.1 child tasks with durable artifacts, completion policies, structured receipts, and controller-aware executor routing.

Workflow `ready_for_review` means AGPair has an evidence pack for controller verification, not final user-facing success. `workflow watch --json` emits low-noise state changes and artifact paths, not full raw logs.

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

## Executor lifecycle

Every external executor is a registered module. Add, disable, deprecate, or
remove executors through the shared profile contract rather than through custom
state-machine branches. See [Executor Lifecycle](executor-lifecycle.md).

Current active executor ids are `antigravity-cli`, `grok-cli`, `claude-code`,
and `codex`. `codex` means an AGPair-managed external Codex CLI worker, not
Codex native subagents. `claude-code` means an AGPair-managed external Claude
Code worker, not Claude Code native subagents.

## Release and privacy checklist

Before publishing or opening a PR:

- Run the targeted tests plus the full unit/integration suite.
- Run real smoke for the controller matrix and keep smoke reports local.
- Run `git diff --check`.
- Inspect `git status --short --untracked-files=all`.
- Do not stage `.agpair/`, `~/.agpair`, raw executor logs, local receipts, session transcripts, personal Codex/Claude config, or generated hook debug output.
- Sanitize local paths and private artifact references from committed docs.
- Check GitHub About/description/topics manually; repository metadata is outside the source diff and can become stale.
