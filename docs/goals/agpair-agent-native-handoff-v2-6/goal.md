# AGPair Agent-Native Handoff V2.6

## Objective

Implement `docs/superpowers/plans/2026-06-12-agent-native-handoff-v2-6.md` end to end so AGPair external executors feel close to native subagents for ordinary read-only and code-writing delegation.

## Original Request

开始改造 `2026-06-12-agent-native-handoff-v2-6.md`。

## Intake Summary

- Input shape: `existing_plan`
- Audience: AGPair users running Codex or Claude Code as controller agents.
- Authority: `requested`
- Proof type: `test`
- Completion proof: focused tests, full pytest, compile/checks, docs/skills review, and real executor report + implementation smoke for `antigravity-cli`, `grok-cli`, and `claude-code` all pass with native-like adoption semantics.
- Goal oracle: current code and docs satisfy the V2.6 plan acceptance criteria, especially relaxed admission, useful-result-first adoption, `agent_result`, salvage behavior, diff/apply adoption, and real executor smoke.
- Likely misfire: only updating docs or tests while leaving AGPair behavior still rigid, or making the architecture more complex instead of removing friction.
- Blind spots considered: preserving safety gates, avoiding new storage complexity, not confusing raw model thoughts with completed reports, keeping native subagents as fallback, and not overfitting to one executor.
- Existing plan facts: preserve and execute `docs/superpowers/plans/2026-06-12-agent-native-handoff-v2-6.md` as the implementation source of truth.

## Goal Oracle

The oracle for this goal is:

`PYTHONPATH=. pytest -q` passes, `python -m compileall agpair` passes, `git diff --check` passes, docs/skills no longer describe stale strictness, and real executor report + implementation smoke prove `agent_result.state=usable` / `adoptable_result=yes` for active Codex-controlled external executors where the local environment is healthy.

The PM must keep comparing task receipts to this oracle. Planning, discovery, a passing tiny slice, or a clean-looking board is not enough. The goal finishes only when a final Judge/PM audit maps receipts and verification back to this oracle and records `full_outcome_complete: true`.

## Goal Kind

`existing_plan`

## Current Tranche

Execute V2.6 as a complete local implementation tranche: validate the plan against current code, implement the largest safe slices, update tests/docs/skills, run focused and full verification, run real executor smoke, and finish with a privacy-safe final audit.

## Non-Negotiable Constraints

- Preserve user work and existing dirty changes; do not revert unrelated changes.
- Use AGPair external executors first for non-trivial bounded implementation/review slices when healthy; Codex remains controller and verifier.
- Do not reintroduce fast/restricted/bare executor modes.
- Keep default executor environments `managed-natural`.
- Keep safety gates: authorization, scope, broad repo guardrail, isolated worktree boundaries, and apply-check.
- Relax only the wrong gates: section-heading admission, perfect receipt formatting, mandatory validation text when diff/scope/apply-check are enough, and total failure on salvageable non-zero exits.
- Do not commit smoke logs, temp worktrees, raw receipts with secrets, or credentials.

## Stop Rule

Stop only when a final audit proves the full original outcome is complete.

Do not stop after planning, discovery, or Judge selection if a safe Worker task can be activated.

Do not stop after a single verified Worker package when V2.6 still has safe local follow-up work. Advance the board to the next highest-leverage safe Worker package and continue unless a phase, risk, rejected-verification, ambiguity, or final-completion review is due.

## Slice Sizing

Safe means bounded, explicit, verified, and reversible. It does not mean tiny.

A good Worker slice should complete a coherent V2.6 behavior package: admission normalization, agent result/adoption semantics, terminal salvage, git evidence inference, controller status/wait/watch surfaces, docs/skills, or real smoke verification.

## Canonical Board

Machine truth lives at:

`docs/goals/agpair-agent-native-handoff-v2-6/state.yaml`

If this charter and `state.yaml` disagree, `state.yaml` wins for task status, active task, receipts, verification freshness, and completion truth.

## Run Command

```text
/goal Follow docs/goals/agpair-agent-native-handoff-v2-6/goal.md.
```

## PM Loop

On every `/goal` continuation:

1. Read this charter.
2. Read `state.yaml`.
3. Re-check the V2.6 plan and current task.
4. Work only on the active board task.
5. Prefer AGPair external executors for non-trivial bounded work, then verify as controller.
6. Write a compact task receipt.
7. Update the board.
8. Continue to the next safe Worker package until the oracle is satisfied.
9. Finish only with a Judge/PM audit receipt that maps receipts and verification back to the original user outcome and records `full_outcome_complete: true`.
