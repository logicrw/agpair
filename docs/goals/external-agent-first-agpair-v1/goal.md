# External Agent First AGPair V1

## Objective

Execute the existing external-agent-first AGPair V1 implementation plan until AGPair can act as the shared external-worker control plane for Codex and Claude Code.

## Original Request

Use `docs/superpowers/plans/2026-06-02-external-agent-first-agpair-v1.md` to make Codex start executing the AGPair refactor.

## Intake Summary

- Input shape: `existing_plan`
- Audience: user and local Codex/Claude Code workflows
- Authority: `requested`
- Proof type: `test`
- Completion proof: unit and integration tests pass; CLI smoke checks pass; Codex and Claude config dry-runs produce only AGPair-managed entries; fake executor matrix passes; docs are concise/current-state; privacy gate reports no real secrets, private endpoints, local user paths, raw logs, session transcripts, or generated local config staged.
- Goal oracle: run the plan's Task 14 verification suite and privacy gate, then have a final Judge/PM audit map all receipts back to the implementation plan and this charter with `full_outcome_complete: true`.
- Likely misfire: completing only the planning/docs layer, or implementing only the CLI happy path while leaving, daemon, wait, Codex/Claude hooks, legacy Gemini readability, docs, or privacy gates stale.
- Blind spots considered: exact noninteractive executor flags may require discovery; local user config must be changed only through idempotent installer/dry-run paths; OMX source must remain untouched; external executor output must remain untrusted until controller verification.
- Existing plan facts: preserve and validate `docs/superpowers/plans/2026-06-02-external-agent-first-agpair-v1.md`; implement its Tasks 1-14; keep `antigravity-cli`, `grok-cli`, `claude-code`, and `codex` as canonical new executor ids; remove Gemini from new routing while preserving historical inspection; use `ready_for_review`; implement dispatch-time authorization, structured blocked retry, low-noise watch, Codex and Claude hooks/config/skills, concise docs, and GitHub privacy gates.

## Goal Oracle

The oracle for this goal is:

`Task 14 verification from docs/superpowers/plans/2026-06-02-external-agent-first-agpair-v1.md passes, plus final Judge/PM audit confirms Codex and Claude Code can prefer external AGPair agents, wait cheaply, retry approval blocks, preserve raw evidence, and submit a privacy-clean GitHub diff.`

The PM must keep comparing task receipts to this oracle. Planning, discovery, a passing small slice, or a clean-looking board is not enough. The goal finishes only when a final Judge/PM audit maps receipts and verification back to this oracle and records `full_outcome_complete: true`.

## Goal Kind

`existing_plan`

## Current Tranche

Complete the full V1 tranche described in the plan, using successive safe verified Worker packages. The first active task validates that the plan is still executable against current repo state, then the PM should move directly into the largest safe Worker package. Do not stop at "ready for implementation."

## Non-Negotiable Constraints

- Follow the implementation plan unless a Judge/PM receipt records a concrete current-state reason to adjust sequencing.
- Do not modify OMX source in V1.
- Do not check user-local Codex or Claude Code config into the repository.
- Use idempotent config installers/dry-runs for Codex and Claude Code settings.
- Preserve raw executor logs by path; do not add default lossy output compression.
- Treat external executor summaries as untrusted until receipts, diffs, and verification commands are inspected.
- Keep public docs concise and target-state oriented; do not lead with old Antigravity IDE/Gemini history.
- Run privacy checks before any GitHub submission.

## Stop Rule

Stop only when a final audit proves the full original outcome is complete.

Do not stop after planning, discovery, or Judge selection if a safe Worker task can be activated.

Do not stop after a single verified Worker package while required plan tasks remain. Advance to the next highest-leverage safe Worker package unless a phase, risk, rejected-verification, ambiguity, or final-completion review is due.

Do not create one Worker/Judge pair per small helper. Group repeated same-shape work into one coherent Worker package and review the package as a whole.

## Slice Sizing

Safe means bounded, explicit, verified, and reversible. It does not mean tiny.

A good Worker package should complete a meaningful vertical slice: executor routing, state/receipt/watch/retry, controller integration, or docs/privacy/regression. Tiny tasks are acceptable only when they unlock a larger slice or isolate a high-risk failure.

## Canonical Board

Machine truth lives at:

`docs/goals/external-agent-first-agpair-v1/state.yaml`

If this charter and `state.yaml` disagree, `state.yaml` wins for task status, active task, receipts, verification freshness, and completion truth.

## Run Command

```text
/goal Follow docs/goals/external-agent-first-agpair-v1/goal.md.
```

## PM Loop

On every `/goal` continuation:

1. Read this charter.
2. Read `state.yaml`.
3. Run the bundled GoalBuddy update checker when available and mention a newer version without blocking.
4. Re-check the existing implementation plan, current task receipt history, dirty diff, and verification state.
5. Work only on the active board task.
6. Assign Scout, Judge, Worker, or PM according to the task.
7. Write a compact task receipt.
8. Update the board.
9. If safe local work remains, choose the next largest reversible Worker package and continue unless blocked.
10. Finish only with a Judge/PM audit receipt that maps receipts and verification back to the original user outcome and records `full_outcome_complete: true`.
