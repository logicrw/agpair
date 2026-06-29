# AGPair 1.0 Usage

`agpair` is the AGPair 1.0 durable task lifecycle layer for external CLI executors.

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
  --task-kind implementation \
  --wait-policy lease \
  --authorization-profile local_mutating \
  --completion-policy evidence \
  --isolated-worktree \
  --body "Goal: implement the smoke fix. Scope: focused repo files only. Required changes: make the smallest code/test change needed. Exit criteria: run the focused test and show evidence."
```

To explicitly use the default external CLI backend:

```bash
agpair task start \
  --executor antigravity-cli \
  --repo-path /absolute/path/to/repo \
  --task-kind quick_review \
  --wait-policy lease \
  --authorization-profile local_readonly \
  --completion-policy report \
  --coordination-role verifier \
  --body "Goal: inspect the target area. Scope: named files only. Required changes: None. This is report-only. Do not edit files. Exit criteria: return findings with evidence."
```

Use `--coordination-role thinker|worker|verifier|synthesizer|gate|general` when the controller wants to make a task's intended role explicit. It is a prompt and status hint only. AGPair still judges completion from receipts, artifacts, diffs, reports, validation, and controller review.

Canonical examples use `--repo-path`, `--body`, and full profile names such as
`local_readonly`. `task start` accepts compatibility aliases like `--repo`,
`--prompt`, and `readonly`, and auto-structures short bodies as a safety net.
Controller skills should still send the full `Goal` / `Scope` /
`Required changes` / `Exit criteria` contract directly.

Use a focused project directory for `--repo-path`. AGPair refuses filesystem
roots, the user home directory, and paths above the user home by default because
external executors can otherwise scan private logs, caches, and unrelated
projects. If a broad path is intentional, pass `--allow-broad-repo-path`; that
override is stored on the task and visible in `task status`.

For bounded implementation/refactor/test-fix work, dispatch an evidence task in
an isolated worktree:

```bash
agpair task start \
  --executor grok-cli \
  --repo-path /absolute/path/to/repo \
  --task-kind implementation \
  --wait-policy lease \
  --authorization-profile local_mutating \
  --completion-policy evidence \
  --isolated-worktree \
  --body "Goal: bounded change. Scope: allowed files. Required changes: edits. Exit criteria: focused validation."
```

Isolated mutating evidence/commit tasks default to `--dirty-snapshot tracked`,
which copies tracked staged/unstaged controller changes into the executor
worktree. Ignored and untracked files are not copied. Use
`--dirty-snapshot off` for a committed-HEAD-only worker baseline.

Other new-task executor ids are:

- `grok-cli`: default external implementation executor
- `antigravity-cli`: strong external implementation and second-opinion executor
- `claude-code`: AGPair-managed external Claude Code CLI executor
- `codex`: AGPair-managed external Codex CLI executor

Historical executor records remain readable for compatibility. New `task start`
and `task retry` dispatches use active registered executor ids only.

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

Executor launch environments:

| Executor | Default mode | Skills/MCP |
| --- | --- | --- |
| `grok-cli` | `managed-natural` | inherit |
| `antigravity-cli` | `managed-natural` | inherit |
| `claude-code` | `managed-natural` when auth is healthy | inherit |
| `codex` | `managed-natural` | inherit |

`managed-natural` means AGPair manages task state, authorization profile,
receipt/log capture, wait/watch, retry, and verification evidence while the
external CLI keeps its normal skills, MCP, memory, plugins, and provider
configuration. If an external attempt is not useful, retry naturally, switch to
another external executor, or let the controller use its native subagents as the
fallback/review lane.

AGPair external-first hooks are controller-only guidance. AGPair-started
executor, probe, smoke, and retry processes are marked as internal so Codex and
Claude hooks no-op instead of recursively injecting delegation guidance or
blocking on unrelated `ready_for_review` tasks.

Local CLI approval modes can be adjusted with environment variables:

- `AGPAIR_ANTIGRAVITY_CLI_BIN=/absolute/path/to/agy`
  Legacy alias: `AGPAIR_ANTIGRAVITY_CLI`
- `AGPAIR_ANTIGRAVITY_APPROVAL_MODE=default|yolo`
  Default: `yolo`
- `AGPAIR_ANTIGRAVITY_MODEL="Gemini 3.1 Pro (Low)"`
  Optional; use when the Antigravity default model times out in `--print` mode.
  This is an Antigravity model label, not the retired Gemini CLI executor.
  Legacy alias: `AGPAIR_ANTIGRAVITY_CLI_MODEL`
- `AGPAIR_ANTIGRAVITY_PRINT_TIMEOUT=30m0s`
- `AGPAIR_GROK_CLI_BIN=/absolute/path/to/grok`
  Legacy alias: `AGPAIR_GROK_CLI`
- `AGPAIR_GROK_OUTPUT_FORMAT=json|streaming-json`
  Default: `json`
- `AGPAIR_GROK_MAX_TURNS=12`
  Default is intentionally bounded for AGPair background tasks; raise it only for larger explicitly scoped work.
- `AGPAIR_CLAUDE_CODE_BIN=/absolute/path/to/claude`
  Legacy alias: `AGPAIR_CLAUDE_CODE_CLI`
- `AGPAIR_CLAUDE_CODE_AUTH_MODE=auto|oauth|ccswitch|api`
  Default: `auto`. Auto mode first uses a valid local Claude Code
  subscription/OAuth login, then falls back to the current Claude provider in
  CC Switch. Force `oauth` to disable provider fallback, `ccswitch` to use the
  CC Switch provider directly, or `api` for a separate worker credential.
- `AGPAIR_CC_SWITCH_HOME=/absolute/path/to/.cc-switch`
  Optional. Defaults to `~/.cc-switch`. AGPair reads CC Switch's current
  Claude provider settings and injects them as worker process environment
  variables; provider secrets are not written to AGPair command files or health
  JSON.
- `AGPAIR_CLAUDE_CODE_MAX_RETRIES=<integer>`
  Default: `0`. AGPair sets `CLAUDE_CODE_MAX_RETRIES` for worker launches so
  invalid OAuth/API credentials fail quickly instead of silently retrying.
  `agpair doctor --fresh` uses the same managed-natural Claude Code surface for
  live auth probes; it does not use bare mode or disable skills/MCP. The health
  JSON reports `auth_satisfied`, `auth_probe_environment_mode`,
  `auth_probe_skill_policy`, `auth_probe_mcp_policy`, `auth_state`, and
  `last_failure_type` for this path. `executor_probe_timeout` and
  `executor_hook_interference` are not credential failures; only
  `executor_auth_required` means the OAuth or CC Switch provider credentials need
  attention.
- `AGPAIR_CLAUDE_CODE_PROBE_CWD=/tmp-like-neutral-path`
  Optional. Live auth probes default to a neutral temp directory so project
  hooks, MCP, and repo context cannot turn a provider check into controller work.
- `AGPAIR_CLAUDE_CODE_SETTINGS=/absolute/path/to/settings.json`
  Optional Claude Code settings JSON or path for API mode.
  Generate a safe template with:
  `agpair claude worker-settings > ~/.agpair/claude-worker-settings.json`
  then set `AGPAIR_CLAUDE_CODE_SETTINGS` to that path and make the helper return
  a valid API key, usually via `ANTHROPIC_API_KEY`.
- `AGPAIR_CLAUDE_CODE_PERMISSION_MODE=<claude --permission-mode value>`
  Default: `bypassPermissions`
- `AGPAIR_CODEX_BIN=/absolute/path/to/codex`
  Legacy alias: `AGPAIR_CODEX_CLI`
- `AGPAIR_CODEX_APPROVAL_MODE=default|full_auto|bypass_all`
  Default: `bypass_all`

Internal launch markers, set by AGPair and not meant for global shells:

- `AGPAIR_INTERNAL_ROLE=probe|executor|smoke`
- `AGPAIR_SUPPRESS_CLIENT_HOOKS=1`
- `AGPAIR_NONINTERACTIVE=1`
- `AGPAIR_ALLOW_NESTED_DELEGATION=1`

Nested AGPair delegation is blocked for executor-launched processes by default.
`--allow-nested-delegation` cannot be self-authorized from inside an executor;
it also requires `AGPAIR_ALLOW_NESTED_DELEGATION=1` from the controller
environment. This keeps Claude Code or other inherited skills from turning a
worker task into another controller loop unless that was explicitly intended.

These knobs are adapter-local escape hatches. The registry profile remains the
shared contract for every executor, and tests require declared noninteractive
flags to match each adapter's default command.

Note: all executors use fresh sessions for retries.

### Completion policy

Not every task needs a commit. Completion policy owns terminal semantics:

- `report`: succeeds with a captured report, stdout report output, or valid structured receipt carrying report evidence.
- `evidence`: succeeds with verifiable evidence such as receipt payloads, artifacts, changed files, or test output.
- `commit`: requires a verifiable commit.
- `auto`: resolves from the authorization profile and task brief.

`local_readonly` and briefs that explicitly say `Required changes: none`, `no changes`, or `禁止写入` should use report/evidence semantics. They must not be blocked just because no commit was created.

### Task kind and wait policy

`--task-kind` gives the controller a simple way to choose the right default
wait and execution budget:

| Task kind | Default wait | Controller lease | Hard budget |
| --- | --- | ---: | ---: |
| `quick_review` | `lease` | 120s | 900s |
| `deep_review` | `lease` | 240s | 1800s |
| `implementation` | `lease` | 300s | 3600s |
| `test_fix` | `lease` | 300s | 3600s |
| `research` | `lease` | 300s | 5400s |
| `smoke` | `strict` | 300s | 600s |
| `generic` | `terminal` | none | none |

`--wait-policy lease` lets the controller wait cheaply for a bounded window. If
the executor is still running, AGPair returns a structured background-running
result instead of forcing the controller to burn model turns polling or killing
the task early. `terminal` and `strict` keep the older behavior: timeout,
watchdog, and terminal failure are command failures.

All dispatching commands (`start`, `retry`) accept:

| Flag | Default | Notes |
|------|---------|-------|
| `--task-kind` | `generic` | One of `quick_review`, `deep_review`, `implementation`, `test_fix`, `research`, `smoke`, or `generic` |
| `--wait-policy` | task-kind default | `terminal`, `lease`, `background`, or `strict` |
| `--controller-wait-seconds` | task-kind default | controller wait lease before a background-running return |
| `--execution-budget-seconds` | task-kind default | hard execution budget before daemon marks the task stuck |
| `--background-ok / --no-background-ok` | task-kind default | whether lease expiry can detach while the executor continues |
| `--wait / --no-wait` | `--wait` | wait after dispatch or return immediately |
| `--interval-seconds` | `5` | requested max local polling interval; waits poll faster during the initial completion window |
| `--timeout-seconds` | `3600` | maximum local wait duration for terminal/strict waits |

For implementation and test-fix tasks, AGPair defaults `--completion-policy auto`
to `evidence`. You still pass `--isolated-worktree` explicitly for mutating code
work so the worker cannot silently edit the controller's active worktree.

With `generic` tasks, `task start` still blocks until the task reaches a
terminal phase unless `--no-wait` is provided.
To return immediately after dispatch:

```bash
agpair task start \
  --repo-path /absolute/path/to/repo \
  --body "Goal: run a quick review. Scope: named files only. Required changes: None. This is report-only. Do not edit files. Exit criteria: return findings with evidence." \
  --no-wait
```

You may also provide your own id:

```bash
agpair task start \
  --task-id TASK-SMOKE-001 \
  --repo-path /absolute/path/to/repo \
  --body "Goal: run a smoke check. Scope: focused repo files only. Required changes: None. This is report-only. Do not edit files. Exit criteria: return command output and evidence."
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

**Parallelism recommendation:** Use useful breadth by default. It is valid to
start multiple tasks with the same executor, including several `grok-cli`
tasks, when each task has a distinct id, prompt, file slice, evaluation angle,
or acceptance criteria. For mutating work, parallelize across isolated
worktrees or disjoint scopes, not inside one controller worktree.

Use `task wait --json` when a controller needs a machine-readable outcome after
dispatch. Lease-based waits may return `controller_lease_expired` or
`soft_no_progress` while the executor remains alive. Terminal waits include
`agent_result` and `recovery_decision`; use `recovery_decision.action`
(`use_result`, `review_then_apply`, `wait_background`, `switch_executor`,
`native_fallback`, `repair_executor`, or `retry_same_executor`) as the
low-friction handoff signal.

### Inspect a task

```bash
agpair task status TASK-SMOKE-001
agpair task status TASK-SMOKE-001 --summary
agpair task status TASK-SMOKE-001 --json
agpair task logs TASK-SMOKE-001
agpair task logs TASK-SMOKE-001 --raw stdout
agpair task logs TASK-SMOKE-001 --raw stderr
```

Use `status --summary` when you only need the phase, executor, adoptability,
artifact, recovery action, and blocker surface.

`status --json` exposes the active attempt, executor id, actual binary name,
pid when available, stdout/stderr paths, log sizes, last output time, small tail
excerpts, liveness state, effective completion policy, and precise blocker
metadata. Full raw logs stay on disk unless explicitly requested.

### Review and apply isolated code changes

For isolated implementation or test-fix tasks, inspect the executor diff before
touching the controller worktree:

```bash
agpair task diff TASK-SMOKE-001
agpair task diff TASK-SMOKE-001 --stat
agpair task diff TASK-SMOKE-001 --json
```

Then check whether the diff applies cleanly to the controller repo:

```bash
agpair task apply TASK-SMOKE-001 --check
```

If the check is clean and the controller accepts the approach, apply the diff:

```bash
agpair task apply TASK-SMOKE-001
```

`task apply` uses the isolated worker's baseline, excludes the controller dirty
snapshot baseline, and leaves the applied changes unstaged in the controller
worktree for normal review and tests. It does not mark the AGPair task accepted;
run `task accept` only after controller verification.

### Fresh retry

```bash
agpair task retry TASK-SMOKE-001 --body "Retry with a fresh executor session."
```

`retry` is always explicit CLI control in AGPair 1.0. The daemon only marks `retry_recommended=true`; it does not auto-retry.
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
- `--json` to emit machine-readable task payloads plus `summary_metrics`, suitable for status lines, hooks, or controller-side filtering

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
- `SubagentStart` hook → `agpair claude hook subagent-start`
- `SubagentStop` / `TaskCreated` / `TaskCompleted` observability hooks

Pass `--include-stop-hook` only when you explicitly want the optional
post-answer `Stop` hook → `agpair claude hook stop` guardrail.

Config management flags:

- default: print the managed JSON snippet only
- `--install` / `--merge`: write the AGPair-managed fragment into Claude Code settings
- `--scope project|user`: choose `.claude/settings.json` in the current repo or `~/.claude/settings.json`; default is `project`
- `--dry-run`: print a unified diff without writing
- `--uninstall`: remove only AGPair-managed entries
- `--sync-skill/--no-sync-skill`: manage the AGPair skill at `.claude/skills/agpair/SKILL.md` or `~/.claude/skills/agpair/SKILL.md`; sync is enabled by default during install/uninstall
- `--force`: replace a conflicting non-AGPair `statusLine`
- `--include-stop-hook`: also install the optional post-answer Stop guardrail

Safety rules:

- AGPair never overwrites a foreign `statusLine` unless `--force` is passed.
- AGPair preserves unrelated hook entries and only de-duplicates by AGPair command identity.
- Uninstall removes only AGPair-managed entries and leaves unrelated settings untouched.
- Skill sync manages only the AGPair skill path and refuses to overwrite a non-AGPair skill.

Notes:

- `statusline` reads the Claude Code JSON payload on stdin, resolves the current repo/worktree, and prints a compact AGPair summary.
- `session-start` injects a short reminder that AGPair external-first routing is available in the current repo.
- `precompact` blocks compaction only while an AGPair task is `acked` or `evidence_ready`; other visible states may still appear in the status line without blocking compaction.
- `user-prompt-submit` injects external-first routing context.
- `stop` blocks only actionable terminal states such as unaccepted `ready_for_review` and `approval_required` when the optional Stop hook is installed.
- `subagent-start` is advisory; Claude Code native subagents remain fallback/review resources.
- AGPair intentionally does **not** provide a default `InstructionsLoaded` reminder hook because Claude Code documents that event as observability-only.
- AGPair intentionally does **not** provide a default `WorktreeCreate` hook because that hook replaces Claude Code’s built-in git-worktree behavior entirely.

### Codex helpers

AGPair can emit Codex hook config so Codex prefers external CLI executors for non-trivial work and avoids model-token polling loops:

```bash
agpair codex config
agpair codex config --install --scope project --repo-path "$REPO" --sync-skill
```

Managed hooks:

- `UserPromptSubmit`: adds short external-first context.
- `SubagentStart`: advisory context only; Codex native subagents remain fallback/review resources.

Codex and Claude Code do not install the post-answer `Stop` hook by default, because it can
surface as a separate after-final hook block. Pass `--include-stop-hook` only
when you want that hard guardrail; it blocks only actionable AGPair terminal
states such as unaccepted `ready_for_review` and `approval_required`.

`--install`, `--uninstall`, and `--dry-run` manage the Codex AGPair skill by
default at `.codex/skills/agpair-codex/SKILL.md` or
`~/.codex/skills/agpair-codex/SKILL.md`. Pass `--no-sync-skill` to manage only
hooks. AGPair refuses to overwrite a non-AGPair skill at that path.

Codex config accepts the same `--include-stop-hook` opt-in flag when you want to
install the optional post-answer Stop guardrail.

### How to judge AGPair value

Do not treat dispatch or process liveness as value. Track completion rate,
usable `agent_result` rate, time to first useful signal, fallback
recommendation rate, controller rework rate, and abandoned/no-progress rate.
The main surfaces are `agpair task status --json`, `agpair task list --json`,
and the `summary_metrics` from `scripts/smoke_real_executors.py`.

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

Exit code `0` can mean terminal success, or a background-running lease outcome
when the task permits background continuation. Use `--json` to read `outcome`,
`agent_result`, `recovery_decision`, `controller_lease_expired`, and
`background_ok`.

Exit code `1` means terminal failure, strict timeout/watchdog failure, missing
task, or a lease outcome that does not permit background continuation.

Some `evidence_ready` tasks can now auto-close when strong repo-side commit evidence exists but a final terminal receipt never arrived. In that case, inspect `task status --json` / `inspect --json` before manually abandoning the task.

When terminal/strict watchdog handling triggers, the message will tell you to
run `agpair task retry <TASK_ID>`. For lease-based tasks with
`background_ok=true`, `soft_no_progress` means inspect `task status --json`,
watch later, or detach while the executor continues.

### Auto-wait options

All dispatching commands (`start`, `retry`) use the task-kind and wait-policy
controls described above.

`status`, `logs`, and `wait` do **not** have `--wait/--no-wait`.

## Workflows

Use `agpair task start` for ordinary work. Start several task ids when a
non-trivial task benefits from multiple `grok-cli` reviews, competing
implementation candidates, or additional `antigravity-cli` / `claude-code`
verification. Use `agpair workflow fanout` for high-value panel work where the
controller wants multiple lane cards plus one synthesis/gate evidence pack.
Use `agpair workflow start` with a manifest when the preset fanout modes are not
enough.

```bash
agpair workflow fanout \
  --controller codex \
  --mode review \
  --topic "Review terminal receipt salvage and workflow synthesis risks" \
  --lane grok-cli:primary \
  --lane grok-cli:adversarial \
  --lane antigravity-cli:second-opinion \
  --repo-path /absolute/path/to/repo \
  --wait --json
agpair workflow fanout \
  --controller codex \
  --mode implementation \
  --topic "Implement a bounded parser fix" \
  --scope "agpair/workflows/*.py and focused tests only" \
  --lane grok-cli:candidate-a \
  --lane claude-code:candidate-b \
  --isolated-worktree \
  --repo-path /absolute/path/to/repo \
  --dry-run --json
agpair workflow validate --file templates/workflows/fanout-synthesize.json
agpair workflow start --file templates/workflows/fanout-synthesize.json --controller codex --repo-path /absolute/path/to/repo --json
agpair workflow status WF-ABC123DEF456 --json
agpair workflow watch WF-ABC123DEF456 --json --cursor '<cursor>'
agpair workflow retry-node WF-ABC123DEF456 scan-routing --authorization-profile local_mutating
agpair workflow cancel WF-ABC123DEF456 --reason 'operator requested'
```

Workflow manifests are declarative. AGPair rejects arbitrary script fields and dispatches normal AGPair child tasks with durable artifacts, completion policies, structured receipts, and controller-aware executor routing.

Workflow `ready_for_review` means AGPair has an evidence pack for controller verification, not final user-facing success. `workflow watch --json` emits low-noise state changes and artifact paths, not full raw logs.

Fanout workflows expose `lane_cards`, `synthesis_result`, and `panel_result` in status/watch/evidence payloads. Treat synthesis as evidence to inspect, not a final answer. Partial or malformed lane output may still be useful, but AGPair marks it as `needs_review` and keeps the controller gate explicit.

## Failure posture

`agpair` is intentionally conservative.

- duplicate receipts are ignored
- stale receipts do not roll task state backward
- invalid continuation targets fail closed
- daemon does not send semantic messages
- daemon does not auto-create fresh retries
- daemon can recommend retry after a soft no-progress window before the hard stuck timeout
- terminal/strict waits fail early on watchdog instead of blind-waiting until the hard timeout
- lease waits with `background_ok=true` return structured background-running outcomes while the executor continues

If transport dispatch fails:
- the CLI exits with code `1`
- a failure event is written to the local journal
- the task is not silently advanced

## Executor lifecycle

Every external executor is a registered module. Add, disable, deprecate, or
remove executors through the shared profile contract rather than through custom
state-machine branches. See [Executor Lifecycle](executor-lifecycle.md).

Current active executor ids are `grok-cli`, `antigravity-cli`, `claude-code`,
and `codex`. `codex` means an AGPair-managed external Codex CLI worker, not
Codex native subagents. `claude-code` means an AGPair-managed external Claude
Code worker, not Claude Code native subagents.

## Release and privacy checklist

Before publishing or opening a PR:

- Run the targeted tests plus the full unit/integration suite.
- Run real smoke for the controller matrix, require `all_success=true` plus
  usable `agent_result.state` and `recovery_decision.action` for each
  attempted executor, and keep smoke reports local.
- Run `git diff --check`.
- Inspect `git status --short --untracked-files=all`.
- Do not stage `.agpair/`, `~/.agpair`, raw executor logs, local receipts, session transcripts, personal Codex/Claude config, or generated hook debug output.
- Sanitize local paths and private artifact references from committed docs.
- Check GitHub About/description/topics manually; repository metadata is outside the source diff and can become stale.
