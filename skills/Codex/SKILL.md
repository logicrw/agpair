---
name: agpair-codex
description: "Use when Codex handles non-trivial coding, refactor, test-fix, research, review, or multi-file work where AGPair external CLI agents can produce implementation candidates, alternative analysis, or verification evidence."
---

# AGPair for Codex

IRON LAW: Non-trivial work requires an explicit routing budget. Use external AGPair lanes by default, but fan out only when each lane has a distinct role, scope, hypothesis, or verification value. Multiple `grok-cli` lanes are valid when they are not duplicate prompts.

Default: external AGPair executor first for non-trivial work. Codex remains the controller and verifier.

Actively outsource low-value, repetitive, time-consuming, or easily verifiable work through AGPair: repo scans, alternative reviews, focused test-fix attempts, multi-file mechanical edits, smoke checks, and implementation slices with clear acceptance criteria. For ordinary non-trivial work, start with a strong external lane: `grok-cli` and `antigravity-cli` are peer first choices. Use both when independent evidence, implementation contrast, or latency hedging is useful. It is acceptable to run several attempts from the same executor at once for parallel review, competing implementation approaches, or file-sliced work, but each attempt needs a distinct task id, role, prompt, or scope.

Use direct Codex edits for tiny local fixes, sensitive judgment-heavy work, or when AGPair is unavailable. If skipping external AGPair entirely for non-trivial work, state the skip reason before proceeding. Use Codex native subagents as narrow controller-side reviewers/helpers when they add clear value, and as fallback when external executors are unavailable, unsuitable, or not good enough.

## Normal Task

For ordinary tasks, send a clear natural brief. AGPair normalizes useful
briefs and should not reject work merely because a section heading is missing.
Do not pass placeholders like `<brief>`, `todo`, or `fix this`.

External lanes do not share Codex's hidden conversation state. The brief must
stand alone: include the goal, relevant paths, constraints, forbidden scope,
expected output, and validation or evidence requirements. If the worker would
need unstated chat context to succeed, either add that context to the brief or
keep the work in Codex/native helper lanes.

For complex mutating work, prefer this structured shape because it gives the
external executor tighter scope and gives the controller better evidence:

```text
Goal:
State the concrete outcome.

Scope:
Allowed files/areas:
Forbidden files/areas:

Required changes:
Describe the expected edit, or say: None. This is report-only. Do not edit files.

Exit criteria:
List required verification, report format, and expected AGPair evidence.
```

```bash
agpair task start \
  --repo-path "$REPO" \
  --controller codex \
  --executor grok-cli \
  --task-kind quick_review \
  --wait-policy lease \
  --authorization-profile local_readonly \
  --completion-policy report \
  --body "$BRIEF"
```

`task start` waits cheaply according to the selected task kind. Lease-based tasks may return a structured background-running result while the executor continues. After completion, inspect status, diff, receipt, raw logs, and required evidence before reporting success.

Default executor environments are `managed-natural` for `grok-cli`, `antigravity-cli`, and healthy `claude-code`: AGPair manages state and evidence, while the external CLI keeps its normal skills, MCP, memory, plugins, and provider config. If an external attempt fails or is low quality, retry naturally, switch to another external executor, or use Codex native subagents as fallback/review.

AGPair external-first routing applies to controller sessions. AGPair-started executor, probe, smoke, and retry processes suppress AGPair client hooks to avoid recursive delegation, but external workers still inherit their normal CLI capabilities, skills, MCP, plugins, memory, and provider config unless an explicit diagnostic mode says otherwise.

Use `agpair policy list --controller codex --json` to inspect the effective executor order, suppression, and lifecycle state. Use `agpair policy disable/enable/priority/reset` for pluggable runtime changes instead of editing source.

## Routing Budget And Fanout

Before doing non-trivial work directly or using native subagents, answer:

1. Which external lane is most likely to produce an adoptable result?
2. What is the routing budget: one lane, several role-based lanes, direct work,
   or a native helper?
3. For every extra lane, what distinct role does it play: implementation,
   review, test-fix, research, adversarial critique, or file-sliced work?
4. If the task mutates files, can every mutating lane use an isolated worktree
   or a disjoint scope?

Default to one high-likelihood external lane for ordinary work. Dispatch
additional lanes when they are role-distinct and the added evidence is likely
to reduce rework, risk, or uncertainty. Do not cap useful fanout by habit, but
also do not add lanes as ceremony. Stop when an added lane would only duplicate
another lane's prompt, scope, or expected evidence.

Skip AGPair entirely only when one of these is true, and state the reason:

- the task is tiny or mostly mechanical;
- the task is sensitive or depends heavily on current controller context;
- external executors are unhealthy, unavailable, or already produced low-quality
  output for this task;
- safe isolation is unavailable for mutating work and a report-only external
  lane would not help;
- the best path is a narrow controller-side check or native helper.

Recommended shapes:

| Work type | Default external shape | Codex controller lanes |
| --- | --- | --- |
| Non-trivial research/review/diagnosis/design | 1-2 strong external lanes by default; 2-4 role-based lanes for high-risk or multi-angle work | `grok-cli` and `antigravity-cli` are peer first lanes; add another `grok-cli` or `claude-code` only with a distinct angle |
| Non-trivial implementation/refactor/test-fix | 1 isolated implementation lane first; add a challenger or review/test lane when risk justifies it | `grok-cli` or `antigravity-cli` implementation lane first; use the other as challenger/reviewer when useful; add `claude-code` for high-value escalation |
| Tiny/sensitive/context-heavy work | 0-1 lane | State the skip reason if AGPair is skipped, then work directly or use a narrow helper |

Give each external lane the same goal, explicit scope, and comparable exit
criteria. For concurrent lanes, use `--no-wait`, then `task wait` / `task watch`
to collect evidence without burning controller turns. For one lane, normal
`task start` waiting is fine.

For code-writing work, fanout is still useful, but every mutating lane must use
`--isolated-worktree` or a disjoint repo/worktree. Do not run multiple mutating
executors in the controller worktree. Safe patterns:

- one primary implementation lane plus one external review/test lane;
- two alternative implementation candidates in separate isolated worktrees;
- multiple `grok-cli` or same-executor instances with distinct task ids,
  prompts, file slices, or acceptance criteria; mutating instances need
  disjoint scopes or separate isolated worktrees.

Codex native subagents remain available as review or narrow helper lanes when
they add useful controller-side verification, and as fallback when external
output is unavailable, unsuitable, or not good enough. Prefer external AGPair
lanes for primary execution, but do not avoid a native helper when it can run in
parallel and materially improve verification. Native helpers are especially
appropriate for hidden-context reasoning, quick local sanity checks, integrating
multiple external outcomes, or reviewing an external diff before adoption.

Anti-patterns:

- Do not use fanout as ceremony when added lanes no longer improve the result.
- Do not run duplicate external lanes with the same prompt, scope, and exit
  criteria.
- Do not run multiple mutating lanes in the controller worktree.
- Do not keep waiting on a silent or low-quality lane after another lane has
  produced adoptable evidence.
- Do not treat task count as success; success is usable evidence that reduces
  controller rework.

Pre-delivery check:

- [ ] Routing budget made: single external lane, role-based fanout, direct work,
      or native helper.
- [ ] Every external brief is self-contained and not dependent on hidden chat
      context.
- [ ] AGPair skip reason stated if no external lane was used for non-trivial work.
- [ ] Every mutating external lane is isolated or disjoint.
- [ ] `task status --json` inspected for each lane.
- [ ] Useful evidence was accepted/adopted or explicitly rejected.
- [ ] Final answer distinguishes external evidence from controller judgment.

## Fusion-Style Workflow Fanout

For high-value research, review, design, implementation choices, or competing
candidate work, prefer a fanout-synthesis workflow instead of manually starting
unrelated tasks. The useful pattern is parallel lane tasks, one synthesis node,
and a controller gate:

```bash
agpair workflow fanout \
  --controller codex \
  --mode review \
  --topic "$TOPIC" \
  --lane grok-cli:primary \
  --lane grok-cli:adversarial \
  --lane antigravity-cli:second-opinion \
  --repo-path "$REPO" \
  --wait --json
```

For implementation or test-fix panels, mutating lanes are isolated by the
fanout preset. Keep the scope explicit:

```bash
agpair workflow fanout \
  --controller codex \
  --mode implementation \
  --topic "$TOPIC" \
  --scope "$ALLOWED_SCOPE" \
  --lane grok-cli:candidate-a \
  --lane antigravity-cli:candidate-b \
  --lane claude-code:reviewer \
  --isolated-worktree \
  --repo-path "$REPO" \
  --dry-run --json
```

Read `panel_result`, `lane_cards`, `synthesis_result`, and `evidence_path`
before answering. The synthesis result is evidence, not final truth. Controller
verification still decides whether to use, apply, retry, switch executor, or
fall back to a native helper.

## Bounded Implementation

For non-trivial implementation, refactor, or test-fix work, dispatch one bounded
isolated implementation slice first unless AGPair is unavailable, unsafe, or
already low quality for this task. Choose `grok-cli` or `antigravity-cli` based
on fit and current health; use both when contrast or latency hedging is worth
the extra coordination. Add a second external implementation or review/test lane
only when risk, uncertainty, or verification value justifies it:

```bash
agpair task start \
  --repo-path "$REPO" \
  --controller codex \
  --executor grok-cli \
  --task-kind implementation \
  --wait-policy lease \
  --authorization-profile local_mutating \
  --completion-policy evidence \
  --isolated-worktree \
  --body "$BRIEF"
```

Use a brief with explicit allowed files, forbidden files, required changes, validation command, and exit criteria. The external worker returns `changed_files`, `validation` or `validation_not_run`, `scope_violations`, report text, and raw evidence paths. Codex integrates or rejects the result in the main worktree after verification.

For isolated mutating evidence/commit tasks, AGPair defaults to `--dirty-snapshot tracked`: tracked staged/unstaged controller changes are copied into the executor worktree before launch. Ignored and untracked files are not copied; use `--dirty-snapshot off` when the worker should start from committed HEAD only.

When `--wait-policy lease` expires and the task is still alive, detach and continue or run a native reviewer in parallel. Do not abandon a complex external task solely because it has not produced a quick final report.

## Parallel Or Async

```bash
agpair task start --repo-path "$WT_A" --body "$BRIEF_A" --no-wait
agpair task start --repo-path "$WT_B" --body "$BRIEF_B" --no-wait
agpair task watch <TASK_ID> --json
agpair task wait <TASK_ID> --json
```

Each `BRIEF_*` must be clear enough to identify the goal, scope, allowed
changes, and expected evidence. Use the structured shape for mutating work
when those boundaries are known.

Do not use repeated Codex prompts as a polling loop. Use `agpair task watch <TASK_ID> --json` or `agpair task wait <TASK_ID>` for low-token waiting.

`watch --json` emits state changes and raw evidence paths; it does not stream full logs. Do not run raw executor output through lossy compression by default.

`wait --json` reports `outcome`, `agent_result`, `recovery_decision`, and whether the controller wait lease expired. Treat `controller_lease_expired` and `soft_no_progress` as background-running outcomes when `recovery_decision.action=wait_background`; otherwise follow `switch_executor`, `native_fallback`, `repair_executor`, or `retry_same_executor`. Inspect `task status --json` rather than burning model turns in a polling loop.

Use Codex App thread automation only for very long tasks that should wake the same thread later.

## Codex Integration

Install or print the managed hooks snippet:

```bash
agpair codex config
agpair codex config --install --scope project --repo-path "$REPO" --sync-skill
```

Managed hooks:

- `UserPromptSubmit`: injects external-first routing context.
- `SubagentStart`: advisory fallback-scope context.

`Stop` is optional. Pass `--include-stop-hook` only when you explicitly want a
post-answer hard guardrail for actionable AGPair terminal states such as
`ready_for_review` and `approval_required`.

Hooks fail open when AGPair state is unavailable. They preserve unrelated Codex
hook settings and remove only AGPair-managed entries on uninstall.

## Review And Adoption

Always inspect:

```bash
agpair task status TASK-123 --json
agpair task logs TASK-123 --include-executor-output
```

Use `recovery_decision` as the controller-facing next step and `agent_result` as the evidence quality state. Follow `recovery_decision.action`: `use_result` for reports, `review_then_apply` for isolated implementation diffs, `wait_background` for live background tasks, `switch_executor` for the next external executor, `native_fallback` for native subagents/direct controller work, `repair_executor` for auth/binary health, and `inspect_evidence` when artifacts need manual inspection. `protocol_result` and `adoption_result` remain compatibility/debug surfaces; do not make low-risk protocol warnings override useful evidence.

For isolated implementation or test-fix tasks, review and apply the executor diff explicitly:

```bash
agpair task diff TASK-123
agpair task apply TASK-123 --check
agpair task apply TASK-123
```

`task apply` leaves changes in the controller worktree for normal Codex review and verification. It does not auto-accept the AGPair task.

After verification, close the loop:

```bash
agpair task accept TASK-123 --adoptable-result yes --controller-rework none
```

If the protocol failed but the report/stdout is still useful, record explicit salvage instead of pretending the executor succeeded:

```bash
agpair task adopt TASK-123 --from-report --adoptable-result partial --controller-rework minor
```

## Workflows

Use `agpair workflow start` only for high-value multi-part, parallel, adversarial, or long-running work. Workflow manifests are declarative; AGPair rejects arbitrary script fields and creates normal AGPair child tasks.

Workflow `ready_for_review` means AGPair has an evidence pack for Codex verification, not final user-facing success.

## Blocked Retry

```bash
agpair task retry TASK-123 --from-block --authorization-profile local_mutating
```

`blocked(approval_required)` is terminal. Retry starts a new attempt with structured blocked context and a dispatch-time authorization profile.

## Executor Order

For Codex as controller, prefer `grok-cli`, then `antigravity-cli`, then `claude-code`.

`claude-code` is the AGPair-managed external Claude Code worker for Codex controllers. It is the cross-controller quality escalation lane, not a native Codex subagent. Its default Claude auth mode is `auto`: `agpair doctor --fresh` first verifies the local Claude Code OAuth/subscription login, then falls back to the current Claude provider selected in CC Switch. Update Claude login or the CC Switch provider if `doctor --fresh` reports `executor_auth_required` or `Invalid Authentication`. Probe timeout is not the same as auth failure; check `doctor --fresh` `last_failure_type` for `executor_probe_timeout` or `executor_hook_interference`. API-key worker mode is only an explicit fallback via `AGPAIR_CLAUDE_CODE_AUTH_MODE=api`.

Do not request the AGPair-managed external `codex` executor by default; it is the Codex CLI worker and is suppressed for Codex controllers unless `--allow-self-executor` is explicitly justified. Use Codex native subagents as the fallback/review lane after external executors are unavailable, unsuitable, or not good enough.

Only route new work to active registered executor ids. Historical task records may remain inspectable for compatibility, but they are not default dispatch targets.

`ready_for_review`, `evidence_ready`, and `committed` mean the external executor claims completion. Codex still verifies the diff, receipt, raw evidence paths, and tests.
