# AGPair Native-Feel External Agent V2.5

## Objective

Implement the V2.5 AGPair改造，使外部 agent 在 Codex / Claude Code 中更接近原生子 agent 体验：可插拔、可持续等待、可采用代码结果、可真实 smoke 验证，同时保持架构简洁。

## Original Request

开始改造 V2.5。

## Intake Summary

- Input shape: `existing_plan`
- Audience: AGPair 的 Codex / Claude Code controller 用户
- Authority: `requested`
- Proof type: `test`
- Completion proof: V2.5 计划中的功能落到代码、文档、skills、本地配置和验证链；单元/集成测试通过；真实外部 executor smoke 覆盖只读与代码写入路径；隐私检查通过。
- Goal oracle: `/goal` 持续对照 `docs/superpowers/plans/2026-06-11-native-feel-external-agent-v2-5.md`，每个任务收据都必须说明覆盖了计划中的哪些要求、跑了哪些验证、剩余风险是什么。
- Likely misfire: 只改文档或只加新配置层，却没有让 task start / retry / doctor / smoke / skills 实际消费统一 policy；或为了极简牺牲可插拔和极速启动的可用性。
- Blind spots considered: 未提交的既有改动、外部 executor auth/binary 差异、真实 smoke 的不稳定性、隐私泄漏、GoalBuddy board 与 repo 实际状态不一致。
- Existing plan facts: `docs/superpowers/plans/2026-06-11-native-feel-external-agent-v2-5.md` 是执行计划；Task 0 必须先做，统一 executor policy overlay 和 startup profile 后再做 wait/adoption/smoke。

## Goal Oracle

The oracle for this goal is:

`V2.5 plan coverage matrix + passing targeted/full tests + real executor smoke evidence + privacy-safe git diff`

The PM must keep comparing task receipts to this oracle. Planning, discovery, a passing tiny slice, or a clean-looking board is not enough. The goal finishes only when a final Judge/PM audit maps receipts and verification back to this oracle and records `full_outcome_complete: true`.

## Goal Kind

`existing_plan`

## Current Tranche

Complete successive safe verified implementation slices from the V2.5 plan until all required behavior is implemented, documented, locally configured, tested, real-smoked where possible, and privacy-checked. Do not stop after plan validation or one package if safe local follow-up work remains.

## Non-Negotiable Constraints

- Preserve existing user changes; do not revert unrelated dirty files.
- Use AGPair external executors first for delegatable implementation/review/test slices when suitable, while Codex remains controller/verifier.
- Keep architecture simple by consolidating duplicated paths; do not reduce required functionality for simplicity.
- Default executors remain `managed-natural + inherit`; do not reintroduce `managed-restricted`, `isolated-bare`, hidden launch special cases, Gemini routing, or Antigravity IDE routing.
- Runtime pluggability must be genuinely convenient: list, inspect, enable, disable, reprioritize, set/reset startup profile, and reset controller policy without source edits.
- Fast startup must be convenient (`--fast`) but explicit, measured, and not a silent capability downgrade.
- Mutating external work should use bounded evidence slices and isolated worktrees where appropriate.
- Do not commit raw executor logs, local user config, provider secrets, CC Switch DB contents, or private receipts.

## Stop Rule

Stop only when a final audit proves the full original outcome is complete.

Do not stop after planning, discovery, or Judge selection if a safe Worker task can be activated.

Do not stop after a single verified Worker package when the broader owner outcome still has safe local follow-up work.

## Canonical Board

Machine truth lives at:

`docs/goals/agpair-native-feel-external-agent-v2-5/state.yaml`

If this charter and `state.yaml` disagree, `state.yaml` wins for task status, active task, receipts, verification freshness, and completion truth.

## Run Command

```text
/goal Follow docs/goals/agpair-native-feel-external-agent-v2-5/goal.md.
```
