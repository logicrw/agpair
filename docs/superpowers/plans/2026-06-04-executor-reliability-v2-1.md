# AGPair Executor Reliability V2.1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make AGPair external executors observable, policy-correct, and empirically verified so Codex and Claude Code can safely prefer external agents before native subagents.

**Architecture:** V2.1 is a reliability hardening layer on top of the existing V1.1 task core and V2 workflow layer. It does not add a new orchestration model. Completion policy remains the source of truth for terminal semantics; AGPair adds running-attempt telemetry, executor isolation/preflight, clearer blocker taxonomy, broad-scope guardrails, and real external-executor smoke gates.

**Tech Stack:** Python 3.12, Typer, SQLite, existing AGPair task/attempt/artifact/receipt/watch/completion-policy primitives, local CLI executors, pytest, isolated git worktrees.

---

## 1. Why V2.1 Exists

V2.1 is based on a real failure from `TASK-694620470CD2`.

Observed evidence:

- `repo_path` was the user's home directory, which is broad, noisy, and not a focused project repo.
- Effective policy was report-only: `authorization_profile=local_readonly`, `completion-policy=report`, `requires_commit=false`, `requires_report=true`.
- `stdout.log` was empty.
- `stderr.log` contained environment/plugin/MCP noise and `grep timed out timeout_secs=60`.
- Final status was `blocked`.
- Final receipt summary was `Process died without committing`, which is misleading for a report-only task.

Correct diagnosis:

- The executor did not produce a structured receipt or usable report.
- The direct failure should be represented as `report_output_missing` or `executor_exited_without_report_receipt`, not `missing_commit`.
- AGPair had enough raw signals to detect a low-quality run earlier, but those signals were not surfaced clearly during `acked/silent`.

V2.1 should prevent this class of confusion by making terminal semantics policy-aware and running attempts externally observable.

### 1.1 ChatGPT Pro Final Audit Input

This plan also incorporates the final external audit artifacts supplied on 2026-06-04:

- `<local-downloads>/FINAL_AUDIT_REPORT.md`
- `<local-downloads>/agpair-final-review-blockers.patch`

Audit metadata:

- Audit conclusion: local blocker fixes are required before merge.
- Audited branch: `agpair-v1-1-v2-implementation`.
- Audited commit: `2d2481501221a0d62cf949795f62ce3a11415578` (`Stop reblocking accepted external receipts`).
- Patch SHA256 from the audit report: `12a97d28be9f37848975df6639f08b2444c7ddc6ec32b1cf125388de213d380a`.
- The external auditor could not `git clone` GitHub because DNS resolution for `github.com` failed in their container, so they reconstructed the tree from the upload zip, final diff, baseline snapshot, and consult package. Future review bundles must avoid that ambiguity.

The patch applies cleanly to the current `agpair-v1-1-v2-implementation` worktree with:

```bash
git apply --check <local-downloads>/agpair-final-review-blockers.patch
```

The audit identifies three blockers that must be fixed before broader V2.1 reliability work starts:

1. `agpair/workflows/evidence.py` reads `changed_files` and `scope_violations` from the receipt top level, but structured terminal receipts place them under `receipt["payload"]`. Workflow evidence packs can silently lose child-task changed-file and scope-violation evidence.
2. `agpair/daemon/loop.py:auto_advance_dependent_tasks` dispatches dependency-unblocked tasks without preserving the stored `authorization_profile` and `authorization_summary`. A delayed `local_readonly` task can accidentally fall back to `local_mutating`.
3. `agpair/executors/local_cli.py:LocalCLIExecutor.poll` can return one extra `RUNNING` heartbeat after `rc.txt` already exists because the wrapper process is still in interpreter shutdown. This creates an `acked` one-tick terminal race and weakens wait/watch determinism.

The audit also records four non-blocking risks that must become release checks:

1. Review packages must include a full runnable tree or clearly mark `code/` as a changed-file subset.
2. GitHub repository About metadata may still contain stale Gemini CLI / Antigravity IDE positioning and must be updated manually outside the source diff.
3. Legacy Gemini compatibility code may remain readable only if new task routing still rejects Gemini and docs do not advertise it as active.
4. Workflow reroute, cancel, daemon sweep, and abnormal recovery paths need additional end-to-end pressure tests beyond the happy-path scheduler/watch/store coverage.

External audit verification evidence:

- Before the blocker patch, `PYTHONPATH=. pytest -x -vv` reached 50 passed and failed at `tests/integration/test_daemon_codex_lifecycle.py::test_codex_lifecycle_success` because the task stayed `acked` instead of reaching `ready_for_review`.
- After the blocker patch, the selected blocker tests passed: `4 passed in 5.19s`.
- After the blocker patch, the affected suites passed: `26 passed in 18.23s`.
- A full suite run after the blocker patch hit a 300-second timeout before completion with no new failure observed before timeout. This is not a pass and must be replaced by a completed full-suite result in V2.1 acceptance.
- Privacy review found no real tokens, private keys, raw executor logs, local AGPair runtime state, Codex/Claude private config, or private receipts in the reviewed surfaces.
- MCP adapter scan found no active AGPair MCP architecture references in product docs, code, tests, or packaging metadata.

## 2. Non-Goals

- Do not reintroduce Gemini for new work.
- Do not reintroduce Antigravity IDE.
- Do not add or keep an MCP architecture path for AGPair. AGPair is a CLI/control-plane integration; MCP adapters are not required for this goal.
- Do not make AGPair emulate every provider's interactive approval UI.
- Do not stream full executor logs into Codex/Claude context by default.
- Do not require `agpair target list` or per-project routing setup.
- Do not merge or push smoke-test commits from real external agents.

## 3. Completion Policy Rule

Completion policy owns success and failure semantics.

Required behavior:

- `completion-policy=report` requires a captured report, stdout report output, or valid structured receipt carrying report evidence.
- `completion-policy=commit` requires commit evidence.
- `completion-policy=evidence` requires machine-checkable evidence, not necessarily a commit.
- `completion-policy=auto` resolves through existing V1.1 effective policy rules.
- `local_readonly` and bodies with `Required changes: none/无/禁止写入` must never be blocked because no commit was created.

Failure wording must follow the effective policy:

| Effective policy | Missing evidence | Blocker type | Summary |
| --- | --- | --- | --- |
| `report` | no report/stdout/receipt | `report_output_missing` | `executor exited without report or terminal receipt` |
| `commit` | no commit evidence | `missing_commit_for_commit_policy` | `commit policy requires a verifiable commit` |
| `evidence` | no artifact/receipt/evidence | `evidence_output_missing` | `executor exited without verifiable evidence` |
| malformed terminal JSON | invalid receipt | `malformed_terminal_receipt` | `executor emitted malformed terminal receipt` |
| command not found | missing binary | `executor_unavailable` | `executor binary was not found` |
| process died before terminal output | crash | `process_crash` plus policy-specific missing evidence | `executor process exited before producing required evidence` |

## 4. Running Attempt Observability

AGPair should expose external observable signals while an attempt is still running.

Add or normalize these fields in `task status --json`, `task watch --json`, and human status where useful:

```json
{
  "active_attempt": {
    "task_id": "TASK-123",
    "attempt_no": 1,
    "executor_backend": "grok-cli",
    "pid": 12345,
    "started_at": "2026-06-04T08:30:00Z",
    "last_output_at": "2026-06-04T08:31:12Z",
    "stdout_path": "/.../stdout.log",
    "stderr_path": "/.../stderr.log",
    "stdout_size_bytes": 0,
    "stderr_size_bytes": 65058,
    "stdout_mtime": null,
    "stderr_mtime": "2026-06-04T08:31:12Z",
    "stdout_tail_excerpt": "",
    "stderr_tail_excerpt": "grep timed out timeout_secs=60",
    "liveness_state": "active_via_output"
  }
}
```

Rules:

- Full logs stay on disk and are loaded only when requested.
- JSON status/watch may include small tail excerpts capped at 2 KB by default.
- `task logs --raw stdout` and `task logs --raw stderr` remain the explicit full-log surfaces.
- Artifact metadata should be available before terminal cleanup, not only after the task reaches a terminal phase.
- Watch events should emit when output metadata changes meaningfully, but must throttle repeated log-growth events.

## 5. Liveness And Watchdog Rule

Current liveness relies too much on heartbeat and git workspace activity. That fails for local CLI executors that do not heartbeat and for broad/non-git paths.

V2.1 liveness order:

1. Terminal receipt exists: terminal handling decides.
2. Process dead: run completion-policy-aware terminal arbitration.
3. Recent heartbeat: `active_via_heartbeat`.
4. Recent stdout/stderr write: `active_via_output`.
5. Recent focused git workspace activity: `active_via_workspace`.
6. Process alive but no fresh signal: `silent`.
7. Silence beyond threshold: `no_progress` / watchdog.

Required behavior:

- `acked + silent` must not wait for one hour with no diagnostic.
- Wait/watch should surface last output time, log sizes, and tail excerpt before timeout.
- If stdout remains empty and stderr only contains provider/plugin/MCP/tooling noise beyond a configurable threshold, mark the attempt as `blocked(no_progress)` or `stuck` with evidence paths.
- Watchdog triggering must not depend exclusively on `retry_recommended`.

Default thresholds:

- First running diagnostic event: 60 seconds after ack if no stdout and no heartbeat.
- Soft no-progress threshold: 180 seconds for no stdout/report/receipt and no meaningful workspace activity.
- Existing hard timeout remains the final guard.
- Thresholds must be configurable by CLI flags/env or existing wait options; do not hard-code provider-specific magic into the core state machine.

## 6. Executor Registry, Isolation, Preflight, And Lifecycle

AGPair should distinguish three health levels:

| Level | Meaning | Example failure |
| --- | --- | --- |
| `binary_available` | command exists and can be invoked | `antigravity: command not found` |
| `launch_clean` | minimal invocation does not immediately drown in environment/bootstrap errors | MCP Broken pipe, plugin parse failure |
| `receipt_capable` | executor can complete a minimal AGPair receipt/report contract | stdout empty, malformed JSON |

`agpair doctor --fresh` should show all three levels when feasible.

`task start` should fail fast for `binary_available=false`.

### Equal Executor Treatment

All external executors are first-class registered modules. AGPair must not grow a special path for Grok, Antigravity, Claude Code, Codex, or any future provider.

Allowed executor differences live only in a central profile/registry layer:

- executor id and display name;
- binary name and environment override;
- command builder reference;
- isolation capability;
- receipt/report capability;
- supported completion policies;
- health probes;
- safety metadata;
- controller routing eligibility;
- lifecycle status.

Core task lifecycle, completion policy, artifact capture, wait/watch, doctor output, smoke harness, and docs generation must consume the same profile schema for every executor. They should not contain scattered provider-name conditionals except at a single explicit routing/policy boundary or inside the executor adapter itself.

Controller-specific suppression is routing policy, not executor special treatment:

- Codex suppressing external `codex` is a controller capability decision.
- Claude Code suppressing external `claude-code` is a controller capability decision.
- Diagnostic mode can include self executors only through an explicit diagnostic flag.

Acceptance check:

- A repository search for executor ids in core orchestration files should find registry/profile usage, tests, docs, or adapter-local code, not one-off control-flow branches.
- Adding, disabling, deprecating, or removing an executor should require one profile change plus the adapter/docs/tests lifecycle checklist below, not edits across unrelated state-machine files.

Every registered external executor must expose the same minimum health surface. Grok is not special here; it is only the first executor that made the failure obvious. V2.1 must cover:

- `antigravity-cli`
- `grok-cli`
- `claude-code`
- `codex` as an external Codex CLI worker

For each registered external executor, AGPair must define:

- executor id;
- binary name and env override;
- controller self-suppression rule;
- safety metadata;
- isolation profile;
- noninteractive command shape;
- supported completion policies;
- receipt/report capability;
- startup/preflight probe;
- fake-executor unit test;
- real smoke eligibility.

If launch or receipt capability is unknown, `task start` may proceed only with explicit low-confidence metadata. Status should clearly say:

```json
{
  "executor_health": {
    "executor_id": "grok-cli",
    "binary_name": "grok",
    "binary_available": true,
    "launch_clean": false,
    "receipt_capable": "unknown",
    "last_failure_type": "mcp_startup_noise"
  }
}
```

### Executor Isolation Profiles

Each registered executor needs an isolation profile. The implementation should start with a shared data structure, not ad hoc per-file comments.

Minimum profile fields:

```json
{
  "executor_id": "grok-cli",
  "binary_name": "grok",
  "binary_env_var": "AGPAIR_GROK_BIN",
  "supports_isolated_config_home": true,
  "supports_turn_budget": "unknown",
  "supports_streaming_json": "unknown",
  "default_output_mode": "json",
  "self_executor_for_controllers": [],
  "recommended_for_controllers": ["codex", "claude-code", "generic"]
}
```

Controller self-suppression is part of this profile:

- Codex controller should normally route to `antigravity-cli`, `grok-cli`, and `claude-code`. It should not use external `codex` because Codex already has native subagents.
- Claude Code controller should normally route to `antigravity-cli`, `grok-cli`, and external `codex`. It should not use external `claude-code` because Claude Code already has native subagents.
- Generic/diagnostic mode may test every registered executor, including self executors, only when explicitly passed `--allow-self-executor` or equivalent diagnostic intent.

### Known Executor Risk Notes Under The Shared Profile

Known executor risks should be represented as profile data and adapter-local command construction, not as provider-specific branches in core AGPair logic.

For example, the Grok executor must not inherit noisy Claude/Codex plugin state by accident when AGPair needs a clean worker. That risk is real, but the solution still belongs in the shared isolation profile plus the Grok adapter.

Implementation requirements:

- Keep the default binary configurable by env/PATH, as today.
- Add an executor isolation profile for Grok under the shared registered-executor contract.
- Prefer Grok CLI flags for noninteractive isolation if available.
- If Grok has no stable flags for a behavior, isolate by environment variables and a temporary config home.
- Add a turn budget when the CLI supports it.
- Prefer streaming JSON output if the CLI supports it and still allows AGPair's final terminal receipt contract.
- Never depend on Grok's private chain-of-thought or internal planning stream. AGPair only needs external process/log/receipt signals.

Candidate command shape, subject to actual Grok CLI support:

```bash
grok \
  --cwd "$REPO" \
  --output-format streaming-json \
  --always-approve \
  --max-turns 12 \
  --single "$BODY"
```

If `streaming-json` or `--max-turns` is unsupported, doctor should record that explicitly and the executor should fall back to the currently supported mode with lower health confidence.

### New External Agent Onboarding Contract

Adding a new external executor must become a repeatable checklist, not a custom debugging project.

Onboarding a new executor requires:

1. Add one executor adapter file or a thin subclass of the shared local CLI adapter.
2. Register one metadata/profile entry with executor id, binary name, env override, command builder, isolation profile, safety metadata, supported policies, controller suppression, and doctor probes.
3. Add unit tests for command construction, binary/env selection, receipt contract injection, isolation profile, and unsupported-feature fallback.
4. Add doctor tests for binary unavailable, launch noisy, receipt capable, and low-confidence unknown states.
5. Add fake-executor smoke tests that do not require provider credentials.
6. Add real smoke matrix support so the executor is automatically included when eligible for a controller.
7. Update docs and skills with current behavior only.

The final onboarding acceptance for a new executor is:

- `agpair doctor --fresh` reports it in the same schema as existing executors.
- `task start` can refuse unavailable binaries before dispatch.
- fake smoke passes in CI.
- real smoke either reaches `ready_for_review` with durable evidence or returns a precise blocker.
- controller-specific routing includes or suppresses it according to profile metadata, not scattered hard-coded lists.
- stale docs and skills do not mention unsupported active behavior.

### External Agent Offboarding And Uninstall Contract

Removing or retiring an executor must be as procedural as adding one. AGPair should support executor lifecycle states in the same registry/profile layer:

| State | Meaning | New task behavior | Doctor/docs behavior |
| --- | --- | --- | --- |
| `active` | supported for new tasks when healthy | eligible through routing policy | shown as active |
| `disabled` | locally unavailable or intentionally turned off | refused with actionable reason | shown as disabled with binary/config hint |
| `deprecated` | still readable for old tasks, not recommended for new work | refused by default or requires explicit override | shown as deprecated with replacement |
| `removed` | no longer available for new dispatch | refused always for new starts | legacy task/status records remain readable |

Offboarding an executor requires:

1. Change the registry lifecycle status and replacement guidance.
2. Remove it from normal controller routing without touching the task state machine.
3. Preserve legacy task readability: old task records, receipts, logs, and executor ids must still render clearly.
4. Make `task start --executor <id>` fail before dispatch with a precise reason for `disabled`, `deprecated`, or `removed`.
5. Make `doctor --fresh` show lifecycle status and next action.
6. Update fake tests, routing tests, doctor tests, and smoke eligibility tests.
7. Remove or rewrite active docs/skills mentions; keep only concise legacy notes when needed.
8. Run stale-doc scans before commit.

Uninstalling a local executor binary should not require editing AGPair source. The binary preflight should mark it `disabled` or `binary_available=false`, routing should skip it, and direct use should fail fast with an actionable install/config hint.

Lifecycle acceptance:

- No executor removal should require deleting historical database rows or receipts.
- No removed/deprecated executor should appear in active executor order, active README quick starts, skills routing, or real smoke defaults.
- Direct CLI selection of a non-active executor should never fall through to another executor silently.
- Backward-compatible parsing of old task records should be covered by tests.

## 7. Broad Repo-Path Guardrail

AGPair should warn or refuse broad roots for external executors unless explicitly allowed.

High-risk paths:

- user home, for example `$HOME`;
- filesystem roots;
- non-git directories with many children;
- directories containing large caches, model data, node_modules forests, or memory/log archives.

Required behavior:

- `task start --repo-path /Users/<user>` should refuse by default for external executors.
- The refusal must explain why and how to override.
- Add `--allow-broad-repo-path` only if the project already accepts similar override flags, otherwise reuse `--force` where semantically appropriate.
- A broad-path override should be visible in status/receipt metadata.
- Docs should recommend a temporary focused workdir for local-log/report research.

## 8. Real External Executor Smoke Gate

After V2.1 fixes, every registered external executor must be tested at least at the executor-health/diagnostic layer, and every controller must test the external executors it should normally use.

Registered executors:

- `antigravity-cli`
- `grok-cli`
- `claude-code`
- `codex` as an external Codex CLI worker

Controller-specific smoke matrix:

| Controller | Must smoke | Must not use as normal external worker | Fallback after external failure |
| --- | --- | --- | --- |
| Codex | `antigravity-cli`, `grok-cli`, `claude-code` | external `codex` | Codex native subagents |
| Claude Code | `antigravity-cli`, `grok-cli`, external `codex` | external `claude-code` | Claude Code native subagents |
| Generic diagnostic | all registered executors | none if `--allow-self-executor` is explicit | controller-defined |

Rules:

- Codex controller smoke must not require external `codex`; that executor is redundant with Codex native subagents for normal Codex use.
- Claude Code controller smoke must not require external `claude-code`; that executor is redundant with Claude Code native subagents for normal Claude Code use.
- A separate diagnostic mode may test all registered executors, including self executors, only when labeled as diagnostic and explicitly allowed.
- Final acceptance must report both controller-specific smoke results and all-registered diagnostic health.
- Normal controller smoke should include only lifecycle-active executors that are eligible for that controller.
- Diagnostic smoke should report every registered executor with lifecycle status, including disabled/deprecated/removed entries, without treating intentional unavailability as a failed integration.
- Directly requested smoke of a disabled/deprecated/removed executor should return the same precise lifecycle blocker as `task start --executor <id>`.

Smoke tests must run in disposable isolated worktrees so external agents can modify AGPair without polluting the main branch.

Recommended harness:

```bash
python3 scripts/smoke_real_executors.py \
  --repo-path /path/to/agpair \
  --controller codex \
  --executors antigravity-cli,grok-cli,claude-code \
  --timeout-seconds 900

python3 scripts/smoke_real_executors.py \
  --repo-path /path/to/agpair \
  --controller claude-code \
  --executors antigravity-cli,grok-cli,codex \
  --timeout-seconds 900

python3 scripts/smoke_real_executors.py \
  --repo-path /path/to/agpair \
  --controller diagnostic \
  --all-registered \
  --allow-self-executor \
  --timeout-seconds 900
```

The harness should:

1. create one temporary git worktree per executor;
2. run `agpair doctor --fresh --repo-path "$WT"`;
3. dispatch a tiny mutating task with `--authorization-profile local_mutating`;
4. require `--completion-policy commit` or `--completion-policy evidence`, depending on the executor's contract;
5. wait through `agpair task wait` or `task start --wait`;
6. collect status JSON, receipt path, stdout/stderr metadata, diff/commit evidence, and logs excerpt;
7. remove each temporary worktree after evidence is collected;
8. write a local smoke report under an ignored path such as `.agpair/smoke/`.

Minimal task body:

```text
Goal: Verify that this AGPair executor can make a tiny repo-local change and return AGPair-compatible evidence.

Scope: Work only inside this disposable AGPair git worktree. Do not access user home, private logs, credentials, browser state, or network resources.

Required changes: Create or update tests/fixtures/external_executor_smoke/<executor>.txt with one line containing the executor id and task id. Commit the change if the completion policy is commit.

Exit criteria: Return a valid AGPair terminal receipt with changed_files, validation or validation_not_run, scope_violations, raw_log_path, and receipt_path. The controller must be able to inspect status JSON, raw logs, and git evidence.
```

Acceptance:

- Each available executor either reaches `ready_for_review` with usable evidence or fails with a precise blocker type.
- Codex-controller smoke attempts `antigravity-cli`, `grok-cli`, and `claude-code`, and explicitly records that external `codex` is suppressed because native Codex subagents are the controller fallback.
- Claude Code-controller smoke attempts `antigravity-cli`, `grok-cli`, and external `codex`, and explicitly records that external `claude-code` is suppressed because Claude Code native subagents are the controller fallback.
- Diagnostic all-registered smoke reports health for every registered external executor.
- Diagnostic all-registered smoke labels lifecycle status for active, disabled, deprecated, and removed executors.
- `command not found` becomes `executor_unavailable`.
- Startup/plugin/MCP noise without useful output becomes a launch/health failure, not a vague wait timeout.
- Report-only tasks never mention missing commits.
- Smoke artifacts are local-only and are not staged for GitHub.

## 9. Files To Modify

Expected implementation surfaces:

- `agpair/workflows/evidence.py`
  - Read child `changed_files` and `scope_violations` from `terminal_receipt_json["payload"]`.
  - Keep a compatibility fallback for any legacy receipts that already placed those fields at the top level.
  - Ensure workflow evidence packs do not silently drop changed-file or scope-violation evidence.

- `agpair/daemon/loop.py`
  - Preserve stored dispatch-time authorization when `auto_advance_dependent_tasks` dispatches delayed tasks.
  - Pass `authorization_profile=task.authorization_profile` and `authorization_summary=task.authorization_summary`.

- `agpair/executors/base.py`
  - Extend the `ExecutorAdapter.dispatch` protocol so all executors can accept authorization fields.

- `agpair/executors/antigravity.py`
  - Accept the extended dispatch signature even if the current bus backend ignores the authorization fields.
  - Do not break existing AgentBus dispatch behavior.

- `agpair/executors/local_cli.py`
  - Replace policy-blind `Process died without committing` arbitration.
  - Attach policy-specific blocker type and summary.
  - Preserve stdout/stderr/report/receipt paths on every terminal failure.
  - Treat existing `rc.txt` as a durable terminal signal so a wrapper process in interpreter shutdown does not emit a spurious `RUNNING` heartbeat.

- `agpair/completion.py`
  - Keep completion-policy evaluation as the source of truth.
  - Add or expose helper decisions for process-death/no-output cases if current APIs are insufficient.

- `agpair/task_terminal.py`
  - Ensure terminal receipt/report/artifact persistence handles report-only failures and malformed receipts consistently.

- `agpair/artifacts.py`
  - Add reusable metadata helpers for size, mtime, tail excerpt, and safe path payloads if current helpers only cover terminal artifacts.

- `agpair/cli/task.py`
  - Add live active-attempt artifact metadata to `status`, `logs`, and `watch`.
  - Keep full raw log output behind explicit `--raw`.

- `agpair/cli/wait.py`
  - Make wait/watch exit and diagnostics aware of no-progress output silence, not only `retry_recommended`.

- `agpair/runtime_liveness.py`
  - Add output-based liveness classification.
  - Preserve existing heartbeat/workspace semantics.

- `agpair/executors/grok_cli.py`
  - Add isolation-aware command construction.
  - Add feature detection/fallback for max turns and streaming JSON if feasible.

- `agpair/executors/antigravity_cli.py`
  - Ensure the executor participates in the same health, isolation, receipt-capability, and smoke metadata as Grok.

- `agpair/executors/claude_code.py`
  - Ensure the executor participates in shared metadata and is suppressed for Claude Code controllers by profile, not one-off code.

- `agpair/executors/codex.py`
  - Ensure the external Codex CLI worker participates in shared metadata and is suppressed for Codex controllers by profile, not one-off code.

- `agpair/executors/registry.py`, `agpair/executors/lifecycle.py`, `agpair/executors/policy.py`, `agpair/executors/routing.py`, or equivalent central executor metadata modules
  - Add executor health, capability, isolation, lifecycle, onboarding, and offboarding fields if no central place exists.
  - Keep controller-specific executor ordering and self-suppression in one place.
  - Keep lifecycle states (`active`, `disabled`, `deprecated`, `removed`) out of the task state machine.
  - Prevent scattered executor-id conditionals in core orchestration code.

- `agpair/cli/doctor.py`
  - Report binary, launch, receipt-capability, binary name, lifecycle status, and last failure.

- `tests/unit/test_local_cli_executor.py`
  - Cover report-only process-death wording and blocker type.
  - Cover `rc.txt` terminal arbitration so polling does not stay `RUNNING` once the wrapper has written an exit code.

- `tests/unit/test_completion_policy_v1_1.py`
  - Cover policy-specific missing evidence decisions.

- `tests/unit/test_grok_cli_executor.py`
  - Cover isolation command/env construction and unsupported-flag fallback.

- `tests/unit/test_antigravity_cli_executor.py`
  - Cover metadata, command construction, binary/env selection, and receipt contract participation.

- `tests/unit/test_claude_code_executor.py`
  - Cover metadata, command construction, controller self-suppression, and receipt contract participation.

- `tests/unit/test_codex_executor.py`
  - Cover metadata, command construction, controller self-suppression, and receipt contract participation for the external Codex CLI worker.

- `tests/unit/test_executor_routing.py`
  - Cover Codex controller executor order, Claude Code controller executor order, and diagnostic all-registered mode.

- `tests/unit/test_executor_onboarding.py`
  - Cover that every registered executor has required metadata fields and test coverage hooks.

- `tests/unit/test_executor_lifecycle.py`
  - Cover active, disabled, deprecated, and removed executor behavior for routing, direct CLI selection, doctor output, and legacy task readability.

- `tests/integration/test_task_start_and_status.py`
  - Cover status JSON active-attempt output metadata.

- `tests/integration/test_task_wait.py`
  - Cover no-progress/silent-output watchdog behavior.

- `tests/integration/test_liveness_guard.py`
  - Cover output-based liveness for non-git repo paths.

- `tests/integration/test_doctor.py`
  - Cover multi-level executor health output.

- `tests/integration/test_workflow_scheduler.py`
  - Cover workflow evidence aggregation from nested receipt payload fields.
  - Assert scope violations are preserved in the workflow evidence pack.

- `tests/unit/test_auto_advance.py`
  - Cover delayed dependency auto-dispatch preserving authorization profile and summary.

- `tests/integration/test_daemon_codex_lifecycle.py`
  - Keep the regression that proves local executor terminal polling reaches `ready_for_review` deterministically.

- `tests/integration/test_workflow_recovery.py` or a new targeted workflow resilience test file
  - Add pressure tests for reroute, cancel, daemon sweep, restart recovery, and abnormal child-task terminal states.

- `scripts/smoke_real_executors.py`
  - Add the real executor smoke harness.
  - Support `--controller codex`, `--controller claude-code`, `--controller diagnostic`, explicit executor lists, `--all-registered`, and `--allow-self-executor`.

- `docs/usage.md`, `docs/usage.zh-CN.md`, `docs/getting-started.en.md`, `docs/getting-started-zh.md`, `docs/workflows.md`, `docs/workflows.zh-CN.md`, `README.md`, `README.zh-CN.md`
  - Document completion-policy semantics, broad-path guardrails, and real smoke workflow.
  - State that Gemini, if present in code for legacy readability, is not an active executor for new tasks.
  - Remove or merge redundant historical docs that describe old active executors, old MCP architecture, or obsolete completion behavior.

- `docs/executor-lifecycle.md` or equivalent concise docs section
  - Document the external agent onboarding, disabling, deprecation, removal, uninstall, and smoke checklist.
  - Keep the page focused on current behavior and required steps, not historical rationale.

- `skills/Codex/SKILL.md`, `skills/Claude/SKILL.md`
  - Clarify that controllers should inspect live evidence paths and fall back when external executors show no progress.
  - Describe controller-specific self-executor suppression without making external `codex` or external `claude-code` sound like native subagents.

- release checklist outside source control
  - Update GitHub repository About metadata if it still mentions Gemini CLI or Antigravity IDE.
  - Ensure future review packages either contain a full runnable tree or explicitly mark changed-file-only snapshots.

## 10. Implementation Tasks

### Task 0: Apply Final External Audit Blockers

**Files:**

- Modify: `agpair/workflows/evidence.py`
- Modify: `agpair/daemon/loop.py`
- Modify: `agpair/executors/base.py`
- Modify: `agpair/executors/antigravity.py`
- Modify: `agpair/executors/local_cli.py`
- Test: `tests/integration/test_workflow_scheduler.py`
- Test: `tests/unit/test_auto_advance.py`
- Test: `tests/integration/test_daemon_codex_lifecycle.py`

- [ ] Verify the final audit patch still applies:

```bash
git apply --check <local-downloads>/agpair-final-review-blockers.patch
```

Expected: exits 0.

- [ ] Add or keep a workflow scheduler test that stores a child terminal receipt shaped like:

```json
{
  "status": "EVIDENCE_PACK",
  "payload": {
    "changed_files": ["docs/scan.md"],
    "scope_violations": [{"path": "../outside.txt"}]
  }
}
```

Expected assertion: `workflow.evidence.json` contains `changed_files=["docs/scan.md"]` and preserves the scope violation with the originating node id.

- [ ] Update `build_workflow_evidence_pack` so it reads child evidence from `terminal_receipt_json["payload"]` when present, while preserving top-level fallback for legacy receipts.

- [ ] Add or keep an auto-advance test where task `T-B` is created with `depends_on=["T-A"]` and `authorization_profile="local_readonly"`.

Expected assertion: after `auto_advance_dependent_tasks`, the executor dispatch call receives `authorization_profile="local_readonly"` and the stored `authorization_summary`.

- [ ] Update `auto_advance_dependent_tasks` to pass `authorization_profile=task.authorization_profile` and `authorization_summary=task.authorization_summary`.

- [ ] Extend `ExecutorAdapter.dispatch` and every non-local dispatch implementation touched by the type contract to accept `authorization_profile` and `authorization_summary`.

Expected: existing executors remain backward-compatible and may ignore the fields only when their transport cannot use them yet.

- [ ] Add or keep a local CLI lifecycle regression where the wrapper has written `rc.txt`.

Expected assertion: `LocalCLIExecutor.poll` does not return a `RUNNING` heartbeat after `rc.txt` exists; daemon lifecycle can reach `ready_for_review` in the same tick.

- [ ] Update `LocalCLIExecutor.poll` so `rc.txt` is treated as a durable terminal signal and sets `process_alive=false`.

- [ ] Run the exact external-audit blocker tests:

```bash
PYTHONPATH=. python3 -m compileall -q agpair
PYTHONPATH=. pytest -q \
  tests/unit/test_auto_advance.py::TestAutoAdvanceDependentTasks::test_deps_satisfied_dispatch_preserves_authorization_profile \
  tests/integration/test_workflow_scheduler.py::test_scheduler_dispatches_dependency_free_nodes \
  tests/integration/test_workflow_scheduler.py::test_scheduler_marks_workflow_ready_after_child_and_gate \
  tests/integration/test_daemon_codex_lifecycle.py::test_codex_lifecycle_success
```

Expected: compile passes and the selected tests pass.

- [ ] Run the broader affected suites:

```bash
PYTHONPATH=. pytest -q tests/unit/test_auto_advance.py tests/integration/test_workflow_scheduler.py tests/integration/test_daemon_codex_lifecycle.py
```

Expected: all affected tests pass.

### Task 1: Policy-Aware Terminal Failure Semantics

**Files:**

- Modify: `agpair/executors/local_cli.py`
- Modify if needed: `agpair/completion.py`
- Test: `tests/unit/test_local_cli_executor.py`
- Test: `tests/unit/test_completion_policy_v1_1.py`

- [ ] Add failing tests for a report-only local CLI attempt that exits or dies with empty stdout, no report, and no valid receipt.
- [ ] Assert the receipt/status uses `blocker_type=report_output_missing` or `executor_exited_without_report_receipt`.
- [ ] Assert the summary does not contain `commit`.
- [ ] Add parallel tests for commit policy to preserve `missing_commit_for_commit_policy`.
- [ ] Implement the smallest change in local CLI terminal arbitration.
- [ ] Run:

```bash
pytest tests/unit/test_local_cli_executor.py tests/unit/test_completion_policy_v1_1.py -q
```

Expected: all tests pass.

### Task 2: Running Artifact Telemetry

**Files:**

- Modify: `agpair/artifacts.py`
- Modify: `agpair/cli/task.py`
- Test: `tests/integration/test_task_start_and_status.py`

- [ ] Add tests that create an `acked` task with a live local CLI temp directory containing stdout/stderr files.
- [ ] Assert `task status --json` includes size, mtime, paths, and capped tail excerpts.
- [ ] Assert human status shows concise paths and last output time without dumping full logs.
- [ ] Implement reusable artifact metadata helpers.
- [ ] Wire status JSON and human output.
- [ ] Run:

```bash
pytest tests/integration/test_task_start_and_status.py -q
```

Expected: all tests pass.

### Task 3: Output-Based Liveness And No-Progress Watchdog

**Files:**

- Modify: `agpair/runtime_liveness.py`
- Modify: `agpair/cli/wait.py`
- Modify: `agpair/cli/task.py`
- Test: `tests/integration/test_liveness_guard.py`
- Test: `tests/integration/test_task_wait.py`

- [ ] Add tests for recent stderr/stdout mtime producing `active_via_output`.
- [ ] Add tests for non-git broad path with process alive, no heartbeat, no output growth becoming `silent`.
- [ ] Add tests for `task wait` returning a watchdog/no-progress diagnostic before the hard timeout when configured thresholds are exceeded.
- [ ] Preserve existing heartbeat and workspace activity tests.
- [ ] Implement output-based liveness and throttled no-progress diagnostics.
- [ ] Run:

```bash
pytest tests/integration/test_liveness_guard.py tests/integration/test_task_wait.py -q
```

Expected: all tests pass.

### Task 4: Registered Executor Preflight, Isolation, And Lifecycle Contract

**Files:**

- Modify: `agpair/executors/base.py`
- Modify: `agpair/executors/antigravity_cli.py`
- Modify: `agpair/executors/grok_cli.py`
- Modify: `agpair/executors/claude_code.py`
- Modify: `agpair/executors/codex.py`
- Modify/Create: `agpair/executors/registry.py`
- Modify/Create: `agpair/executors/lifecycle.py`
- Modify: `agpair/executors/policy.py`
- Modify: `agpair/executors/routing.py`
- Modify: `agpair/cli/doctor.py`
- Modify: executor metadata module if introduced.
- Create: `tests/unit/test_executor_onboarding.py`
- Create: `tests/unit/test_executor_lifecycle.py`
- Test: `tests/unit/test_antigravity_cli_executor.py`
- Test: `tests/unit/test_grok_cli_executor.py`
- Test: `tests/unit/test_claude_code_executor.py`
- Test: `tests/unit/test_codex_executor.py`
- Test: `tests/unit/test_executor_routing.py`
- Test: `tests/integration/test_doctor.py`

- [ ] Add a central executor metadata/profile contract covering executor id, binary name, env override, adapter/command builder, isolation profile, safety metadata, supported policies, receipt capability, controller suppression, lifecycle status, replacement guidance, and doctor probes.
- [ ] Add a rule that core lifecycle/completion/watch/doctor/smoke code consumes registry data and does not branch on provider ids outside routing policy or adapter-local command construction.
- [ ] Add tests proving every registered executor has required metadata.
- [ ] Add tests proving `active`, `disabled`, `deprecated`, and `removed` lifecycle states affect routing, direct CLI selection, doctor output, and smoke eligibility consistently.
- [ ] Add tests for Antigravity CLI, Grok CLI, Claude Code, and external Codex CLI worker command construction and binary/env selection.
- [ ] Add tests for Grok command construction including isolation env/flags that are actually supported.
- [ ] Add tests for fallback behavior when `--max-turns` or streaming JSON is not supported.
- [ ] Add routing tests proving Codex controller uses `antigravity-cli`, `grok-cli`, and `claude-code`, while suppressing external `codex`.
- [ ] Add routing tests proving Claude Code controller uses `antigravity-cli`, `grok-cli`, and external `codex`, while suppressing external `claude-code`.
- [ ] Add diagnostic routing tests proving all registered executors can be tested when self executors are explicitly allowed.
- [ ] Add doctor tests for `binary_available`, `launch_clean`, `receipt_capable`, `binary_name`, `lifecycle_status`, `replacement_executor`, and `last_failure_type`.
- [ ] Add tests proving legacy task/status rendering remains readable for deprecated or removed executor ids.
- [ ] Add tests proving direct selection of a disabled/deprecated/removed executor fails fast and does not silently reroute.
- [ ] Implement binary and launch probes without requiring private credentials.
- [ ] Keep probes fast and cacheable.
- [ ] Document onboarding, disabling, deprecation, removal, uninstall, and smoke eligibility in `docs/executor-lifecycle.md` or an equivalent concise docs section.
- [ ] Run:

```bash
pytest \
  tests/unit/test_antigravity_cli_executor.py \
  tests/unit/test_grok_cli_executor.py \
  tests/unit/test_claude_code_executor.py \
  tests/unit/test_codex_executor.py \
  tests/unit/test_executor_routing.py \
  tests/unit/test_executor_health.py \
  tests/unit/test_executor_onboarding.py \
  tests/unit/test_executor_lifecycle.py \
  tests/integration/test_doctor.py \
  -q
```

Expected: all tests pass.

### Task 5: Broad Repo-Path Guardrail

**Files:**

- Modify: `agpair/cli/task.py`
- Modify: `docs/usage.md`
- Modify: `docs/usage.zh-CN.md`
- Test: `tests/integration/test_task_start_and_status.py`

- [ ] Add tests that `task start --repo-path "$HOME"` refuses by default for external executors.
- [ ] Add tests for explicit override.
- [ ] Assert refusal explains why broad roots are risky.
- [ ] Implement guardrail with a narrow override flag or existing force semantics.
- [ ] Document focused workdir guidance.
- [ ] Run:

```bash
pytest tests/integration/test_task_start_and_status.py -q
```

Expected: all tests pass.

### Task 6: Real External Executor Smoke Harness

**Files:**

- Create: `scripts/smoke_real_executors.py`
- Modify: `.gitignore` if needed for `.agpair/smoke/`
- Test: `tests/integration/test_real_executor_smoke_harness.py`

- [ ] Add harness unit/integration tests using fake executor binaries so CI does not require real Antigravity/Grok/Claude/Codex CLIs.
- [ ] Implement isolated worktree creation.
- [ ] Implement one task dispatch per executor with durable status/receipt/log collection.
- [ ] Implement cleanup that removes temp worktrees and leaves only ignored local smoke reports.
- [ ] Implement clear unavailable-executor reporting.
- [ ] Run:

```bash
pytest tests/integration/test_real_executor_smoke_harness.py -q
```

Expected: all tests pass.

Manual local smoke after implementation:

```bash
python3 scripts/smoke_real_executors.py \
  --repo-path /path/to/agpair \
  --controller codex \
  --executors antigravity-cli,grok-cli,claude-code \
  --timeout-seconds 900

python3 scripts/smoke_real_executors.py \
  --repo-path /path/to/agpair \
  --controller claude-code \
  --executors antigravity-cli,grok-cli,codex \
  --timeout-seconds 900

python3 scripts/smoke_real_executors.py \
  --repo-path /path/to/agpair \
  --controller diagnostic \
  --all-registered \
  --allow-self-executor \
  --timeout-seconds 900
```

Expected:

- Codex-controller smoke attempts `antigravity-cli`, `grok-cli`, and `claude-code`; external `codex` is explicitly suppressed for normal Codex use.
- Claude Code-controller smoke attempts `antigravity-cli`, `grok-cli`, and external `codex`; external `claude-code` is explicitly suppressed for normal Claude Code use.
- Diagnostic all-registered smoke covers every registered executor, including self executors only under explicit diagnostic mode.
- each available executor reaches `ready_for_review` or produces a precise blocker;
- all status payloads include receipt/log/artifact evidence paths;
- smoke worktrees are removed or clearly listed for manual cleanup;
- no smoke report, raw log, private receipt, or temp worktree is staged for commit.

### Task 7: Workflow Recovery Pressure Tests

**Files:**

- Modify: `tests/integration/test_workflow_recovery.py` if it exists.
- Create: `tests/integration/test_workflow_recovery_pressure.py` if no focused recovery file exists.
- Modify: `agpair/workflows/scheduler.py` only if tests expose a real scheduler bug.
- Modify: `agpair/daemon/loop.py` only if tests expose a real daemon recovery bug.

- [ ] Add a workflow reroute test where a child task blocks with a recoverable executor failure and the scheduler reroutes to the next eligible executor without losing previous artifact paths.
- [ ] Add a workflow cancel test where cancellation preserves child task receipts, stdout/stderr/report paths, and evidence pack references.
- [ ] Add a daemon restart recovery test where a workflow with one completed child and one pending child resumes scheduling idempotently.
- [ ] Add a daemon sweep/abnormal terminal-state test where a child process disappears without receipt and the workflow records a precise blocker instead of silently hanging.
- [ ] Run:

```bash
PYTHONPATH=. pytest -q tests/integration/test_workflow_recovery.py tests/integration/test_workflow_recovery_pressure.py
```

Expected: all existing and new recovery tests pass. If one of the two files does not exist, run the one that exists plus the newly created file.

Do not add workflow framework complexity to satisfy these tests. Use existing workflow store, scheduler, child task, artifact, and completion-policy primitives.

### Task 8: Documentation, Skills, Lifecycle Docs, And Privacy Gate

**Files:**

- Modify: `README.md`
- Modify: `README.zh-CN.md`
- Modify: `docs/getting-started.en.md`
- Modify: `docs/getting-started-zh.md`
- Modify: `docs/usage.md`
- Modify: `docs/usage.zh-CN.md`
- Modify: `docs/workflows.md`
- Modify: `docs/workflows.zh-CN.md`
- Modify/Create: `docs/executor-lifecycle.md`
- Delete or merge: redundant docs that still describe removed active executors, old MCP architecture, or obsolete completion-policy behavior.
- Modify: `skills/Codex/SKILL.md`
- Modify: `skills/Claude/SKILL.md`

- [ ] Document that external executors are preferred but must be judged by receipts, artifacts, logs, and tests.
- [ ] Document equal treatment: executor differences live in registry/profile/adapter metadata, not scattered special cases.
- [ ] Document the lifecycle workflow for onboarding, disabling, deprecation, removal, local binary uninstall, fake tests, real smoke, and docs updates.
- [ ] Document that report-only tasks do not commit.
- [ ] Document the broad-path warning and focused workdir recommendation.
- [ ] Document real smoke command and expected local-only outputs.
- [ ] Remove or merge redundant historical docs instead of leaving stale parallel explanations.
- [ ] Update retained docs to describe only current behavior; avoid long historical "formerly" explanations unless needed for legacy compatibility.
- [ ] Confirm docs do not mention Gemini or Antigravity IDE as active executors.
- [ ] Confirm any remaining Gemini code is described only as legacy-readable compatibility and is rejected for new task starts.
- [ ] Confirm docs and skills do not present any MCP adapter as part of the current core architecture.
- [ ] Confirm no retained doc says all tasks require commits or defaults every task to `direct_commit`.
- [ ] Add a release note/checklist item that GitHub About metadata must be updated outside the repo if it still mentions Gemini CLI or Antigravity IDE.
- [ ] Add packaging guidance for future external audits: provide a full runnable tree tarball, or label changed-file-only snapshots clearly so reviewers do not try to run partial `code/` directories as full repos.
- [ ] Run:

```bash
pytest tests/unit -q
pytest tests/integration -q
git diff --check
```

Expected: all tests pass or any unrelated failure is documented with exact evidence. Whitespace check passes.

Stale-doc gate:

```bash
rg -n "Gemini|Antigravity IDE|agpair-mcp|mcp_server|FastMCP|mcp\\[cli\\]|direct_commit|Process died without committing|must commit|required commit" README.md README.zh-CN.md docs skills agpair tests pyproject.toml
```

Expected: no active-behavior references are stale. Any remaining hits are either test fixtures for legacy compatibility or concise legacy notes that cannot be mistaken for current behavior.

Privacy gate:

```bash
git status --short
git diff --cached --name-only
git diff --name-only
```

Expected:

- no user-local `~/.codex`, `~/.claude`, `~/.agpair` files are staged;
- no raw executor logs are staged;
- no private receipts or smoke reports are staged;
- no credentials, session ids, browser state, or absolute private temp paths are staged.

## 11. Final Acceptance

V2.1 is complete only when all are true:

- ChatGPT Pro blocker B1 is fixed: workflow evidence reads nested receipt payload `changed_files` and `scope_violations`.
- ChatGPT Pro blocker B2 is fixed: delayed depends_on auto-dispatch preserves stored authorization profile and summary.
- ChatGPT Pro blocker B3 is fixed: `rc.txt` makes Local CLI polling terminal and prevents the one-tick `acked` race.
- Report-only executor failures no longer mention missing commits.
- Missing report/receipt is represented with a precise blocker type.
- `task status --json` and `task watch --json` expose enough running-attempt metadata for Codex/Claude to judge progress without high-token polling.
- Every registered external executor has health/preflight metadata and required onboarding fields.
- Every registered external executor has an isolation profile, even when the profile explicitly records that only limited isolation is supported.
- Every registered external executor has a lifecycle status and is handled through the same registry/profile contract.
- Disabled, deprecated, removed, and locally uninstalled executors fail fast for new direct starts, are skipped by normal routing, remain readable in legacy task/status views, and are visible in doctor output.
- Executor-specific behavior is isolated to central profile/routing policy or adapter-local command construction; core orchestration files do not contain scattered provider-name special cases.
- Grok CLI startup is isolated as much as the real CLI supports.
- Doctor distinguishes binary availability, launch cleanliness, and receipt capability.
- Broad repo paths are refused or warned with explicit override.
- All normal unit/integration tests pass.
- Codex-controller real smoke attempts `antigravity-cli`, `grok-cli`, and `claude-code`; external `codex` is suppressed because Codex native subagents are the controller fallback.
- Claude Code-controller real smoke attempts `antigravity-cli`, `grok-cli`, and external `codex`; external `claude-code` is suppressed because Claude Code native subagents are the controller fallback.
- Diagnostic all-registered smoke reports health for every registered executor.
- The external `codex` smoke is clearly labeled as an external Codex CLI worker, not Codex native subagents.
- The external `claude-code` smoke is clearly labeled as an external Claude Code worker, not Claude Code native subagents.
- New external executor onboarding is documented and covered by tests so adding another agent requires one metadata/profile entry plus adapter/tests/docs/smoke, not scattered routing changes.
- External executor offboarding/uninstall is documented and covered by tests so removing or disabling an agent requires one lifecycle/profile change plus docs/tests/smoke updates, not task-state-machine edits.
- Redundant/stale docs are deleted or merged, and retained docs use current concise wording.
- Smoke artifacts remain local-only and are not committed or pushed.
- Full unit and integration suites complete, not merely "no failures before a timeout".
- Workflow reroute, cancel, daemon sweep, and restart recovery have explicit pressure-test coverage.
- Future external-review packages are either full runnable trees or clearly labeled changed-file subsets.
- GitHub repository About metadata is checked and manually updated if stale.
- Legacy Gemini compatibility, if still present, is not exposed as an active new-task executor in CLI policy, docs, skills, or GitHub metadata.

## 12. Implementation Order

1. Task 0: final external-audit blockers.
2. Task 1: policy-aware terminal failures.
3. Task 2: running artifact telemetry.
4. Task 3: output-based liveness and no-progress watchdog.
5. Task 4: registered executor preflight, isolation, and lifecycle contract.
6. Task 5: broad repo-path guardrail.
7. Task 6: controller-aware real executor smoke harness.
8. Task 7: workflow recovery pressure tests.
9. Task 8: docs, skills, lifecycle docs, full verification, privacy gate.

Do not run real external smoke before Tasks 0-5 are implemented. The smoke gate is meant to validate the fixed control plane, not to repeat known noisy failures.
