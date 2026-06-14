# AGPair Fusion-Style Fanout Synthesis V2.7

## Objective

Implement `docs/superpowers/plans/2026-06-14-fusion-style-fanout-synthesis-v2-7.md` end to end so AGPair can run Fusion-style multi-agent fanout, preserve partial external-agent evidence, synthesize lane results, and hand a controller-friendly result back to Codex or Claude Code.

The implementation must stay simple and native-feeling: reuse the current workflow/task/evidence model, avoid patch piles, avoid executor special cases, and avoid adding friction for the controller AI.

## Original Request

实现 Fusion-Style Fanout Synthesis V2.7 Implementation Plan 改造，注意尽可能使用优雅简洁的架构，而不是打很多补丁、制造很多步骤，也不要让 AGPair 变得难用，对主控 AI 不友好。

## Intake Summary

- Input shape: existing implementation plan.
- Authority: user requested implementation.
- Target outcome: AGPair supports a first-class `fanout -> synthesis -> gate` workflow for review, research, implementation candidates, and partial evidence salvage.
- Audience: Codex and Claude Code controllers using AGPair external agents.
- Main risk: implementing the plan as a heavy orchestration layer instead of a small extension of the existing workflow model.

## Goal Oracle

The goal is complete only when all of these are true:

- `agpair workflow fanout --dry-run --json` produces a readable, deterministic workflow description with lanes, synthesis, and gate.
- Unit/integration tests cover lane cards, synthesis results, panel result gates, partial-output salvage, source policy, rubrics, and CLI dry-run behavior.
- Realistic smoke or harness evidence proves at least one multi-lane fanout path can produce adoptable evidence without relying on a single perfect executor.
- Docs and skills explain how a controller should use fanout without making AGPair harder to operate.
- Privacy/secret checks pass before any GitHub submission.

## Non-Negotiable Constraints

- Do not turn AGPair into an online benchmark/router or black-box judge.
- Do not add OpenRouter/Fusion as a dependency; use the product lesson, not their service.
- Do not hide executor failures. Preserve stdout/stderr/report excerpts as evidence and let synthesis use degraded lanes.
- Do not weaken existing safety gates. Synthesis can recommend, but controller verification remains final.
- Do not special-case executor identities except where current routing rules already require controller self-worker suppression.
- Prefer refactoring, deletion, and reuse over new layers. If a slice needs many new modules, pause for Judge review.
- Keep the controller UX small: one high-level fanout command/preset should be enough for common use.

## Run Command

```text
/goal Follow docs/goals/agpair-fusion-style-fanout-synthesis-v2-7/goal.md.
```

## PM Loop

Use `docs/goals/agpair-fusion-style-fanout-synthesis-v2-7/state.yaml` as board truth. The first active task validates the V2.7 plan against the current repo and chooses the largest safe Worker package before implementation starts.
