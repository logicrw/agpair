---
name: agpair
description: "Use when Hermes should delegate non-trivial coding, review, research, or verification work through AGPair external CLI agents."
version: 1.0.0
platforms: [macos, linux]
metadata:
  hermes:
    tags: [agpair, delegation, external-agents, coding-agents, review]
    related_skills: [hermes-agent, subagent-driven-development, codex, claude-code]
---

# AGPair for Hermes

Use AGPair as Hermes' durable external-agent control plane. Hermes remains the
controller, verifier, and final decision maker.

Core rule: external-first, not external-always. For non-trivial coding,
refactor, test-fix, research, review, or multi-file work, consider an AGPair
lane before doing everything directly in Hermes. Skip AGPair when the task is
tiny, highly sensitive, depends on hidden Hermes context, or requires local
home-state inspection that should not be delegated.

Hermes is not an AGPair executor identity. Do not inherit Codex or Claude Code
self-executor suppression. For Hermes controllers, all active external AGPair
executors are valid:

1. `grok-cli`
2. `antigravity-cli`
3. `codex`
4. `claude-code`

Inspect the live policy before relying on exact ordering:

```bash
agpair policy list --controller hermes --json
```

If the policy and this skill disagree, the live AGPair policy is the execution
truth. Prefer explicit `--executor` when you want a specific lane.

## When To Use

Use AGPair when an external worker can produce useful evidence:

- implementation candidates in isolated worktrees;
- independent code review or adversarial critique;
- repo scans, test-fix attempts, and repetitive verification;
- research or diagnosis with a bounded question;
- role-distinct fanout where each lane has a different scope, hypothesis, or
  review angle.

Use Hermes directly, or a Hermes native subagent, when the work is:

- a tiny local edit or one-command check;
- sensitive credential, auth, config, or memory inspection;
- dependent on current hidden conversation state;
- not safely isolatable for mutating external work;
- best handled as controller-side synthesis or final review.

AGPair-started workers do not share Hermes' hidden session state. Every AGPair
brief must stand alone: include the goal, repo path, allowed scope, forbidden
scope, constraints, required output, and validation/evidence requirements.

## Routing Budget

Before dispatching, choose a routing budget:

1. one external lane;
2. several role-based lanes;
3. direct Hermes work;
4. Hermes native subagent fallback or reviewer.

Add lanes only when they are distinct. Do not start duplicate external tasks
with the same prompt, scope, and exit criteria.

For mutating work, use `--isolated-worktree` unless each mutating lane has a
disjoint worktree or disjoint file scope. Do not run multiple mutating external
executors in the controller worktree.

## Brief Shape

For complex work, use this shape:

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

Never pass placeholders such as `<brief>`, `todo`, or `fix this`.

## Dispatch

Report-only review or diagnosis:

```bash
agpair task start \
  --repo-path "$REPO" \
  --controller hermes \
  --executor grok-cli \
  --task-kind quick_review \
  --wait-policy lease \
  --authorization-profile local_readonly \
  --completion-policy report \
  --body "$BRIEF"
```

Bounded implementation, refactor, or test-fix:

```bash
agpair task start \
  --repo-path "$REPO" \
  --controller hermes \
  --executor grok-cli \
  --task-kind implementation \
  --wait-policy lease \
  --authorization-profile local_mutating \
  --completion-policy evidence \
  --isolated-worktree \
  --body "$BRIEF"
```

Use the other executors explicitly when they fit the work better:

```bash
--executor antigravity-cli
--executor codex
--executor claude-code
```

For parallel work, dispatch asynchronously and collect evidence cheaply:

```bash
agpair task start --repo-path "$REPO" --controller hermes --executor grok-cli --body "$BRIEF" --no-wait
agpair task watch TASK-123 --json
agpair task wait TASK-123 --json
```

## Fanout Workflow

For high-value review, design, diagnosis, or implementation choices, use a
fanout workflow instead of unrelated manual tasks:

```bash
agpair workflow fanout \
  --controller hermes \
  --mode review \
  --topic "$TOPIC" \
  --lane grok-cli:primary \
  --lane antigravity-cli:second-opinion \
  --lane codex:challenger \
  --lane claude-code:reviewer \
  --repo-path "$REPO" \
  --wait --json
```

For implementation panels, keep mutating lanes isolated:

```bash
agpair workflow fanout \
  --controller hermes \
  --mode implementation \
  --topic "$TOPIC" \
  --scope "$ALLOWED_SCOPE" \
  --lane grok-cli:candidate-a \
  --lane antigravity-cli:candidate-b \
  --lane codex:reviewer \
  --isolated-worktree \
  --repo-path "$REPO" \
  --dry-run --json
```

Read `panel_result`, `lane_cards`, `synthesis_result`, and `evidence_path`.
The synthesis is evidence, not final truth.

## Review Gate

Treat `ready_for_review`, `evidence_ready`, and `committed` as review gates, not
automatic completion.

Always inspect:

```bash
agpair task status TASK-123 --json
agpair task logs TASK-123 --include-executor-output
```

Use `recovery_decision.action` as the controller-facing next step:

- `use_result`: report evidence is usable;
- `review_then_apply`: inspect and apply an isolated diff;
- `wait_background`: task is still running;
- `switch_executor`: retry with another external executor;
- `native_fallback`: use Hermes direct work or a Hermes native subagent;
- `repair_executor`: fix auth, binary, hook, or runtime health first;
- `inspect_evidence`: read artifacts manually.

Use `artifact_result` as the evidence map when a result is partial, malformed, or surprising: `report`/`stdout_salvage` can still be incorporated, blocked `diff` must not be applied, and `nothing_useful` means retry/switch unless a global hard blocker requires inspection.

For isolated implementation or test-fix tasks:

```bash
agpair task diff TASK-123
agpair task apply TASK-123 --check
agpair task apply TASK-123
```

`task apply` leaves changes in the controller worktree for normal Hermes review
and verification. It does not auto-accept the AGPair task.

After local verification, close the AGPair loop:

```bash
agpair task accept TASK-123 --adoptable-result yes --controller-rework none
```

If the protocol failed but the report or stdout is still useful, record salvage. This updates `artifact_result` and `agent_result`; it does not claim the executor followed protocol perfectly:

```bash
agpair task adopt TASK-123 --from-report --adoptable-result partial --controller-rework minor
```

## Hermes Native Subagent Fallback

Hermes has native delegation through the `delegation` toolset and the
`delegate_task` tool. Use it as a fallback or controller-side reviewer, not as a
replacement for AGPair's receipt, status, diff, apply, and adoption lifecycle.

Use native Hermes subagents when:

- AGPair is unavailable or a selected executor is unhealthy;
- AGPair returns `native_fallback`;
- the task needs hidden Hermes session context;
- you need a quick local reviewer before adopting an external diff;
- the task is sensitive and should stay inside Hermes' current local context.

Native fallback shape:

```python
delegate_task(
    goal="Review the AGPair diff for spec compliance",
    context="Include the task goal, changed files, exact checks, and expected output.",
    toolsets=["terminal", "file"],
)
```

Do not run a native mutating subagent and an AGPair mutating lane on the same
files at the same time.

## Pre-Delivery Check

- [ ] Routing budget selected: AGPair lane, role-based fanout, direct Hermes
      work, or native fallback.
- [ ] Every external brief is self-contained.
- [ ] AGPair skip reason stated if non-trivial work stays in Hermes.
- [ ] Mutating external lanes are isolated or disjoint.
- [ ] `task status --json` inspected for every AGPair task.
- [ ] Useful evidence was accepted, adopted, or explicitly rejected.
- [ ] Final answer distinguishes external evidence from Hermes judgment.
