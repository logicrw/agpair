# AGPair Practical External-First V2.2 Implementation

## Objective

Execute the AGPair Practical External-First V2.2 plan end to end so AGPair reliably acts as the external-agent-first control plane for Codex and Claude Code in real local use.

## Original Request

用户问“怎么让 Codex 执行这个改造”，目标是让 Codex 按最新 `AGPair Practical External-First V2.2 改造计划` 真正完成代码、测试、文档、本地配置同步、真实 executor smoke、隐私检查和提交准备。

## Intake Summary

- Input shape: `existing_plan`
- Audience: AGPair maintainer and local Codex / Claude Code users
- Authority: `requested`
- Proof type: `test`
- Completion proof: the latest V2.2 plan is implemented or explicitly blocked with evidence; targeted and full tests pass; real executor smoke produces adoptable reports or precise blockers; local Codex/Claude config is synced by explicit dry-run/apply; privacy scan shows no secrets/raw runtime artifacts; final audit maps receipts to the V2.2 oracle.
- Goal oracle: current repo plus local installed config proves the V2.2 behavior, not merely that docs were edited.
- Likely misfire: stopping after planning, fake executor tests, README edits, or dispatch-only smoke while AGPair still cannot reliably return adoptable external-agent results.
- Blind spots considered: no-progress must enter the `task wait`/daemon main path, stderr bootstrap noise must not count as progress, Claude Code natural-mode inheritance must be evidenced, local skill sync must be a first-class explicit operation, and external executor equality must not erase adapter-specific command construction.
- Existing plan facts: preserve and execute `docs/superpowers/plans/2026-06-08-practical-external-agent-first-v2-2.md`; it supersedes older V1/V1.1/V2/V2.1 plans for this tranche.

## Goal Oracle

The oracle for this goal is:

`PYTHONPATH=. pytest -q && git diff --check && real executor smoke for Codex, Claude Code, and diagnostic matrices completes with adoptable_result/fallback evidence, followed by local config sync dry-run/apply proof and a privacy-safe final audit.`

The PM must keep comparing task receipts to this oracle. Planning, discovery, a passing tiny slice, or a clean-looking board is not enough. The goal finishes only when a final Judge/PM audit maps receipts and verification back to this oracle and records `full_outcome_complete: true`.

## Goal Kind

`existing_plan`

## Current Tranche

Implement the current V2.2 execution tranche in successive safe slices:

1. validate the latest plan against the current repo and lock the baseline;
2. implement external-agent-first control-plane fixes for completion, receipt parsing, wait/watch/no-progress, auth, privacy, local config sync, and smoke reporting;
3. update concise docs and installed Codex/Claude skills/hooks/config;
4. run targeted tests, full tests, real executor smoke, privacy checks, and final audit.

## Non-Negotiable Constraints

- Use the latest `docs/superpowers/plans/2026-06-08-practical-external-agent-first-v2-2.md` as the source plan.
- Do not revive Gemini for new work or Antigravity IDE as a new executor path.
- Active external executors default to `managed-natural + inherit + inherit`; legacy modes may remain readable but must not be default dispatch routes.
- Do not default to bare/restricted/isolated execution, capability bundles, or MCP architecture.
- Keep controller suppression separate from executor launch configuration.
- For Codex controller, default external executor order is `antigravity-cli`, `grok-cli`, `claude-code`; external `codex` is diagnostic/self-executor only unless explicitly allowed.
- For Claude Code controller, default external executor order is `antigravity-cli`, `grok-cli`, external `codex`; external `claude-code` is diagnostic/self-executor only unless explicitly allowed.
- Codex main remains controller/verifier. Prefer AGPair external executors for delegatable review, smoke, and bounded implementation slices; use native subagents only when external executors are unavailable, unsuitable, or not good enough.
- No implementation task may finish without tests or explicit evidence-backed blocker.
- Do not commit raw `.agpair`, `.codex`, `.claude`, local private config, smoke raw logs, receipts with secrets, or API keys.

## Stop Rule

Stop only when a final audit proves the full original owner outcome is complete.

Do not stop after planning, discovery, or Judge selection if a safe Worker task can be activated.

Do not stop after a single verified Worker package when the broader owner outcome still has safe local follow-up work. Advance the board to the next highest-leverage safe Worker package and continue unless a phase, risk, rejected-verification, ambiguity, or final-completion review is due.

Do not create one Worker/Judge pair per repeated helper. Put repeated same-shape work into one Worker package and review the package as a whole.

## Slice Sizing

Safe means bounded, explicit, verified, and reversible. It does not mean tiny.

A good task is the largest safe useful slice that produces a working behavior improvement, a verified reliability fix, or a deployable config/doc/smoke surface.

Tiny tasks are allowed only when the failure is isolated, the risk is high, the scope is unknown, or the tiny task unlocks a larger slice.

## Canonical Board

Machine truth lives at:

`docs/goals/agpair-practical-external-first-v2-2/state.yaml`

If this charter and `state.yaml` disagree, `state.yaml` wins for task status, active task, receipts, verification freshness, and completion truth.

## Run Command

```text
/goal Follow docs/goals/agpair-practical-external-first-v2-2/goal.md.
```

## PM Loop

On every `/goal` continuation:

1. Read this charter.
2. Read `state.yaml`.
3. Run the bundled GoalBuddy update checker when available and mention a newer version without blocking.
4. Use the latest V2.2 plan as the implementation source of truth.
5. Work only on the active board task.
6. Use AGPair external executors for bounded delegatable work when they can materially help, then consume status/report/receipt/artifacts and accept handled tasks.
7. Write a compact task receipt.
8. Update the board.
9. If safe local work remains, choose the next largest reversible Worker package and continue unless blocked.
10. Finish only with a Judge/PM audit receipt that maps receipts and verification back to the original user outcome and records `full_outcome_complete: true`.
