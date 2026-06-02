# External Agent First AGPair V1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor AGPair so Codex and Claude Code can dispatch non-trivial work to cheap external CLI agents first, with dispatch-time authorization, structured blocked context, low-token waiting, and state-aware retry.

**Architecture:** Codex and Claude Code remain controllers and verifiers; AGPair becomes their shared external-worker scheduler, durable polling layer, and receipt ledger. V1 uses simple executor routing policy, local CLI executors, terminal blocked states, fresh retry attempts instead of runtime approval/resume, low-noise watch events instead of model polling, and client-specific hooks/skills/config to prefer external agents before native subagents.

**Tech Stack:** Python, Typer CLI, SQLite task storage, local shell CLI executors, git worktrees, pytest.

---

## 1. Why This Change Exists

Current AGPair still carries older assumptions:

- `target` looks more important than it should be. The desired default is any current git repo, not a manually registered project list.
- Executor names are ambiguous. `antigravity` can mean IDE, desktop app, old bridge, or CLI.
- Gemini CLI remains in routing even though the desired replacement is Antigravity CLI.
- Antigravity support currently routes through `AgentBusClient` / bridge semantics, which is the IDE-oriented path we do not want for the new default.
- Codex may start native subagents, but the desired cost policy is external agents first, Codex subagents only as fallback.
- Existing retry is too thin: it starts a fresh body, but does not automatically construct a rich retry brief from the blocked attempt.

The desired product behavior:

```text
Codex or Claude Code main agent plans, dispatches, integrates, and verifies.
AGPair external CLI agents execute first.
Native subagents are fallback or review resources, not the default worker pool.
```

## 2. Constraints

- **Scale:** Single-user local workstation orchestration, but multiple simultaneous task attempts can exist. V1 should handle at least 3 external CLI attempts across isolated worktrees without shared-state confusion.
- **Consistency:** Strong local task-state consistency in SQLite. A retry attempt must not ambiguously mutate the old attempt's terminal meaning.
- **Latency:** Dispatch and status commands should remain interactive shell commands. Long execution is delegated to executor processes and existing wait/watch loops.
- **Team:** Small project, local-first Python codebase. Prefer simple explicit code over dynamic framework abstractions.
- **Cost:** Cost priority matters. External agents are preferred because they are cheaper; Codex/Claude native subagents are fallback.

## 3. Non-Goals

Do not implement these in V1:

- Runtime approval where Codex grants permission to an already-running executor process.
- Pause/resume protocol across Antigravity CLI, Grok CLI, Claude Code, Gemini, and Codex CLI.
- Full capability profile, dynamic scoring, or benchmark-driven executor selection.
- Antigravity IDE bridge or desktop app execution path for new default routing.
- Gemini CLI as a valid new-task executor.
- Production deploy, git push, destructive cleanup, or credential-changing workflows inside default authorization profiles.
- Automatic trust in external executor completion without Codex-side diff and test verification.
- A Claude Code Monitor clone inside Codex. Codex should use AGPair blocking wait/watch, Codex App thread automations, and Stop-hook continuation where appropriate.
- Default RTK-style output rewriting for Codex. The user prefers preserving raw warnings, partial output, and diagnostic context in Codex.
- OMX source-code changes in V1. The integration must live in AGPair-owned Codex hooks, skills, docs, and optional plugin/config install surfaces.
- Hard-disabling Codex native subagents. Native subagents remain fallback when external agents are unavailable, unsuitable, or not good enough.
- Unconditional delegation. AGPair should be external-first, not external-only; tiny direct edits, highly interactive judgment, destructive/credentialed work, or work that cannot be independently verified should stay with Codex until it is safe to dispatch.
- Default auto-commit as the meaning of success. External agents may create commits only when the authorization profile allows it; AGPair success means `ready_for_review` with evidence, not necessarily a git commit.
- Checking user-local Codex or Claude Code config into the AGPair repository. The repository may ship commands, tests, skills, docs, and example snippets; actual `~/.codex/...`, `~/.claude/...`, and project-local private settings stay local unless explicitly intended as sanitized project config.
- Long public documentation that explains old behavior first. Public docs should state the current positioning and the current commands, with migration/history kept in changelog or internal plans.

## 4. Architecture Options Considered

### Option A: Runtime Approval / Resume State Machine

Executor starts with limited authority. When it needs more, it pauses, emits an approval request, waits for Codex/user approval, and resumes the same process.

Pros:

- Elegant user mental model.
- Could preserve exact process context.

Cons:

- Requires every executor CLI to pause, declare a request, wait, and resume consistently.
- Antigravity CLI, Grok CLI, Claude Code, and Codex CLI have different permission prompts and noninteractive modes.
- AGPair would end up simulating each vendor CLI's confirmation mechanism.
- Requires approval request table, compare-and-swap state transitions, expiry, audit log, and executor-specific resume semantics.

Decision: Reject for V1. Keep as possible V2 only if blocked/retry proves too wasteful.

### Option B: Dispatch-Time Authorization + Blocked Retry

Codex gives an authorization budget when dispatching. Executor works inside that budget. If it needs more, it emits a structured `BLOCKED` receipt with `blocker_type=approval_required`. Codex retries a new attempt with expanded authorization and a richer context bundle.

Pros:

- Consistent across all CLI executors.
- Simple terminal states.
- Easy to audit, test, and retry.
- Does not require process-level resume support.

Cons:

- New attempt does not resume from the exact original process line.
- Retry prompt quality matters.

Decision: Use for V1.

### Option C: Full Executor Capability Profile

Every executor declares a large structured capability matrix, and AGPair dynamically scores executor choices.

Pros:

- Flexible long-term.
- Good for heterogeneous executors once behavior is measured.

Cons:

- Premature for current needs.
- Requires benchmark data we do not have yet.
- More fields create more stale assumptions.

Decision: Reject for V1. Use a thin routing table instead.

### Option D: Thin Executor Routing Policy

Keep a small static policy describing executor id, CLI env var, default priority, enabled state, default authorization profile, preferred task tags, and avoid tags.

Pros:

- Makes current judgment explicit.
- Easy to edit after real usage.
- Avoids scattering executor priority in `if/elif` chains.

Cons:

- Less expressive than a full capability system.

Decision: Use for V1.

## 5. Final Decision Record

Decisions:

- Canonical executor ids for new work are `antigravity-cli`, `grok-cli`, `claude-code`, and `codex`.
- `antigravity-cli` is the default first executor for non-trivial implementation work.
- `grok-cli` is the cheap challenger/backup for exploration, second opinion, and retries.
- `claude-code` is the quality escalation executor for complex refactor/review work.
- `codex` is fallback only when external agents are unavailable or unsuitable.
- `gemini` / `gemini_cli` is no longer valid for new task start or retry routing.
- Historical `gemini_cli` records remain readable in `task status`, `task list`, and `task inspect`; retrying them must require choosing a supported executor.
- `target` remains optional profile storage. It is not a whitelist and not required for dispatch.
- If `--repo-path` and `--target` are both omitted, AGPair should auto-detect the current git root from `cwd`.
- Antigravity IDE and old bridge/agent-bus routing are removed from the new default path. Antigravity means Antigravity CLI only.
- V1 blocked approval is terminal. Retry opens a new attempt.
- Codex main agent remains responsible for scope, brief, dispatch, integration, diff inspection, and verification.
- Codex continuous waiting is shell-first: use `agpair task start --wait` for normal single tasks, and `agpair task watch <TASK_ID> --json` for intentional background or parallel tasks.
- Codex App thread automations are an optional long-duration heartbeat layer, not the default task monitor.
- Codex hooks provide external-agent-first routing guidance and state-aware continuation, but they must not create a high-frequency model polling loop.
- Claude Code hooks provide the same external-agent-first routing and state-aware continuation. Use command hooks by default; do not use prompt/agent hooks for low-token controller logic unless a future feature needs model judgment inside the hook.
- OMX should prefer external AGPair workers when AGPair is installed and healthy; when AGPair is missing or unhealthy, OMX behavior should remain unchanged.
- `UserPromptSubmit` can inject external-first developer context. `Stop` can continue a turn when AGPair state proves work remains. `SubagentStart` should not be treated as a reliable hard veto for native subagents.
- AGPair is the durable controller between Codex and external agents. It owns task state, watch cursors, raw log paths, authorization profile, retry context, and terminal receipts. Codex should not hold those as fragile conversation-only state.
- AGPair is also the durable controller between Claude Code and external agents. Claude Code may have its own background tasks, Monitor, hooks, skills, subagents, and settings scopes; AGPair still owns external task lifecycle, receipt validation, retry context, and low-noise wait/watch.
- `watch --json` is a state-change protocol, not a log stream. It emits compact events and points to raw log files instead of copying full executor output into controller context.
- External executor output is untrusted input. Codex may use summaries as hints, but completion must be accepted only after inspecting diff, commands, exit codes, receipts, and required validation evidence.
- The canonical successful terminal state is `ready_for_review`. Existing `COMMITTED` receipts can remain as a wire/legacy receipt kind, but AGPair status should not imply a commit is mandatory. A receipt may include optional `commit_ref`.
- `blocked(approval_required)` receipts must include an authorization delta: current profile, requested profile, requested actions, reason, risk, and safe retry flag.
- Executor routing stays thin, but V1 must still record health signals: binary availability, malformed receipts, recent failures, consecutive stuck attempts, and whether the executor is currently eligible.
- Hook integration must fail open. If AGPair is missing, unhealthy, outside a git repo, or unable to inspect state, hooks must not block Codex or OMX.
- Client configuration is part of the product surface. V1 must update repo skills and docs, and provide idempotent install/uninstall commands for Codex and Claude Code settings. Local installed copies are deployment artifacts, not source files.
- Public documentation should be concise and target-state oriented: say what AGPair is, how to dispatch, how to wait, how to retry, and how to install Codex/Claude integration. Avoid leading with legacy executor tables or old Antigravity/Gemini history.
- Before any GitHub submission, run a privacy gate that checks diffs and docs for local absolute paths, access tokens, API keys, bearer tokens, private endpoints, session ids, raw logs, and generated user config.

Rejected:

- Runtime approval/resume state machine in V1.
- Full executor capability profile in V1.
- Gemini CLI as a new-task executor.
- Antigravity IDE bridge as a default executor.
- Codex native subagents as the first extra-worker option.
- Runtime model-loop polling as the normal way to wait for external agents.
- Modifying OMX internals just to prefer AGPair in V1.
- Treating `SubagentStart` as a hard enforcement point.
- Trusting executor prose summaries as proof of completion.

## 6. Target State Machine

V1 state machine:

```text
new -> acked -> ready_for_review
             -> blocked(approval_required)
             -> blocked(other)
             -> stuck
```

Notes:

- `ready_for_review` means the external executor claims completion and has produced a terminal receipt/evidence pack. Codex still must inspect and verify before final user-facing completion.
- A task or receipt may include `commit_ref`, but commit creation is optional and controlled by authorization.
- Historical `committed` status or `COMMITTED` receipt names remain readable as legacy aliases during migration.
- `blocked(approval_required)` is terminal for the attempt.
- `task retry --from-block ...` creates a new attempt on the same task id.
- The retry attempt must carry the previous attempt context in the new prompt.
- Do not mutate an old terminal attempt into a running process.

V2 candidate state machine, explicitly deferred:

```text
acked -> approval_required -> acked/resumed
```

V2 requires approval tables, CAS transitions, expiry, executor capability declaration, audit logging, and resume protocol per executor. Do not build it in V1.

## 7. Authorization Profiles

V1 authorization is a dispatch-time budget, not a runtime grant.

Recommended profiles:

### `local_readonly`

Allowed:

- Read repo files.
- Run read-only shell commands.
- Run `git status`, `git diff`, `git log`, `rg`, test discovery commands.

Denied:

- File edits.
- Commits.
- Package install.
- Network mutation.
- Git push.
- Destructive cleanup.

Default use:

- Exploration, review, second opinion, failure diagnosis.

### `local_mutating`

Allowed:

- Edit files inside the repo or assigned isolated worktree.
- Run tests, linters, formatters, build commands.
- Create local commits if task requires commit-based completion.

Denied:

- Git push.
- Production deploy.
- Credential changes.
- Repo-external sensitive-path edits.
- Destructive cleanup outside assigned worktree.
- Printing secrets.

Default use:

- Implementation, refactor, test-fix loops.

### `local_test_heavy`

Allowed:

- Same as `local_mutating`.
- Longer test/build commands.
- More aggressive local cache/temp writes inside normal tool caches.

Denied:

- Same as `local_mutating`.

Default use:

- "Run tests and fix until green" tasks.

### `external_network`

Allowed:

- Same as `local_mutating`.
- Network reads required for docs or package metadata.

Denied:

- Credential mutation.
- Purchase/billing/deploy actions.
- Git push.

Default use:

- Only when brief explicitly needs external docs/references. Do not use as default.

Blocked receipt for authorization boundary:

```json
{
  "schema_version": "1",
  "task_id": "TASK-123",
  "attempt_no": 1,
  "review_round": 0,
  "status": "BLOCKED",
  "summary": "Need authorization to edit files outside the assigned scope.",
  "payload": {
    "blocker_type": "approval_required",
    "recoverable": true,
    "suggested_action": "retry_with_expanded_authorization",
    "authorization_profile": "local_readonly",
    "requested_authorization_profile": "local_mutating",
    "requested_actions": ["modify agpair/cli/task.py", "modify agpair/storage/schema.sql"],
    "authorization_delta": {
      "allow_file_edits": true,
      "allow_test_commands": true,
      "allow_local_commit": false
    },
    "request_reason": "The original readonly profile allowed inspection but not the implementation edits required by the brief.",
    "risk_assessment": "Repo-local file edits only; no network mutation, credential changes, git push, or destructive cleanup requested.",
    "last_error_excerpt": "Denied by authorization profile: file write requested.",
    "safe_to_retry": true,
    "raw_log_path": "/tmp/agpair_antigravity-cli_TASK-123/stderr.log"
  }
}
```

## 8. Retry Contract

New CLI shape:

```bash
agpair task retry TASK-123 \
  --from-block \
  --authorization-profile local_mutating
```

Optional executor override:

```bash
agpair task retry TASK-123 \
  --from-block \
  --authorization-profile local_mutating \
  --executor claude-code
```

Retry context bundle must include:

- Original brief from the task creation journal entry.
- Previous blocked reason.
- Previous structured terminal receipt, if present.
- Previous attempt summary and log tail.
- Current `git status --short`.
- Current diff for the repo/worktree.
- Current HEAD and recent commits relevant to the task id.
- New authorization profile and denied/allowed actions summary.
- Previous authorization delta, including requested actions and risk assessment.
- Required receipt schema.

Retry behavior:

- `--from-block` is valid only for `blocked` tasks.
- If blocker is not `approval_required`, `--from-block` may still work, but the generated prompt must preserve the real blocker type.
- If current executor is legacy `gemini_cli`, retry must require an explicit supported executor or route through the new routing table.
- If there is an active waiter or live process, existing guards still apply unless `--force` is explicit.

## 9. Executor Routing Policy

Do not implement a full capability profile in V1. Implement a thin routing table.

Suggested module:

- Create: `agpair/executors/routing.py`

Suggested data model:

```python
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ExecutorRoute:
    executor_id: str
    env_var: str | None
    priority: int
    enabled: bool
    default_authorization_profile: str
    default_for: tuple[str, ...]
    avoid_for: tuple[str, ...]


ROUTES: tuple[ExecutorRoute, ...] = (
    ExecutorRoute(
        executor_id="antigravity-cli",
        env_var="AGPAIR_ANTIGRAVITY_CLI",
        priority=10,
        enabled=True,
        default_authorization_profile="local_mutating",
        default_for=("implementation", "test_fix", "refactor"),
        avoid_for=("ambiguous_product_judgment", "production_deploy"),
    ),
    ExecutorRoute(
        executor_id="grok-cli",
        env_var="AGPAIR_GROK_CLI",
        priority=20,
        enabled=True,
        default_authorization_profile="local_readonly",
        default_for=("exploration", "second_opinion", "cheap_retry"),
        avoid_for=("production_deploy",),
    ),
    ExecutorRoute(
        executor_id="claude-code",
        env_var="AGPAIR_CLAUDE_CODE_CLI",
        priority=30,
        enabled=True,
        default_authorization_profile="local_mutating",
        default_for=("complex_refactor", "code_review", "quality_escalation"),
        avoid_for=("production_deploy",),
    ),
    ExecutorRoute(
        executor_id="codex",
        env_var="AGPAIR_CODEX_CLI",
        priority=90,
        enabled=True,
        default_authorization_profile="local_mutating",
        default_for=("fallback_only",),
        avoid_for=("default_external_work",),
    ),
)
```

Initial routing:

```text
implementation/refactor/test_fix -> antigravity-cli
exploration/second_opinion/cheap_retry -> grok-cli
complex_refactor/code_review/quality_escalation -> claude-code
external unavailable/failed or task too context-coupled -> codex or Codex native subagent
```

Selection order:

1. Explicit `--executor`.
2. Target default executor, if target exists.
3. `AGPAIR_DEFAULT_EXECUTOR`.
4. Routing table by task tags, if task tags exist.
5. Default fallback `antigravity-cli`.

Important distinction:

- `codex` executor in AGPair means external Codex CLI worker.
- Codex native subagent is not an AGPair executor and remains a controller-side fallback.

Delegation eligibility gate:

AGPair should dispatch externally only when the work is delegatable:

- The task can be described with a bounded brief and explicit success criteria.
- The executor can work inside a repo or isolated worktree without needing live user approval.
- Completion can be verified by files, diff, commands, receipts, or deterministic artifacts.
- Failure can be retried, ignored, or rolled back without destructive external side effects.
- Required context can be passed as files, paths, diffs, or short summaries without depending on hidden Codex conversation state.

Codex should keep direct ownership when:

- The change is tiny and cheaper to do directly than to package.
- The task requires immediate interactive judgment or visual/UI inspection by Codex.
- The task is credentialed, production-facing, destructive, or otherwise outside default authorization.
- The current Codex context contains high-value details that cannot be safely summarized into an executor brief.
- Prior external attempts were unavailable, malformed, stuck, or not good enough.

Minimum executor health model:

V1 still avoids a full capability profile, but routing needs enough health data to decide when to fall back.

Track at least:

```text
executor_id
binary_path
available
last_checked_at
last_success_at
recent_failure_count
consecutive_stuck_count
malformed_receipt_count
last_error_excerpt
eligible
```

Fallback rules:

- If the explicit executor is unavailable, fail with a clear error instead of silently choosing another executor.
- If the implicit/default executor is unavailable or recently unhealthy, try the next healthy external executor before `codex`.
- If all external executors are unavailable or fail the health gate, Codex may use the AGPair `codex` executor or Codex native subagents as fallback.
- A malformed receipt is an executor quality failure. Preserve raw logs and do not mark the attempt `ready_for_review`.

## 10. Codex Controller Continuity and OMX Adapter

Codex has three usable continuation surfaces. V1 must use them deliberately:

### Blocking Wait / Watch

Default path for Codex-controlled AGPair tasks:

```bash
agpair task start --repo-path "$REPO" --executor antigravity-cli --body "<brief>"
```

`task start` waits by default. Waiting occurs in the local process, so Codex does not spend a model turn on each poll. Use this for normal single-task delegation.

Intentional background or parallel path:

```bash
agpair task start --repo-path "$WT_A" --executor antigravity-cli --body "<brief A>" --no-wait
agpair task start --repo-path "$WT_B" --executor grok-cli --body "<brief B>" --no-wait
agpair task watch TASK-A --json
agpair task watch TASK-B --json
```

`watch --json` should remain compact and state-change-oriented. It must not print a full status blob every interval and must not stream raw executor logs into Codex context. It should emit only on:

- State change.
- Heartbeat/activity change.
- Terminal receipt creation or update.
- Stale/watchdog threshold crossing.
- Watch cursor reset or recovery.
- Explicit timeout.

Suggested event shape:

```json
{
  "schema_version": "1",
  "event": "terminal_receipt",
  "task_id": "TASK-123",
  "attempt_no": 2,
  "state": "ready_for_review",
  "summary": "Executor produced a terminal receipt and validation evidence.",
  "receipt_path": ".agpair/tasks/TASK-123/attempt-2/receipt.json",
  "raw_log_path": ".agpair/tasks/TASK-123/attempt-2/stdout.log",
  "cursor": "attempt-2:receipt:7"
}
```

Watch output budget:

- One JSON object per meaningful event.
- No full diff unless explicitly requested by a separate command.
- No repeated heartbeat line if nothing changed.
- Raw logs, full receipts, and diffs are referenced by path and loaded only when Codex needs them.
- `task start --wait` should internally use the same event/cursor machinery so behavior is identical between wait and watch.

### Codex App Thread Automation

Thread automations are optional for very long tasks or tasks likely to outlive a single Codex turn/tool wait. They should:

- Wake the same thread at a minute-level interval chosen by the controller.
- Run a compact status command such as `agpair task status <TASK_ID> --json`.
- Report only terminal, blocked, stale/watchdog, or newly actionable states.
- Stop or pause themselves after terminal verification is complete.

Do not make thread automation the default for ordinary AGPair dispatch. It creates model turns and therefore has token cost.

V1 should document thread automation as an optional manual/App-level workflow. Add an AGPair helper only later if implementation discovery proves Codex App automation creation is stable enough to support from CLI.

### Codex Stop Hook Continuation

The AGPair Codex Stop hook is a guardrail, not a poller.

It may continue the turn only when local AGPair state proves one of these is true:

- A task was dispatched in this thread/repo and no watch/wait was attached yet.
- A task reached `ready_for_review` or legacy `committed`, and Codex still needs to inspect diff, optional commits, and required evidence.
- A task reached `blocked`, and Codex needs to produce a retry decision or a concise blocked report.
- A task crossed a stale/watchdog threshold and Codex must mark it `stuck`, switch executor, or report the blocker.

It must not continue on every `acked` state just to poll again. If a task is still running and a blocking `agpair task watch ... --json` process is already active, the hook should allow the turn to remain quiet.

### Codex Hooks and OMX

V1 adapts OMX behavior without modifying OMX source:

- Add an AGPair-owned Codex config surface: `agpair codex config`.
- Install user or project Codex hooks that call AGPair-owned hook commands.
- `UserPromptSubmit` hook detects `agpair` availability, repo path, and basic AGPair health. If healthy, it injects external-first routing context.
- If AGPair is not installed, not on `PATH`, not in a git repo, or doctor/basic state check fails, the hook exits quietly and OMX keeps its normal native subagent preference.
- `Stop` hook handles state-aware continuation as described above.
- Do not hard-disable `multi_agent`, do not edit OMX generated files, and do not treat `SubagentStart` as a reliable hard veto. At most, `SubagentStart` can add context reminding the child that external AGPair state may exist.
- Hooks must be idempotent and fail open. If JSON input is missing, AGPair state is unreadable, `agpair` is not on `PATH`, or the repo cannot be resolved, emit nothing and exit successfully unless a debug flag asks for diagnostics.
- Hook installation must merge by command identity and must not overwrite unrelated Codex, user, or OMX hook entries.

Suggested `UserPromptSubmit` additional context:

```text
AGPair external-first routing is available in this repository. For non-trivial implementation, refactor, test-fix, research, or review work, prefer dispatching through AGPair external CLI executors before using Codex native subagents. Codex main remains the controller and verifier. Use native subagents only when AGPair is unavailable, an external executor is unsuitable, or external results are not good enough.
```

Suggested Stop-hook continuation reasons:

```text
AGPair task TASK-123 is active and no wait/watch has been attached. Run `agpair task watch TASK-123 --json` and continue from the terminal state.
```

```text
AGPair task TASK-123 reached ready_for_review. Inspect git status, optional commit/diff, receipt, raw log paths, and required evidence before finalizing.
```

```text
AGPair task TASK-123 is blocked with blocker_type=approval_required. Do not keep polling. Decide whether to retry with `agpair task retry TASK-123 --from-block --authorization-profile ...` or report the blocker.
```

## 11. Claude Code Controller Adapter

Claude Code uses the same AGPair core state as Codex, but the client integration surface is different.

Current Claude Code facts to design against:

- Settings are scoped as managed, user, project, and local. User settings live under `~/.claude/`; project settings live under `.claude/`; local settings are not committed. Source: [Claude Code settings](https://code.claude.com/docs/en/settings).
- Skills are loaded on demand and are better than long always-on `CLAUDE.md` procedures for AGPair usage. Source: [Claude Code skills](https://code.claude.com/docs/en/skills).
- Hooks are deterministic lifecycle controls. Relevant events include `UserPromptSubmit`, `Stop`, `SubagentStart`, `SubagentStop`, `TaskCreated`, `TaskCompleted`, `ConfigChange`, `PreCompact`, and `PostToolBatch`. Source: [Claude Code hooks](https://code.claude.com/docs/en/hooks).
- Recent Claude Code changelog entries added or improved background tasks, hook resilience, subagent metadata, `terminalSequence`, background task completion notifications, `claude agents --cwd`, and `background_tasks` / `session_crons` fields in stop-related hook payloads. Source: [Claude Code changelog](https://code.claude.com/docs/en/changelog).

V1 Claude integration:

- Keep `agpair claude statusline` as the passive always-visible state hint.
- Keep `SessionStart` as a low-cost startup reminder, but add `UserPromptSubmit` so external-first guidance is injected on actual prompts when AGPair is healthy in the repo.
- Add `Stop` so Claude Code does not finalize when AGPair has an actionable active task, `ready_for_review` task, approval block, or stale/watchdog event.
- Keep `PreCompact`, but update it for `ready_for_review` and low-noise watch semantics.
- Add `SubagentStart` as advisory only. It may remind a native subagent that AGPair external-first is active, but it must not hard-block native subagents.
- Add `SubagentStop` as a verifier/audit hook. It can read `last_assistant_message` from hook payload and record whether a native subagent was used as fallback, without parsing full subagent transcripts.
- Add `TaskCreated` / `TaskCompleted` only as observability in V1. Do not hard-map Claude Code native tasks into AGPair tasks until implementation discovery proves the mapping is stable.
- Prefer command hooks over prompt hooks or agent hooks for AGPair control. Prompt/agent hooks spend model turns and are not the low-token path.
- If Claude Code exposes `background_tasks` or `session_crons` in Stop/SubagentStop input, use them as extra context, not as source of truth. AGPair task state remains authoritative for AGPair work.

Claude config policy:

- `agpair claude config` must install/update/uninstall only AGPair-managed entries.
- It must preserve unrelated statusline, hooks, permissions, plugins, MCP config, and user settings unless `--force` explicitly replaces an AGPair-managed slot.
- It should support `--scope project|user`, `--dry-run`, `--install`, and `--uninstall`.
- Project config may be committed only if it is generic and sanitized. User config under `~/.claude/` is never committed.
- The canonical skill source is `skills/Claude/SKILL.md`; installed copies under `~/.claude/skills/agpair/SKILL.md` are deployment artifacts.

## 12. Worktree Policy

Serial single task:

- May use current worktree.
- If current worktree is dirty, AGPair should surface that in status and prompt context.

Parallel tasks:

- Must use `--isolated-worktree`.
- Each independent write task gets a separate `.agpair/worktrees/<TASK_ID>` worktree unless `--worktree-boundary` is explicit.

Dirty base repo:

- If dirty files overlap intended scope, Codex should not dispatch multiple writers.
- Safe default is isolated worktree plus explicit context about base dirty files.

Scope violations:

- External agent changing files outside assigned scope is a quality failure.
- Receipt payload should include `scope_violations`.
- Codex must inspect diff before accepting completion.

## 13. Receipt Contract

Current `terminal_receipts.py` already supports:

- `EVIDENCE_PACK`
- `BLOCKED`
- `COMMITTED`

V1 should keep `schema_version="1"` and extend payload conventions instead of changing the top-level schema. The storage/status layer should surface successful terminal work as `ready_for_review`; `COMMITTED` can remain an accepted receipt kind or legacy alias.

Trust boundary:

- Treat all executor prose as untrusted. `summary`, `confidence`, and `residual_risks` help Codex triage, but they are not proof.
- Trust is earned through machine-checkable evidence: changed files, validation commands, exit codes, raw log paths, receipt paths, diff/commit references, and scope checks.
- If the receipt is malformed, references missing files, claims commands without exit codes, or omits required evidence, mark the attempt `blocked(validation_failure)` or `stuck`, not `ready_for_review`.
- Preserve raw output by path. Do not apply RTK-style rewriting or lossy compression by default.

Recommended successful terminal payload fields:

```json
{
  "changed_files": ["agpair/cli/task.py"],
  "validation": [
    {
      "command": "pytest tests/integration/test_task_start_and_status.py -q",
      "exit_code": 0,
      "log_path": "/tmp/agpair_antigravity-cli_TASK-123/pytest.log"
    }
  ],
  "residual_risks": ["No live Antigravity CLI smoke in CI"],
  "scope_violations": [],
  "commands_run": ["pytest tests/unit/test_local_cli_executor.py -q"],
  "commit_ref": "abc1234",
  "raw_log_path": "/tmp/agpair_antigravity-cli_TASK-123/stdout.log",
  "receipt_path": ".agpair/tasks/TASK-123/attempt-2/receipt.json",
  "confidence": "medium",
  "claimed_state": "ready_for_review"
}
```

Recommended `BLOCKED` payload fields:

```json
{
  "blocker_type": "approval_required",
  "recoverable": true,
  "suggested_action": "retry_with_expanded_authorization",
  "authorization_profile": "local_readonly",
  "requested_authorization_profile": "local_mutating",
  "requested_actions": ["edit files", "run tests"],
  "authorization_delta": {
    "allow_file_edits": true,
    "allow_test_commands": true,
    "allow_local_commit": false
  },
  "request_reason": "Implementation requires repo-local edits not allowed by local_readonly.",
  "risk_assessment": "No git push, deploy, credential mutation, or destructive cleanup requested.",
  "last_error_excerpt": "Denied by authorization profile",
  "raw_log_path": "/tmp/agpair_grok-cli_TASK-123/stderr.log",
  "safe_to_retry": true
}
```

Machine-readable blocker types:

```text
approval_required
executor_unavailable
executor_auth_failed
executor_runtime_failure
validation_failure
workspace_conflict
scope_violation
unsupported_operation
unknown
```

Receipt validation rules:

- `approval_required` must include `requested_authorization_profile`, `requested_actions`, `authorization_delta`, `request_reason`, `risk_assessment`, `safe_to_retry`, and `raw_log_path`.
- `ready_for_review`/successful receipts must include `changed_files`, `validation` or an explicit `validation_not_run` reason, `scope_violations`, `raw_log_path`, and `receipt_path`.
- `scope_violations` must be an array. Empty means the executor claims no violation; Codex still verifies with diff.
- `commit_ref` is optional. Absence of a commit is valid unless the authorization profile or task brief required a commit.
- `confidence` is advisory only.

## 14. File Map

Files to modify:

- `agpair/cli/task.py`
  - Executor choices.
  - Repo-path auto-detection.
  - `--authorization-profile`.
  - `--from-block`.
  - Retry context bundle generation.
  - `watch --json` command surface backed by `agpair/watch.py`.
  - Status payload backend table.
  - Surface `ready_for_review` as the successful terminal state while accepting legacy `committed`/`COMMITTED`.
  - Use generic `executor_session_id` wording in user-facing output. Treat any existing `antigravity_session_id` storage as a legacy compatibility detail.

- `agpair/cli/wait.py`
  - Update local-CLI wait logic for new backend ids.
  - Use `ready_for_review` and receipt validation instead of committed-only success.
  - Avoid Antigravity-specific wording for local CLI waits.

- `agpair/cli/codex.py`
  - Codex config snippet/install/uninstall for AGPair hooks.
  - `UserPromptSubmit` hook for external-first routing context.
  - `Stop` hook for state-aware continuation.
  - Optional `SubagentStart` advisory context only; no hard veto.

- `agpair/cli/claude.py`
  - Refresh existing Claude Code config installer.
  - Add `UserPromptSubmit`, `Stop`, `SubagentStart`, `SubagentStop`, `TaskCreated`, and `TaskCompleted` AGPair-managed hook commands.
  - Keep `statusLine`, `SessionStart`, and `PreCompact`, but update them for `ready_for_review`, low-noise watch, and external-first routing.
  - Preserve unrelated user/project settings; uninstall only AGPair-managed entries.
  - Fail open when AGPair state is unreadable.

- `agpair/cli/app.py`
  - Register `agpair codex` Typer subcommand.
  - Keep `agpair claude` registered and extend it rather than creating a second Claude entry point.

- `agpair/mcp_server.py`
  - Update MCP task-start executor validation to the new ids.
  - Allow omitted `repo_path` / `target` when the CLI can detect the git root.
  - Reject Gemini for new task creation while keeping old task inspection readable through CLI/status surfaces.

- `agpair/daemon/loop.py`
  - Update default executor lookup to the new routing table.
  - Remove Antigravity-specific default assumptions from dispatch/poll messages.
  - Keep legacy Antigravity bridge polling only behind the legacy executor path if it remains.

- `agpair/targets.py`
  - Update allowed `default_executor` values.
  - Keep target optional.

- `agpair/models.py`
  - Add authorization fields to `TaskRecord`.
  - Keep `ExecutorSafetyMetadata`; do not turn it into a large capability profile.
  - Add or expose `ready_for_review` terminal state and optional `commit_ref`.
  - Add or expose generic `executor_session_id` for current code. Keep `antigravity_session_id` only as migration/read compatibility if renaming the SQLite column is too risky in V1.

- `agpair/storage/schema.sql`
  - Add authorization and retry context columns.
  - Add watch cursor/last activity fields if not already represented in the task journal.
  - Add `executor_session_id` or a compatibility view/accessor so new code does not expose Antigravity-specific storage names.

- `agpair/storage/db.py`
  - Add migrations for new columns.

- `agpair/storage/tasks.py`
  - Persist authorization profile.
  - Preserve block/retry context.
  - Support retry with executor/profile override.
  - Persist executor health signals and terminal receipt validation result if this project keeps task-derived health in SQLite.
  - Read/write generic executor session ids while preserving old rows.

- `agpair/executors/health.py`
  - New lightweight executor health model.
  - Binary availability, recent failures, consecutive stuck attempts, malformed receipts, and eligibility decisions.

- `agpair/watch.py`
  - Low-noise watch event generator.
  - Cursor handling.
  - Shared event stream used by `task start --wait` and `task watch --json`.

- `agpair/executors/__init__.py`
  - New registry ids.
  - Remove Gemini from new-task registry.
  - Keep legacy status behavior safe.

- `agpair/executors/base.py`
  - Keep the executor protocol generic.
  - If the dispatch result still uses `session_id`, document it as an executor session id, not an Antigravity session.

- `agpair/executors/local_cli.py`
  - Inject authorization contract and receipt contract into local CLI prompts.
  - Preserve existing task-id commit contract.
  - Preserve raw stdout/stderr logs by path.
  - Validate terminal receipt enough to avoid marking malformed output as `ready_for_review`.

- `agpair/executors/antigravity_cli.py`
  - New Antigravity CLI adapter.

- `agpair/executors/grok_cli.py`
  - New Grok CLI adapter.

- `agpair/executors/claude_code.py`
  - New Claude Code adapter.

- `agpair/executors/codex.py`
  - Rename the active backend id from old `codex_cli` semantics to canonical `codex`, or provide a registry alias that stores `codex` for new work and reads `codex_cli` as legacy.
  - Keep Codex as fallback external worker, not the first external executor.

- `agpair/executors/gemini.py`
  - Remove from active registry and tests, or leave only if needed for historical import compatibility. New `task start` and `task retry` must not use it.

- `agpair/cli/doctor.py`
  - Check configured CLI paths and report missing executors.
  - Do not require all executors to exist for AGPair to function.
  - Show health signals without making non-default missing executors fatal.

- `skills/Codex/SKILL.md`
  - External-agent-first routing.
  - Antigravity CLI only.
  - No Gemini.
  - Codex native subagent as fallback.
  - Prefer `task start --wait` and `task watch --json`; do not simulate Claude Monitor with repeated model prompts.
  - Explain when to use Codex App thread automation as a long-duration heartbeat.

- `skills/Claude/SKILL.md`
  - External-agent-first routing for Claude Code.
  - Antigravity CLI only.
  - No Gemini for new work.
  - Prefer `task start --wait` and `task watch --json`.
  - Use Claude Code native subagents/background tasks only as fallback, review, or when AGPair is unavailable/insufficient.
  - Keep the skill short; move detailed rationale to docs.

- `docs/usage.md`
  - CLI examples and authorization/retry contract.
  - Current positioning only; remove stale Gemini/Codex priority tables.
  - Codex and Claude Code config install commands.

- `docs/usage.zh-CN.md`
  - Chinese examples and controller guidance.
  - Current positioning only; remove stale Gemini/Codex priority tables.
  - Codex and Claude Code config install commands.

- `docs/getting-started.en.md`
  - Current setup path only.
  - Replace old Antigravity IDE bridge / Gemini examples with Antigravity CLI, Grok CLI, Claude Code, and Codex fallback wording.

- `docs/getting-started-zh.md`
  - Current setup path only.
  - Replace old Antigravity IDE bridge / Gemini examples with Antigravity CLI, Grok CLI, Claude Code, and Codex fallback wording.

- `docs/codex-controller-research.zh-CN.md`
  - Refresh stale v0.121 conclusion with the new Codex App automation, hooks, and subagent facts.
  - Keep the boundary clear: Codex still has no direct Claude Monitor equivalent.

- `docs/claude-code-integration.zh-CN.md`
  - Refresh with current Claude Code settings/hook/skill capability.
  - Focus on current AGPair positioning and V1 integration, not a long historical changelog.
  - Cite official Claude Code docs/changelog links.

- `README.md`
  - Reposition AGPair as an external-agent-first control plane for Codex and Claude Code.
  - Keep setup concise.
  - Remove stale executor priority claims.

- `README.zh-CN.md`
  - Mirror the concise current positioning in Chinese.
  - Remove stale executor priority claims and old historical framing from the main path.

- `skills/claw.json`
  - Refresh skill metadata so descriptions and tags do not advertise Gemini or the old bridge as current routing.

- `.gitignore`
  - Ensure local state/config/log artifacts stay out of Git when this repo generates test config.
  - Keep `.agpair/`, `.omx/`, `.venv/`, build artifacts, and any generated local settings/log outputs ignored.

Tests to update or add:

- `tests/unit/test_gemini_executor.py`
  - Remove or replace with legacy rejection tests.

- `tests/unit/test_local_cli_executor.py`
  - Authorization prompt injection.
  - Receipt payload conventions.

- `tests/unit/test_codex_executor.py`
  - Executor id compatibility after rename to `codex`.

- Add: `tests/unit/test_executor_routing.py`
  - Thin routing table.
  - Default order.
  - Legacy Gemini rejection.
  - Delegation eligibility gate.

- Add: `tests/unit/test_executor_health.py`
  - Availability.
  - Recent failure/stuck/malformed receipt counters.
  - Explicit executor unavailable fails clearly.
  - Implicit/default routing can fall back to next healthy external executor.

- Add: `tests/unit/test_authorization_profiles.py`
  - Profile validation.
  - Blocked receipt interpretation.
  - Authorization delta validation.

- Add: `tests/unit/test_receipt_validation.py`
  - Successful receipts require machine-checkable evidence.
  - Malformed receipts are not `ready_for_review`.
  - `commit_ref` is optional unless required by brief/profile.

- Add: `tests/integration/test_task_watch_events.py`
  - `watch --json` emits only meaningful events.
  - Raw logs are referenced by path instead of streamed repeatedly.
  - `task start --wait` and `task watch --json` share event semantics.

- Add: `tests/integration/test_task_retry_from_block.py`
  - `--from-block` bundle generation.
  - Executor/profile override.
  - Authorization delta is copied into the new attempt prompt.

- Update: `tests/integration/test_task_start_and_status.py`
  - New executor ids.
  - Status payload supported/legacy backends.
  - `ready_for_review` successful terminal state with legacy `committed` readability.

- Update: `tests/unit/test_mcp_server.py`
  - New executor ids in MCP start-task validation.
  - Omitted repo locator behavior when CLI can detect git root.
  - Gemini rejection for new MCP task starts.

- Update: `tests/integration/test_task_wait.py`
  - `ready_for_review` terminal behavior.
  - Low-noise wait/watch wording.
  - Generic `executor_session_id` output.

- Update: `tests/integration/test_task_wait_inline_poll.py`
  - New local CLI backend ids.
  - Generic executor session cleanup.

- Update: `tests/integration/test_daemon_codex_lifecycle.py`
  - Legacy `codex_cli` readability plus new `codex` backend id for new work.

- Update or replace: `tests/integration/test_daemon_gemini_lifecycle.py`
  - Gemini no longer starts new tasks.
  - Historical `gemini_cli` rows remain inspectable or sweepable only.

- Update: `tests/unit/test_auto_advance.py`
  - Dependent task auto-advance uses the routing table and new canonical executor ids.

- Update: `tests/integration/test_doctor.py`
  - CLI preflight reporting.

- Add: `tests/integration/test_codex_cli.py`
  - Codex config payload.
  - Codex hooks emit context only when AGPair is available and repo-local.
  - Stop hook continuation decisions.
  - Hooks fail open when AGPair state is unreadable.

- Update: `tests/integration/test_claude_cli.py`
  - Claude config payload includes refreshed AGPair-managed hooks.
  - Config install preserves unrelated hooks/settings.
  - Config uninstall removes only AGPair entries.
  - `UserPromptSubmit` injects external-first context only when healthy/repo-local.
  - `Stop` blocks only actionable AGPair states.
  - `SubagentStart` is advisory only.
  - `SubagentStop` can record fallback evidence from `last_assistant_message`.
  - `TaskCreated` / `TaskCompleted` are observability-only in V1.
  - Hooks fail open when AGPair state is unreadable.

- Add: `tests/integration/test_fake_executors.py`
  - Fake executor success.
  - Fake executor `approval_required`.
  - Fake executor timeout/stuck.
  - Fake executor malformed JSON.
  - Fake executor validation failure.
  - Fake executor dirty worktree / scope violation.

## 15. Implementation Tasks

### Task 1: Add Executor Names and Routing Table

**Files:**

- Create: `agpair/executors/routing.py`
- Modify: `agpair/executors/__init__.py`
- Modify: `tests/unit/test_executor_routing.py`

- [ ] **Step 1: Write routing unit tests**

Create `tests/unit/test_executor_routing.py` with tests for:

```python
from agpair.executors.routing import default_executor_id, is_supported_executor


def test_default_executor_is_antigravity_cli():
    assert default_executor_id() == "antigravity-cli"


def test_supported_executor_ids_exclude_gemini():
    assert is_supported_executor("antigravity-cli")
    assert is_supported_executor("grok-cli")
    assert is_supported_executor("claude-code")
    assert is_supported_executor("codex")
    assert not is_supported_executor("gemini")
    assert not is_supported_executor("gemini_cli")
```

- [ ] **Step 2: Run the failing tests**

Run:

```bash
pytest tests/unit/test_executor_routing.py -q
```

Expected: fails because `agpair.executors.routing` does not exist.

- [ ] **Step 3: Implement `agpair/executors/routing.py`**

Use the thin routing table from section 9. Add helpers:

```python
def supported_executor_ids() -> tuple[str, ...]:
    return tuple(route.executor_id for route in ROUTES if route.enabled)


def is_supported_executor(executor_id: str | None) -> bool:
    return bool(executor_id) and executor_id in supported_executor_ids()


def default_executor_id() -> str:
    return "antigravity-cli"
```

- [ ] **Step 4: Update registry**

Update `agpair/executors/__init__.py` so active ids are:

```text
antigravity-cli
grok-cli
claude-code
codex
```

Do not include `gemini` or `gemini_cli` in active `LOCAL_CLI_BACKENDS`.

- [ ] **Step 5: Run tests**

Run:

```bash
pytest tests/unit/test_executor_routing.py -q
```

Expected: pass.

### Task 2: Replace Antigravity IDE/Bus Executor With Antigravity CLI Adapter

**Files:**

- Create: `agpair/executors/antigravity_cli.py`
- Modify: `agpair/executors/antigravity.py`
- Modify: `agpair/executors/__init__.py`
- Add or update: `tests/unit/test_antigravity_cli_executor.py`

- [ ] **Step 1: Locate binary configuration contract**

Use this env var:

```text
AGPAIR_ANTIGRAVITY_CLI
```

Fallback command name:

```text
antigravity
```

The implementation must not call the IDE bridge or `AgentBusClient`.

- [ ] **Step 2: Write adapter test**

Test should instantiate `AntigravityCLIExecutor` and assert:

```python
assert executor.backend_id == "antigravity-cli"
assert executor.safety_metadata.requires_human_interaction is False
```

For command building, use a fake binary path from `AGPAIR_ANTIGRAVITY_CLI`.

- [ ] **Step 3: Implement adapter as `LocalCLIExecutor` subclass**

Adapter should mirror existing local CLI pattern:

```python
class AntigravityCLIExecutor(LocalCLIExecutor):
    def __init__(self, antigravity_bin: str | None = None) -> None:
        super().__init__(
            bin_path=antigravity_bin or os.environ.get("AGPAIR_ANTIGRAVITY_CLI", "antigravity"),
            backend_id="antigravity-cli",
            build_cmd=self._build_antigravity_cmd,
        )
```

The exact CLI invocation may require implementation discovery. The adapter must remain noninteractive and must accept prompt body plus repo cwd.

- [ ] **Step 4: Preserve old class only as legacy wrapper if needed**

If `AntigravityExecutor` remains, mark it as legacy bridge in comments and remove it from default routing. New code paths should use `AntigravityCLIExecutor`.

- [ ] **Step 5: Run targeted tests**

Run:

```bash
pytest tests/unit/test_antigravity_cli_executor.py tests/unit/test_executor_routing.py -q
```

Expected: pass.

### Task 3: Add Grok CLI and Claude Code Executors

**Files:**

- Create: `agpair/executors/grok_cli.py`
- Create: `agpair/executors/claude_code.py`
- Modify: `agpair/executors/__init__.py`
- Add: `tests/unit/test_grok_cli_executor.py`
- Add: `tests/unit/test_claude_code_executor.py`

- [ ] **Step 1: Define binary env vars**

Use:

```text
AGPAIR_GROK_CLI
AGPAIR_CLAUDE_CODE_CLI
```

Fallbacks:

```text
grok
claude
```

- [ ] **Step 2: Write tests for ids and command construction**

Expected ids:

```text
grok-cli
claude-code
```

- [ ] **Step 3: Implement adapters as `LocalCLIExecutor` subclasses**

Use noninteractive execution modes for each CLI. If exact flags require discovery, add narrow tests around the command builder after discovery rather than hiding command strings in docs.

Do not hardcode developer-machine paths in code, tests, docs, or committed config. Use env vars, `PATH`, or fake test paths from temporary directories.

- [ ] **Step 4: Run targeted tests**

Run:

```bash
pytest tests/unit/test_grok_cli_executor.py tests/unit/test_claude_code_executor.py -q
```

Expected: pass.

### Task 4: Make Repo Path Optional by Auto-Detecting Git Root

**Files:**

- Modify: `agpair/targets.py`
- Modify: `agpair/cli/task.py`
- Add or update: `tests/integration/test_task_start_and_status.py`

- [ ] **Step 1: Write test for omitted `--repo-path` and `--target`**

Use a temporary git repo. Run `agpair task start` from inside that repo without `--repo-path` and assert stored `repo_path` is the git toplevel.

- [ ] **Step 2: Implement git root detection**

Add helper in `targets.py`:

```python
def detect_git_root(cwd: str | None = None) -> str | None:
    ...
```

Use:

```bash
git rev-parse --show-toplevel
```

Return `None` outside a git repo.

- [ ] **Step 3: Update `resolve_repo_path`**

Behavior:

```text
--repo-path + --target -> error
--target -> target repo
--repo-path -> explicit repo
neither -> detected git root
outside git repo -> error
```

- [ ] **Step 4: Keep target optional**

Do not require `agpair target list` before dispatch.

- [ ] **Step 5: Run tests**

Run:

```bash
pytest tests/integration/test_task_start_and_status.py -q
```

Expected: pass.

### Task 5: Add Authorization Profile Persistence

**Files:**

- Modify: `agpair/models.py`
- Modify: `agpair/storage/schema.sql`
- Modify: `agpair/storage/db.py`
- Modify: `agpair/storage/tasks.py`
- Add: `tests/unit/test_authorization_profiles.py`

- [ ] **Step 1: Add model fields**

Add to `TaskRecord`:

```python
authorization_profile: str = "local_mutating"
authorization_summary: str | None = None
```

- [ ] **Step 2: Add schema columns**

Add to `tasks`:

```sql
authorization_profile TEXT NOT NULL DEFAULT 'local_mutating',
authorization_summary TEXT
```

- [ ] **Step 3: Add generic executor session compatibility**

Prefer adding a generic model/accessor field:

```python
executor_session_id: str | None = None
```

If renaming the SQLite column from `antigravity_session_id` is too risky in V1, keep the physical column and map it through repository/model helpers so new status, watch, doctor, and docs say `executor_session_id`. New user-facing output must not present non-Antigravity CLI sessions as Antigravity sessions.

- [ ] **Step 4: Add migrations**

In `_migrate_schema`, add both columns when missing.

- [ ] **Step 5: Update repository create/read**

`TaskRepository.create_task(...)` should accept `authorization_profile` and `authorization_summary`.

- [ ] **Step 6: Validate profiles**

Create validation helper that accepts only:

```text
local_readonly
local_mutating
local_test_heavy
external_network
```

- [ ] **Step 7: Run unit tests**

Run:

```bash
pytest tests/unit/test_authorization_profiles.py -q
```

Expected: pass.

### Task 6: Inject Authorization and Receipt Contracts Into Local CLI Prompts

**Files:**

- Modify: `agpair/executors/local_cli.py`
- Add or update: `tests/unit/test_local_cli_executor.py`

- [ ] **Step 1: Write prompt contract tests**

Assert local CLI prompt includes:

```text
Task ID
authorization profile
allowed actions
denied actions
structured receipt JSON requirements
commit message must include task id
```

- [ ] **Step 2: Extend `_body_with_task_contract`**

Add authorization profile and receipt schema. Keep the existing task-id commit contract.

- [ ] **Step 3: Thread authorization through `dispatch`**

If changing `ExecutorAdapter.dispatch` signature is too broad, pass authorization context by adding it to task body before calling `dispatch` in `task.py`. Prefer the smallest change that keeps tests clear.

- [ ] **Step 4: Run tests**

Run:

```bash
pytest tests/unit/test_local_cli_executor.py -q
```

Expected: pass.

### Task 7: Update `task start` Executor Selection

**Files:**

- Modify: `agpair/cli/task.py`
- Modify: `agpair/cli/wait.py`
- Modify: `agpair/daemon/loop.py`
- Modify: `agpair/mcp_server.py`
- Modify: `agpair/targets.py`
- Modify: `agpair/executors/codex.py`
- Modify: `tests/integration/test_cli_help.py`
- Modify: `tests/integration/test_task_start_and_status.py`

- [ ] **Step 1: Update help text**

Allowed new task executors:

```text
antigravity-cli, grok-cli, claude-code, codex
```

- [ ] **Step 2: Update `_configured_default_executor`**

`AGPAIR_DEFAULT_EXECUTOR` must validate against the new ids.

- [ ] **Step 3: Update target default validation**

`TargetManager._normalize_default_executor` must accept new ids and reject old Gemini ids.

- [ ] **Step 4: Update executor instantiation**

Replace current `if selected_executor == ...` chain with registry lookup.

- [ ] **Step 5: Update secondary entry points**

Update MCP task creation, daemon dispatch/poll, wait logic, and Codex executor storage so they use the same registry and canonical ids:

```text
new ids accepted: antigravity-cli, grok-cli, claude-code, codex
legacy ids readable: antigravity, codex_cli, gemini_cli
new Gemini starts rejected everywhere, including MCP
local CLI wait uses executor_session_id and ready_for_review
daemon messages no longer default to Antigravity wording for non-Antigravity executors
```

- [ ] **Step 6: Default to `antigravity-cli`**

If no explicit/default executor exists, store `antigravity-cli`, not `None`.

- [ ] **Step 7: Run integration tests**

Run:

```bash
pytest tests/integration/test_cli_help.py tests/integration/test_task_start_and_status.py tests/unit/test_mcp_server.py tests/integration/test_task_wait.py tests/integration/test_task_wait_inline_poll.py tests/integration/test_daemon_codex_lifecycle.py tests/unit/test_auto_advance.py -q
```

Expected: pass.

### Task 8: Implement `retry --from-block`

**Files:**

- Modify: `agpair/cli/task.py`
- Modify: `agpair/storage/tasks.py`
- Add: `tests/integration/test_task_retry_from_block.py`

- [ ] **Step 1: Write tests for blocked retry**

Scenarios:

```text
blocked approval_required + --from-block -> creates new attempt with generated retry body
blocked non-approval + --from-block -> preserves blocker type in generated body
ready_for_review or legacy committed + --from-block -> exits with error
legacy gemini_cli + --from-block without --executor -> exits with helpful error
--executor claude-code override -> stores claude-code for new attempt
approval_required authorization_delta -> copied into generated retry body
```

- [ ] **Step 2: Add CLI flags**

Add:

```text
--from-block
--authorization-profile
--executor
```

to `task retry`.

- [ ] **Step 3: Build retry context bundle**

Create helper in `task.py` or a new small module:

```text
original brief
previous blocked reason
terminal receipt
journal tail
git status
git diff
recent commits
new authorization profile
previous authorization_delta
requested actions and risk assessment
```

- [ ] **Step 4: Update retry dispatch**

For supported local CLI executors, dispatch generated body.

For old bridge/nonlocal paths, do not continue old bridge behavior for new executor ids.

- [ ] **Step 5: Run tests**

Run:

```bash
pytest tests/integration/test_task_retry_from_block.py -q
```

Expected: pass.

### Task 9: Preserve Historical Gemini Readability While Removing New Gemini Routing

**Files:**

- Modify: `agpair/cli/task.py`
- Modify: `agpair/executors/__init__.py`
- Modify: `tests/integration/test_task_start_and_status.py`
- Modify or replace: `tests/integration/test_daemon_gemini_lifecycle.py`
- Modify: `tests/unit/test_mcp_server.py`
- Add or update: `tests/unit/test_executor_routing.py`

- [ ] **Step 1: Status for old records**

If `executor_backend` is `gemini_cli`, `task status --json` should show:

```json
{
  "active_executor_backend": "gemini_cli",
  "legacy_executor": true,
  "retry_supported": false
}
```

Exact field names may vary, but the status must make legacy/unavailable clear.

- [ ] **Step 2: Reject new starts**

`agpair task start --executor gemini` and `--executor gemini_cli` must fail.

- [ ] **Step 3: Reject implicit Gemini defaults**

Targets or `AGPAIR_DEFAULT_EXECUTOR=gemini` must fail with a migration message.

- [ ] **Step 4: Preserve old daemon/read behavior without new Gemini starts**

Update old Gemini daemon lifecycle tests so they no longer dispatch new `--executor gemini` tasks. They should cover only one of these:

```text
legacy gemini_cli row can be inspected
legacy gemini_cli session cleanup does not crash
new Gemini start/retry/MCP dispatch is rejected
```

- [ ] **Step 5: Run tests**

Run:

```bash
pytest tests/unit/test_executor_routing.py tests/integration/test_task_start_and_status.py tests/integration/test_daemon_gemini_lifecycle.py tests/unit/test_mcp_server.py -q
```

Expected: pass.

### Task 10: Add Watch, Receipt Validation, and Executor Health Guardrails

**Files:**

- Create: `agpair/executors/health.py`
- Create: `agpair/watch.py`
- Modify: `agpair/cli/task.py`
- Modify: `agpair/terminal_receipts.py`
- Modify: `agpair/executors/local_cli.py`
- Add: `tests/unit/test_executor_health.py`
- Add: `tests/unit/test_receipt_validation.py`
- Add: `tests/integration/test_task_watch_events.py`
- Add: `tests/integration/test_fake_executors.py`

- [ ] **Step 1: Write executor health tests**

Create `tests/unit/test_executor_health.py`:

```python
from agpair.executors.health import ExecutorHealth, choose_healthy_executor, executor_is_eligible


def test_available_executor_is_eligible():
    health = ExecutorHealth(executor_id="antigravity-cli", available=True)
    assert executor_is_eligible(health)


def test_malformed_receipts_make_executor_ineligible():
    health = ExecutorHealth(
        executor_id="antigravity-cli",
        available=True,
        malformed_receipt_count=3,
    )
    assert not executor_is_eligible(health)


def test_explicit_unavailable_executor_fails_without_silent_fallback():
    health = {
        "antigravity-cli": ExecutorHealth(executor_id="antigravity-cli", available=False),
        "grok-cli": ExecutorHealth(executor_id="grok-cli", available=True),
    }
    chosen = choose_healthy_executor(
        ["antigravity-cli", "grok-cli"],
        health,
        explicit_executor="antigravity-cli",
    )
    assert chosen is None


def test_implicit_routing_uses_next_healthy_external_executor():
    health = {
        "antigravity-cli": ExecutorHealth(executor_id="antigravity-cli", available=False),
        "grok-cli": ExecutorHealth(executor_id="grok-cli", available=True),
        "codex": ExecutorHealth(executor_id="codex", available=True),
    }
    assert choose_healthy_executor(["antigravity-cli", "grok-cli", "codex"], health) == "grok-cli"
```

Run:

```bash
pytest tests/unit/test_executor_health.py -q
```

Expected: fails because `agpair.executors.health` does not exist.

- [ ] **Step 2: Implement lightweight executor health**

Create `agpair/executors/health.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Mapping, Sequence


@dataclass(frozen=True)
class ExecutorHealth:
    executor_id: str
    available: bool
    binary_path: str | None = None
    last_checked_at: datetime | None = None
    last_success_at: datetime | None = None
    recent_failure_count: int = 0
    consecutive_stuck_count: int = 0
    malformed_receipt_count: int = 0
    last_error_excerpt: str | None = None


def executor_is_eligible(health: ExecutorHealth) -> bool:
    if not health.available:
        return False
    if health.malformed_receipt_count >= 3:
        return False
    if health.consecutive_stuck_count >= 2:
        return False
    if health.recent_failure_count >= 5:
        return False
    return True


def choose_healthy_executor(
    candidates: Sequence[str],
    health_by_executor: Mapping[str, ExecutorHealth],
    *,
    explicit_executor: str | None = None,
) -> str | None:
    if explicit_executor is not None:
        health = health_by_executor.get(explicit_executor)
        return explicit_executor if health and executor_is_eligible(health) else None

    for executor_id in candidates:
        health = health_by_executor.get(executor_id)
        if health and executor_is_eligible(health):
            return executor_id
    return None
```

Run:

```bash
pytest tests/unit/test_executor_health.py -q
```

Expected: pass.

- [ ] **Step 3: Write receipt validation tests**

Create `tests/unit/test_receipt_validation.py`:

```python
from agpair.terminal_receipts import validate_terminal_receipt_payload


def test_ready_for_review_requires_machine_checkable_evidence():
    payload = {
        "changed_files": ["agpair/cli/task.py"],
        "validation": [{"command": "pytest tests/unit -q", "exit_code": 0, "log_path": "/tmp/pytest.log"}],
        "scope_violations": [],
        "raw_log_path": "/tmp/stdout.log",
        "receipt_path": ".agpair/tasks/TASK-123/attempt-1/receipt.json",
        "claimed_state": "ready_for_review",
    }
    assert validate_terminal_receipt_payload("COMMITTED", payload).ok


def test_commit_ref_is_optional_for_ready_for_review():
    payload = {
        "changed_files": ["agpair/cli/task.py"],
        "validation": [{"command": "pytest tests/unit -q", "exit_code": 0, "log_path": "/tmp/pytest.log"}],
        "scope_violations": [],
        "raw_log_path": "/tmp/stdout.log",
        "receipt_path": ".agpair/tasks/TASK-123/attempt-1/receipt.json",
        "claimed_state": "ready_for_review",
    }
    result = validate_terminal_receipt_payload("COMMITTED", payload)
    assert result.ok
    assert "commit_ref" not in result.required_missing


def test_malformed_success_receipt_is_rejected():
    payload = {
        "summary": "Done",
        "confidence": "high",
        "claimed_state": "ready_for_review",
    }
    result = validate_terminal_receipt_payload("COMMITTED", payload)
    assert not result.ok
    assert "changed_files" in result.required_missing
    assert "raw_log_path" in result.required_missing


def test_approval_required_requires_authorization_delta():
    payload = {
        "blocker_type": "approval_required",
        "recoverable": True,
        "suggested_action": "retry_with_expanded_authorization",
        "authorization_profile": "local_readonly",
        "requested_authorization_profile": "local_mutating",
        "requested_actions": ["edit files"],
        "authorization_delta": {"allow_file_edits": True},
        "request_reason": "Readonly profile cannot edit files.",
        "risk_assessment": "Repo-local edits only.",
        "safe_to_retry": True,
        "raw_log_path": "/tmp/stderr.log",
    }
    assert validate_terminal_receipt_payload("BLOCKED", payload).ok
```

Run:

```bash
pytest tests/unit/test_receipt_validation.py -q
```

Expected: fails because `validate_terminal_receipt_payload` does not exist.

- [ ] **Step 4: Implement terminal receipt validation**

Modify `agpair/terminal_receipts.py` with a small result type and validator:

```python
from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class ReceiptValidationResult:
    ok: bool
    required_missing: tuple[str, ...] = ()


def validate_terminal_receipt_payload(kind: str, payload: Mapping[str, Any]) -> ReceiptValidationResult:
    if kind == "BLOCKED" and payload.get("blocker_type") == "approval_required":
        required = (
            "requested_authorization_profile",
            "requested_actions",
            "authorization_delta",
            "request_reason",
            "risk_assessment",
            "safe_to_retry",
            "raw_log_path",
        )
    elif kind in {"COMMITTED", "EVIDENCE_PACK"} or payload.get("claimed_state") == "ready_for_review":
        required = (
            "changed_files",
            "scope_violations",
            "raw_log_path",
            "receipt_path",
        )
        has_validation = bool(payload.get("validation")) or bool(payload.get("validation_not_run"))
        missing = tuple(field for field in required if field not in payload)
        if not has_validation:
            missing = (*missing, "validation")
        return ReceiptValidationResult(ok=not missing, required_missing=missing)
    else:
        required = ()

    missing = tuple(field for field in required if field not in payload)
    return ReceiptValidationResult(ok=not missing, required_missing=missing)
```

Run:

```bash
pytest tests/unit/test_receipt_validation.py -q
```

Expected: pass.

- [ ] **Step 5: Write low-noise watch tests**

Create `tests/integration/test_task_watch_events.py`:

```python
from agpair.watch import WatchEvent, should_emit_watch_event


def test_watch_emits_state_changes():
    previous = WatchEvent(task_id="TASK-123", state="acked", cursor="1")
    current = WatchEvent(task_id="TASK-123", state="ready_for_review", cursor="2")
    assert should_emit_watch_event(previous, current)


def test_watch_suppresses_unchanged_heartbeat():
    previous = WatchEvent(task_id="TASK-123", state="acked", cursor="1", heartbeat="same")
    current = WatchEvent(task_id="TASK-123", state="acked", cursor="1", heartbeat="same")
    assert not should_emit_watch_event(previous, current)


def test_watch_event_references_raw_log_path_without_streaming_log_body():
    event = WatchEvent(
        task_id="TASK-123",
        state="ready_for_review",
        cursor="attempt-1:receipt:3",
        raw_log_path=".agpair/tasks/TASK-123/attempt-1/stdout.log",
        summary="Terminal receipt available.",
    )
    payload = event.to_json_dict()
    assert payload["raw_log_path"].endswith("stdout.log")
    assert "log_body" not in payload
```

Run:

```bash
pytest tests/integration/test_task_watch_events.py -q
```

Expected: fails because `agpair.watch` does not exist.

- [ ] **Step 6: Implement watch event helpers**

Create `agpair/watch.py`:

```python
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WatchEvent:
    task_id: str
    state: str
    cursor: str
    heartbeat: str | None = None
    summary: str | None = None
    receipt_path: str | None = None
    raw_log_path: str | None = None
    event: str = "state"

    def to_json_dict(self) -> dict[str, str]:
        payload = {
            "schema_version": "1",
            "event": self.event,
            "task_id": self.task_id,
            "state": self.state,
            "cursor": self.cursor,
        }
        for key in ("heartbeat", "summary", "receipt_path", "raw_log_path"):
            value = getattr(self, key)
            if value is not None:
                payload[key] = value
        return payload


def should_emit_watch_event(previous: WatchEvent | None, current: WatchEvent) -> bool:
    if previous is None:
        return True
    return (
        previous.state != current.state
        or previous.cursor != current.cursor
        or previous.heartbeat != current.heartbeat
        or previous.receipt_path != current.receipt_path
        or previous.raw_log_path != current.raw_log_path
    )
```

Wire `agpair/cli/task.py` so `task start --wait` and `task watch --json` use this event shape. The first implementation may build `WatchEvent` from existing task status/journal rows; do not add a second polling mechanism if one already exists.

Run:

```bash
pytest tests/integration/test_task_watch_events.py -q
```

Expected: pass.

- [ ] **Step 7: Write fake executor matrix tests**

Create `tests/integration/test_fake_executors.py` with fixture executors that emit controlled receipts. The fixture can be a temporary executable script registered through an `AGPAIR_FAKE_EXECUTOR` path or through the test registry hook used by existing executor tests. It must support these modes:

```text
success
approval_required
malformed_json
timeout
validation_failure
scope_violation
```

Test cases:

```python
def test_fake_executor_success_reaches_ready_for_review(runner, fake_executor):
    fake_executor.mode = "success"
    result = runner.invoke(app, ["task", "start", "--executor", fake_executor.name, "--body", "touch a test file"])
    assert result.exit_code == 0
    status = runner.invoke(app, ["task", "status", "--json"])
    assert '"ready_for_review"' in status.stdout


def test_fake_executor_approval_required_can_retry_from_block(runner, fake_executor):
    fake_executor.mode = "approval_required"
    result = runner.invoke(app, ["task", "start", "--executor", fake_executor.name, "--authorization-profile", "local_readonly", "--body", "edit file"])
    assert result.exit_code == 0
    assert '"approval_required"' in result.stdout


def test_fake_executor_malformed_receipt_does_not_reach_ready_for_review(runner, fake_executor):
    fake_executor.mode = "malformed_json"
    result = runner.invoke(app, ["task", "start", "--executor", fake_executor.name, "--body", "do work"])
    assert result.exit_code != 0 or '"ready_for_review"' not in result.stdout


def test_fake_executor_scope_violation_is_not_silently_accepted(runner, fake_executor):
    fake_executor.mode = "scope_violation"
    result = runner.invoke(app, ["task", "start", "--executor", fake_executor.name, "--body", "edit assigned file only"])
    assert result.exit_code != 0 or '"scope_violations": []' not in result.stdout
```

Run:

```bash
pytest tests/integration/test_fake_executors.py -q
```

Expected: fail until fake executor fixture and CLI wiring are implemented.

- [ ] **Step 8: Preserve raw output and reject malformed receipts in local CLI executor**

Modify `agpair/executors/local_cli.py` so every executor attempt writes raw stdout/stderr to stable paths such as:

```text
.agpair/tasks/TASK-123/attempt-1/stdout.log
.agpair/tasks/TASK-123/attempt-1/stderr.log
```

Before marking success, validate the terminal receipt with `validate_terminal_receipt_payload`. If validation fails:

```text
state = blocked(validation_failure)
raw_log_path = <stdout/stderr path>
last_error_excerpt = <receipt validation error>
```

Do not trust a prose summary that says work is done.

- [ ] **Step 9: Run guardrail tests**

Run:

```bash
pytest tests/unit/test_executor_health.py tests/unit/test_receipt_validation.py tests/integration/test_task_watch_events.py tests/integration/test_fake_executors.py -q
```

Expected: pass.

### Task 11: Add Codex Hooks and OMX External-First Adapter

**Files:**

- Create: `agpair/cli/codex.py`
- Modify: `agpair/cli/app.py`
- Modify: `skills/Codex/SKILL.md`
- Modify: `docs/usage.md`
- Modify: `docs/usage.zh-CN.md`
- Modify: `docs/codex-controller-research.zh-CN.md`
- Add: `tests/integration/test_codex_cli.py`

- [ ] **Step 1: Write Codex config tests**

Add tests that mirror the existing Claude config shape but use Codex hook names and Codex config locations.

Required assertions:

```python
def test_codex_config_emits_userprompt_and_stop_hooks(runner):
    result = runner.invoke(app, ["codex", "config"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"] == "agpair codex hook user-prompt-submit"
    assert payload["hooks"]["Stop"][0]["hooks"][0]["command"] == "agpair codex hook stop"


def test_codex_config_install_preserves_foreign_hooks(tmp_path, monkeypatch):
    settings_path = tmp_path / ".codex" / "hooks.json"
    settings_path.parent.mkdir()
    settings_path.write_text(json.dumps({
        "hooks": {
            "Stop": [{"hooks": [{"type": "command", "command": "/tmp/foreign-stop.sh"}]}]
        }
    }))
    result = runner.invoke(app, ["codex", "config", "--install", "--repo-path", str(tmp_path)])
    assert result.exit_code == 0
    updated = json.loads(settings_path.read_text())
    stop_commands = [
        hook["command"]
        for entry in updated["hooks"]["Stop"]
        for hook in entry["hooks"]
    ]
    assert "/tmp/foreign-stop.sh" in stop_commands
    assert "agpair codex hook stop" in stop_commands


def test_codex_hooks_fail_open_when_state_is_unreadable(runner, monkeypatch):
    monkeypatch.setenv("AGPAIR_STATE_DIR", "/path/that/does/not/exist")
    result = runner.invoke(app, ["codex", "hook", "stop"], input="{}")
    assert result.exit_code == 0
    assert result.stdout.strip() == ""


def test_codex_stop_does_not_block_for_plain_acked_state(runner, agpair_task_factory):
    agpair_task_factory(state="acked", watch_attached=True)
    result = runner.invoke(app, ["codex", "hook", "stop"], input="{}")
    assert result.exit_code == 0
    assert result.stdout.strip() == ""
```

Run:

```bash
pytest tests/integration/test_codex_cli.py -q
```

Expected: fail because `agpair codex` does not exist yet.

- [ ] **Step 2: Implement `agpair/cli/codex.py` config management**

Implement commands:

```bash
agpair codex config
agpair codex config --install --scope project --repo-path <path>
agpair codex config --uninstall --scope project --repo-path <path>
agpair codex hook user-prompt-submit
agpair codex hook stop
agpair codex hook subagent-start
```

Config payload:

```json
{
  "hooks": {
    "UserPromptSubmit": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "agpair codex hook user-prompt-submit"
          }
        ]
      }
    ],
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "agpair codex hook stop",
            "timeout": 30
          }
        ]
      }
    ],
    "SubagentStart": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "agpair codex hook subagent-start"
          }
        ]
      }
    ]
  }
}
```

Do not overwrite unrelated user or OMX hooks. Merge AGPair-managed entries by command identity.

- [ ] **Step 3: Register the Codex subcommand**

Modify `agpair/cli/app.py`:

```python
from agpair.cli.codex import app as codex_app

app.add_typer(codex_app, name="codex")
```

Run:

```bash
agpair codex --help
agpair codex config
```

Expected: help prints and config emits JSON.

- [ ] **Step 4: Implement `UserPromptSubmit` external-first hook**

Hook behavior:

```text
if no repo path can be resolved -> emit nothing
if agpair state cannot be initialized -> emit nothing
if AGPair has no usable external executor configured -> emit nothing or a warning only in debug output
otherwise -> emit Codex JSON with UserPromptSubmit additionalContext
```

Output shape:

```json
{
  "hookSpecificOutput": {
    "hookEventName": "UserPromptSubmit",
    "additionalContext": "AGPair external-first routing is available in this repository. For non-trivial implementation, refactor, test-fix, research, or review work, prefer dispatching through AGPair external CLI executors before using Codex native subagents. Codex main remains the controller and verifier. Use native subagents only when AGPair is unavailable, an external executor is unsuitable, or external results are not good enough."
  }
}
```

Do not block the prompt.

- [ ] **Step 5: Implement state-aware `Stop` hook**

The Stop hook must inspect the most relevant active AGPair task for the repo and return one of these:

```json
{
  "decision": "block",
  "reason": "AGPair task TASK-123 is active and no wait/watch has been attached. Run `agpair task watch TASK-123 --json` and continue from the terminal state."
}
```

```json
{
  "decision": "block",
  "reason": "AGPair task TASK-123 reached ready_for_review. Inspect git status, optional commit/diff, receipt, raw log paths, and required evidence before finalizing."
}
```

```json
{
  "decision": "block",
  "reason": "AGPair task TASK-123 is blocked with blocker_type=approval_required. Do not keep polling. Decide whether to retry with `agpair task retry TASK-123 --from-block --authorization-profile ...` or report the blocker."
}
```

Do not continue purely because a task is `acked`. If there is no actionable AGPair state, emit nothing.

- [ ] **Step 6: Implement `SubagentStart` advisory only**

If Codex starts a native subagent while AGPair is healthy, emit additional context, not a denial:

```text
AGPair external-first routing is active in the parent repo. If this native subagent was started for implementation work only because external agents were unavailable or insufficient, stay within the assigned fallback scope and report why external execution was bypassed.
```

This is a soft guard. Do not rely on it to enforce external-first routing.

- [ ] **Step 7: Update Codex skill**

`skills/Codex/SKILL.md` must say:

```text
Default: external AGPair executor first for non-trivial work.
Normal single task: `agpair task start ...` with default wait.
Parallel or intentionally async: `--no-wait`, then `agpair task watch <TASK_ID> --json`.
Do not use repeated Codex prompts as a polling loop.
Use Codex App thread automation only for very long tasks that should wake this same thread later.
Codex native subagents are fallback/review resources, not the default worker pool.
```

Remove stale Gemini recommendations.

- [ ] **Step 8: Update Codex controller research doc**

Refresh `docs/codex-controller-research.zh-CN.md`:

```text
The old v0.121 conclusion is stale.
Current Codex has App thread automations and lifecycle hooks.
Codex still does not have a direct Claude Code Monitor equivalent.
AGPair should provide the low-token wait/watch layer.
OMX should be adapted through AGPair-owned Codex hooks/skills, not by modifying OMX source in V1.
```

- [ ] **Step 9: Run Codex integration tests**

Run:

```bash
pytest tests/integration/test_codex_cli.py tests/integration/test_cli_help.py -q
```

Expected: pass.

### Task 12: Upgrade Claude Code Hooks, Skills, and Config Adapter

**Files:**

- Modify: `agpair/cli/claude.py`
- Modify: `skills/Claude/SKILL.md`
- Modify: `docs/claude-code-integration.zh-CN.md`
- Modify: `docs/usage.md`
- Modify: `docs/usage.zh-CN.md`
- Update: `tests/integration/test_claude_cli.py`

- [ ] **Step 1: Write Claude config tests for refreshed hooks**

Update `tests/integration/test_claude_cli.py` so `agpair claude config` includes:

```python
def test_claude_config_emits_external_first_hooks():
    result = CliRunner().invoke(app, ["claude", "config"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    hooks = payload["hooks"]
    assert hooks["UserPromptSubmit"][0]["hooks"][0]["command"] == "agpair claude hook user-prompt-submit"
    assert hooks["Stop"][0]["hooks"][0]["command"] == "agpair claude hook stop"
    assert hooks["SubagentStart"][0]["hooks"][0]["command"] == "agpair claude hook subagent-start"
    assert hooks["SubagentStop"][0]["hooks"][0]["command"] == "agpair claude hook subagent-stop"
    assert hooks["TaskCreated"][0]["hooks"][0]["command"] == "agpair claude hook task-created"
    assert hooks["TaskCompleted"][0]["hooks"][0]["command"] == "agpair claude hook task-completed"
```

Also keep existing assertions for:

```text
statusLine -> agpair claude statusline
SessionStart -> agpair claude hook session-start
PreCompact -> agpair claude hook precompact
foreign hooks/settings preserved
uninstall removes only AGPair-managed entries
```

Run:

```bash
pytest tests/integration/test_claude_cli.py -q
```

Expected: fail until the new hooks are implemented.

- [ ] **Step 2: Add fail-open hook tests**

Add tests:

```python
def test_claude_hooks_fail_open_when_state_is_unreadable(runner, monkeypatch):
    monkeypatch.setenv("AGPAIR_STATE_DIR", "/path/that/does/not/exist")
    for hook_name in ("user-prompt-submit", "stop", "subagent-start", "subagent-stop", "task-created", "task-completed"):
        result = runner.invoke(app, ["claude", "hook", hook_name], input="{}")
        assert result.exit_code == 0
        assert result.stdout.strip() == ""
```

Run:

```bash
pytest tests/integration/test_claude_cli.py -q
```

Expected: fail until hooks catch unreadable state and return quietly.

- [ ] **Step 3: Implement config payload changes in `agpair/cli/claude.py`**

Update `_managed_config_payload()` to include command hooks:

```json
{
  "statusLine": {
    "type": "command",
    "command": "agpair claude statusline",
    "refreshInterval": 5
  },
  "hooks": {
    "SessionStart": [{"hooks": [{"type": "command", "command": "agpair claude hook session-start"}]}],
    "UserPromptSubmit": [{"hooks": [{"type": "command", "command": "agpair claude hook user-prompt-submit"}]}],
    "Stop": [{"hooks": [{"type": "command", "command": "agpair claude hook stop"}]}],
    "PreCompact": [{"hooks": [{"type": "command", "command": "agpair claude hook precompact"}]}],
    "SubagentStart": [{"hooks": [{"type": "command", "command": "agpair claude hook subagent-start"}]}],
    "SubagentStop": [{"hooks": [{"type": "command", "command": "agpair claude hook subagent-stop"}]}],
    "TaskCreated": [{"hooks": [{"type": "command", "command": "agpair claude hook task-created"}]}],
    "TaskCompleted": [{"hooks": [{"type": "command", "command": "agpair claude hook task-completed"}]}]
  }
}
```

Merge by AGPair command identity. Preserve unrelated hooks and settings. Do not overwrite non-AGPair `statusLine` unless `--force` is explicit.

- [ ] **Step 4: Implement `UserPromptSubmit` hook**

Behavior:

```text
if no repo path can be resolved -> emit nothing
if AGPair state cannot be initialized -> emit nothing
if no healthy AGPair external executor exists -> emit nothing or debug-only warning
otherwise -> emit additionalContext telling Claude Code to prefer AGPair external executors for delegatable non-trivial work
```

Output:

```json
{
  "hookSpecificOutput": {
    "hookEventName": "UserPromptSubmit",
    "additionalContext": "AGPair external-first routing is available in this repository. For non-trivial implementation, refactor, test-fix, research, or review work, prefer AGPair external CLI executors before Claude Code native subagents/background tasks. Claude Code remains controller and verifier. Use native subagents only when AGPair is unavailable, unsuitable, or not good enough."
  }
}
```

- [ ] **Step 5: Implement state-aware `Stop` hook**

The Stop hook must block only actionable states:

```text
active AGPair task with no wait/watch attached -> block with watch command
ready_for_review -> block and ask Claude to inspect receipt/diff/raw logs
blocked(approval_required) -> block and ask Claude to decide retry/report
stale/watchdog event -> block and ask Claude to mark stuck/switch/report
plain acked with active watch attached -> emit nothing
no AGPair state -> emit nothing
```

If Claude Code provides `background_tasks` or `session_crons`, use them only to avoid false finalization; AGPair state remains authoritative for AGPair tasks.

- [ ] **Step 6: Implement subagent and task observability hooks**

Implement:

```bash
agpair claude hook subagent-start
agpair claude hook subagent-stop
agpair claude hook task-created
agpair claude hook task-completed
```

Rules:

```text
SubagentStart -> advisory only; no hard block
SubagentStop -> record fallback evidence from last_assistant_message when available; no transcript parsing by default
TaskCreated -> observability only in V1; no hard mapping to AGPair task
TaskCompleted -> observability only in V1; no terminal AGPair mutation unless task id is explicitly linked in a future version
```

- [ ] **Step 7: Update Claude skill**

Rewrite `skills/Claude/SKILL.md` around current behavior only:

```text
Default: AGPair external executors first for delegatable non-trivial work.
Normal single task: agpair task start ... and wait.
Parallel/async: --no-wait, then agpair task watch <TASK_ID> --json.
Use Claude Code Monitor/background tasks only to observe AGPair watch output, not as AGPair's state source.
Use Claude Code native subagents only as fallback/review when AGPair is unavailable, unsuitable, or not good enough.
Do not route new work to Gemini.
Treat ready_for_review as needing controller verification.
```

Keep the skill short. Move detailed reasoning to docs.

- [ ] **Step 8: Refresh Claude integration doc from official current docs**

Update `docs/claude-code-integration.zh-CN.md`:

```text
AGPair is Claude Code's external-agent-first durable execution layer.
Claude Code settings scopes: user/project/local/managed.
Claude Code skills are on-demand; keep AGPair procedures in skills, not always-on CLAUDE.md.
Claude Code hooks give deterministic lifecycle control; AGPair uses command hooks.
AGPair does not replace Claude Code background tasks/subagents; it provides durable external task state.
```

Use source links:

```text
https://code.claude.com/docs/en/settings
https://code.claude.com/docs/en/skills
https://code.claude.com/docs/en/hooks
https://code.claude.com/docs/en/changelog
```

- [ ] **Step 9: Run Claude adapter tests**

Run:

```bash
pytest tests/integration/test_claude_cli.py tests/integration/test_cli_help.py -q
```

Expected: pass.

### Task 13: Update Doctor and Documentation

**Files:**

- Modify: `agpair/cli/doctor.py`
- Modify: `README.md`
- Modify: `README.zh-CN.md`
- Modify: `docs/usage.md`
- Modify: `docs/usage.zh-CN.md`
- Modify: `docs/getting-started.en.md`
- Modify: `docs/getting-started-zh.md`
- Modify: `skills/Codex/SKILL.md`
- Modify: `skills/Claude/SKILL.md`
- Modify: `skills/claw.json`
- Modify: `docs/claude-code-integration.zh-CN.md`
- Add or update: `tests/integration/test_doctor.py`

- [ ] **Step 1: Doctor checks**

Doctor should report:

```text
antigravity-cli binary found/missing
grok-cli binary found/missing
claude-code binary found/missing
codex binary found/missing
default executor
authorization profiles supported
executor health summary
recent malformed receipt / stuck counters
Codex hook install status when applicable
Claude Code hook install status when applicable
```

Missing non-default executors should be warnings, not fatal.

- [ ] **Step 2: Update Codex skill**

Rules:

```text
External agents first.
Antigravity means Antigravity CLI.
Gemini is removed.
Codex native subagent only after external unavailable or not good enough.
Codex main verifies all external results.
Use AGPair wait/watch for low-token waiting.
Do not copy raw command output through lossy compression by default.
Treat ready_for_review as "needs Codex verification", not final completion.
```

- [ ] **Step 3: Update Claude skill**

Rules:

```text
External agents first.
Antigravity means Antigravity CLI.
Gemini is removed.
Claude Code native subagents/background tasks only after external unavailable or not good enough.
Claude Code main verifies all external results.
Use AGPair wait/watch for low-token waiting.
Treat ready_for_review as "needs Claude verification", not final completion.
```

- [ ] **Step 4: Update usage docs**

Update `docs/usage.md`, `docs/usage.zh-CN.md`, `docs/getting-started.en.md`, and `docs/getting-started-zh.md`.

Add examples:

```bash
agpair task start --executor antigravity-cli --authorization-profile local_mutating --body "..."
agpair task start --executor antigravity-cli --authorization-profile local_mutating --body "..." --no-wait
agpair task watch TASK-123 --json
agpair task retry TASK-123 --from-block --authorization-profile local_mutating
```

Docs must explicitly state:

```text
watch --json emits state-change events and paths to raw logs; it does not stream full logs.
ready_for_review means external executor claims completion; Codex still verifies diff and evidence.
Claude Code uses the same AGPair state and should also verify diff and evidence.
commit_ref is optional unless the brief/profile requires a commit.
OMX adaptation is AGPair hook/skill based and must fail open when AGPair is unavailable.
```

Remove stale executor strategy tables from `docs/usage.md` and `docs/usage.zh-CN.md`. The docs should no longer say:

```text
Claude parallel / isolated-worktree: codex, then gemini
Codex parallel / isolated-worktree: gemini
```

Replace with:

```text
Default external executor: antigravity-cli
Cheap challenger/backup: grok-cli
Quality escalation: claude-code
Fallback external Codex worker: codex
Native subagents: controller fallback/review only
```

- [ ] **Step 5: Update skill metadata**

Update `skills/claw.json` so its description, tags, and model/tool hints do not present Gemini or the old Antigravity bridge as the current recommended path. The metadata should match the short product position:

```text
external-agent-first task dispatch for Codex and Claude Code
antigravity-cli, grok-cli, claude-code, codex fallback
no Gemini for new work
```

- [ ] **Step 6: Rewrite README positioning**

Update `README.md` and `README.zh-CN.md` so the main path is concise:

```text
AGPair is an external-agent-first control plane for Codex and Claude Code.
Controllers plan and verify.
AGPair dispatches external CLI executors, persists task state, waits cheaply, validates receipts, and supports retry.
```

README should show only current setup:

```bash
agpair task start --executor antigravity-cli --authorization-profile local_mutating --body "..."
agpair task watch TASK-123 --json
agpair task retry TASK-123 --from-block --authorization-profile local_mutating
agpair codex config --install --scope project
agpair claude config --install --scope project
```

Move historical details to `CHANGELOG.md` or keep them out of the main path. Do not explain old Antigravity IDE/Gemini defaults in the main README.

- [ ] **Step 7: Add client configuration docs**

Docs must distinguish:

```text
Repository source files:
- skills/Codex/SKILL.md
- skills/Claude/SKILL.md
- agpair/cli/codex.py
- agpair/cli/claude.py

Local installed copies:
- ~/.codex/skills/agpair/SKILL.md
- ~/.claude/skills/agpair/SKILL.md
- ~/.codex hooks/config files
- ~/.claude/settings.json

Project optional config:
- .claude/settings.json only when sanitized and intentionally shared
- Codex project config only when sanitized and intentionally shared

Never commit:
- ~/.claude/settings.json
- ~/.codex/*
- .claude/settings.local.json
- raw AGPair state/logs
- generated hook debug output
```

- [ ] **Step 8: Add release privacy checklist**

Add a short section to `CONTRIBUTING.md` or `docs/usage.md`:

```bash
git status --short
git diff --check
rg -n "([A]NTHROPIC_API_KEY|[O]PENAI_API_KEY|[X]AI_API_KEY|[A]GPAIR_.*TOKEN|[Aa]uthorization:[[:space:]]+Bearer|sk-[A-Za-z0-9_-]{20,}|xox[baprs]-|gh[pousr]_[A-Za-z0-9]|/(Users|home)/[[:alnum:]_.-]+/|[a]iapi\\.ulucky\\.cn|[s]uper-secret|[c]onfigured-secret)" .
git ls-files --others --exclude-standard
```

Expected:

```text
No real API keys, bearer tokens, OAuth tokens, local absolute user paths, private proxy endpoints, raw logs, session transcripts, or generated local config are staged.
Test fixture strings are allowed only when clearly fake and already covered by tests.
```

- [ ] **Step 9: Run tests**

Run:

```bash
pytest tests/integration/test_doctor.py tests/integration/test_cli_help.py -q
```

Expected: pass.

### Task 14: Final Regression, Local Config Smoke, and Privacy Gate

**Files:**

- No mandatory source changes.
- Update `CHANGELOG.md` only after implementation is complete and verified.

- [ ] **Step 1: Run unit tests**

Run:

```bash
pytest tests/unit -q
```

Expected: pass.

- [ ] **Step 2: Run integration tests**

Run:

```bash
pytest tests/integration -q
```

Expected: pass.

- [ ] **Step 3: Run CLI help smoke**

Run:

```bash
agpair task start --help
agpair task retry --help
agpair doctor --help
agpair codex config
agpair claude config
```

Expected:

- New executor ids appear.
- Gemini does not appear as a new-task executor.
- `--authorization-profile` appears where implemented.
- `--from-block` appears on retry.
- `task watch` or equivalent watch help documents low-noise `--json` behavior.
- Codex config payload includes AGPair-managed UserPromptSubmit/Stop hooks.
- Claude config payload includes AGPair-managed statusLine plus external-first/state-aware hooks.

- [ ] **Step 4: Run one fake/local executor smoke**

Use a controlled fake CLI or a test fixture that writes a receipt. Verify:

```text
start -> acked -> ready_for_review
```

Also verify:

```text
raw stdout/stderr log paths exist
receipt validates
commit_ref may be absent
Codex/controller still inspects diff before final completion
```

- [ ] **Step 5: Run one blocked/retry smoke**

Use a controlled fake CLI that emits `BLOCKED` with `approval_required`. Verify:

```text
start -> blocked
retry --from-block --authorization-profile local_mutating -> acked -> ready_for_review
```

- [ ] **Step 6: Run malformed/stuck fake executor smoke**

Use controlled fake CLIs:

```text
malformed receipt -> blocked(validation_failure) or stuck, never ready_for_review
timeout/stale heartbeat -> stuck or actionable watchdog event
scope violation -> blocked(scope_violation) or ready_for_review with non-empty scope_violations that Codex rejects
```

- [ ] **Step 7: Run Codex hook fail-open smoke**

Run AGPair Codex hooks with unreadable/missing state:

```bash
AGPAIR_STATE_DIR=/path/that/does/not/exist agpair codex hook user-prompt-submit
AGPAIR_STATE_DIR=/path/that/does/not/exist agpair codex hook stop
```

Expected: exit 0, no blocking decision, no traceback.

- [ ] **Step 8: Run Claude hook fail-open smoke**

Run AGPair Claude hooks with unreadable/missing state:

```bash
AGPAIR_STATE_DIR=/path/that/does/not/exist agpair claude hook user-prompt-submit
AGPAIR_STATE_DIR=/path/that/does/not/exist agpair claude hook stop
AGPAIR_STATE_DIR=/path/that/does/not/exist agpair claude hook subagent-stop
```

Expected: exit 0, no blocking decision, no traceback.

- [ ] **Step 9: Run local config dry-run smoke**

Run:

```bash
agpair codex config --install --scope project --dry-run
agpair claude config --install --scope project --dry-run
```

Expected:

```text
Diffs show only AGPair-managed entries.
No unrelated user/OMX/Claude/Codex config is removed.
No generated config is written during dry-run.
```

- [ ] **Step 10: Run privacy gate before GitHub submission**

Run:

```bash
git status --short
git diff --check
rg -n "([A]NTHROPIC_API_KEY|[O]PENAI_API_KEY|[X]AI_API_KEY|[A]GPAIR_.*TOKEN|[Aa]uthorization:[[:space:]]+Bearer|sk-[A-Za-z0-9_-]{20,}|xox[baprs]-|gh[pousr]_[A-Za-z0-9]|/(Users|home)/[[:alnum:]_.-]+/|[a]iapi\\.ulucky\\.cn|[s]uper-secret|[c]onfigured-secret)" .
git ls-files --others --exclude-standard
```

Expected:

```text
Only intended source/docs/tests are staged.
No real secrets, private proxy endpoints, local absolute user paths, raw logs, session transcripts, or generated local config are staged.
Untracked internal planning/visual files are either intentionally committed after review or left untracked.
```

## 16. Completion Criteria

The change is complete only when all are true:

- `agpair task start` works without a target when run inside a git repo.
- `target` remains usable but optional.
- New-task executor ids are `antigravity-cli`, `grok-cli`, `claude-code`, and `codex`.
- Gemini is rejected for new start/retry but historical records are inspectable.
- Antigravity new default uses Antigravity CLI, not IDE bridge.
- Authorization profile is visible in task status.
- Successful external attempts surface as `ready_for_review`; `commit_ref` is optional unless explicitly required.
- Structured `BLOCKED approval_required` receipts produce useful failure context.
- `BLOCKED approval_required` receipts include authorization delta, requested actions, request reason, risk assessment, safe retry flag, and raw log path.
- `task retry --from-block --authorization-profile ...` builds a rich retry attempt.
- `task watch --json` is low-noise: state-change events only, with receipt/raw-log paths instead of repeated full logs.
- Raw executor stdout/stderr is preserved by path and is not rewritten through default RTK-style compression.
- Malformed receipts and executor prose-only success claims do not produce `ready_for_review`.
- Thin executor health gates can skip unhealthy implicit/default external executors and fall back only after external options are unavailable or not good enough.
- Explicit unavailable executor selection fails clearly instead of silently routing elsewhere.
- Delegation eligibility is documented and enforced enough that tiny, interactive, destructive, credentialed, or unverifiable tasks are not blindly dispatched.
- Codex-facing docs say external agents first and native subagents only as fallback.
- Claude-facing docs say external agents first and native subagents/background tasks only as fallback or observation surfaces.
- `agpair codex config` can install/uninstall Codex hooks without overwriting unrelated OMX/user hooks.
- Codex `UserPromptSubmit` hook injects external-first context only when AGPair is available and repo-local.
- Codex `Stop` hook continues only for actionable AGPair states; it does not create a repeated model-prompt polling loop.
- Codex hooks fail open when AGPair is missing, unhealthy, outside a repo, or state is unreadable.
- `agpair claude config` can install/uninstall Claude Code hooks/statusline without overwriting unrelated user/project hooks or settings.
- Claude Code `UserPromptSubmit` hook injects external-first context only when AGPair is healthy and repo-local.
- Claude Code `Stop` hook continues only for actionable AGPair states and does not create a model-prompt polling loop.
- Claude Code `SubagentStart` is advisory only; `SubagentStop`, `TaskCreated`, and `TaskCompleted` are observability-only in V1.
- Claude Code hooks fail open when AGPair is missing, unhealthy, outside a repo, or state is unreadable.
- OMX source is untouched; OMX behavior remains normal when AGPair hooks do not inject context.
- Documentation distinguishes AGPair low-token wait/watch from Codex App thread automations and from Claude Code Monitor.
- README and usage docs state current AGPair positioning concisely and do not lead with legacy executor history.
- Repository docs use sanitized placeholders (`$REPO`, `~`, `/path/to/repo`) instead of user-local absolute paths.
- GitHub-ready diff passes the privacy gate: no real secrets, private endpoints, raw logs, session transcripts, generated local settings, or accidental user config.
- Fake executor matrix covers success, approval_required retry, malformed receipt, timeout/stuck, validation failure, dirty worktree, and scope violation.
- Unit and integration tests pass.

## 17. Open Questions

- **(implementation discovery):** Exact noninteractive invocation flags for Antigravity CLI.
- **(implementation discovery):** Exact noninteractive invocation flags for Grok CLI.
- **(implementation discovery):** Whether Claude Code CLI should use existing `claude` invocation directly or a safer wrapper profile.
- **(implementation discovery):** Whether existing `antigravity` bridge code should remain behind a legacy command name for compatibility or be removed after tests confirm no live dependency.
- **(implementation discovery):** Whether Codex App heartbeat automation should be created by an AGPair helper command later, or left as a manual App-level workflow in V1.

No user-blocking question remains for V1. The implementation can proceed under the decisions in this document.
