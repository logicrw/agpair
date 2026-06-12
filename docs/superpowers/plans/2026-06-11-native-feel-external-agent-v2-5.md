# Native-Feel External Agent V2.5 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make AGPair external executors feel close to native subagents for both read-only and code-writing work: external agents stay the preferred first lane, long-running tasks can continue in the background without wasting controller tokens, and Codex / Claude Code can review, adopt, or fall back with clear evidence instead of manual salvage.

**Architecture:** Extend and simplify the existing V2.3/V2.4 surfaces instead of adding a new orchestration layer. Keep executor environments `managed-natural` with inherited skills/MCP/provider config. Collapse executor routing, lifecycle, user/project enablement, and health decisions into one resolved policy view, add a thin controller-wait lease model on top of existing task/attempt records, improve live signal reporting, and add first-class diff/apply adoption for isolated implementation tasks. Native subagents remain fallback/review lanes, not the default worker pool.

**Tech Stack:** Python 3.12, Typer, SQLite migrations, AGPair local CLI executors, git worktrees, git diff/apply, pytest, real executor smoke harness, Codex / Claude Code skills and hooks.

---

## 0. This Plan's Contract

This plan is not a rewrite of AGPair. It is a product/ergonomics pass over the already landed model:

- V2.3 already introduced protocol/adoption results, report/evidence policies, isolated worktrees, dirty snapshots, and real executor smoke.
- V2.4 already introduced controller-vs-worker hook boundaries so AGPair-started executors keep normal skills/MCP without recursively behaving like controllers.
- The remaining gap is the day-to-day experience: controllers still tend to use external agents mostly for read-only review, sometimes abandon slow tasks too early, and still need too much manual work to adopt isolated code changes.

All implementation must satisfy these constraints:

- Simplicity means fewer concepts, fewer duplicated paths, and fewer hidden modes. It must not mean weaker executor pluggability, weaker startup ergonomics, or fewer user-facing controls.
- Keep `managed-natural + inherit` as the default for every active external executor.
- Do not reintroduce `managed-restricted`, `isolated-bare`, capability bundles, or hidden per-executor launch special cases.
- Do not build a second executor registry. `ExecutorSpec` remains the compiled-in adapter contract; user/project choices are only a thin override layer resolved by the existing policy path.
- Do not make "fast startup" mean degraded default intelligence. Fast startup is an explicit, measurable launch profile for tiny/smoke tasks; normal work keeps full skills/MCP/provider behavior.
- Prefer deleting duplicated eligibility and routing checks over adding new parallel helpers.
- Do not make AGPair an MCP server or second semantic brain.
- Do not disable native Codex / Claude Code subagents. They stay useful as fallback, reviewer, or narrow helper.
- Do not treat "no quick useful signal" as automatic task failure for complex work.
- Do not let a background external task silently mutate the user's active worktree. Mutating work remains isolated by default.
- Do not commit raw smoke logs, provider secrets, receipts with private paths beyond normal local paths, or executor worktrees.

## 1. Target Product Behavior

### 1.1 External-First, Not External-Only

Default routing for non-trivial delegatable work:

```text
controller direct work
  -> use for tiny edits, sensitive local judgment, or work too coupled to current context

AGPair external executor
  -> preferred first lane for bounded implementation, refactor, test-fix, repo scan, review, research, smoke, and mechanical multi-file work

native subagent
  -> fallback when AGPair is unavailable/unsuitable/not good enough
  -> reviewer of external output
  -> optional parallel backup for high-risk or time-sensitive tasks
```

Controller-specific executor suppression remains:

```text
Codex controller:
  use antigravity-cli -> grok-cli -> claude-code
  do not default to external codex; native Codex subagents are its own fallback/review lane

Claude Code controller:
  use antigravity-cli -> grok-cli -> codex
  do not default to external claude-code; Claude Code native subagents are its own fallback/review lane
```

### 1.2 No More Hard Early Abandon For Complex Tasks

The earlier "if AGPair has no useful signal quickly, switch away" rule is too crude.

Replace it with:

```text
external-first
+ controller wait lease
+ background execution budget
+ structured live signal state
+ native fallback/review only when evidence says external is unavailable, unsuitable, low quality, or over budget
```

Correct behavior examples:

| Situation | Correct behavior |
| --- | --- |
| Quick read-only review has no stdout/stderr/report for several minutes | return a soft background/stalled action, allow retry/switch, do not pretend useful work exists |
| Complex implementation has no early stdout but process is alive and worktree has activity | keep task running, controller may detach and continue other work |
| Complex implementation has no visible output and no process/worktree activity until execution budget expires | mark `stuck(no_progress_budget_exceeded)` |
| Executor exits without report/evidence | terminal `blocked(report_output_missing)` or `blocked(evidence_output_missing)` |
| Executor returns usable diff in isolated worktree | `ready_for_review`, `adoptable_result=yes|partial`, `task diff` / `task apply --check` available |
| External result is poor | record `adoptable_result=no` or `controller_rework=major`, retry/switch/native fallback |

### 1.3 Native-Subagent-Like Code Adoption

For code-writing work, AGPair should make the common path as direct as:

```bash
agpair task start \
  --repo-path "$REPO" \
  --controller codex \
  --task-kind implementation \
  --authorization-profile local_mutating \
  --completion-policy evidence \
  --isolated-worktree \
  --body "$BRIEF"

agpair task status TASK --json
agpair task diff TASK
agpair task apply TASK --check
agpair task apply TASK
```

The controller still verifies tests and code quality, but it should not need to manually find a temp worktree, extract logs, construct patches, or guess which files changed.

## 2. New Core Concepts

### 2.1 Task Kind

Add a persisted `task_kind` to tasks and attempts:

```text
quick_review       short report/review, expected to produce text quickly
deep_review        more expensive read-only review or analysis
implementation     bounded code change, default isolated worktree + evidence policy
test_fix           failing test/build fix, default isolated worktree + evidence policy
research           external docs/web/repo research, report/evidence policy
smoke              tiny executor verification task
generic            backward-compatible default
```

The field is advisory but operational: it selects default wait lease and execution budget, and it appears in status/watch/smoke results.

### 2.2 Wait Policy

Add a persisted `wait_policy`:

```text
terminal     old strict behavior: wait until terminal phase or timeout
lease        wait for terminal phase until controller lease expires, then detach if still running
background   return immediately after dispatch, keep daemon/watch evidence
strict       CI-style strict wait; no soft detach
```

Skills should prefer:

```text
quick_review    -> lease
deep_review     -> lease
implementation  -> lease
test_fix        -> lease
research        -> lease or background
smoke           -> strict
```

### 2.3 Controller Wait Lease vs Execution Budget

Separate "how long the controller should block this turn" from "how long AGPair lets the executor run":

| Task kind | Controller wait lease | Execution budget | Default background ok |
| --- | ---: | ---: | --- |
| `quick_review` | 120s | 900s | true |
| `deep_review` | 240s | 1800s | true |
| `implementation` | 300s | 3600s | true |
| `test_fix` | 300s | 3600s | true |
| `research` | 300s | 5400s | true |
| `smoke` | 300s | 600s | false |
| `generic` | existing CLI timeout | existing daemon timeout | false |

These are defaults, not hidden constants. Users can override them:

```bash
agpair task start \
  --repo-path "$REPO" \
  --controller codex \
  --task-kind implementation \
  --controller-wait-seconds 180 \
  --execution-budget-seconds 5400 \
  --wait-policy lease \
  --authorization-profile local_mutating \
  --completion-policy evidence \
  --isolated-worktree \
  --body "$BRIEF"
```

### 2.4 Signal State

Status/watch should say what AGPair can actually observe:

```json
{
  "signal_state": {
    "state": "active_via_output",
    "last_signal_at": "2026-06-11T12:00:00Z",
    "last_signal_type": "stdout_growth",
    "stdout_bytes": 2048,
    "stderr_bytes": 912,
    "bootstrap_noise_only": false,
    "process_alive": true,
    "controller_silence_seconds": 74.2,
    "execution_budget_remaining_seconds": 3120.0
  },
  "controller_action": {
    "action": "detach_and_continue",
    "reason": "controller_wait_lease_expired_but_executor_still_running",
    "safe_to_use_native_parallel_review": true,
    "should_abandon": false
  }
}
```

This replaces the ambiguous mental model where `acked + silent` either means "still thinking" or "useless". AGPair can only report external signals, not model internals.

### 2.5 Runtime Executor Policy Overlay

Keep the current static executor profiles, but make runtime routing configurable through one small overlay resolved by the existing policy path:

```text
ExecutorSpec
  -> built-in adapter contract: id, binary, lifecycle, safety, default priority, controller suppression

ExecutorPolicyOverlay
  -> local/project/user intent: enabled/disabled for controller, priority/order, default startup profile

ResolvedExecutorPolicy
  -> single truth used by task start, retry, workflow scheduler, doctor, smoke, and docs/skill diagnostics
```

This is not a new registry. The implementation should keep `EXECUTOR_SPECS` as the only compiled-in executor list and add only a thin JSON-backed overlay for local choices:

```json
{
  "version": 1,
  "controllers": {
    "codex": {
      "disabled": ["claude-code"],
      "priority": ["antigravity-cli", "grok-cli"],
      "startup_profile": {
        "grok-cli": "natural"
      }
    }
  },
  "global": {
    "disabled": []
  }
}
```

Desired user-facing behavior:

```bash
agpair policy list --controller codex
agpair policy show --controller codex --json
agpair policy disable claude-code --controller codex
agpair policy enable claude-code --controller codex
agpair policy priority --controller codex antigravity-cli grok-cli claude-code
agpair policy startup-profile --controller codex grok-cli fast
agpair policy reset --controller codex
```

Rules:

- Runtime pluggability must be complete enough for daily use: list, inspect, enable, disable, reprioritize, set/reset startup profile, and reset controller policy without editing source code.
- Controller-specific disable affects only that controller. Disabling `claude-code` for Codex does not disable it for Claude Code diagnostics or generic use.
- Global disable applies to all controllers and must be visible as `executor_disabled_by_policy`.
- Directly requesting a disabled executor fails before dispatch with a precise reason. It must not silently fall through to the next executor.
- `policy show/list --json`, `doctor`, and task status must expose the policy source: `static_spec`, `project_overlay`, `user_overlay`, `task_override`.
- Project overlay may be added only if it stays simple and local to the repo, such as `.agpair/executors.json`. Do not require projects to create a target entry just to use default routing.
- If user and project overlays conflict, direct task flags win, then project overlay, then user overlay, then static spec.

This directly supports the practical case:

```text
Codex controller can temporarily stop using external claude-code
without deleting the claude-code adapter, changing lifecycle status,
editing skills, or affecting Claude Code controller routing.
```

### 2.6 Startup Profile

Do not add `managed-restricted` or `isolated-bare` back. Keep:

```text
environment_mode = managed-natural
skill_policy = inherit
mcp_policy = inherit
```

Add a separate optional `startup_profile`:

```text
natural   default; current full CLI behavior, full skills/MCP/provider config
fast      explicit tiny-task/smoke optimization, only for executors that prove it works
```

`startup_profile` is a launch optimization, not a capability model. It may tune safe startup details such as shorter turn budgets, noninteractive flags already supported by the adapter, or CLI-native "skip heavy initialization" flags when they exist. It must not silently disable web, memory, skills, MCP, plugins, or subagents unless the user explicitly asks for fast mode and the adapter documents the exact effect.

Rules:

- Default for all real work is `natural`.
- `fast` is opt-in through task flags, `--fast` shorthand, or policy overlay, never an implicit fallback.
- If a user explicitly requests `fast` for an unsupported executor, fail fast with `startup_profile_unsupported`.
- If `fast` is slower, less reliable, or produces lower-quality receipts in smoke, keep it unavailable for that executor.
- Status, receipt, doctor, and smoke output must show `startup_profile` and `startup_profile_source`.
- Do not add executor-specific branches outside adapters. Each adapter owns how `fast` maps to its command, if at all.

Intended usage:

```bash
agpair task start \
  --repo-path "$REPO" \
  --controller codex \
  --executor grok-cli \
  --task-kind smoke \
  --startup-profile fast \
  --authorization-profile local_readonly \
  --completion-policy report \
  --body "$BRIEF"
```

Convenience shorthand:

```bash
agpair task start --repo-path "$REPO" --task-kind smoke --fast --body "$BRIEF"
```

`--fast` must be only a shorthand for `--startup-profile fast`; it must not imply different authorization, completion policy, executor selection, or hidden skill/MCP changes.

Implementation should benchmark `natural` vs `fast` on the same tiny report and tiny implementation smoke. Keep `fast` only when it is demonstrably faster without hurting receipt/report/adoption quality.

## 3. Files And Responsibilities

### Executor Policy Resolution

- Modify: `agpair/executors/policy.py`
  - Keep `ExecutorSpec` as the built-in adapter contract.
  - Add a resolved policy view that merges static specs, lifecycle, controller suppression, health, user/project overlays, and task overrides once.
  - Make `resolve_controller_policy()` return overlay reasons and source metadata instead of recomputing ad hoc eligibility.
  - Keep the existing controller self-suppression rule profile-driven.

- Create or modify: `agpair/executors/config.py`
  - Store only runtime overlay data: controller disables, global disables, controller priority, and optional startup profile defaults.
  - Use a small JSON manager pattern similar to `agpair/targets.py`.
  - Reject unknown executor ids, rejected legacy ids, and unsupported startup profiles at write time.

- Modify: `agpair/executors/registry.py`
  - Replace duplicated `enabled_by_default + lifecycle` checks with the resolved policy view where controller context exists.
  - Keep historical profile lookup simple and static.

- Modify: `agpair/cli/policy.py`
  - Extend the existing `agpair policy` command; do not add a second `agpair executor` command group unless implementation discovery proves the existing group becomes unclear.
  - Add `list`, `enable`, `disable`, `priority`, `startup-profile`, and `reset`.
  - `show/list --json` must print final order, skipped executors, direct-request rejection reason, and source of each decision.

- Modify: `agpair/config.py`
  - Add `executor_policy_path` under `~/.agpair/` for local runtime overlay.
  - If project overlay is implemented, keep it repo-local under `.agpair/executors.json` and never auto-create it.

- Modify: `agpair/cli/doctor.py`, `scripts/smoke_real_executors.py`
  - Consume the same resolved policy view instead of hand-building slightly different executor lists.
  - Report disabled/skipped executors as precise policy blockers, not as missing binaries.

### Startup Profile

- Modify: `agpair/executors/policy.py`
  - Add supported startup profiles to `ExecutorSpec`, defaulting to `("natural",)`.
  - Add `startup_profile` and `startup_profile_source` to resolved launch metadata.

- Modify: local CLI adapters only where a proven fast profile exists.
  - Default command construction must remain unchanged for `natural`.
  - Any `fast` command delta must be unit-tested and documented in the profile.

- Modify: `agpair/models.py`, `agpair/storage/schema.sql`, `agpair/storage/db.py`, `agpair/storage/tasks.py`
  - Persist `startup_profile` and `startup_profile_source` on attempts if needed for status/receipt audit.
  - Do not create a separate startup state machine.

### Model / Persistence

- Modify: `agpair/models.py`
  - Add `task_kind`, `wait_policy`, `controller_wait_seconds`, `execution_budget_seconds`, `background_ok`.
  - Add optional `controller_action_json` only if computed action must be persisted. Prefer computed status first.

- Modify: `agpair/storage/schema.sql`
  - Add the same task and attempt columns with backward-compatible defaults.

- Modify: `agpair/storage/db.py`
  - Add idempotent migrations.

- Modify: `agpair/storage/tasks.py`
  - Create/hydrate/update new fields.
  - Ensure retry preserves task kind and budgets unless explicitly overridden.

### Wait / Watch / Liveness

- Modify: `agpair/cli/wait.py`
  - Extend `WaitResult` with `outcome`, `controller_lease_expired`, and `recommended_action`.
  - Make lease expiry a non-failure outcome when `background_ok=true` and the executor is still plausibly running.

- Modify: `agpair/runtime_liveness.py`
  - Add signal summary helpers.
  - Keep bootstrap/plugin/MCP stderr noise visible but do not count it as useful signal.

- Modify: `agpair/watch.py`
  - Add stable watch event fields for signal state and controller action.

- Modify: `agpair/daemon/loop.py`
  - Use per-task execution budget for hard stuck.
  - Keep soft watchdog as recommendation, not automatic controller failure for background-ok tasks.

### CLI Surfaces

- Modify: `agpair/cli/task.py`
  - Add `--task-kind`, `--wait-policy`, `--controller-wait-seconds`, `--execution-budget-seconds`, `--background-ok/--no-background-ok`.
  - Add status JSON fields for signal state, budget, and controller action.
  - Add `task diff TASK`.
  - Add `task apply TASK --check` and `task apply TASK`.

- Modify: `agpair/cli/app.py`
  - Include new task fields in `inspect` output.

### Isolated Worktree Adoption

- Modify: `agpair/executors/local_cli.py`
  - Record a worker diff baseline after dirty snapshot is applied.
  - For tracked dirty snapshot, create an internal baseline commit inside the isolated worktree before launching the executor.
  - Store `worker_base_head`, `worker_base_created`, and `worker_diff_base_reason` in state.

- Create: `agpair/worktree_adoption.py`
  - Compute worker diff against the worker baseline.
  - Validate apply safety in the controller repo.
  - Apply patch to the controller repo with conflict reporting.

### Skills / Hooks / Docs

- Modify: `skills/Codex/SKILL.md`
- Modify: `skills/Claude/SKILL.md`
- Modify: `~/.codex/skills/agpair/SKILL.md` during local sync only, not in repository commit.
- Modify: `~/.codex/skills/agpair-codex/SKILL.md` during local sync only, not in repository commit.
- Modify: `~/.claude/skills/agpair/SKILL.md` during local sync only, not in repository commit.
- Modify: `README.md`, `README.zh-CN.md`, `docs/usage.md`, `docs/usage.zh-CN.md`, `docs/executor-lifecycle.md`.

### Tests / Smoke

- Create: `tests/unit/test_executor_policy_overlay.py`
- Modify: `tests/unit/test_executor_onboarding.py`
- Modify: `tests/unit/test_executor_lifecycle.py`
- Modify: `tests/integration/test_task_start_and_status.py`
- Modify: `tests/integration/test_task_wait.py`
- Modify: `tests/integration/test_task_watch_events.py`
- Modify: `tests/integration/test_daemon_stuck_detection.py`
- Modify: `tests/integration/test_liveness_guard.py`
- Modify: `tests/unit/test_local_cli_executor_isolated.py`
- Create: `tests/unit/test_wait_policy.py`
- Create: `tests/unit/test_worktree_adoption.py`
- Modify: `tests/integration/test_real_executor_smoke_harness.py`
- Modify: `scripts/smoke_real_executors.py`

## 4. Task 0: Simplify Executor Policy Before Adding More Behavior

**Files:**
- Create: `tests/unit/test_executor_policy_overlay.py`
- Modify: `tests/unit/test_executor_onboarding.py`
- Modify: `tests/unit/test_executor_lifecycle.py`
- Modify: `agpair/executors/policy.py`
- Create or modify: `agpair/executors/config.py`
- Modify: `agpair/executors/registry.py`
- Modify: `agpair/cli/policy.py`
- Modify: `agpair/config.py`
- Modify: `agpair/cli/doctor.py`
- Modify: `scripts/smoke_real_executors.py`
- Modify: `docs/executor-lifecycle.md`

This task must be implemented before task kind/wait lease work. Its purpose is to make later behavior simpler: one resolved executor policy decides order, availability, disablement, startup profile, and direct-request rejection.

- [ ] **Step 1: Write failing tests for controller-specific disable and priority**

Create tests proving these exact outcomes:

```python
def test_codex_can_disable_external_claude_code_without_affecting_generic_policy(tmp_path):
    overlay = ExecutorPolicyOverlay.from_dict({
        "version": 1,
        "controllers": {"codex": {"disabled": ["claude-code"]}},
    })

    codex = resolve_controller_policy(controller="codex", overlay=overlay)
    assert "claude-code" not in codex.eligible_executors
    assert any("disabled by policy" in reason for reason in codex.reasons)

    generic = resolve_controller_policy(controller="generic", overlay=overlay)
    assert "claude-code" in generic.eligible_executors


def test_direct_request_for_policy_disabled_executor_fails_fast(tmp_path):
    overlay = ExecutorPolicyOverlay.from_dict({
        "version": 1,
        "controllers": {"codex": {"disabled": ["claude-code"]}},
    })

    decision = resolve_controller_policy(
        controller="codex",
        requested_executor="claude-code",
        overlay=overlay,
    )

    assert decision.selected_executor is None
    assert decision.rejected_executor == "claude-code"
    assert any("executor_disabled_by_policy" in reason for reason in decision.reasons)


def test_controller_priority_overlay_reorders_without_changing_static_specs(tmp_path):
    overlay = ExecutorPolicyOverlay.from_dict({
        "version": 1,
        "controllers": {
            "codex": {"priority": ["grok-cli", "antigravity-cli", "claude-code"]},
        },
    })

    decision = resolve_controller_policy(controller="codex", overlay=overlay)

    assert decision.eligible_executors[:3] == ("grok-cli", "antigravity-cli", "claude-code")
    assert EXECUTOR_SPECS["antigravity-cli"].default_priority == 10
```

Do not make tests depend on private local binaries. Use `require_available=False` for unit tests.

- [ ] **Step 2: Implement the smallest overlay object**

Add a small dataclass/manager, not a new registry:

```python
@dataclass(frozen=True)
class ExecutorPolicyOverlay:
    disabled_global: tuple[str, ...] = ()
    controller_disabled: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    controller_priority: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    controller_startup_profiles: Mapping[str, Mapping[str, str]] = field(default_factory=dict)
```

Rules:

- Normalize executor ids through existing `normalize_executor_id`.
- Reject Gemini and unknown executors on write.
- Keep absent overlay equivalent to current behavior.
- Keep lifecycle status (`active`, `disabled`, `deprecated`, `removed`) separate from runtime disable. Lifecycle is product support; overlay is local routing intent.

- [ ] **Step 3: Create one resolved policy view**

Refactor `resolve_controller_policy()` so the flow is explicit and single-pass:

```text
normalize controller/request
load overlay if one was not passed
start with EXECUTOR_SPECS sorted by static priority
apply controller priority overlay
apply lifecycle and static enabled checks
apply global/controller runtime disables
apply controller self-suppression unless allow_self_executor
apply require_available health filter
handle requested executor as a validation path, not as silent fallback
return selected executor, eligible list, skipped/suppressed lists, reasons, and sources
```

Do not add task-state-machine branches. Do not copy this logic into doctor, smoke, scheduler, or registry.

- [ ] **Step 4: Extend existing `agpair policy` CLI**

Use the existing command group:

```bash
agpair policy list --controller codex --json
agpair policy disable claude-code --controller codex
agpair policy enable claude-code --controller codex
agpair policy priority --controller codex antigravity-cli grok-cli claude-code
agpair policy startup-profile --controller codex grok-cli fast
agpair policy reset --controller codex
```

Expected UX:

- `list` shows final order and skipped reasons.
- `disable`/`enable` only edits the overlay file.
- `priority` persists exactly the known executor ids supplied by the user and appends unspecified eligible executors by static priority.
- `startup-profile` persists a controller/executor default and rejects unsupported profiles before writing.
- `reset --controller codex` removes only Codex-specific overlay.
- No command writes secrets, provider settings, CC Switch config, or raw executor logs.

- [ ] **Step 5: Add explicit startup profile without changing defaults**

Add `startup_profile` to resolved launch metadata:

```text
natural   default for all executors
fast      opt-in only; supported only when adapter declares and tests prove it
```

Implementation rules:

- `natural` must produce byte-for-byte equivalent default command construction, except for explicit metadata fields.
- `--fast` must map exactly to `--startup-profile fast`.
- `fast` must fail with `startup_profile_unsupported` unless the executor profile declares support.
- Do not add `managed-restricted`, `isolated-bare`, `--bare`, `--no-memory`, `--disable-web-search`, or MCP/skill-disabling flags as defaults.
- If an adapter supports `fast`, keep the command delta inside that adapter and test it there.

- [ ] **Step 6: Make doctor and smoke consume the same policy**

`doctor`, workflow scheduler, and `scripts/smoke_real_executors.py` should ask the resolved policy for controller-eligible executors instead of rebuilding order manually.

Add tests proving:

- Codex smoke skips external `codex` by default and also skips overlay-disabled `claude-code`.
- Claude Code smoke skips external `claude-code` by default and respects its own overlay independently.
- Disabled executors report `executor_disabled_by_policy`, not `executor_unavailable`.
- `--allow-self-executor` affects self-suppression only, not global/runtime disable.

- [ ] **Step 7: Update docs with current behavior only**

Update `docs/executor-lifecycle.md` to say:

```text
ExecutorSpec defines what AGPair knows how to run.
ExecutorPolicyOverlay defines what this machine/project currently wants to use.
ResolvedExecutorPolicy is the only dispatch truth.
```

Do not explain old Gemini/IDE behavior. Do not describe `managed-restricted` or `isolated-bare` as supported modes.

- [ ] **Step 8: Verification**

Run:

```bash
PYTHONPATH=. pytest tests/unit/test_executor_policy_overlay.py tests/unit/test_executor_onboarding.py tests/unit/test_executor_lifecycle.py -q
PYTHONPATH=. pytest tests/integration/test_doctor.py tests/integration/test_real_executor_smoke_harness.py -q
```

Then run a no-private-data CLI check:

```bash
agpair policy list --controller codex --json
agpair policy disable claude-code --controller codex
agpair policy list --controller codex --json
agpair policy enable claude-code --controller codex
```

Expected: disabling/enabling changes only `~/.agpair/executors.json` or the configured overlay file, and no repository source file is modified by those commands.

## 5. Task 1: Persist Task Kind And Wait Budgets

**Files:**

- Modify: `agpair/models.py`
- Modify: `agpair/storage/schema.sql`
- Modify: `agpair/storage/db.py`
- Modify: `agpair/storage/tasks.py`
- Modify: `agpair/cli/task.py`
- Create: `tests/unit/test_wait_policy.py`
- Modify: `tests/integration/test_task_start_and_status.py`

- [ ] **Step 1: Add failing tests for task-kind normalization**

Create `tests/unit/test_wait_policy.py`:

```python
import pytest

from agpair.wait_policy import (
    DEFAULT_WAIT_BUDGETS,
    normalize_task_kind,
    normalize_wait_policy,
    resolve_wait_budget,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("quick-review", "quick_review"),
        ("deep_review", "deep_review"),
        ("implementation", "implementation"),
        ("test-fix", "test_fix"),
        ("research", "research"),
        ("smoke", "smoke"),
        (None, "generic"),
    ],
)
def test_normalize_task_kind_aliases(raw, expected):
    assert normalize_task_kind(raw) == expected


def test_resolve_implementation_budget_defaults_to_background_lease():
    budget = resolve_wait_budget(task_kind="implementation", wait_policy=None)

    assert budget.task_kind == "implementation"
    assert budget.wait_policy == "lease"
    assert budget.controller_wait_seconds == DEFAULT_WAIT_BUDGETS["implementation"].controller_wait_seconds
    assert budget.execution_budget_seconds == DEFAULT_WAIT_BUDGETS["implementation"].execution_budget_seconds
    assert budget.background_ok is True


def test_strict_wait_policy_disables_background_detach():
    budget = resolve_wait_budget(task_kind="implementation", wait_policy="strict")

    assert budget.wait_policy == "strict"
    assert budget.background_ok is False


def test_unknown_task_kind_rejected():
    with pytest.raises(ValueError, match="task kind"):
        normalize_task_kind("large-magic")
```

- [ ] **Step 2: Implement `agpair/wait_policy.py`**

Add a small pure module:

```python
from __future__ import annotations

from dataclasses import dataclass

VALID_TASK_KINDS = {
    "quick_review",
    "deep_review",
    "implementation",
    "test_fix",
    "research",
    "smoke",
    "generic",
}

VALID_WAIT_POLICIES = {"terminal", "lease", "background", "strict"}


@dataclass(frozen=True)
class WaitBudget:
    task_kind: str
    wait_policy: str
    controller_wait_seconds: float | None
    execution_budget_seconds: float | None
    background_ok: bool


DEFAULT_WAIT_BUDGETS: dict[str, WaitBudget] = {
    "quick_review": WaitBudget("quick_review", "lease", 120.0, 900.0, True),
    "deep_review": WaitBudget("deep_review", "lease", 240.0, 1800.0, True),
    "implementation": WaitBudget("implementation", "lease", 300.0, 3600.0, True),
    "test_fix": WaitBudget("test_fix", "lease", 300.0, 3600.0, True),
    "research": WaitBudget("research", "lease", 300.0, 5400.0, True),
    "smoke": WaitBudget("smoke", "strict", 300.0, 600.0, False),
    "generic": WaitBudget("generic", "terminal", None, None, False),
}
```

Implement:

```python
def normalize_task_kind(value: str | None) -> str:
    normalized = (value or "generic").strip().lower().replace("-", "_")
    if normalized not in VALID_TASK_KINDS:
        raise ValueError(f"task kind must be one of: {', '.join(sorted(VALID_TASK_KINDS))}")
    return normalized


def normalize_wait_policy(value: str | None, *, task_kind: str = "generic") -> str:
    if value is None or not str(value).strip():
        return DEFAULT_WAIT_BUDGETS[task_kind].wait_policy
    normalized = str(value).strip().lower().replace("-", "_")
    if normalized not in VALID_WAIT_POLICIES:
        raise ValueError(f"wait policy must be one of: {', '.join(sorted(VALID_WAIT_POLICIES))}")
    return normalized
```

- [ ] **Step 3: Add schema columns**

Add to `tasks`:

```sql
task_kind TEXT NOT NULL DEFAULT 'generic',
wait_policy TEXT NOT NULL DEFAULT 'terminal',
controller_wait_seconds REAL,
execution_budget_seconds REAL,
background_ok INTEGER NOT NULL DEFAULT 0
```

Add matching columns to `task_attempts` so retries and smoke evidence can report the attempt-level contract.

- [ ] **Step 4: Add migration**

In `agpair/storage/db.py`, add idempotent `ALTER TABLE` defaults for both `tasks` and `task_attempts`.

Expected defaults for existing DB rows:

```text
task_kind=generic
wait_policy=terminal
controller_wait_seconds=NULL
execution_budget_seconds=NULL
background_ok=0
```

- [ ] **Step 5: Extend models and repository methods**

Add fields to `TaskRecord` and `TaskAttemptRecord`, and thread them through:

```python
task_kind: str = "generic"
wait_policy: str = "terminal"
controller_wait_seconds: float | None = None
execution_budget_seconds: float | None = None
background_ok: bool = False
```

Update these existing repository surfaces so every new field is accepted, persisted, and hydrated:

- `TaskRepository.create_task`
- `TaskRepository.record_attempt_start`
- `TaskRepository.apply_retry_dispatch`
- row hydration helpers for `TaskRecord` and `TaskAttemptRecord`

- [ ] **Step 6: Add CLI options**

In `task start` and `task retry`, add:

```python
task_kind: str | None = typer.Option(None, "--task-kind", help="Task kind: quick_review, deep_review, implementation, test_fix, research, smoke, or generic."),
wait_policy: str | None = typer.Option(None, "--wait-policy", help="Wait policy: terminal, lease, background, or strict."),
controller_wait_seconds: float | None = typer.Option(None, "--controller-wait-seconds", min=0),
execution_budget_seconds: float | None = typer.Option(None, "--execution-budget-seconds", min=1),
background_ok: bool | None = typer.Option(None, "--background-ok/--no-background-ok"),
```

If `--task-kind implementation` or `--task-kind test_fix` is used and the user did not explicitly set these options:

```text
authorization_profile -> keep user value, but docs/skills should pass local_mutating
completion_policy -> evidence when requested policy is auto
isolated_worktree -> true unless explicitly disabled is not supported; keep manual opt-in at code level if needed for compatibility
dirty_snapshot -> tracked for isolated mutating evidence tasks
```

Do not silently widen authorization. A readonly implementation task should fail validation with a clear message instead of becoming mutating.

- [ ] **Step 7: Status JSON includes the contract**

Add to `build_task_payload()`:

```json
{
  "task_kind": "implementation",
  "wait_policy": "lease",
  "controller_wait_seconds": 300.0,
  "execution_budget_seconds": 3600.0,
  "background_ok": true
}
```

- [ ] **Step 8: Focused verification**

Run:

```bash
PYTHONPATH=. pytest tests/unit/test_wait_policy.py tests/integration/test_task_start_and_status.py -q
```

Expected: new task metadata persists, retries preserve it, and status prints both JSON and human fields.

## 6. Task 2: Make Wait Lease A First-Class Outcome

**Files:**

- Modify: `agpair/cli/wait.py`
- Modify: `agpair/cli/task.py`
- Modify: `tests/integration/test_task_wait.py`
- Modify: `tests/integration/test_liveness_guard.py`

- [ ] **Step 1: Extend `WaitResult`**

Change:

```python
@dataclass(frozen=True)
class WaitResult:
    phase: str
    timed_out: bool
    watchdog_triggered: bool = False
```

to:

```python
@dataclass(frozen=True)
class WaitResult:
    phase: str
    outcome: str
    timed_out: bool = False
    watchdog_triggered: bool = False
    controller_lease_expired: bool = False
    recommended_action: str | None = None
```

Allowed outcomes:

```text
terminal_success
terminal_failure
strict_timeout
controller_lease_expired
soft_no_progress
background_started
missing_task
```

- [ ] **Step 2: Preserve old strict behavior when no lease is requested**

Existing commands without task-kind/wait-policy changes should keep current behavior:

```text
terminal success -> exit 0
terminal failure -> exit 1
timeout -> exit 1
watchdog in strict/terminal mode -> exit 1
```

- [ ] **Step 3: Implement lease behavior**

In `wait_for_terminal_phase`, add parameters:

```python
controller_wait_seconds: float | None = None
background_ok: bool = False
strict_watchdog: bool = True
```

Rules:

```text
if terminal phase:
  return terminal_success or terminal_failure

if controller_wait_seconds elapsed and background_ok and task is still acked:
  return controller_lease_expired with recommended_action=detach_and_continue

if soft watchdog/no-progress fires and background_ok:
  return soft_no_progress with recommended_action=inspect_or_continue_background

if soft watchdog/no-progress fires and not background_ok:
  keep current failure behavior
```

Do not abandon or mark stuck from the wait command. The daemon owns hard budget terminal transitions.

- [ ] **Step 4: `task start` uses task wait budget**

`maybe_auto_wait()` should read the task after dispatch and pass:

```python
controller_wait_seconds=task.controller_wait_seconds
background_ok=task.background_ok
strict_watchdog=task.wait_policy in {"terminal", "strict"}
```

If `wait_policy=background`, return immediately after dispatch with exit code 0 and a message naming `task watch` / `task wait`.

- [ ] **Step 5: JSON output for lease expiry**

For `agpair task wait TASK --json`, return:

```json
{
  "ok": true,
  "task_id": "TASK-123",
  "phase": "acked",
  "outcome": "controller_lease_expired",
  "controller_lease_expired": true,
  "recommended_action": "detach_and_continue",
  "background_ok": true
}
```

For non-JSON output:

```text
Task TASK-123 is still running in background after controller wait lease.
Recommended: detach and continue; later run `agpair task wait TASK-123` or inspect `agpair task status TASK-123 --json`.
```

- [ ] **Step 6: Tests**

Add tests:

```python
def test_background_ok_lease_expiry_returns_success_without_abandoning(tmp_path: Path):
    repo = _make_repo(tmp_path)
    repo.create_task(
        task_id="TASK-LEASE",
        repo_path="/r",
        task_kind="implementation",
        wait_policy="lease",
        controller_wait_seconds=60,
        execution_budget_seconds=3600,
        background_ok=True,
    )
    repo.mark_acked(task_id="TASK-LEASE", session_id="s-lease")

    result = wait_for_terminal_phase(
        paths.db_path,
        "TASK-LEASE",
        interval_seconds=5,
        timeout_seconds=3600,
        controller_wait_seconds=60,
        background_ok=True,
        _clock=FakeClock(),
    )
    assert result.outcome == "controller_lease_expired"
    assert result.controller_lease_expired is True
    assert result.phase == "acked"
    assert repo.get_task("TASK-LEASE").phase == "acked"
```

```python
def test_strict_wait_still_fails_on_watchdog(tmp_path: Path):
    repo = _make_repo(tmp_path)
    repo.create_task(task_id="TASK-STRICT", repo_path="/r", wait_policy="strict", background_ok=False)
    repo.mark_acked(task_id="TASK-STRICT", session_id="s-strict")
    repo.recommend_retry(task_id="TASK-STRICT")

    result = wait_for_terminal_phase(
        _make_paths(tmp_path).db_path,
        "TASK-STRICT",
        interval_seconds=5,
        timeout_seconds=120,
        background_ok=False,
        strict_watchdog=True,
        _clock=FakeClock(),
    )
    assert result.watchdog_triggered is True
    assert exit_code_for_dispatch(result) == 1
```

Run:

```bash
PYTHONPATH=. pytest tests/integration/test_task_wait.py tests/integration/test_liveness_guard.py -q
```

## 7. Task 3: Surface Live Signals And Controller Actions

**Files:**

- Modify: `agpair/runtime_liveness.py`
- Modify: `agpair/artifacts.py`
- Modify: `agpair/watch.py`
- Modify: `agpair/cli/task.py`
- Modify: `tests/integration/test_task_watch_events.py`
- Modify: `tests/integration/test_task_start_and_status.py`

- [ ] **Step 1: Add signal summary model**

In `agpair/runtime_liveness.py`, add:

```python
@dataclass(frozen=True)
class SignalSummary:
    state: str
    last_signal_at: str | None
    last_signal_type: str | None
    stdout_bytes: int | None = None
    stderr_bytes: int | None = None
    bootstrap_noise_only: bool = False
    process_alive: bool | None = None
    controller_silence_seconds: float | None = None
    execution_budget_remaining_seconds: float | None = None
```

Implement `build_signal_summary(task, *, now)` using:

- `last_heartbeat_at`
- `last_workspace_activity_at`
- stdout/stderr stat metadata
- `is_bootstrap_noise()`
- local executor `state.json` process liveness when available

- [ ] **Step 2: Add controller action model**

In the same module or new `agpair/controller_action.py`, add a pure helper:

```python
def recommend_controller_action(task: TaskRecord, signal: SignalSummary) -> dict[str, object]:
    """Return a JSON-serializable controller action for the current task state."""
```

Rules:

```text
terminal success -> inspect_and_accept
terminal failure -> retry_switch_or_native_fallback
acked + lease expired + background_ok -> detach_and_continue
acked + soft no signal + background_ok -> inspect_logs_or_continue_background
acked + soft no signal + not background_ok -> retry_or_switch_executor
ready_for_review/evidence_ready not approved -> verify_then_accept
```

- [ ] **Step 3: Extend status payload**

Add:

```json
{
  "signal_state": {
    "state": "active_via_output",
    "last_signal_at": "2026-06-11T12:00:00Z",
    "last_signal_type": "stdout_growth",
    "stdout_bytes": 2048,
    "stderr_bytes": 912,
    "bootstrap_noise_only": false,
    "process_alive": true
  },
  "controller_action": {
    "action": "detach_and_continue",
    "reason": "controller_wait_lease_expired_but_executor_still_running",
    "should_abandon": false
  }
}
```

Keep existing `liveness_state`, stdout/stderr paths, and artifact metadata for compatibility.

- [ ] **Step 4: Extend watch events**

Extend `WatchEvent` with optional:

```python
signal_state: dict | None = None
controller_action: dict | None = None
stdout_bytes: int | None = None
stderr_bytes: int | None = None
last_signal_at: str | None = None
```

`should_emit_watch_event()` should emit when signal type or artifact byte count changes, but not every poll with identical metadata.

- [ ] **Step 5: Tests**

Add:

```python
def test_watch_event_emits_stdout_growth_without_log_body():
    previous = WatchEvent(task_id="TASK", state="acked", cursor="1", stdout_bytes=0)
    current = WatchEvent(task_id="TASK", state="acked", cursor="1", stdout_bytes=128)

    assert should_emit_watch_event(previous, current)
    assert "log_body" not in current.to_json_dict()
```

Run:

```bash
PYTHONPATH=. pytest tests/integration/test_task_watch_events.py tests/integration/test_task_start_and_status.py -q
```

## 8. Task 4: Use Execution Budget For Hard Stuck

**Files:**

- Modify: `agpair/daemon/loop.py`
- Modify: `agpair/storage/tasks.py`
- Modify: `tests/integration/test_daemon_stuck_detection.py`
- Modify: `tests/integration/test_liveness_guard.py`

- [ ] **Step 1: Add budget-aware candidate query**

Current daemon uses one global timeout. Add repository method:

```python
def list_execution_budget_expired_candidates(self, *, now_iso: str) -> list[TaskRecord]:
    """Return acked tasks whose per-task execution budget has expired."""
```

SQLite condition:

```sql
phase='acked'
AND execution_budget_seconds IS NOT NULL
AND datetime(created_at, '+' || execution_budget_seconds || ' seconds') <= datetime(:now_iso)
```

Keep existing global hard timeout as fallback for old tasks with no budget.

- [ ] **Step 2: Soft watchdog remains recommendation**

`mark_watchdog_tasks()` may still set `retry_recommended=1`, but for `background_ok=1` this is not equivalent to task failure. Journal text should be explicit:

```text
soft_no_progress_recommended
```

Do not change the task phase at soft watchdog time.

- [ ] **Step 3: Hard budget marks stuck**

When execution budget expires and no terminal receipt/evidence exists:

```text
phase=stuck
stuck_reason=no_progress_budget_exceeded after N seconds
failure_context.blocker_type=no_progress_budget_exceeded
recommended_next_action=retry_switch_or_native_fallback
```

- [ ] **Step 4: Tests**

Add:

```python
def test_background_task_not_failed_at_soft_watchdog(tmp_path):
    repo = _make_repo(tmp_path)
    repo.create_task(task_id="TASK-SOFT", repo_path="/r", wait_policy="lease", background_ok=True)
    repo.mark_acked(task_id="TASK-SOFT", session_id="s-soft")
    mark_watchdog_tasks(_make_paths(tmp_path), current=NOW, watchdog_seconds=60, timeout_seconds=3600)
    task = repo.get_task("TASK-SOFT")

    assert task.phase == "acked"
    assert task.retry_recommended is True
```

```python
def test_background_task_marks_stuck_after_execution_budget(tmp_path):
    repo = _make_repo(tmp_path)
    repo.create_task(
        task_id="TASK-HARD",
        repo_path="/r",
        wait_policy="lease",
        execution_budget_seconds=60,
        background_ok=True,
    )
    repo.mark_acked(task_id="TASK-HARD", session_id="s-hard")
    mark_execution_budget_expired_tasks(_make_paths(tmp_path), current=NOW_PLUS_120_SECONDS)
    task = repo.get_task("TASK-HARD")

    assert task.phase == "stuck"
    assert "no_progress_budget_exceeded" in task.stuck_reason
```

Run:

```bash
PYTHONPATH=. pytest tests/integration/test_daemon_stuck_detection.py tests/integration/test_liveness_guard.py -q
```

## 9. Task 5: Add Native-Like Diff And Apply For Isolated Code Tasks

**Files:**

- Modify: `agpair/executors/local_cli.py`
- Create: `agpair/worktree_adoption.py`
- Modify: `agpair/cli/task.py`
- Modify: `tests/unit/test_local_cli_executor_isolated.py`
- Create: `tests/unit/test_worktree_adoption.py`
- Modify: `tests/integration/test_task_start_and_status.py`

- [ ] **Step 1: Record worker diff baseline**

In isolated worktree dispatch, after dirty snapshot is applied and before launching the executor:

```python
worker_base_head = _prepare_worker_diff_baseline(
    execution_repo_path=execution_repo_path,
    task_id=task_id,
    dirty_snapshot_applied=dirty_snapshot_applied,
)
state["worker_base_head"] = worker_base_head
state["worker_base_created"] = bool(worker_base_head and worker_base_head != start_head)
state["worker_diff_base_reason"] = "dirty_snapshot_baseline" if dirty_snapshot_applied else "start_head"
```

Implementation rule:

```text
if no dirty snapshot applied:
  worker_base_head = start_head

if dirty snapshot applied:
  create an internal detached worktree commit containing the applied tracked dirty snapshot
  worker_base_head = that internal commit
```

Use local-only commit metadata:

```bash
git -C "$execution_repo_path" add -A
git -C "$execution_repo_path" \
  -c user.name=AGPair \
  -c user.email=agpair@local.invalid \
  commit -m "AGPair baseline snapshot TASK-123"
```

If there is nothing to commit after applying dirty snapshot, keep `worker_base_head=start_head`.

Do not push. Do not create a commit in the user's active worktree.

- [ ] **Step 2: Create `agpair/worktree_adoption.py`**

Add pure helpers:

```python
@dataclass(frozen=True)
class WorktreeDiff:
    task_id: str
    execution_repo_path: str
    base_ref: str
    patch: str
    stat: str
    changed_files: tuple[str, ...]


@dataclass(frozen=True)
class ApplyCheck:
    ok: bool
    reason: str | None = None
    stderr: str = ""
```

Implement:

```python
def build_worktree_diff(*, task: TaskRecord, session_state: Mapping[str, object]) -> WorktreeDiff:
    """Return the patch between worker_base_head and the executor worktree."""

def check_apply_to_controller_repo(*, repo_path: str, patch: str) -> ApplyCheck:
    """Run git apply --check against the controller repo without modifying files."""

def apply_to_controller_repo(*, repo_path: str, patch: str) -> ApplyCheck:
    """Apply the worker patch to the controller repo and report conflicts."""
```

Use:

```bash
git -C "$execution_repo_path" diff --binary "$worker_base_head"
git -C "$execution_repo_path" diff --stat "$worker_base_head"
git -C "$repo_path" apply --check --3way --whitespace=nowarn -
git -C "$repo_path" apply --3way --whitespace=nowarn -
```

- [ ] **Step 3: Add `task diff`**

Command:

```bash
agpair task diff TASK-123
agpair task diff TASK-123 --stat
agpair task diff TASK-123 --json
```

Rules:

- Only supported for isolated worktree attempts with an executor session state.
- Refuse if the worktree is gone.
- Use `worker_base_head`, not raw `start_head`, to avoid replaying the controller's pre-existing dirty snapshot.

JSON shape:

```json
{
  "ok": true,
  "task_id": "TASK-123",
  "base_ref": "abc123",
  "changed_files": ["agpair/foo.py"],
  "stat": " agpair/foo.py | 2 ++\n 1 file changed, 2 insertions(+)",
  "patch_path": null
}
```

- [ ] **Step 4: Add `task apply`**

Command:

```bash
agpair task apply TASK-123 --check
agpair task apply TASK-123
agpair task apply TASK-123 --json
```

Rules:

- Refuse if task phase is not `ready_for_review` or `evidence_ready` unless `--force` is passed.
- Refuse if no isolated worktree diff exists.
- Refuse if `adoptable_result=no` unless `--force` is passed.
- `--check` never modifies files.
- Default apply leaves changes unstaged in the controller repo.
- Record journal event `controller_applied_diff` with changed file list.
- Do not mark task accepted automatically. The controller must still run tests and then call `task accept`.

- [ ] **Step 5: Tests for dirty snapshot baseline**

Test shape:

```python
def test_worker_diff_excludes_controller_dirty_snapshot(tmp_path):
    # base repo has file a.txt committed as "base"
    # controller dirty snapshot changes a.txt to "controller dirty"
    # isolated worker starts with dirty snapshot applied
    # internal baseline commit records "controller dirty"
    # worker changes b.txt
    # task diff contains b.txt only, not a.txt
```

- [ ] **Step 6: Tests for apply safety**

Add:

```python
def test_task_apply_check_refuses_conflict(tmp_path):
    repo, task_id = make_conflicting_isolated_task(tmp_path)
    result = CliRunner().invoke(app, ["task", "apply", task_id, "--check", "--json"])
    payload = json.loads(result.stdout)

    assert payload["ok"] is False
    assert payload["error"] == "apply_conflict"
```

```python
def test_task_apply_applies_worker_patch_to_controller_repo(tmp_path):
    repo, task_id = make_applyable_isolated_task(tmp_path)
    result = CliRunner().invoke(app, ["task", "apply", task_id, "--json"])
    payload = json.loads(result.stdout)

    assert payload["ok"] is True
    assert (repo / "target.py").read_text() == "worker change\n"
```

Run:

```bash
PYTHONPATH=. pytest tests/unit/test_local_cli_executor_isolated.py tests/unit/test_worktree_adoption.py tests/integration/test_task_start_and_status.py -q
```

## 10. Task 6: Make Controller Skills Prefer Real External Implementation

**Files:**

- Modify: `skills/Codex/SKILL.md`
- Modify: `skills/Claude/SKILL.md`
- Modify: `tests/integration/test_codex_cli.py`
- Modify: `tests/integration/test_claude_cli.py`

- [ ] **Step 1: Update skill routing language**

Both skills must say:

```text
For non-trivial implementation/refactor/test-fix, dispatch one bounded AGPair implementation slice first unless:
- the task is tiny and direct edit is clearly cheaper;
- the task is credential/sensitive/local-state heavy;
- AGPair doctor says every appropriate external executor is unavailable;
- a previous external attempt for the same task was adoptable_result=no or controller_rework=major;
- the user explicitly asks not to delegate.
```

- [ ] **Step 2: Replace hard early-abandon guidance**

Remove any wording equivalent to:

```text
if no useful signal quickly, abandon external and use native
```

Replace with:

```text
If controller wait lease expires but the task is still alive, detach and continue or run a native reviewer in parallel. Do not abandon a complex external task solely because it has not produced a quick final report.
```

- [ ] **Step 3: Add canonical code task command**

Codex skill should show:

```bash
agpair task start \
  --repo-path "$REPO" \
  --controller codex \
  --task-kind implementation \
  --wait-policy lease \
  --authorization-profile local_mutating \
  --completion-policy evidence \
  --isolated-worktree \
  --body "$BRIEF"
```

Claude skill should show same command with `--controller claude-code`.

- [ ] **Step 4: Add adoption commands**

Both skills must include:

```bash
agpair task diff TASK-123
agpair task apply TASK-123 --check
agpair task apply TASK-123
agpair task accept TASK-123 --adoptable-result yes --controller-rework none
```

- [ ] **Step 5: Hook context tests**

Existing hook tests should assert:

- external-first appears;
- bounded implementation appears;
- `task_kind implementation` or `--task-kind implementation` appears;
- native subagents are described as fallback/review, not forbidden;
- no hidden `managed-restricted`, `isolated-bare`, or self-worker default wording appears.

Run:

```bash
PYTHONPATH=. pytest tests/integration/test_codex_cli.py tests/integration/test_claude_cli.py -q
```

## 11. Task 7: Real Executor Smoke Must Include Code-Writing Adoption

**Files:**

- Modify: `scripts/smoke_real_executors.py`
- Modify: `tests/integration/test_real_executor_smoke_harness.py`
- Modify: `docs/executor-lifecycle.md`

- [ ] **Step 1: Add smoke scenario types**

Smoke harness should run two scenarios for each eligible executor:

```text
report_smoke:
  local_readonly + completion-policy report
  validates report/receipt/evidence capture

implementation_smoke:
  local_mutating + completion-policy evidence + isolated-worktree
  writes one tiny allowed file
  validates task diff and task apply --check
```

Do not apply smoke diffs to the real repo by default. Use disposable repo/worktree fixtures.

- [ ] **Step 2: Enforce controller matrix**

Normal smoke:

```text
Codex controller: antigravity-cli, grok-cli, claude-code
Claude controller: antigravity-cli, grok-cli, codex
```

Diagnostic smoke may include self executors only with explicit `--allow-self-executor`.

- [ ] **Step 3: Smoke result schema**

Add:

```json
{
  "executor_id": "antigravity-cli",
  "controller": "codex",
  "scenario": "implementation_smoke",
  "outcome": "ready_for_review",
  "adoptable_result": "yes",
  "diff_available": true,
  "apply_check_ok": true,
  "controller_wait_outcome": "terminal_success",
  "time_to_first_signal_seconds": 18.4,
  "controller_rework": "none"
}
```

- [ ] **Step 4: Failure classes**

Smoke must classify failures as:

```text
executor_unavailable
executor_auth_required
executor_probe_timeout
executor_hook_interference
no_progress_budget_exceeded
report_output_missing
evidence_output_missing
terminal_receipt_missing
diff_missing
apply_conflict
adoptable_result_no
```

- [ ] **Step 5: Tests**

Add fake executor tests proving the harness:

- runs both scenarios;
- skips suppressed self executor;
- records `apply_check_ok`;
- treats lease expiry as non-fatal when the task later completes;
- fails if implementation smoke has no diff.

Run:

```bash
PYTHONPATH=. pytest tests/integration/test_real_executor_smoke_harness.py -q
```

## 12. Task 8: Update Docs With Current Behavior Only

**Files:**

- Modify: `README.md`
- Modify: `README.zh-CN.md`
- Modify: `docs/usage.md`
- Modify: `docs/usage.zh-CN.md`
- Modify: `docs/executor-lifecycle.md`
- Modify: `docs/superpowers/plans/README.md`

- [ ] **Step 1: Make the project positioning concise**

Docs should describe AGPair as:

```text
AGPair is a local task lifecycle and evidence layer that lets a controller AI delegate bounded work to external CLI agents, then wait, verify, adopt, retry, or fall back with structured evidence.
```

Do not explain old bridge/IDE/Gemini history in prominent sections. Keep legacy notes only where needed for old task readability.

- [ ] **Step 2: Update quickstart**

Quickstart must include:

```bash
agpair doctor --fresh
agpair task start --repo-path "$REPO" --task-kind quick_review --authorization-profile local_readonly --completion-policy report --body "$BRIEF"
agpair task start --repo-path "$REPO" --task-kind implementation --authorization-profile local_mutating --completion-policy evidence --isolated-worktree --body "$BRIEF"
agpair task diff TASK
agpair task apply TASK --check
```

- [ ] **Step 3: Explain wait lease simply**

Use this wording:

```text
`--wait-policy lease` lets the controller wait cheaply for a bounded window. If the executor is still running, AGPair returns a structured background-running result instead of forcing the controller to burn model turns polling or killing the task early.
```

- [ ] **Step 4: Remove stale wording**

Run:

```bash
rg -n "managed-restricted|isolated-bare|managed-isolated|Gemini.*new|Antigravity IDE.*executor|no useful signal quickly.*abandon|must use native subagent" README.md README.zh-CN.md docs skills agpair tests || true
```

Expected: no user-facing recommendation of removed modes or Gemini/new IDE routing. Historical tests/fixtures may mention old strings only when clearly marked legacy.

## 13. Task 9: Sync Local Codex And Claude Code Configuration Safely

**Files outside repo, do not commit:**

- `~/.codex/skills/agpair/SKILL.md`
- `~/.codex/skills/agpair-codex/SKILL.md`
- `~/.claude/skills/agpair/SKILL.md`
- `~/.codex/hooks.json`
- `~/.claude/settings.json`

- [ ] **Step 1: Use AGPair config commands where possible**

After repo docs/skills are updated, sync with AGPair's own config helpers:

```bash
agpair codex config --install --scope user --dry-run
agpair claude config --install --scope user --dry-run
```

Inspect diffs first.

- [ ] **Step 2: Install only AGPair-managed entries**

Run the non-dry commands only after the dry-run shows AGPair-managed hooks/skills and no unrelated deletions:

```bash
agpair codex config --install --scope user
agpair claude config --install --scope user
```

- [ ] **Step 3: Verify local config**

Run:

```bash
agpair doctor --fresh
agpair codex config --scope user --dry-run
agpair claude config --scope user --dry-run
```

Expected:

- no unmanaged AGPair skill drift;
- controller hooks installed;
- AGPair-started executor/probe hooks still no-op through `AGPAIR_INTERNAL_ROLE`;
- no private provider secrets printed.

Do not commit local user config files.

## 14. Task 10: Verification And Privacy Gate

**Files:**

- No new production files unless tests uncover issues.

- [ ] **Step 1: Focused tests**

Run:

```bash
PYTHONPATH=. pytest \
  tests/unit/test_wait_policy.py \
  tests/unit/test_worktree_adoption.py \
  tests/unit/test_local_cli_executor_isolated.py \
  tests/integration/test_task_wait.py \
  tests/integration/test_task_watch_events.py \
  tests/integration/test_task_start_and_status.py \
  tests/integration/test_daemon_stuck_detection.py \
  tests/integration/test_liveness_guard.py \
  tests/integration/test_codex_cli.py \
  tests/integration/test_claude_cli.py \
  tests/integration/test_real_executor_smoke_harness.py \
  -q
```

- [ ] **Step 2: Full test suite**

Run:

```bash
PYTHONPATH=. pytest -q
```

- [ ] **Step 3: Diff hygiene**

Run:

```bash
git diff --check
git status --short
```

- [ ] **Step 4: Privacy scan**

Run:

```bash
rg -n "sk-[A-Za-z0-9]|ANTHROPIC_API_KEY|OPENAI_API_KEY|MOONSHOT|KIMI|api\\.moonshot|Bearer [A-Za-z0-9._-]+|~/.cc-switch|auth\\.json|refresh_token|access_token" .
```

Expected:

- no secrets;
- documentation may mention env var names, but not values;
- raw executor logs and smoke reports are not staged.

- [ ] **Step 5: Real executor verification**

Run report and implementation smoke for all controller-eligible executors installed on the machine:

```bash
PYTHONPATH=. python scripts/smoke_real_executors.py --controller codex --scenario report_smoke
PYTHONPATH=. python scripts/smoke_real_executors.py --controller codex --scenario implementation_smoke
PYTHONPATH=. python scripts/smoke_real_executors.py --controller claude-code --scenario report_smoke
PYTHONPATH=. python scripts/smoke_real_executors.py --controller claude-code --scenario implementation_smoke
```

Expected:

- Codex matrix attempts `antigravity-cli`, `grok-cli`, `claude-code`; external `codex` is suppressed.
- Claude matrix attempts `antigravity-cli`, `grok-cli`, `codex`; external `claude-code` is suppressed.
- Unavailable/auth-failed executors are reported as precise blockers, not generic failure.
- At least one non-self executor per controller produces `adoptable_result=yes` or useful `partial` in both report and implementation scenario, or the final report states exactly which credential/binary blocks remain.

## 15. Acceptance Criteria

The V2.5 implementation is complete only when all statements are true:

- Executor routing, lifecycle, runtime enable/disable, controller priority, self-suppression, health filtering, and startup profile all resolve through one policy view.
- Users can disable, re-enable, reprioritize, and reset an executor per controller without editing source code, skills, lifecycle status, or provider config.
- Users can set/reset a default startup profile per controller/executor and use `--fast` for one-off tiny tasks.
- Direct selection of a policy-disabled executor fails before dispatch with `executor_disabled_by_policy`.
- Default executor launches remain `managed-natural + inherit` and keep full skills/MCP/provider behavior.
- `startup_profile=fast` is explicit, source-tracked, unsupported-by-default, and retained only for executors where smoke proves it is faster without lower-quality evidence.
- `task start` accepts `--task-kind`, `--wait-policy`, controller wait lease, execution budget, and background-ok controls.
- Status JSON exposes task kind, wait policy, budgets, signal state, and controller action.
- `task wait --json` can return a non-failure background-running result for lease expiry.
- Soft no-progress does not kill or fail a complex background-ok task before execution budget.
- Hard execution budget still marks genuinely stalled tasks as `stuck(no_progress_budget_exceeded)`.
- Watch events expose signal changes and artifact byte growth without streaming full logs.
- Non-trivial implementation guidance in Codex / Claude skills uses AGPair external bounded implementation first.
- Native subagents remain documented as fallback/review lanes.
- Isolated code tasks have `task diff`, `task apply --check`, and `task apply`.
- Dirty snapshot baseline is not replayed as worker output during apply.
- Real executor smoke covers both read-only report and tiny mutating implementation paths.
- Docs no longer imply quick no-signal equals automatic abandon for complex tasks.
- No `managed-restricted`, `isolated-bare`, or hidden per-executor special mode is reintroduced.
- No repo commit includes local secrets, CC Switch database contents, raw executor logs, or local user config files.

## 16. Execution Order

Implement in this order:

1. Task 0: resolved executor policy, runtime overlay, and optional startup profile.
2. Task 1: persisted task kind and budgets.
3. Task 2: wait lease outcome.
4. Task 3: signal state and controller action.
5. Task 4: execution budget hard stuck.
6. Task 5: diff/apply adoption for isolated implementation.
7. Task 6: controller skills and hook context.
8. Task 7: real executor smoke.
9. Task 8: docs cleanup.
10. Task 9: local config sync.
11. Task 10: full verification and privacy gate.

Do not start Task 1 before Task 0 is done, because task start, retry, smoke, doctor, and skills must all consume the same executor policy view. Do not start Task 5 before Task 1 is persisted, because diff/apply status needs task kind and background semantics. Do not update local user config before repo skill text and tests are stable.

## 17. Stop Rules

Stop and report a blocker if:

- a schema migration would break existing task DB readability;
- `task wait` loses backward-compatible strict behavior for old commands;
- executor disable/priority behavior requires editing source code or multiple config surfaces;
- a fast startup profile disables skills/MCP/provider behavior by default or cannot be proven faster in smoke;
- `task apply --check` cannot reliably detect conflicts;
- dirty snapshot baseline cannot be separated from worker changes;
- real executor smoke reveals an executor-specific problem that would require reintroducing hidden launch special cases;
- privacy scan finds staged secrets or raw auth/provider state.

If a single external executor is unavailable due to local credentials or missing binary, do not block the entire V2.5 implementation. Record the precise blocker and prove the rest of the controller matrix works.
