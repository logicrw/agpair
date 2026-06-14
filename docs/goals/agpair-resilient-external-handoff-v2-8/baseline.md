# AGPair V2.8 Baseline

Created: 2026-06-14

Source plan: `docs/superpowers/plans/2026-06-14-resilient-external-handoff-v2-8.md`

This baseline is intentionally summarized and sanitized. It records enough current-state evidence to prove why V2.8 is needed without copying private task logs, provider config, API keys, OAuth material, CC Switch data, or raw home-directory state.

## Scope Freeze

V2.8 changes only recovery, observability, stdout/report salvage, smoke metrics, Codex/Claude skills, and current behavior docs.

Out of scope:

- new executor launch modes;
- hidden auto-fallback loops;
- new fanout/synthesis semantics beyond reusing the canonical recovery vocabulary;
- hosted routers;
- capability bundles or MCP/skills isolation redesign;
- runtime pause/resume approval;
- database-heavy scoring or reputation tables.

## Current Policy Snapshot

Command:

```bash
agpair policy list --controller codex --json
```

Sanitized result:

- `ok=true`
- selected executor: `grok-cli`
- eligible executors: `grok-cli`, `antigravity-cli`, `claude-code`
- suppressed executors: `codex`
- reason: Codex controller suppresses external `codex` by default
- active executor ids in registry: `grok-cli`, `antigravity-cli`, `claude-code`, `codex`
- all four executors reported `available=true`, `binary_available=true`, `lifecycle_status=active`
- executor environment defaults remain `managed-natural`
- skill and MCP policy remain `inherit`

Command:

```bash
agpair policy list --controller claude --json
```

Sanitized result:

- `ok=true`
- selected executor: `grok-cli`
- eligible executors: `grok-cli`, `antigravity-cli`, `codex`
- suppressed executors: `claude-code`
- reason: Claude controller suppresses external `claude-code` by default
- active executor ids in registry: `grok-cli`, `antigravity-cli`, `claude-code`, `codex`
- all four executors reported `available=true`, `binary_available=true`, `lifecycle_status=active`
- executor environment defaults remain `managed-natural`
- skill and MCP policy remain `inherit`

## Current Smoke Snapshot

Command:

```bash
python scripts/smoke_real_executors.py --help
```

Sanitized result:

- command exited successfully;
- script supports `--repo-path`, `--controller`, `--scenario`, `--executors`, `--all-registered`, `--allow-self-executor`, timeout/no-progress options, dirty snapshot, and `--keep-worktrees`;
- script does not currently expose a `--json` option in help output;
- existing per-row fields include `time_to_first_useful_signal_seconds`, `fallback_suggestion`, and `controller_rework`;
- V2.8 must add or normalize product summary metrics and canonical recovery output without turning the smoke script into a second recovery policy engine.

## Current Compatibility Field Inventory

Command:

```bash
rg -n "\"agent_result\"|\"adoption_result\"|\"protocol_result\"|\"controller_action\"|\"recommended_action\"" agpair tests scripts
```

Sanitized result:

- `agpair/cli/task.py` produces `protocol_result`, `adoption_result`, `agent_result`, and top-level `controller_action`.
- `agpair/cli/wait.py` produces `recommended_action` and still uses lease/no-progress wording that is not yet canonicalized.
- `agpair/watch.py` emits `controller_action` / `agent_result` changes.
- `agpair/workflows/synthesis.py`, `agpair/workflows/evidence.py`, and `agpair/workflows/watch.py` consume or expose `adoption_result`, `agent_result`, `protocol_result`, `panel_result`, and workflow synthesis action fields.
- `scripts/smoke_real_executors.py` consumes status protocol/adoption/agent results and produces smoke-level `controller_action`.
- Tests assert legacy action strings such as `retry_or_switch_executor`, `detach_and_continue`, and workflow `fall_back`.

Compatibility fields to preserve while adding `recovery_decision`:

- `agent_result`
- `adoption_result`
- `protocol_result`
- `controller_action`
- `recommended_action`
- task phase/status fields
- executor id fields
- artifact/evidence path fields

## Known Failure Samples

- `agpair/recovery.py` is absent, so there is no single recovery decision model.
- `agpair/agent_result.py` still has legacy adoption actions and does not expose the full V2.8 canonical recovery vocabulary.
- `agpair/cli/wait.py`, `agpair/runtime_liveness.py`, and `scripts/smoke_real_executors.py` use separate recommendation strings.
- `agpair/workflows/synthesis.py` still accepts/emits `fall_back`, which must be mapped to `native_fallback`.
- `agpair/cli/task.py` still has `_no_useful_signal_agent_result`; current behavior can classify stdout-without-receipt as blocked even when salvageable report text exists.
- `scripts/smoke_real_executors.py` has useful per-row signals but no canonical summary layer and no help-visible JSON output option.
- T001 external review lane `TASK-V28-T001-JUDGE` showed `acked`, `liveness_state=silent`, `bootstrap_noise_only=true`, `stdout_bytes=0`, and no terminal receipt/report when inspected. This is a live example of the no-progress/recovery problem V2.8 must make clear and actionable.
- The same external review later produced useful stdout but no terminal receipt. Its useful findings were salvaged into this baseline and the GoalBuddy T001 receipt, then the task was abandoned to avoid a stale background process. This is a live sample for stdout-report salvage.

## Plan-To-Code Mismatches To Correct During Implementation

These are implementation details to fix while following the V2.8 plan. They do not invalidate the architecture or acceptance criteria.

- `TaskRepository.mark_acked(...)` currently uses `session_id=...`; plan snippets that mention `antigravity_session_id=` or `executor_session_id=` must be adapted to the real API.
- `TaskRepository.create_task(...)` currently uses `executor_backend=...`; plan snippets that mention `executor=` must be adapted to the real API.
- `scripts/smoke_real_executors.py` currently exposes `--executors`, `--scenario`, timeout/no-progress options, dirty snapshot, and `--keep-worktrees`; it does not expose help-visible `--json`, `--executor`, `--task-kind`, `--completion-policy`, `--fake-executor`, or `--json-output` flags. V2.8 smoke changes must either add the intended output surface or update tests/docs to match the real CLI.
- The source plan's baseline path is `docs/goals/agpair-resilient-external-handoff-v2-8/`, while the GoalBuddy board truth is `docs/goals/agpair-v2-8-final-resilient-handoff/`. Keep the baseline path for the V2.8 receipt, but keep task truth in the GoalBuddy board path.
- `arbitrate_terminal_attempt(...)` does not yet exist. If added, it must be a thin public wrapper over existing terminal/local executor salvage logic, not a second arbitration stack.

## Large-File Smell Baseline

Pure-LOC check found several already-large files:

- `agpair/cli/task.py`: about 2620 pure lines.
- `agpair/cli/wait.py`: about 386 pure lines.
- `scripts/smoke_real_executors.py`: about 968 pure lines.

V2.8 implementation should avoid adding broad new logic to these files when a focused module can own it. In particular, recovery decision selection should live in `agpair/recovery.py`, and smoke fallback logic should call that layer rather than expanding local policy branches.

## Verification Receipt

Commands run for this baseline:

```bash
agpair policy list --controller codex --json
agpair policy list --controller claude --json
python scripts/smoke_real_executors.py --help
rg -n "\"agent_result\"|\"adoption_result\"|\"protocol_result\"|\"controller_action\"|\"recommended_action\"" agpair tests scripts
git diff --check
```

Results:

- policy checks passed and matched the expected Codex/Claude controller suppression matrix;
- smoke help rendered successfully;
- compatibility field inventory located active producers and tests;
- `git diff --check` passed at baseline creation time.
