---
name: agpair
description: "Use AGPair from Claude Code to delegate non-trivial coding work to external CLI executors first, then watch, verify, and retry blocked attempts with structured context."
---

# AGPair For Claude Code

Claude Code is the controller and verifier. AGPair is the durable external-agent control plane.

## Default Route

Use AGPair before Claude Code native subagents for non-trivial implementation, refactor, test-fix, research, review, or multi-file work.

Actively outsource low-value, repetitive, time-consuming, or easily verifiable work through AGPair: repo scans, alternative reviews, focused test-fix attempts, multi-file mechanical edits, smoke checks, and implementation slices with clear acceptance criteria.

Prefer executors in this order:

1. `antigravity-cli` for default external implementation work.
2. `grok-cli` for cheap parallel review, research, or alternative implementation attempts.
3. `codex` when an AGPair-managed external Codex CLI worker is useful as a fallback executor.

`codex` is the AGPair-managed external Codex CLI worker for Claude Code controllers. It is the cross-controller fallback lane, not a Claude Code native subagent.

Do not request the AGPair-managed external `claude-code` executor by default; Claude Code already has native subagents and `claude-code` is suppressed for Claude Code controllers unless `--allow-self-executor` is explicitly justified.

Do not route new work to Gemini. Legacy `gemini_cli` tasks may be inspected or cleaned up, but not used for new `task start` or retry dispatch.

Use Claude Code native subagents only when AGPair is unavailable, unsuitable for the task, or an external result is not good enough. Native subagents are fallback, review, or narrow helper lanes, not the default execution lane.

Default executor environments are `managed-natural` for all active external CLI executors: AGPair manages state and evidence, while the external CLI keeps its normal skills, MCP, memory, plugins, and provider config.

AGPair external-first routing applies to controller sessions. AGPair-started executor, probe, smoke, and retry processes suppress AGPair client hooks to avoid recursive delegation, but external workers still inherit their normal CLI capabilities, skills, MCP, plugins, memory, and provider config unless an explicit diagnostic mode says otherwise.

## Dispatch

For one external task, let `task start` wait by default:

```bash
agpair task start \
  --repo-path "$REPO" \
  --executor antigravity-cli \
  --authorization-profile local_mutating \
  --body "$BRIEF"
```

For non-trivial implementation, refactor, or test-fix work, dispatch one bounded mutating slice first unless the task is tiny, sensitive, external executors are unhealthy, or a prior external result was low quality:

```bash
agpair task start \
  --repo-path "$REPO" \
  --controller claude-code \
  --executor antigravity-cli \
  --authorization-profile local_mutating \
  --completion-policy evidence \
  --isolated-worktree \
  --body "$BRIEF"
```

Use a brief with explicit allowed files, forbidden files, required changes, validation command, and exit criteria. The external worker returns `changed_files`, `validation` or `validation_not_run`, `scope_violations`, report text, and raw evidence paths. Claude Code integrates or rejects the result in the main worktree after verification.

For isolated mutating evidence/commit tasks, AGPair defaults to `--dirty-snapshot tracked`: tracked staged/unstaged controller changes are copied into the executor worktree before launch. Ignored and untracked files are not copied; use `--dirty-snapshot off` when the worker should start from committed HEAD only.

For parallel or background work, dispatch asynchronously and attach a low-noise watch:

```bash
agpair task start \
  --repo-path "$REPO" \
  --executor antigravity-cli \
  --authorization-profile local_mutating \
  --body "$BRIEF" \
  --no-wait

agpair task watch TASK-123 --json
```

`watch --json` emits state changes and raw evidence paths. Do not stream full executor logs into the main Claude context unless the terminal receipt or raw path needs inspection.

## Workflows

Use `agpair workflow start` only for high-value multi-part, parallel, adversarial, or long-running work. Workflow manifests are declarative; AGPair rejects arbitrary script fields and creates normal V1.1 child tasks.

Workflow `ready_for_review` means AGPair has an evidence pack for Claude Code verification, not final user-facing success.

## Authorization

Pick the narrowest dispatch-time authorization profile that can finish the task:

- `local_readonly`: inspect-only work.
- `local_mutating`: normal local edits and tests.
- `local_test_heavy`: long or heavy local validation.
- `external_network`: work that needs external network access.

AGPair V1 does not pause a running executor for live approval. If an executor needs more authority, it must return `blocked(approval_required)`.

## Blocked Retry

When a task is blocked for approval, do not keep polling. Retry with structured block context:

```bash
agpair task retry TASK-123 \
  --from-block \
  --authorization-profile local_mutating
```

`--from-block` carries the original brief, blocked reason, terminal receipt, journal tail, git status, diff/commits, and the new authorization profile into a fresh attempt.

If an external attempt fails or is low quality, retry naturally, switch to another external executor, or use Claude Code native subagents as fallback/review.

## Review Gate

Treat `ready_for_review`, `evidence_ready`, and `committed` as review gates, not automatic completion.

Before reporting success:

- inspect `agpair task status TASK-123 --json`;
- inspect changed files, git status, and relevant diff/commit evidence;
- read receipt and raw log paths when the claim is surprising or high-risk;
- run the narrowest meaningful local verification.

Use `protocol_result` to judge AGPair receipt quality and `adoption_result` to judge whether Claude Code can use the result. `adoptable_result=yes` means directly adoptable after normal verification; `partial` means usable with bounded controller review or minor rework; `no` means retry, switch executor, or use native subagents.

After verification, close the loop:

```bash
agpair task accept TASK-123 --adoptable-result yes --controller-rework none
```

If the protocol failed but report/stdout evidence is still useful, record explicit salvage:

```bash
agpair task adopt TASK-123 --from-report --adoptable-result partial --controller-rework minor
```

Claude Code remains accountable for final quality even when AGPair executors did the edits.

## Claude Code Integration

Install or print the managed settings snippet:

```bash
agpair claude config
agpair claude config --install --scope project --repo-path "$REPO" --sync-skill
```

Managed hooks:

- `UserPromptSubmit`: injects external-first routing context.
- `Stop`: blocks only actionable AGPair terminal states such as `ready_for_review` and `approval_required`.
- `SubagentStart`: advisory fallback-scope context.
- `SubagentStop`, `TaskCreated`, `TaskCompleted`: observability-only in V1.
- `SessionStart` and `PreCompact`: lightweight status/compaction guardrails.

Hooks fail open when AGPair state is unavailable. They preserve unrelated Claude Code settings and remove only AGPair-managed entries on uninstall.

Claude Code worker auth mode is `auto`: OAuth/subscription first, then the current Claude provider selected in CC Switch. Probe timeout is not the same as auth failure; check `agpair doctor --fresh` `last_failure_type` for `executor_probe_timeout` or `executor_hook_interference` before changing credentials.
