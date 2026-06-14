# Resilient External Handoff V2.8 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make AGPair external handoffs fail fast, fail clearly, recover predictably, and preserve useful work so Codex and Claude Code can prefer external agents without frequent manual rescue.

**Architecture:** Keep the AGPair 3.0 model: `managed-natural`, inherited skills/MCP/provider config, external-first routing, lease-based waiting, useful-result-first adoption, and controller verification. Do not add a new orchestration stack. Refactor the remaining rough edges into one recovery decision layer that reads existing task state, liveness, artifacts, policy, and executor health, then exposes a native-agent-like controller action: use, review/apply, continue waiting, retry, switch executor, or fall back to a native helper.

**Tech Stack:** Python 3.12, Typer, SQLite, AGPair task/attempt records, local CLI executors, terminal receipts, runtime liveness, executor policy, isolated worktrees, pytest, real executor smoke harness, Codex / Claude Code skills.

---

## 0. Product Contract

This plan follows `2026-06-12-agent-native-handoff-v2-6.md` and `2026-06-14-fusion-style-fanout-synthesis-v2-7.md`.

V2.6 made useful results more important than perfect protocol shape. V2.7 added multi-lane fanout and synthesis. V2.8 is the reliability pass that makes the single-lane and multi-lane paths feel safe to use every day.

The goal is not "external agents never fail." External CLIs can still fail because of auth, quota, network, provider behavior, tool bugs, bad prompts, or poor model output.

The goal is:

```text
No meaningless hangs.
No confusing failures.
No lost useful output.
No manual guessing about whether to wait, retry, switch, or fall back.
```

Hard requirements:

- Keep active executor ids unchanged: `grok-cli`, `antigravity-cli`, `claude-code`, `codex`.
- Keep controller-aware suppression: Codex does not default to external `codex`; Claude Code does not default to external `claude-code`.
- Keep default executor environment `managed-natural + inherit`; do not turn off skills, MCP, memory, plugins, or provider config by default.
- Do not reintroduce `managed-restricted`, `isolated-bare`, hidden launch modes, capability bundles, Gemini routing, or Antigravity IDE routing.
- Do not silently abandon complex external work only because the first controller lease expired.
- Do not silently auto-switch executors for direct user-selected executors.
- Do not reintroduce strict heading-based task admission. Natural briefs may be normalized when they contain enough real goal, scope, authorization, and exit-signal information.
- Do not mark raw model thoughts, bootstrap logs, or cancellation metadata as a completed report.
- Do not trust external output without controller verification for code changes.
- Do not make AGPair a judge of intellectual quality. AGPair decides whether there is bounded, inspectable, useful evidence.

The product test is:

```text
When an external lane is slow, noisy, malformed, partial, or failed,
does the controller receive one clear low-effort next action?
```

The acceptable actions are:

```text
use_result
review_then_apply
inspect_evidence
wait_background
retry_same_executor
switch_executor
native_fallback
repair_executor
```

### 0.1 Final-Pass Freeze Contract

This is intended to be the last reliability redesign before implementation. The implementer must treat V2.8 as a scope freeze, not as permission to keep adding agent orchestration concepts.

Allowed changes:

- add one canonical `recovery_decision` layer;
- make existing status, wait, watch, smoke, and skill surfaces consume that layer;
- preserve useful stdout/report evidence instead of losing it behind protocol errors;
- make no-progress, auth, binary, timeout, and malformed-output cases clear and recoverable;
- document the current behavior in active user docs and skills.

Disallowed changes:

- new executor launch modes;
- hidden auto-fallback loops;
- new fanout/synthesis semantics;
- new capability profile, skills bundle, or MCP bundle architecture;
- hosted routing or OpenRouter-compatible API work;
- database-heavy reputation/scoring tables;
- edits to historical plan files as if they were current docs.

Success means the controller can decide the next step from `task status --json` or `task wait --json` without reading raw logs for common cases. Raw logs remain available as evidence, not as the normal control plane.

### 0.2 Compatibility Contract

All V2.8 output changes must be additive unless a field is already documented as experimental.

Keep these existing fields and meanings:

- `agent_result`
- `adoption_result`
- `protocol_result`
- `controller_action`
- `recommended_action`
- task phase/status fields
- executor id fields
- artifact and evidence path fields

Add:

- `recovery_decision`
- `signal_state.progress_quality`
- product smoke metrics

Do not remove `retry_or_switch_executor` immediately. Keep it as a compatibility alias where older controller code may still read it, but make new controller guidance prefer the concrete `recovery_decision.action` values.

### 0.3 Single Source Of Truth

Do not duplicate recovery logic across CLI commands and scripts.

`agpair/recovery.py` is the canonical layer for controller-facing action selection. The following surfaces must call into it or consume its serialized result:

- `agpair/cli/task.py`
- `agpair/cli/wait.py`
- `agpair/watch.py`
- `scripts/smoke_real_executors.py`
- workflow fanout/synthesis summaries
- Codex and Claude skill guidance

Existing helpers such as smoke fallback suggestions, liveness summaries, adoption result mapping, and executor policy resolution should be reused or moved behind this layer. Do not copy/paste a second fallback tree into smoke, wait, or workflow code.

### 0.4 Canonical Action Vocabulary

`recovery_decision.action` is the canonical controller-facing recommendation.

Other action-like fields remain for compatibility, but must map into this vocabulary:

| Canonical action | Meaning | Legacy aliases / sources |
| --- | --- | --- |
| `use_result` | External evidence is directly usable after normal controller verification. | `controller_action=use_result`, `verify_then_accept` |
| `review_then_apply` | External code/diff evidence exists and must be reviewed before applying. | `controller_action=review_then_apply` |
| `inspect_evidence` | Evidence may be useful but needs manual inspection before adoption. | `inspect_logs_or_continue_background` when artifacts exist |
| `wait_background` | Controller lease expired but execution budget/activity suggests the task can keep running. | `detach_and_continue`, `continue_waiting` |
| `retry_same_executor` | A fresh attempt on the same executor is the safest next action. | `retry`, same-executor half of `retry_or_switch_executor` |
| `switch_executor` | The current executor produced no useful signal or is unsuitable; another external executor is eligible. | `switch_executor`, switch half of `retry_or_switch_executor` |
| `native_fallback` | No useful external lane remains; use the controller's native helper or direct work. | `fall_back`, `retry_switch_or_native_fallback` with no eligible executor |
| `repair_executor` | Executor setup/auth/binary/provider is broken and must be fixed before retrying. | auth/binary/doctor blockers |

Rules:

- `recommended_action` may remain in `task wait --json` for one compatibility window, but it must be derived from `recovery_decision.action`.
- `controller_action=retry_or_switch_executor` may remain as legacy adoption vocabulary, but new controller guidance must read the concrete recovery action.
- Workflow fanout and synthesis may summarize many lane decisions, but must not emit a separate action vocabulary.
- Human output may use friendly wording, but JSON must use the canonical action strings above.

## 1. Current Evidence And Remaining Gaps

### 1.1 What Already Exists

Current code already has the right foundations:

- `agpair/task_brief.py` normalizes ordinary natural briefs.
- `agpair/terminal_arbitration.py` separates receipt parsing, stdout salvage, and thought-only failure.
- `agpair/agent_result.py` exposes `usable`, `needs_review`, and `blocked`.
- `agpair/adoption.py` maps protocol/adoption evidence into controller-facing results.
- `agpair/runtime_liveness.py` distinguishes silent, heartbeat, workspace, and output activity.
- `agpair/cli/wait.py` supports lease expiry, background waits, soft no-progress, and strict timeouts.
- `agpair/executors/policy.py` resolves executor priority, suppression, lifecycle, and health.
- `scripts/smoke_real_executors.py` can test real external executor paths.
- `agpair workflow fanout` can aggregate multiple external lanes.

### 1.2 What Still Causes Bad User Experience

The remaining product failures are mostly coordination failures:

1. `task status --json` can show activity but still leave the controller unsure whether to wait, retry, switch, or salvage.
2. `_no_useful_signal_agent_result` treats stdout without receipt/report as blocked even when stdout may contain a useful report that should become `needs_review`.
3. `recommended_action` is inconsistent across `task status`, `task wait`, liveness, adoption, and smoke output.
4. Executor switching is still too manual. The controller can infer the next executor, but AGPair should surface the next executor and exact retry command.
5. Executor health has binary/auth/probe data, but recent no-progress, malformed-output, and salvage-quality samples are not yet first-class routing evidence.
6. `soft_no_progress` is sometimes treated as failure and sometimes as background-running. The distinction should be explicit.
7. Real smoke output proves individual mechanics, but does not yet produce a small product dashboard: adoptable rate, first useful signal, no-progress rate, fallback rate, and controller rework.
8. Codex / Claude skills still need a tighter recovery rubric so controllers do not keep waiting, duplicate work, or abandon complex tasks prematurely.

## 2. Target Mental Model

AGPair should act like a local air-traffic controller:

```text
Agent does the work.
AGPair watches the runway.
Controller decides whether to use the result.
```

AGPair should not try to make every executor look identical internally. It should normalize only the surface the controller needs:

```json
{
  "agent_result": {
    "state": "needs_review",
    "controller_action": "switch_executor",
    "summary": "Executor produced only bootstrap noise and exhausted its budget.",
    "hard_blockers": ["no_useful_executor_signal", "execution_budget_exhausted"],
    "soft_warnings": ["bootstrap_noise_only"],
    "evidence_paths": {
      "stdout": ".../stdout.log",
      "stderr": ".../stderr.log"
    }
  },
  "recovery_decision": {
    "action": "switch_executor",
    "next_executor": "antigravity-cli",
    "reason": "grok-cli exhausted budget with no usable report or receipt.",
    "command": "agpair task retry TASK-123 --from-block --executor antigravity-cli"
  }
}
```

Hard gates still block adoption:

- executor binary unavailable;
- auth required or invalid;
- approval required;
- authorization violation;
- scope violation;
- forbidden file mutation;
- isolated worktree apply-check failure;
- report/evidence truly missing;
- process crash with no useful artifacts;
- thought-only / cancelled output with no report or diff.

Soft signals should not block adoption by themselves:

- wrapped JSON;
- mixed text plus JSON;
- schema version alias;
- status alias;
- missing validation text when diff/scope/apply-check are good;
- stderr bootstrap/plugin noise when useful stdout/report/diff exists;
- stdout report without terminal receipt, if it passes report salvage checks.

## 3. File And Responsibility Plan

### Add

- `docs/goals/agpair-resilient-external-handoff-v2-8/baseline.md`
  - Captures the implementation baseline before code changes.
  - Records current policy output, existing smoke behavior, known failing examples, and scope freeze decisions.
  - Serves as the final implementation receipt anchor.

- `agpair/recovery.py`
  - Defines `RecoveryDecision`.
  - Converts `agent_result`, liveness, wait outcome, executor policy, and health into the next controller action.
  - Produces a human-readable reason and a copyable command.

- `tests/unit/test_recovery_decision.py`
  - Unit tests for action selection, next executor selection, and direct-executor no-silent-switch behavior.

- `tests/integration/test_task_recovery_status.py`
  - Status/wait integration tests proving the same recovery decision is visible everywhere.

### Modify

- `agpair/agent_result.py`
  - Extend `ControllerAction` with `wait_background`, `retry_same_executor`, `switch_executor`, `native_fallback`, and `repair_executor`.
  - Add optional `evidence_paths` to `AgentResult`.

- `agpair/adoption.py`
  - Keep useful-result-first behavior.
  - Stop converting salvageable stdout-only evidence into hard blocked when report salvage passes.

- `agpair/runtime_liveness.py`
  - Add a bounded progress quality classifier: `none`, `bootstrap_noise`, `partial_output`, `usable_artifact`.
  - Keep private logs private: expose sizes, timestamps, classifier, and safe excerpts only.

- `agpair/cli/task.py`
  - Add `recovery_decision` to `task status --json`.
  - Show `recovery_action`, `next_executor`, and `retry_command` in human status.
  - Replace local ad-hoc controller action inference with `agpair/recovery.py`.

- `agpair/cli/wait.py`
  - Use the same recovery decision for `wait --json`.
  - Treat controller lease expiry as `wait_background`, not failure.
  - Treat exhausted no-useful-signal budget as `switch_executor` or `retry_same_executor` depending on policy and executor availability.

- `agpair/executors/health.py`
  - Surface recent no-progress and malformed-output counters if they already exist in storage.
  - If persistence is not available, expose this as a computed optional field without adding a heavy new table.

- `agpair/executors/policy.py`
  - Add a helper that returns the next eligible executor for a controller and current failed executor.
  - Respect direct executor selection: direct requests get a recommendation, not silent reroute.

- `scripts/smoke_real_executors.py`
  - Emit product metrics: `adoptable_result`, `agent_result.state`, `controller_action`, `recovery_action`, `time_to_first_useful_signal_seconds`, `no_progress`, `fallback_suggestion`, and `controller_rework`.
  - Reuse the canonical recovery decision instead of maintaining a separate fallback suggestion tree.

- `agpair/workflows/synthesis.py`
  - Replace workflow-only action aliases such as `fall_back` with canonical `native_fallback`.
  - Keep parser compatibility for old synthesis payloads by mapping aliases into canonical values.

- `agpair/workflows/watch.py`
  - Include workflow-level `recovery_decision` derived from lane-level decisions and `panel_result`.

- `agpair/workflows/evidence.py`
  - Reuse lane-level `recovery_decision` when summarizing fanout and synthesis results.
  - Workflow-level summaries may aggregate lane decisions, but must not invent a parallel recovery vocabulary.

- `tests/unit/test_workflow_synthesis.py`, `tests/integration/test_workflow_fanout_synthesis.py`, `tests/integration/test_workflow_watch.py`
  - Prove workflow fanout/synthesis uses the canonical recovery vocabulary.

- `skills/Codex/SKILL.md`
  - Update recovery guidance: wait background for complex active tasks; switch executor on no useful signal; use native helper only after external lanes are unavailable, unsuitable, or low quality.

- `skills/Claude/SKILL.md`
  - Mirror the same recovery guidance, with controller-specific self-executor suppression wording.

- `README.md`, `README.zh-CN.md`, `docs/usage.md`, `docs/usage.zh-CN.md`, `docs/executor-lifecycle.md`
  - Explain recovery decisions and product metrics in current behavior docs.

## 4. Task 0: Capture Baseline, Freeze Scope, And Protect Compatibility

**Files:**

- Create: `docs/goals/agpair-resilient-external-handoff-v2-8/baseline.md`
- Modify: no production code in this task

- [ ] **Step 1: Create the implementation baseline receipt**

Create `docs/goals/agpair-resilient-external-handoff-v2-8/baseline.md` with this structure:

````markdown
# AGPair V2.8 Baseline

## Scope Freeze

V2.8 changes only recovery, observability, stdout/report salvage, smoke metrics, skills, and current behavior docs.

Out of scope: new executor modes, hidden auto-fallback loops, new fanout semantics, hosted routers, capability bundles, MCP/skills isolation redesign, runtime pause/resume approval, and database-heavy scoring.

## Current Policy Snapshot

Paste sanitized output from:

```bash
agpair policy list --controller codex --json
agpair policy list --controller claude --json
```

## Current Smoke Snapshot

Paste sanitized summary output from the existing smoke harness or explain why a real executor is currently unavailable.

## Known Failure Samples

Record one-line sanitized examples for:

- lease expired while task may still be active;
- no useful signal / bootstrap noise;
- stdout report without formal receipt;
- malformed mixed text plus JSON;
- direct executor failure that must not silently switch;
- report-only task that must not mention missing commit.

## Compatibility Fields To Preserve

- `agent_result`
- `adoption_result`
- `protocol_result`
- `controller_action`
- `recommended_action`
- task phase/status fields
- executor id fields
- artifact/evidence path fields

## Verification Receipt

Fill this after implementation with exact commands, pass/fail status, and sanitized real-executor results.
````

Do not paste raw auth files, provider URLs containing credentials, API keys, OAuth tokens, CC Switch database rows, private `.agpair` task logs, or full local home paths.

- [ ] **Step 2: Record current routing behavior**

Run:

```bash
agpair policy list --controller codex --json
agpair policy list --controller claude --json
```

Expected:

- Codex policy suppresses external `codex` by default.
- Claude policy suppresses external `claude-code` by default.
- Active executor ids remain `grok-cli`, `antigravity-cli`, `claude-code`, and `codex`.

If the local machine cannot run `agpair` from PATH, run the repo-local CLI exactly as the project currently documents and record that command in the baseline.

- [ ] **Step 3: Record current smoke and failure behavior**

Run the lightest existing smoke command that works before V2.8 code changes:

```bash
python scripts/smoke_real_executors.py --help
```

If the script already supports fake or dry-run execution, run that too and save the sanitized result in the baseline. If it requires real executor credentials that are not available, record `executor_auth_required` or the exact sanitized blocker. Do not treat unavailable local credentials as a plan failure.

- [ ] **Step 4: Freeze public JSON compatibility**

Before editing production code, inspect the current JSON surfaces:

```bash
rg -n "\"agent_result\"|\"adoption_result\"|\"protocol_result\"|\"controller_action\"|\"recommended_action\"" agpair tests scripts
```

Record the files that currently produce or assert those fields. V2.8 may add `recovery_decision`, but must not remove these existing fields.

- [ ] **Step 5: Run document-only verification**

Run:

```bash
git diff --check docs/superpowers/plans/2026-06-14-resilient-external-handoff-v2-8.md
```

Expected: no whitespace errors.

## 5. Task 1: Add A Single Recovery Decision Model

**Files:**

- Create: `agpair/recovery.py`
- Create: `tests/unit/test_recovery_decision.py`
- Modify: `agpair/agent_result.py`

- [ ] **Step 1: Write recovery decision tests**

Create `tests/unit/test_recovery_decision.py`:

```python
from agpair.recovery import RecoveryInput, choose_recovery_decision


def test_usable_report_recommends_use_result() -> None:
    decision = choose_recovery_decision(
        RecoveryInput(
            task_id="TASK-1",
            controller="codex",
            current_executor="grok-cli",
            requested_executor=None,
            agent_result={
                "state": "usable",
                "controller_action": "use_result",
                "hard_blockers": [],
                "soft_warnings": [],
            },
            liveness_state=None,
            wait_outcome=None,
            execution_budget_exhausted=False,
            next_eligible_executor="antigravity-cli",
        )
    )

    assert decision.action == "use_result"
    assert decision.next_executor is None
    assert decision.command is None


def test_no_signal_with_next_executor_recommends_switch() -> None:
    decision = choose_recovery_decision(
        RecoveryInput(
            task_id="TASK-2",
            controller="codex",
            current_executor="grok-cli",
            requested_executor=None,
            agent_result={
                "state": "blocked",
                "controller_action": "retry_or_switch_executor",
                "hard_blockers": ["no_useful_executor_signal", "execution_budget_exhausted"],
                "soft_warnings": ["bootstrap_noise_only"],
            },
            liveness_state="silent",
            wait_outcome="soft_no_progress",
            execution_budget_exhausted=True,
            next_eligible_executor="antigravity-cli",
        )
    )

    assert decision.action == "switch_executor"
    assert decision.next_executor == "antigravity-cli"
    assert decision.command == "agpair task retry TASK-2 --from-block --executor antigravity-cli"


def test_direct_executor_request_does_not_silently_switch() -> None:
    decision = choose_recovery_decision(
        RecoveryInput(
            task_id="TASK-3",
            controller="codex",
            current_executor="antigravity-cli",
            requested_executor="antigravity-cli",
            agent_result={
                "state": "blocked",
                "controller_action": "retry_or_switch_executor",
                "hard_blockers": ["executor_response_timeout"],
                "soft_warnings": [],
            },
            liveness_state="silent",
            wait_outcome="strict_timeout",
            execution_budget_exhausted=True,
            next_eligible_executor="grok-cli",
        )
    )

    assert decision.action == "retry_same_executor"
    assert decision.next_executor == "grok-cli"
    assert "agpair task retry TASK-3 --from-block --executor grok-cli" in decision.alternative_command


def test_auth_failure_recommends_repair_executor() -> None:
    decision = choose_recovery_decision(
        RecoveryInput(
            task_id="TASK-4",
            controller="codex",
            current_executor="claude-code",
            requested_executor=None,
            agent_result={
                "state": "blocked",
                "controller_action": "retry_or_switch_executor",
                "hard_blockers": ["executor_auth_required"],
                "soft_warnings": [],
            },
            liveness_state=None,
            wait_outcome="terminal_failure",
            execution_budget_exhausted=False,
            next_eligible_executor="grok-cli",
        )
    )

    assert decision.action == "repair_executor"
    assert decision.command == "agpair doctor --fresh"
    assert decision.next_executor == "grok-cli"


def test_approval_required_does_not_silently_switch() -> None:
    decision = choose_recovery_decision(
        RecoveryInput(
            task_id="TASK-5",
            controller="codex",
            current_executor="grok-cli",
            requested_executor=None,
            agent_result={
                "state": "blocked",
                "controller_action": "retry_or_switch_executor",
                "hard_blockers": ["approval_required"],
                "soft_warnings": [],
            },
            liveness_state=None,
            wait_outcome="terminal_failure",
            execution_budget_exhausted=False,
            next_eligible_executor="antigravity-cli",
        )
    )

    assert decision.action == "retry_same_executor"
    assert "--from-block" in decision.command
    assert decision.next_executor == "antigravity-cli"


def test_scope_violation_requires_inspection_or_native_fallback() -> None:
    decision = choose_recovery_decision(
        RecoveryInput(
            task_id="TASK-6",
            controller="codex",
            current_executor="grok-cli",
            requested_executor=None,
            agent_result={
                "state": "blocked",
                "controller_action": "inspect_evidence",
                "hard_blockers": ["scope_violation"],
                "soft_warnings": [],
            },
            liveness_state=None,
            wait_outcome="terminal_failure",
            execution_budget_exhausted=False,
            next_eligible_executor="antigravity-cli",
        )
    )

    assert decision.action in {"inspect_evidence", "native_fallback"}
```

- [ ] **Step 2: Run the recovery tests and verify they fail**

Run:

```bash
PYTHONPATH=. pytest tests/unit/test_recovery_decision.py -q
```

Expected: fail with `ModuleNotFoundError: No module named 'agpair.recovery'`.

- [ ] **Step 3: Extend controller actions**

Modify `agpair/agent_result.py`:

```python
ControllerAction = Literal[
    "use_result",
    "review_then_apply",
    "inspect_evidence",
    "wait_background",
    "retry_same_executor",
    "switch_executor",
    "native_fallback",
    "repair_executor",
    "retry_or_switch_executor",
]
```

Add `evidence_paths` to the dataclass:

```python
    evidence_paths: dict[str, str] | None = None
```

In `to_dict()`, keep `hard_blockers` and `soft_warnings` as lists and include `evidence_paths` only when not `None`.

- [ ] **Step 4: Implement the recovery model**

Create `agpair/recovery.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

RecoveryAction = Literal[
    "use_result",
    "review_then_apply",
    "inspect_evidence",
    "wait_background",
    "retry_same_executor",
    "switch_executor",
    "native_fallback",
    "repair_executor",
]


@dataclass(frozen=True, slots=True)
class RecoveryInput:
    task_id: str
    controller: str | None
    current_executor: str | None
    requested_executor: str | None
    agent_result: dict[str, Any] | None
    liveness_state: str | None
    wait_outcome: str | None
    execution_budget_exhausted: bool
    next_eligible_executor: str | None = None


@dataclass(frozen=True, slots=True)
class RecoveryDecision:
    action: RecoveryAction
    reason: str
    next_executor: str | None = None
    command: str | None = None
    alternative_command: str | None = None

    def to_dict(self) -> dict[str, object | None]:
        return {
            "action": self.action,
            "reason": self.reason,
            "next_executor": self.next_executor,
            "command": self.command,
            "alternative_command": self.alternative_command,
        }


def _strings(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(str(item) for item in value if item)


def _retry_command(task_id: str, executor: str | None) -> str:
    if executor:
        return f"agpair task retry {task_id} --from-block --executor {executor}"
    return f"agpair task retry {task_id} --from-block"


def choose_recovery_decision(data: RecoveryInput) -> RecoveryDecision:
    agent = data.agent_result or {}
    state = str(agent.get("state") or "")
    controller_action = str(agent.get("controller_action") or "")
    blockers = set(_strings(agent.get("hard_blockers")))

    if controller_action == "use_result":
        return RecoveryDecision(
            action="use_result",
            reason="External executor produced usable controller evidence.",
        )
    if controller_action == "review_then_apply":
        return RecoveryDecision(
            action="review_then_apply",
            reason="External executor produced code or diff evidence that must be reviewed before applying.",
        )
    if controller_action == "inspect_evidence":
        return RecoveryDecision(
            action="inspect_evidence",
            reason="External executor produced usable controller evidence.",
        )

    if data.wait_outcome == "controller_lease_expired" and not data.execution_budget_exhausted:
        return RecoveryDecision(
            action="wait_background",
            reason="Controller wait lease expired, but execution budget is still available.",
            command=f"agpair task wait {data.task_id} --json",
        )

    if "executor_auth_required" in blockers or "executor_auth_failed" in blockers:
        return RecoveryDecision(
            action="repair_executor",
            reason="Executor authentication is unhealthy.",
            next_executor=data.next_eligible_executor,
            command="agpair doctor --fresh",
            alternative_command=_retry_command(data.task_id, data.next_eligible_executor),
        )

    if "approval_required" in blockers or "authorization_profile_insufficient" in blockers:
        return RecoveryDecision(
            action="retry_same_executor",
            reason="The task exceeded its dispatch-time authorization and needs a fresh attempt with an expanded authorization profile.",
            next_executor=data.next_eligible_executor,
            command=_retry_command(data.task_id, data.current_executor),
            alternative_command=_retry_command(data.task_id, data.next_eligible_executor),
        )

    if "scope_violation" in blockers or "authorization_violation" in blockers:
        return RecoveryDecision(
            action="inspect_evidence",
            reason="Executor crossed a task boundary; inspect evidence before reusing any output.",
            next_executor=data.next_eligible_executor,
            alternative_command=_retry_command(data.task_id, data.next_eligible_executor),
        )

    no_useful_signal = bool(
        blockers.intersection(
            {
                "no_useful_executor_signal",
                "terminal_receipt_missing",
                "report_missing",
                "execution_budget_exhausted",
                "executor_response_timeout",
                "no_progress_budget_exceeded",
            }
        )
    )

    if state == "blocked" and no_useful_signal:
        if data.requested_executor:
            return RecoveryDecision(
                action="retry_same_executor",
                reason="The explicitly requested executor did not produce useful evidence; AGPair must not silently switch it.",
                next_executor=data.next_eligible_executor,
                command=_retry_command(data.task_id, data.current_executor),
                alternative_command=_retry_command(data.task_id, data.next_eligible_executor),
            )
        if data.next_eligible_executor:
            return RecoveryDecision(
                action="switch_executor",
                reason="The current executor produced no useful evidence within its budget.",
                next_executor=data.next_eligible_executor,
                command=_retry_command(data.task_id, data.next_eligible_executor),
            )
        return RecoveryDecision(
            action="native_fallback",
            reason="No eligible external executor remains; use the controller's native helper or handle directly.",
        )

    if state == "blocked":
        return RecoveryDecision(
            action="retry_same_executor",
            reason="Executor is blocked but may recover with a fresh attempt.",
            command=_retry_command(data.task_id, data.current_executor),
            alternative_command=_retry_command(data.task_id, data.next_eligible_executor),
            next_executor=data.next_eligible_executor,
        )

    return RecoveryDecision(
        action="inspect_evidence",
        reason="AGPair could not classify the result confidently; inspect artifacts before deciding.",
    )
```

- [ ] **Step 5: Run the recovery tests and verify they pass**

Run:

```bash
PYTHONPATH=. pytest tests/unit/test_recovery_decision.py -q
```

Expected: all tests pass.

## 6. Task 2: Surface Recovery Decisions In Status And Wait

**Files:**

- Modify: `agpair/cli/task.py`
- Modify: `agpair/cli/wait.py`
- Modify: `agpair/executors/policy.py`
- Modify: `agpair/workflows/synthesis.py`
- Modify: `agpair/workflows/watch.py`
- Modify: `agpair/workflows/evidence.py`
- Modify: `tests/integration/test_task_start_and_status.py`
- Modify: `tests/integration/test_task_wait.py`
- Modify: `tests/unit/test_workflow_synthesis.py`
- Modify: `tests/integration/test_workflow_fanout_synthesis.py`
- Modify: `tests/integration/test_workflow_watch.py`

- [ ] **Step 1: Add status integration tests**

Append to `tests/integration/test_task_start_and_status.py`:

```python
def test_task_status_json_includes_recovery_decision_for_no_signal(tmp_path: Path, monkeypatch) -> None:
    runner = CliRunner()
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / "home"))

    result = runner.invoke(
        app,
        [
            "task",
            "start",
            "--repo-path",
            str(tmp_path),
            "--controller",
            "codex",
            "--executor",
            "grok-cli",
            "--task-id",
            "TASK-RECOVERY-NO-SIGNAL",
            "--authorization-profile",
            "local_readonly",
            "--completion-policy",
            "report",
            "--execution-budget-seconds",
            "1",
            "--no-wait",
            "--body",
            "Review this directory and report findings.",
        ],
    )
    assert result.exit_code == 0

    tasks = TaskRepository(AppPaths.from_env().db_path)
    task = tasks.get_task("TASK-RECOVERY-NO-SIGNAL")
    assert task is not None
    tasks.mark_acked(
        task_id=task.task_id,
        antigravity_session_id="/tmp/missing-session",
        executor_session_id="/tmp/missing-session",
    )

    status = runner.invoke(app, ["task", "status", "TASK-RECOVERY-NO-SIGNAL", "--json"])
    assert status.exit_code == 0
    payload = json.loads(status.stdout)

    assert payload["recovery_decision"]["action"] in {"switch_executor", "retry_same_executor", "native_fallback"}
    assert payload["recovery_decision"]["reason"]
```

Add any missing imports at the top of the file: `json`, `Path`, `CliRunner`, `app`, `AppPaths`, and `TaskRepository`. If an import already exists, reuse it and do not duplicate it.

- [ ] **Step 2: Add wait JSON integration test**

Append to `tests/integration/test_task_wait.py`:

```python
def test_task_wait_json_uses_same_recovery_decision_for_soft_no_progress(tmp_path: Path, monkeypatch) -> None:
    runner = CliRunner()
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / "home"))

    result = runner.invoke(
        app,
        [
            "task",
            "start",
            "--repo-path",
            str(tmp_path),
            "--controller",
            "codex",
            "--executor",
            "grok-cli",
            "--task-id",
            "TASK-WAIT-RECOVERY",
            "--task-kind",
            "quick_review",
            "--wait-policy",
            "lease",
            "--controller-wait-seconds",
            "0",
            "--authorization-profile",
            "local_readonly",
            "--completion-policy",
            "report",
            "--no-wait",
            "--body",
            "Summarize the repo.",
        ],
    )
    assert result.exit_code == 0

    payload = json.loads(
        runner.invoke(app, ["task", "wait", "TASK-WAIT-RECOVERY", "--json", "--timeout-seconds", "1"]).stdout
    )
    assert payload["recommended_action"] in {"detach_and_continue", "wait_background"}
    assert payload["recovery_decision"]["action"] == "wait_background"
```

- [ ] **Step 3: Add next executor helper**

Modify `agpair/executors/policy.py` with a focused helper:

```python
def next_eligible_executor(
    *,
    controller: str | None,
    current_executor: str | None,
    requested_executor: str | None = None,
    allow_self_executor: bool = False,
) -> str | None:
    if requested_executor:
        return None
    resolved = resolve_controller_policy(
        controller=controller,
        requested_executor=None,
        allow_self_executor=allow_self_executor,
    )
    for candidate in resolved.eligible_executors:
        if candidate != current_executor:
            return candidate
    return None
```

`resolve_controller_policy` already returns `ExecutorPolicyDecision.eligible_executors`; use that field directly and do not add a parallel policy resolver.

If existing executor health data marks a candidate as binary-missing, auth-required, provider-invalid, or recently hard-failed for the same reason, skip that candidate when choosing the recommended next executor. If health data is unavailable, fall back to policy order. Do not add a heavy reputation table for this; use current health/preflight data only.

- [ ] **Step 4: Wire status to recovery**

In `agpair/cli/task.py`, after `agent_result_payload` and `controller_action` are derived, call `choose_recovery_decision(...)` and include:

```python
"recovery_decision": recovery_decision.to_dict(),
```

Human status output should include:

```text
recovery_action: switch_executor
next_executor: antigravity-cli
retry_command: agpair task retry TASK-123 --from-block --executor antigravity-cli
```

Only print `next_executor` and `retry_command` when present.

- [ ] **Step 5: Wire wait JSON to recovery**

In `agpair/cli/wait.py`, when returning JSON, include the same `recovery_decision` shape. For `controller_lease_expired`, set action to `wait_background`.

Do not change non-JSON human wait output beyond adding one concise recommended action line.

- [ ] **Step 6: Wire workflow fanout/synthesis to the same vocabulary**

Modify workflow code so lane-level and panel-level recommendations use canonical recovery action strings:

- map existing `fall_back` to `native_fallback`;
- keep old synthesis payloads readable by accepting `fall_back` as an input alias;
- add `recovery_decision` to `workflow_status_payload(...)` when lane or panel evidence is available;
- make workflow recovery a summary of lane decisions, not a separate policy engine.

Add or update tests:

```python
def test_synthesis_accepts_legacy_fall_back_but_outputs_native_fallback() -> None:
    result = validate_synthesis_result(
        {
            "workflow_id": "WF-1",
            "consensus": [],
            "contradictions": [],
            "unique_insights": [],
            "blind_spots": [],
            "recommended_controller_action": "fall_back",
        }
    )

    assert result["recommended_controller_action"] == "native_fallback"
```

Also update `tests/integration/test_workflow_fanout_synthesis.py` or `tests/integration/test_workflow_watch.py` to assert:

```python
assert payload["recovery_decision"]["action"] in {
    "use_result",
    "review_then_apply",
    "inspect_evidence",
    "wait_background",
    "retry_same_executor",
    "switch_executor",
    "native_fallback",
    "repair_executor",
}
```

- [ ] **Step 7: Run targeted tests**

Run:

```bash
PYTHONPATH=. pytest \
  tests/unit/test_recovery_decision.py \
  tests/unit/test_workflow_synthesis.py \
  tests/integration/test_task_start_and_status.py::test_task_status_json_includes_recovery_decision_for_no_signal \
  tests/integration/test_task_wait.py::test_task_wait_json_uses_same_recovery_decision_for_soft_no_progress \
  tests/integration/test_workflow_fanout_synthesis.py \
  tests/integration/test_workflow_watch.py \
  -q
```

Expected: all tests pass.

## 7. Task 3: Make Stdout Salvage Clear Instead Of Blocked

**Files:**

- Modify: `agpair/terminal_arbitration.py`
- Modify: `agpair/adoption.py`
- Modify: `agpair/cli/task.py`
- Modify: `tests/unit/test_terminal_arbitration.py`
- Modify: `tests/unit/test_adoption_result.py`
- Modify: `tests/integration/test_task_start_and_status.py`

- [ ] **Step 1: Add terminal arbitration tests**

Append to `tests/unit/test_terminal_arbitration.py`:

```python
def test_budget_exhausted_stdout_report_becomes_needs_review_salvage(tmp_path: Path) -> None:
    stdout = tmp_path / "stdout.log"
    stdout.write_text("结论：外部 agent 已完成审查，主要风险是 receipt 缺失。\\n", encoding="utf-8")
    stderr = tmp_path / "stderr.log"
    stderr.write_text("", encoding="utf-8")

    result = arbitrate_terminal_attempt(
        task_id="TASK-SALVAGE",
        attempt_no=1,
        completion_policy="report",
        authorization_profile="local_readonly",
        returncode=1,
        stdout_path=stdout,
        stderr_path=stderr,
        receipt=None,
        process_timed_out=False,
    )

    assert result.receipt["status"] == "EVIDENCE_PACK"
    assert result.receipt["payload"]["arbitration"] == "report_salvage_after_nonzero_exit"
    assert result.agent_result["state"] == "needs_review"
    assert result.agent_result["controller_action"] == "inspect_evidence"


def test_thought_only_cancelled_stdout_stays_blocked(tmp_path: Path) -> None:
    stdout = tmp_path / "stdout.log"
    stdout.write_text('{"text":"I am thinking about the task","stopReason":"Cancelled"}\\n', encoding="utf-8")

    result = arbitrate_terminal_attempt(
        task_id="TASK-THOUGHT",
        attempt_no=1,
        completion_policy="report",
        authorization_profile="local_readonly",
        returncode=1,
        stdout_path=stdout,
        stderr_path=None,
        receipt=None,
        process_timed_out=True,
    )

    assert result.receipt["status"] == "BLOCKED"
    assert result.receipt["payload"]["blocker_type"] == "executor_turn_budget_exhausted"
    assert result.agent_result["state"] == "blocked"
```

Add the public wrapper `arbitrate_terminal_attempt(...)` to `agpair/terminal_arbitration.py`; do not test private process internals in this task. This wrapper must be thin and should reuse existing salvage helpers in `agpair/terminal_arbitration.py`, `agpair/executors/local_cli.py`, or `agpair/task_terminal.py` where available. Do not create a second terminal arbitration stack.

- [ ] **Step 2: Run and verify failing tests**

Run:

```bash
PYTHONPATH=. pytest tests/unit/test_terminal_arbitration.py -q
```

Expected: the new tests fail only where the public arbitration surface is missing or not yet returning `agent_result`.

- [ ] **Step 3: Extend terminal arbitration result**

Modify `agpair/terminal_arbitration.py` so arbitration returns both:

```python
receipt: dict[str, Any]
agent_result: dict[str, Any]
```

Rules:

- readable report stdout after non-zero exit becomes `EVIDENCE_PACK` with `needs_review`;
- thought-only/cancelled stdout remains `BLOCKED`;
- bootstrap-only stderr never counts as report;
- if a structured receipt exists and passes hard gates, it wins over salvage;
- salvage must preserve stdout/stderr paths.

- [ ] **Step 4: Update adoption**

Modify `agpair/adoption.py` so a report salvage receipt produces:

```json
{
  "adoptable_result": "partial",
  "agent_result": {
    "state": "needs_review",
    "controller_action": "inspect_evidence",
    "soft_warnings": ["stdout_report_salvaged", "terminal_receipt_missing"]
  }
}
```

It must not produce `adoptable_result=yes` unless the report is structured enough and hard gates are clean.

- [ ] **Step 5: Update status no-signal logic**

In `agpair/cli/task.py`, change `_no_useful_signal_agent_result` so stdout bytes alone are not hard-blocked when a durable report path or salvage candidate exists.

Expected behavior:

- stdout with report path: `needs_review` / `inspect_evidence`;
- stdout with thought-only or cancellation: `blocked`;
- zero stdout plus bootstrap-only stderr: `blocked` / `switch_executor` or `retry_same_executor`.

- [ ] **Step 6: Run targeted tests**

Run:

```bash
PYTHONPATH=. pytest \
  tests/unit/test_terminal_arbitration.py \
  tests/unit/test_adoption_result.py \
  tests/integration/test_task_start_and_status.py \
  -q
```

Expected: all tests pass.

## 8. Task 4: Make No-Progress Observable And Actionable

**Files:**

- Modify: `agpair/runtime_liveness.py`
- Modify: `agpair/watch.py`
- Modify: `agpair/cli/task.py`
- Modify: `tests/integration/test_liveness_guard.py`
- Modify: `tests/integration/test_task_watch_events.py`
- Modify: `tests/integration/test_task_start_and_status.py`

- [ ] **Step 1: Add signal quality tests**

Append to `tests/integration/test_liveness_guard.py`:

```python
def test_signal_summary_classifies_bootstrap_noise_only(tmp_path: Path) -> None:
    task = make_acked_task_with_session(tmp_path)
    session = Path(task.executor_session_id)
    session.mkdir(parents=True, exist_ok=True)
    (session / "stderr.log").write_text("INFO plugin discovered name=web-dev-tools\\n", encoding="utf-8")

    summary = build_signal_summary(task)

    assert summary.bootstrap_noise_only is True
    assert summary.stdout_bytes == 0
    assert summary.progress_quality == "bootstrap_noise"


def test_signal_summary_classifies_stdout_as_partial_output(tmp_path: Path) -> None:
    task = make_acked_task_with_session(tmp_path)
    session = Path(task.executor_session_id)
    session.mkdir(parents=True, exist_ok=True)
    (session / "stdout.log").write_text("I inspected the files and found one issue.\\n", encoding="utf-8")

    summary = build_signal_summary(task)

    assert summary.progress_quality == "partial_output"
    assert summary.last_signal_type == "stdout"
```

Add this helper inside `tests/integration/test_liveness_guard.py`:

```python
def make_acked_task_with_session(tmp_path: Path) -> TaskRecord:
    repo = _seed_acked_task(tmp_path, task_id="TASK-SIGNAL")
    task = repo.get_task("TASK-SIGNAL")
    assert task is not None
    session = tmp_path / "session-signal"
    session.mkdir(parents=True, exist_ok=True)
    repo.mark_acked(task_id=task.task_id, session_id=str(session))
    updated = repo.get_task(task.task_id)
    assert updated is not None
    return updated
```

- [ ] **Step 2: Extend `SignalSummary`**

Modify `agpair/runtime_liveness.py`:

```python
progress_quality: str = "none"
last_safe_excerpt: str | None = None
```

Allowed `progress_quality` values:

```text
none
bootstrap_noise
partial_output
usable_artifact
```

`last_safe_excerpt` must be bounded to 500 characters and must not include full raw logs.

- [ ] **Step 3: Emit watch event when agent result changes**

Modify `agpair/watch.py` so watch events already carrying `agent_result_changed` also include `recovery_decision` when available.

Expected JSON event:

```json
{
  "event": "agent_result_changed",
  "agent_result": {"state": "blocked"},
  "recovery_decision": {"action": "switch_executor"}
}
```

- [ ] **Step 4: Update status JSON**

Modify `agpair/cli/task.py` to include:

```json
"signal_state": {
  "progress_quality": "bootstrap_noise",
  "last_safe_excerpt": "INFO plugin discovered..."
}
```

Keep existing fields for compatibility.

- [ ] **Step 5: Run targeted tests**

Run:

```bash
PYTHONPATH=. pytest \
  tests/integration/test_liveness_guard.py \
  tests/integration/test_task_watch_events.py \
  tests/integration/test_task_start_and_status.py::test_task_status_json_marks_silent_acked_without_artifacts_as_blocked_signal \
  -q
```

Expected: all tests pass.

## 9. Task 5: Add Explicit Retry/Switch Commands Without Silent Magic

**Files:**

- Modify: `agpair/cli/task.py`
- Modify: `agpair/executors/policy.py`
- Modify: `tests/integration/test_task_retry_from_block.py`
- Modify: `tests/integration/test_task_start_and_status.py`

- [ ] **Step 1: Add retry command tests**

Append to `tests/integration/test_task_retry_from_block.py`:

```python
def test_retry_from_block_can_use_recommended_next_executor(tmp_path: Path, monkeypatch) -> None:
    runner = CliRunner()
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / "home"))

    create_blocked_task_with_executor(
        task_id="TASK-SWITCH",
        repo_path=tmp_path,
        executor="grok-cli",
        blocker_type="no_progress_budget_exceeded",
    )

    result = runner.invoke(
        app,
        [
            "task",
            "retry",
            "TASK-SWITCH",
            "--from-block",
            "--next-executor",
            "--no-wait",
        ],
    )

    assert result.exit_code == 0
    retry = TaskRepository(AppPaths.from_env().db_path).get_task("TASK-SWITCH")
    assert retry is not None
    assert retry.active_executor_backend != "grok-cli"
```

Add this local helper to `tests/integration/test_task_retry_from_block.py` if no equivalent helper already exists:

```python
def create_blocked_task_with_executor(*, task_id: str, repo_path: Path, executor: str, blocker_type: str) -> None:
    paths = AppPaths.from_env()
    repo = TaskRepository(paths.db_path)
    repo.create_task(task_id=task_id, repo_path=str(repo_path), executor=executor)
    receipt = {
        "schema_version": "1",
        "task_id": task_id,
        "attempt_no": 1,
        "review_round": 0,
        "status": "BLOCKED",
        "summary": blocker_type,
        "payload": {"blocker_type": blocker_type, "recoverable": True},
    }
    repo.mark_blocked(
        task_id=task_id,
        reason=blocker_type,
        terminal_receipt_json=json.dumps(receipt),
    )
```

- [ ] **Step 2: Add `--next-executor` option**

Modify `agpair/cli/task.py` retry command:

```text
--next-executor
```

Behavior:

- valid only with `--from-block`;
- ignored when explicit `--executor` is also present, with a clear error;
- uses `next_eligible_executor(...)`;
- fails with `no_eligible_executor` if none is available;
- prints the selected executor in human and JSON output.

- [ ] **Step 3: Keep direct executor safety**

Direct `task start --executor antigravity-cli` must not silently switch. Status may recommend a switch, but execution remains on the requested executor unless the controller explicitly runs retry with another executor.

Add or update a test in `tests/integration/test_task_start_and_status.py`:

```python
def test_direct_executor_failure_recommends_but_does_not_switch(tmp_path: Path, monkeypatch) -> None:
    payload = status_for_direct_failed_executor(tmp_path, monkeypatch, executor="antigravity-cli")

    assert payload["active_executor_backend"] == "antigravity-cli"
    assert payload["recovery_decision"]["action"] in {"retry_same_executor", "repair_executor"}
    assert payload["recovery_decision"].get("next_executor") in {None, "grok-cli", "claude-code"}
```

- [ ] **Step 4: Run targeted tests**

Run:

```bash
PYTHONPATH=. pytest \
  tests/integration/test_task_retry_from_block.py \
  tests/integration/test_task_start_and_status.py \
  -q
```

Expected: all tests pass.

## 10. Task 6: Add Executor Usefulness Metrics To Smoke Output

**Files:**

- Modify: `scripts/smoke_real_executors.py`
- Modify: `tests/integration/test_real_executor_smoke_harness.py`
- Modify: `README.md`
- Modify: `README.zh-CN.md`
- Modify: `docs/usage.md`
- Modify: `docs/usage.zh-CN.md`

- [ ] **Step 1: Add smoke harness metrics test**

Append to `tests/integration/test_real_executor_smoke_harness.py`:

```python
def test_smoke_summary_reports_product_usefulness_metrics(tmp_path: Path) -> None:
    payload = run_fake_smoke_matrix(tmp_path)

    assert "summary" in payload
    summary = payload["summary"]
    assert "completion_rate" in summary
    assert "adoptable_result_rate" in summary
    assert "time_to_first_useful_signal_seconds" in summary
    assert "no_progress_rate" in summary
    assert "fallback_recommended_rate" in summary
    assert "controller_rework_rate" in summary
```

Add this helper to `tests/integration/test_real_executor_smoke_harness.py` if the file does not already expose a parsed fake smoke payload:

```python
def run_fake_smoke_matrix(tmp_path: Path) -> dict:
    repo = _make_repo(tmp_path)
    fake = _fake_executor(tmp_path / "fake-executor")
    result_path = tmp_path / "smoke.json"
    subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--repo-path",
            str(repo),
            "--controller",
            "codex",
            "--executor",
            "grok-cli",
            "--fake-executor",
            str(fake),
            "--json-output",
            str(result_path),
        ],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return json.loads(result_path.read_text(encoding="utf-8"))
```

- [ ] **Step 2: Emit metrics from smoke script**

Modify `scripts/smoke_real_executors.py` so every result row includes:

```json
{
  "executor": "grok-cli",
  "task_id": "TASK-...",
  "agent_result": {"state": "usable", "controller_action": "use_result"},
  "recovery_decision": {"action": "use_result"},
  "time_to_first_useful_signal_seconds": 12.4,
  "no_progress": false,
  "controller_rework": "none"
}
```

If any of these per-row fields already exist, preserve the current implementation and only normalize names or fill missing fields. The main V2.8 smoke change is adding consistent summary metrics and canonical recovery decisions, not rewriting the harness.

Summary fields:

```json
{
  "completion_rate": 1.0,
  "adoptable_result_rate": 1.0,
  "time_to_first_useful_signal_seconds": {"median": 12.4, "max": 33.1},
  "no_progress_rate": 0.0,
  "fallback_recommended_rate": 0.0,
  "controller_rework_rate": 0.0
}
```

Do not print secrets, raw logs, provider URLs, API keys, or local home paths in the report.

Reuse the same recovery helper that powers `task status --json`. If the smoke script already has `_fallback_suggestion` or another local fallback helper, either move that behavior into `agpair/recovery.py` or reduce the local helper to a thin formatter around `RecoveryDecision`. The smoke script must not become a second policy engine.

- [ ] **Step 3: Document the metrics**

Update current user-facing docs only, not old plan files:

- `README.md`
- `README.zh-CN.md`
- `docs/usage.md`
- `docs/usage.zh-CN.md`

Add concise wording:

```text
AGPair health is measured by usable external results, not by dispatch count.
Track completion rate, adoptable-result rate, time to first useful signal,
no-progress rate, fallback recommendation rate, and controller rework rate.
```

- [ ] **Step 4: Run targeted tests**

Run:

```bash
PYTHONPATH=. pytest tests/integration/test_real_executor_smoke_harness.py -q
python scripts/smoke_real_executors.py --help
```

Expected: tests pass and help output renders.

## 11. Task 7: Update Skills So Controllers Recover Consistently

**Files:**

- Modify: `skills/Codex/SKILL.md`
- Modify: `skills/Claude/SKILL.md`
- Modify: `tests/integration/test_codex_cli.py`
- Modify: `tests/integration/test_claude_cli.py`

- [ ] **Step 1: Add skill wording tests if existing tests assert copied snippets**

Use `rg` verification instead of adding brittle full-text skill tests. The required text check is:

```text
wait_background
switch_executor
native_fallback
recovery_decision
```

Run this command after editing both skills:

```bash
rg -n "recovery_decision|wait_background|switch_executor|native_fallback|repair_executor" skills/Codex/SKILL.md skills/Claude/SKILL.md
```

- [ ] **Step 2: Update Codex skill**

Modify `skills/Codex/SKILL.md`:

```markdown
When `task status --json` includes `recovery_decision`, follow it unless controller evidence clearly contradicts it:

- Prefer external lanes for non-trivial bounded work. Use multiple external lanes when the task naturally decomposes or when second opinions are valuable.
- `wait_background`: do not poll in a prompt loop; detach or use `task wait/watch`.
- `switch_executor`: retry with the recommended external executor unless the task was explicitly pinned.
- `retry_same_executor`: retry only when the same executor is likely to recover.
- `repair_executor`: run `agpair doctor --fresh` or ask the user to fix auth/provider.
- `native_fallback`: use Codex native helper or direct work because external lanes are unavailable or not useful.

Do not abandon complex external work only because the controller lease expired. Do not keep waiting after execution budget is exhausted with no useful signal.
```

- [ ] **Step 3: Update Claude skill**

Modify `skills/Claude/SKILL.md` with the same recovery rubric, changing native fallback wording to Claude Code native subagents/background helpers.

- [ ] **Step 4: Run text checks**

Run:

```bash
rg -n "recovery_decision|wait_background|switch_executor|native_fallback|repair_executor" skills/Codex/SKILL.md skills/Claude/SKILL.md
```

Expected: both skill files include the recovery rubric.

## 12. Task 8: Verification Matrix With Real Executors

**Files:**

- Modify: `docs/usage.md`
- Modify: `docs/usage.zh-CN.md`
- Modify: `docs/executor-lifecycle.md`
- Modify: `docs/superpowers/plans/2026-06-14-resilient-external-handoff-v2-8.md` only if implementation discoveries require a small correction.

- [ ] **Step 1: Run focused unit and integration tests**

Run:

```bash
PYTHONPATH=. pytest \
  tests/unit/test_recovery_decision.py \
  tests/unit/test_terminal_arbitration.py \
  tests/unit/test_adoption_result.py \
  tests/integration/test_task_start_and_status.py \
  tests/integration/test_task_wait.py \
  tests/integration/test_real_executor_smoke_harness.py \
  tests/unit/test_workflow_synthesis.py \
  tests/integration/test_workflow_fanout_synthesis.py \
  tests/integration/test_workflow_watch.py \
  -q
```

Expected: all tests pass.

- [ ] **Step 2: Run compile and whitespace checks**

Run:

```bash
python -m compileall -q agpair
git diff --check
```

Expected: both commands exit 0.

- [ ] **Step 3: Run policy and doctor checks**

Run:

```bash
agpair policy list --controller codex --json
agpair policy list --controller claude --json
agpair doctor --fresh --json
```

Expected:

- active executor order is visible;
- Codex self-executor suppression is visible;
- Claude Code self-executor suppression is visible;
- unhealthy executors show explicit blocker classes;
- no secrets are printed.

- [ ] **Step 4: Run Codex-controller real executor smoke matrix**

Run a bounded real smoke that does not mutate project files:

```bash
python scripts/smoke_real_executors.py \
  --repo-path /Users/chenrongwei/Projects/agpair \
  --controller codex \
  --task-kind quick_review \
  --authorization-profile local_readonly \
  --completion-policy report \
  --executor grok-cli \
  --executor antigravity-cli \
  --executor claude-code \
  --json
```

Expected:

- Codex-controller matrix includes `grok-cli`, `antigravity-cli`, and `claude-code`;
- Codex-controller matrix does not default to external `codex`, because Codex has native helpers;
- every healthy executor produces either `agent_result.state in {"usable", "needs_review"}` or a precise `repair_executor` / `switch_executor` / `native_fallback` decision;
- no lane remains ambiguous as only `acked/silent`;
- no report-only failure mentions missing commit;
- summary metrics include adoptable rate and no-progress rate.

If a local executor is unavailable because of auth/provider setup, that is acceptable only if the report says `repair_executor` with a precise blocker. It is not acceptable to report a generic timeout or missing commit.

- [ ] **Step 5: Run Claude-controller real executor smoke matrix**

Run the corresponding Claude-controller matrix:

```bash
python scripts/smoke_real_executors.py \
  --repo-path /Users/chenrongwei/Projects/agpair \
  --controller claude \
  --task-kind quick_review \
  --authorization-profile local_readonly \
  --completion-policy report \
  --executor grok-cli \
  --executor antigravity-cli \
  --executor codex \
  --json
```

Expected:

- Claude-controller matrix includes `grok-cli`, `antigravity-cli`, and external `codex`;
- Claude-controller matrix does not default to external `claude-code`, because Claude Code has native helpers;
- unhealthy local auth/provider state becomes `repair_executor`, not a generic timeout;
- no lane requires the controller to inspect raw logs before knowing whether to wait, retry, switch, or use native fallback.

Native subagents are still allowed as fallback/review resources in both controllers. They are not AGPair executors and should not be represented as executor ids in these smoke matrices.

- [ ] **Step 6: Run a controlled no-progress smoke**

Use the existing fake silent executor harness or add one to `scripts/smoke_real_executors.py` test mode. Verify:

```text
agent_result.state=blocked
recovery_decision.action in {switch_executor, retry_same_executor, native_fallback}
hard_blockers include no_useful_executor_signal or no_progress_budget_exceeded
stderr bootstrap noise is classified as bootstrap_noise
```

- [ ] **Step 7: Privacy scan before commit**

Run:

```bash
git diff --stat
git diff -- README.md README.zh-CN.md docs/usage.md docs/usage.zh-CN.md docs/executor-lifecycle.md skills/Codex/SKILL.md skills/Claude/SKILL.md
ggshield secret scan path . --json
```

Expected:

- no raw `.agpair` state, smoke logs, receipts with private paths, provider config, API keys, OAuth tokens, or CC Switch database contents are staged;
- docs mention behavior and commands, not local credentials.

## 13. Acceptance Criteria

This plan is complete only when all of the following are true:

- `docs/goals/agpair-resilient-external-handoff-v2-8/baseline.md` exists and records sanitized before/after evidence.
- `task status --json` includes one `agent_result` and one `recovery_decision` for active, terminal, failed, and partial tasks.
- `task wait --json` uses the same recovery decision vocabulary as `task status --json`.
- Existing JSON fields such as `agent_result`, `adoption_result`, `protocol_result`, `controller_action`, and `recommended_action` remain available for older consumers.
- Controller lease expiry returns `wait_background`, not failure.
- Budget-exhausted no-signal tasks return `switch_executor`, `retry_same_executor`, or `native_fallback`, with a copyable command when possible.
- Direct executor requests do not silently switch.
- Natural-language task briefs with enough real information are normalized instead of rejected for missing exact headings.
- Useful stdout report salvage becomes `needs_review`, not total failure.
- Thought-only, cancelled, or bootstrap-only output remains blocked.
- Report-only tasks never fail with commit wording.
- Smoke output measures usefulness, not just dispatch.
- Workflow fanout/synthesis summaries reuse lane-level `recovery_decision` instead of inventing separate recovery semantics.
- Codex and Claude skills explain how to follow recovery decisions.
- Codex-controller and Claude-controller real executor smoke matrices prove healthy executors are usable and unhealthy executors fail clearly.
- Common failure decisions do not require raw log inspection; logs are evidence for verification and debugging.

## 14. Non-Goals

- Do not build runtime pause/resume approval across third-party CLIs.
- Do not disable skills/MCP/provider config by default.
- Do not add a new database-heavy scoring framework.
- Do not automatically commit or apply external code changes.
- Do not auto-switch a user-pinned executor without explicit retry.
- Do not silently launch multiple replacement lanes after failure. Recommend or expose copyable retry/switch commands; the controller decides.
- Do not add a hosted model router or OpenRouter-compatible API layer.
- Do not edit old historical plans except this V2.8 plan if implementation discoveries require correction.
- Do not continue adding post-V2.8 features while implementing this plan. New feature work needs fresh evidence from repeated real failures or a separate plan.

## 15. Implementation Order

Use this dependency order. Task numbers are document anchors, not implementation priority:

1. Task 0: baseline, scope freeze, and compatibility inventory.
2. Task 3: stdout salvage and thought-only guard.
3. Task 1: recovery model.
4. Task 2: status/wait/workflow wiring.
5. Task 4: liveness observability.
6. Task 5: explicit retry/switch command.
7. Task 6: smoke metrics.
8. Task 7: skill updates.
9. Task 8: verification matrix.

Each task should be committed separately if the user asks for commits. Do not commit while tests are red. Do not push until privacy scan and real executor smoke have been reviewed.

## 16. Self-Review Checklist

- [ ] The plan does not reintroduce `managed-restricted`, `isolated-bare`, hidden launch modes, Gemini routing, or Antigravity IDE routing.
- [ ] The plan does not add another orchestration vocabulary outside `recovery_decision`.
- [ ] The plan keeps external executors powerful by default through `managed-natural + inherit`.
- [ ] The plan keeps task admission practical and natural-language-friendly.
- [ ] The plan preserves public JSON compatibility for existing controllers.
- [ ] The plan distinguishes controller lease expiry from execution failure.
- [ ] The plan distinguishes useful stdout salvage from thought-only output.
- [ ] The plan provides exact files, test names, commands, and expected results.
- [ ] The plan keeps native subagents available as fallback/review instead of disabling them.
- [ ] The plan measures adoptable external results rather than dispatch count.
- [ ] The plan verifies both Codex-controller and Claude-controller executor matrices.
- [ ] The plan gives Codex/Claude one clear next action for every bad external lane.
