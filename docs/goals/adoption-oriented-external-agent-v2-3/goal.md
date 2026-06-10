# Adoption-Oriented External Agent V2.3 Implementation

## Objective

Implement `docs/superpowers/plans/2026-06-09-adoption-oriented-external-agent-v2-3.md` end to end in AGPair code, tests, docs, and local config sync so external agents produce adoptable review and code results rather than only successful dispatches.

## Original Request

用户要求用 `/goal-prep` 以 v2.3 计划为源，把所有研究发现真正落到代码层面；代码和架构要保持精简，不用的代码和架构应删除，不要降级或注释掉。

## Goal Oracle

The oracle is:

```text
Protocol/adoption model is implemented, mutating bounded external work is verifiably adoptable, status/watch/smoke expose adoption evidence, Codex and Claude skills route non-trivial implementation through AGPair first, local config sync is explicit, focused tests and full tests pass, real executor smoke produces adoptable-result evidence or precise blockers, and privacy scan shows no secrets or raw runtime artifacts are staged.
```

## Hard Constraints

- Source plan: `docs/superpowers/plans/2026-06-09-adoption-oriented-external-agent-v2-3.md`.
- Prefer AGPair external executors for bounded review, smoke, and implementation slices before native subagents.
- Keep AGPair a CLI control plane, not an MCP architecture.
- Do not default-disable skills/MCP or revive bare/restricted/isolated executor special modes.
- Do not revive Gemini as an active new-task executor or Antigravity IDE as a new executor path.
- Keep active executor defaults equal: `managed-natural + inherit + inherit`.
- Keep self-executor suppression in controller routing, not executor launch configuration.
- Delete unused code/paths when a design is superseded; do not leave commented-out or downgrade-only architecture.
- Do not commit raw `.agpair`, `.codex`, `.claude`, smoke logs, local private config, receipts with secrets, or API keys.

## Run Command

```text
/goal Follow docs/goals/adoption-oriented-external-agent-v2-3/goal.md.
```

## Completion Proof

Completion requires:

- focused unit/integration tests for receipt normalization, adoption model, completion policy, scope validation, delegation guard, wait/watch/liveness, CLI status, config sync, and smoke harness;
- `PYTHONPATH=. pytest -q`;
- `git diff --check`;
- real executor smoke for Codex, Claude Code, and diagnostic matrices, or precise blocker receipts;
- local Codex/Claude skill/config sync proof;
- final audit mapping every v2.3 requirement to code/test/doc evidence.
