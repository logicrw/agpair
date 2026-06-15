# AGPair V2.8 Final Resilient External Handoff

## Objective

Treat `docs/superpowers/plans/2026-06-14-resilient-external-handoff-v2-8.md` as the final AGPair reliability redesign and implement it end to end so external agents are practical, recoverable, and controller-friendly without adding another orchestration layer.

## Original Request

Use GoalBuddy to make the V2.8 AGPair plan the final改造 and彻底解决所有问题.

## Intake Summary

- Input shape: `existing_plan`
- Audience: AGPair users through Codex and Claude Code controllers
- Authority: `requested`
- Proof type: `test`
- Completion proof: V2.8 acceptance criteria pass with focused pytest, compileall, diff checks, privacy scan, and real executor smoke matrices or precise repair blockers.
- Goal oracle: The V2.8 plan's acceptance criteria are implemented in code/docs/skills, verified by tests and smoke evidence, and a final Judge/PM audit records `full_outcome_complete: true`.
- Likely misfire: Completing planning, wrappers, or partial docs while AGPair still has ambiguous `acked/silent`, lost stdout evidence, fragmented action vocabulary, or unreliable controller recovery.
- Blind spots considered: scope creep, hidden fallback modes, public JSON compatibility, workflow/fanout action drift, real executor auth variance, privacy leakage, and over-strict task admission.
- Existing plan facts: Preserve and execute `docs/superpowers/plans/2026-06-14-resilient-external-handoff-v2-8.md`; keep AGPair 1.0 `managed-natural + inherit`; keep executor ids `grok-cli`, `antigravity-cli`, `claude-code`, `codex`; keep Codex/Claude self-executor suppression; do not reintroduce Gemini, Antigravity IDE, hidden launch modes, capability bundles, or runtime pause/resume approval.

## Goal Oracle

The oracle for this goal is:

`The V2.8 plan acceptance criteria are satisfied by current repo evidence: recovery_decision is the canonical control plane across task status/wait/watch/workflow/smoke/skills; useful stdout/report evidence is preserved; no-progress/auth/scope/approval failures produce clear next actions; public JSON compatibility remains; real executor smoke either succeeds or returns precise repair_executor blockers; final privacy scan is clean.`

The PM must keep comparing task receipts to this oracle. Planning, discovery, a passing tiny slice, or a clean-looking board is not enough. The goal finishes only when a final Judge/PM audit maps receipts and verification back to this oracle and records `full_outcome_complete: true`.

## Goal Kind

`existing_plan`

## Current Tranche

Complete the whole V2.8 reliability tranche in successive safe verified slices. This is intended to be the final AGPair reliability redesign before stopping feature churn. The run should continue through implementation, verification, docs/skills sync, privacy review, and final audit unless a concrete blocker requires user input.

## Non-Negotiable Constraints

- Follow `docs/superpowers/plans/2026-06-14-resilient-external-handoff-v2-8.md` as the source plan.
- Use external AGPair executors first for non-trivial review, implementation, and verification slices when suitable; Codex main remains controller/verifier.
- For code-writing external slices, prefer bounded `--completion-policy evidence --isolated-worktree`, then inspect protocol/adoption results before accepting or adopting.
- Use native subagents only when AGPair is unavailable, unsuitable, or external results are not good enough.
- Do not add new executor modes, hidden auto-fallback loops, capability/MCP bundle architecture, hosted model routing, Gemini routing, or Antigravity IDE routing.
- Do not silently auto-switch direct user-selected executors.
- Do not make task admission rigid again; useful natural-language briefs must normalize when they contain enough real information.
- Preserve public JSON compatibility unless a field is explicitly experimental.
- Do not commit or push until final verification and privacy scan are clean and the user asks for it.
- Never print or commit API keys, OAuth tokens, CC Switch data, private `.agpair` logs, raw provider config, or local secrets.

## Stop Rule

Stop only when a final audit proves the full original outcome is complete.

Do not stop after planning, discovery, or Judge selection if a safe Worker task can be activated.

Do not stop after a single verified Worker package when the broader V2.8 outcome still has safe local follow-up work.

Do not create one Worker/Judge pair per helper or file. Implement coherent packages and verify each package as a whole.

If one executor, smoke lane, auth provider, or real CLI is unavailable, record the precise blocker and keep advancing every local non-destructive task that can still move the outcome.

## Slice Sizing

Safe means bounded, explicit, verified, and reversible. It does not mean tiny.

A good task is the largest safe useful slice. For this goal, good slices are baseline capture, salvage/recovery model, status/wait/workflow wiring, liveness/smoke metrics, skills/docs sync, and final real-executor verification.

## Canonical Board

Machine truth lives at:

`docs/goals/agpair-v2-8-final-resilient-handoff/state.yaml`

If this charter and `state.yaml` disagree, `state.yaml` wins for task status, active task, receipts, verification freshness, and completion truth.

## Run Command

```text
/goal Follow docs/goals/agpair-v2-8-final-resilient-handoff/goal.md.
```

## PM Loop

On every `/goal` continuation:

1. Read this charter.
2. Read `state.yaml`.
3. Re-check the V2.8 plan, current git status, active AGPair config, and the current active task.
4. Work only on the active board task.
5. Prefer AGPair external executor lanes for non-trivial review/implementation/verification, then verify as controller.
6. Write a compact task receipt.
7. Update the board.
8. Continue to the next safe Worker package unless blocked by a real stop condition.
9. Finish only with a Judge/PM audit receipt that maps current evidence back to the Goal Oracle and records `full_outcome_complete: true`.
