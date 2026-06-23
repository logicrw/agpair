# TRINITY/Conductor Role Orchestration V3.0 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 TRINITY 和 Conductor 两篇论文里真正适合 AGPair 的协调思想落成一层轻量、可解释、可采纳的角色与 workflow 调度模型。

**Architecture:** 保留 V2.9 的 Artifact / Adoption / Action 三层采纳架构不动，在它上方增加 Coordination Role 与 Coordination Policy。TRINITY 贡献 `Thinker / Worker / Verifier` 角色语义；Conductor 贡献自然语言 workflow/topology 调度和模型池适配思想；AGPair 不引入黑箱 RL、不让外部 verifier 取代主控采纳、不强制所有任务三步走。V3.0 的第一阶段必须是 metadata-first：先让角色可见、可解释、可忽略，再逐步进入 prompt、preset、evidence 和 metrics。

**Tech Stack:** Python 3.12, Typer, SQLite task records, AGPair workflow manifests, workflow evidence packs, terminal receipts, local CLI executors, isolated worktrees, pytest, ruff, Codex / Claude Code / Hermes AGPair skills.

---

## 0. Source Notes

This plan distills two source papers, verified from arXiv on 2026-06-22:

- [TRINITY: An Evolved LLM Coordinator](https://arxiv.org/abs/2512.04695), v3, last revised 2026-04-27.
- [Learning to Orchestrate Agents in Natural Language with the Conductor](https://arxiv.org/abs/2512.04388), v5, last revised 2026-05-06.

The plan extends these AGPair documents:

- `docs/superpowers/plans/2026-06-21-first-principles-adoption-architecture-v2-9.md`
- `docs/superpowers/plans/2026-06-14-fusion-style-fanout-synthesis-v2-7.md`
- `docs/superpowers/plans/2026-06-09-adoption-oriented-external-agent-v2-3.md`

## 1. Product Contract

AGPair's first-principles contract remains:

```text
AGPair exists to turn unreliable external-agent labor into safe controller leverage.
```

This plan adds a second sentence:

```text
AGPair should make the intended role of external-agent labor explicit before it judges the labor.
```

This sentence has a strict corollary:

```text
A role label can explain intent, but it must never become proof of usefulness.
```

The target is not:

```text
Make every external agent perfect.
```

The target is:

```text
Make every external result easier for the controller to interpret, salvage, verify, and route.
```

The production risk this plan must avoid:

```text
Turning a simple role hint into another protocol gate that makes useful external work harder to adopt.
```

## 1.1 Production Bias Check

This plan is valid only if implementation avoids these predictable drifts:

| Drift | Why it would be bad | Required mitigation |
| --- | --- | --- |
| Role label becomes a success signal | `thinker` / `worker` / `verifier` would become another protocol ceremony | Keep `artifact_result` and `adoption_result` as the only usefulness surfaces |
| Missing role becomes a hard failure | This reintroduces the strictness AGPair removed in V2.9 | Missing expected roles create soft warnings only |
| Every task becomes a fanout workflow | Simple tasks would get slower and more expensive | Single-task `--coordination-role` must stay first-class |
| Existing `role` and new `coordination_role` drift apart | Controllers would not know which field to trust | Keep `role` as lane label; keep `coordination_role` as semantic enum |
| Metrics become optimization targets | Executors could be routed for pretty numbers instead of controller value | Treat metrics as advisory evidence, never adoption truth |
| Verifier output becomes final acceptance | This would invert the controller-verifies contract | Verifier recommendations always pass through controller inspection/adoption |
| Local state/config tasks get externalized | External agents are weaker on local config, SQLite, auth, and home-state truth | Preserve the external-first, not external-always boundary |

If an implementation cannot satisfy these mitigations, stop and reduce scope before adding more orchestration.

## 2. Paper Distillation

### 2.1 TRINITY: What To Adopt

TRINITY's useful idea for AGPair is not the trained 0.6B coordinator or the sep-CMA-ES implementation. The useful idea is the small role/action space:

```text
At each turn:
  choose an agent
  assign exactly one role
  append the result to the transcript
  stop when verification accepts or the budget is exhausted
```

TRINITY defines three roles that map cleanly to AGPair:

| TRINITY role | Paper meaning | AGPair meaning |
| --- | --- | --- |
| `Thinker` | Plan, decompose, critique, propose strategy | External analysis, design review, risk mapping, second opinion |
| `Worker` | Make concrete progress toward a solution | Diff, patch, evidence pack, report, command output, implementation candidate |
| `Verifier` | Check correctness and completeness | Independent review lane, test/lint evidence, adoption recommendation |

AGPair should adopt this vocabulary because it reduces ambiguity:

- a `thinker` can be valuable without a diff;
- a `worker` can be partial but still produce useful artifacts;
- a `verifier` can identify risk without owning final acceptance.

### 2.2 TRINITY: What Not To Adopt Yet

Do not implement these parts in V3.0:

- no hidden-state coordinator;
- no trained lightweight head;
- no sep-CMA-ES training loop;
- no benchmark-driven automatic role routing;
- no rule that every task must run `thinker -> worker -> verifier`;
- no rule that a verifier's accept verdict becomes AGPair's final acceptance.

Reason:

```text
AGPair's user-facing value is auditability and salvage, not leaderboard optimization.
```

### 2.3 Conductor: What To Adopt

Conductor's useful idea for AGPair is natural-language orchestration:

```text
The coordinator writes a workflow, assigns models to roles, shapes instructions, and adapts to the available model pool.
```

This maps to AGPair as:

| Conductor idea | AGPair adaptation |
| --- | --- |
| Natural-language workflow specification | Manifest-level `coordination_policy` and node-level `coordination_role` |
| Targeted communication topology | Presets for review, implementation, test-fix, research, and adversarial verification |
| Model pool adaptation | Rule-based routing using executor health, task kind, authorization, historical metrics, and user requirements |
| Prompt engineering per worker | Role-specific additions in `agpair/executors/task_contract.py` |
| Recursive / dynamic scaling | Only bounded nested workflows with existing delegation guardrails |

### 2.4 Conductor: What Not To Adopt Yet

Do not implement these parts in V3.0:

- no RL-trained conductor;
- no opaque model choosing the entire workflow without explanation;
- no recursive self-selection except through existing AGPair workflow limits and nested-delegation guardrails;
- no automatic routing based only on benchmark scores;
- no use of Graphiti, QMD, or Obsidian as a verifier that can declare truth.

Reason:

```text
AGPair's controller must be able to explain why a task was routed, what was produced, and what remains to verify.
```

## 3. Target Architecture

### 3.1 New Layer

V2.9 has:

```text
Artifact Layer
  What did the external agent leave behind?

Adoption Layer
  Which artifacts are safe/useful enough for the controller to use?

Action Layer
  What should the controller do next?
```

V3.0 adds one layer above them:

```text
Coordination Layer
  What role was this external agent asked to play, and why was this topology chosen?
```

The full flow becomes:

```text
controller intent
  -> coordination_policy + coordination_role
  -> executor task contract
  -> receipt/stdout/diff/evidence
  -> artifact_result
  -> adoption_result + agent_result
  -> recovery_decision
```

The new layer must never bypass the lower layers.

### 3.2 Role Vocabulary

Add a small role vocabulary:

```text
thinker       analysis, planning, critique, decomposition
worker        concrete task progress, implementation, evidence, report
verifier      independent check, validation, risk review
synthesizer   combine multiple lanes into one controller-facing result
gate          enforce policy, scope, authorization, and adoption readiness
general       fallback for existing tasks and backward compatibility
```

The role is a semantic hint, not a terminal state.

Examples:

| Task shape | Role |
| --- | --- |
| "Review this architecture and find blind spots" | `thinker` |
| "Implement this bounded change in an isolated worktree" | `worker` |
| "Check whether this diff is safe to apply" | `verifier` |
| "Compare three external reports and produce a synthesis" | `synthesizer` |
| "Block workflow if evidence is missing or unsafe" | `gate` |

### 3.3 Coordination Policy

Add an optional workflow-level policy:

```json
{
  "coordination_policy": {
    "style": "role_orchestrated",
    "expected_roles": ["thinker", "worker", "verifier"],
    "optional_roles": ["synthesizer"],
    "stop_rule": "controller_verifies",
    "max_coordination_turns": 5,
    "routing_basis": ["task_kind", "authorization_profile", "executor_health", "historical_metrics"]
  }
}
```

Policy constraints:

- `expected_roles` is advisory. Missing expected roles may add `soft_warnings`, but must not create a hard blocker by itself.
- `stop_rule=controller_verifies` is the default and must remain the safe default.
- `max_coordination_turns` limits workflow expansion; it does not permit hidden recursion.
- `routing_basis` must be emitted in evidence so the controller can audit why a topology was chosen.

Avoid a `required_roles` field in V3.0. It sounds strict, and implementers will be tempted to make missing roles block the workflow. If a later version needs strict topology enforcement, add an explicit field such as:

```json
{
  "strict_role_coverage": false
}
```

and keep the default false.

### 3.4 Stop Rules

Adopt TRINITY's budget discipline, not its verifier-owned final answer.

AGPair stop rules:

| Condition | AGPair behavior |
| --- | --- |
| verifier says usable and evidence exists | `ready_for_review`, controller inspects/adopts |
| verifier says blocked | `needs_review` or `blocked` depending on artifacts and hard gates |
| budget exhausted with useful artifacts | salvage via `artifact_result`, usually `needs_review` |
| budget exhausted with no useful signal | `blocked` or `stuck`, recovery decides retry/switch/repair |
| executor emits only bootstrap warnings | do not count as a useful role output |

## 4. User-Facing Examples

### 4.1 Read-Only Design Review

Command shape:

```bash
agpair workflow fanout \
  --repo-path "$REPO" \
  --controller codex \
  --mode review \
  --topic "Evaluate AGPair task adoption semantics" \
  --lane grok-cli:thinker-primary \
  --lane antigravity-cli:thinker-adversarial \
  --lane claude-code:verifier \
  --wait
```

Expected manifest excerpt:

```json
{
  "mode": "review",
  "coordination_policy": {
    "style": "role_orchestrated",
    "expected_roles": ["thinker", "verifier"],
    "stop_rule": "controller_verifies"
  },
  "nodes": [
    {"id": "thinker-primary", "kind": "task", "coordination_role": "thinker"},
    {"id": "thinker-adversarial", "kind": "task", "coordination_role": "thinker"},
    {"id": "verifier", "kind": "task", "coordination_role": "verifier"},
    {"id": "synthesis", "kind": "synthesis", "coordination_role": "synthesizer"},
    {"id": "gate", "kind": "gate", "coordination_role": "gate"}
  ]
}
```

### 4.2 Implementation With Verification

Command shape:

```bash
agpair workflow fanout \
  --repo-path "$REPO" \
  --controller codex \
  --mode implementation \
  --topic "Implement artifact_result propagation in workflow lane cards" \
  --scope "agpair/workflows/synthesis.py, tests/unit/test_workflow_synthesis.py" \
  --lane antigravity-cli:worker-candidate \
  --lane grok-cli:verifier-review \
  --isolated-worktree \
  --wait
```

Expected behavior:

- `worker-candidate` can produce a diff or patch evidence.
- `verifier-review` reviews the candidate output.
- `synthesis` compares candidate evidence and verifier concerns.
- `gate` blocks automatic adoption if scope, apply-check, validation, or artifact evidence is missing.
- The controller still applies at most one candidate and runs tests locally.

### 4.3 Single Task Role Hint

Command shape:

```bash
agpair task start \
  --repo-path "$REPO" \
  --controller codex \
  --executor grok-cli \
  --task-kind quick_review \
  --authorization-profile local_readonly \
  --completion-policy report \
  --coordination-role thinker \
  --body "Find blind spots in the adoption result policy."
```

Expected status excerpt:

```json
{
  "task_id": "TASK-...",
  "coordination_role": "thinker",
  "agent_result": {
    "state": "needs_review",
    "controller_action": "use_result"
  },
  "artifact_result": {
    "primary_artifact": "report"
  }
}
```

## 5. Files And Responsibilities

### New Core Module

- Create: `agpair/roles.py`
  - Owns role vocabulary, normalization, validation, and prompt hints.
  - Does not import workflow, CLI, storage, or executor modules.

### Models And Persistence

- Modify: `agpair/models.py`
  - Add `coordination_role: str | None` to `TaskRecord` only for standalone task status/list/watch surfaces.
  - Do not add it to `TaskAttemptRecord` in Phase A unless a failing test proves attempt-level role history is needed.
- Modify: `agpair/storage/schema.sql`
  - Add a nullable `coordination_role` column to `tasks` only in Phase A.
  - Do not add a workflow-node column in Phase A. `workflow_nodes.role` already exists as the lane label; semantic role can remain in validated manifest/evidence until status performance or query needs prove a column is necessary.
- Modify: `agpair/storage/db.py`
  - Add an idempotent migration for `tasks.coordination_role`.
- Modify: `agpair/storage/tasks.py`
  - Read/write task-level role metadata.
- Modify: `agpair/workflows/models.py`
  - Keep existing `WorkflowNodeRecord.role` as the lane label. Add semantic role to evidence or manifest-derived payloads, not as a second workflow-node truth surface in Phase A.

### Role Truth Surfaces

V3.0 must not create ambiguous role ownership:

| Existing field | Meaning after V3.0 |
| --- | --- |
| `workflow_nodes.role` | User-visible lane label, for example `primary`, `adversarial`, `reviewer` |
| `tasks.child_role` | Backward-compatible copy of the workflow lane label on child tasks |
| `coordination_role` | Semantic enum: `thinker`, `worker`, `verifier`, `synthesizer`, `gate`, `general` |

Rules:

- `role` and `child_role` are labels, not semantic role classes.
- `coordination_role` is the semantic class, but it is never adoption evidence.
- Workflow node semantic roles should be stored in manifest/evidence first.
- Add a workflow-node database column only after a status/list/watch use case requires it.

### CLI And Schema

- Modify: `agpair/cli/task.py`
  - Add `--coordination-role`.
  - Include role in JSON status/list/watch outputs.
- Modify: `agpair/workflows/schema.py`
  - Validate manifest-level `coordination_policy`.
  - Validate node-level `coordination_role`.
- Modify: `agpair/workflows/presets.py`
  - Generate role-aware fanout presets.
- Modify: `agpair/cli/workflow.py`
  - Surface role metadata in workflow JSON and preset output.

### Executor Contract

- Modify: `agpair/executors/task_contract.py`
  - Add a role-specific addendum to the prompt contract.
  - Keep stdout/report/evidence requirements intact.

### Evidence And Synthesis

- Modify: `agpair/workflows/synthesis.py`
  - Include `coordination_role` in lane cards.
  - Require synthesis to summarize role coverage, contradictions, blind spots, and unresolved verification risk.
- Modify: `agpair/workflows/evidence.py`
  - Persist coordination policy, routing basis, role coverage, and role-level metrics in workflow evidence packs.
- Modify: `agpair/recovery.py`
  - Do not choose actions from role alone.
  - Optionally use role metadata only to improve reason text.

### Docs And Skills

- Modify: `README.md`
- Modify: `README.zh-CN.md`
- Modify: `docs/usage.md`
- Modify: `docs/usage.zh-CN.md`
- Modify: `docs/workflows.md`
- Modify: `docs/workflows.zh-CN.md`
- Modify: `skills/Codex/SKILL.md`
- Modify: `skills/Claude/SKILL.md`
- Modify: `skills/Hermes/SKILL.md`

Docs must say:

```text
Role hints improve routing and interpretation. They do not weaken artifact, adoption, scope, or authorization gates.
```

### Tests

- Create: `tests/unit/test_roles.py`
- Modify: `tests/unit/test_task_contract.py`
- Modify: `tests/unit/test_workflow_manifest.py`
- Modify: `tests/unit/test_workflow_presets.py`
- Modify: `tests/unit/test_workflow_synthesis.py`
- Modify: `tests/integration/test_task_start_and_status.py`
- Modify: `tests/integration/test_workflow_fanout_cli.py`
- Modify: `tests/integration/test_workflow_fanout_synthesis.py`
- Modify: `tests/integration/test_workflow_watch.py`

## 6. Task 1: Add Role Vocabulary

**Files:**

- Create: `agpair/roles.py`
- Create: `tests/unit/test_roles.py`

- [ ] **Step 1: Write role normalization tests**

Add `tests/unit/test_roles.py`:

```python
from __future__ import annotations

import pytest

from agpair.roles import ROLE_VALUES, normalize_coordination_role, role_prompt_hint


def test_normalize_coordination_role_accepts_known_roles() -> None:
    assert normalize_coordination_role("Thinker") == "thinker"
    assert normalize_coordination_role("worker") == "worker"
    assert normalize_coordination_role("verifier") == "verifier"
    assert normalize_coordination_role("synthesizer") == "synthesizer"
    assert normalize_coordination_role("gate") == "gate"
    assert normalize_coordination_role(None) is None


def test_normalize_coordination_role_rejects_unknown_role() -> None:
    with pytest.raises(ValueError, match="coordination role must be one of"):
        normalize_coordination_role("judge")


def test_role_prompt_hints_are_non_empty_for_all_roles() -> None:
    for role in ROLE_VALUES:
        assert role_prompt_hint(role)
```

- [ ] **Step 2: Run tests and observe failure**

Run:

```bash
uv run pytest tests/unit/test_roles.py -q
```

Expected before implementation:

```text
ModuleNotFoundError: No module named 'agpair.roles'
```

- [ ] **Step 3: Add `agpair/roles.py`**

Add:

```python
from __future__ import annotations

from enum import StrEnum


class CoordinationRole(StrEnum):
    THINKER = "thinker"
    WORKER = "worker"
    VERIFIER = "verifier"
    SYNTHESIZER = "synthesizer"
    GATE = "gate"
    GENERAL = "general"


ROLE_VALUES: tuple[str, ...] = tuple(role.value for role in CoordinationRole)

ROLE_PROMPT_HINTS: dict[str, str] = {
    "thinker": (
        "Role: thinker. Produce strategy, decomposition, critique, risks, and useful next-step guidance. "
        "Do not claim implementation or validation unless you observed it."
    ),
    "worker": (
        "Role: worker. Make concrete progress toward the requested result. "
        "For code tasks, produce the smallest safe diff and report validation evidence."
    ),
    "verifier": (
        "Role: verifier. Check correctness, completeness, safety, scope, and evidence quality. "
        "You may recommend adoption, but the controller owns final acceptance."
    ),
    "synthesizer": (
        "Role: synthesizer. Compare prior lane outputs. Surface consensus, contradictions, unique insights, "
        "blind spots, and the recommended controller action."
    ),
    "gate": (
        "Role: gate. Enforce policy, authorization, scope, artifact inspectability, and adoption readiness. "
        "Do not override artifact or adoption hard blockers."
    ),
    "general": (
        "Role: general. Follow the task contract and produce the most useful inspectable artifact for the controller."
    ),
}


def normalize_coordination_role(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().lower().replace("-", "_")
    if normalized in ROLE_VALUES:
        return normalized
    allowed = ", ".join(ROLE_VALUES)
    raise ValueError(f"coordination role must be one of: {allowed}")


def role_prompt_hint(value: str | None) -> str:
    role = normalize_coordination_role(value) or CoordinationRole.GENERAL.value
    return ROLE_PROMPT_HINTS[role]
```

- [ ] **Step 4: Run role tests**

Run:

```bash
uv run pytest tests/unit/test_roles.py -q
```

Expected:

```text
3 passed
```

## 7. Task 2: Add Minimal Task-Level Role Metadata

**Files:**

- Modify: `agpair/models.py`
- Modify: `agpair/storage/schema.sql`
- Modify: `agpair/storage/db.py`
- Modify: `agpair/storage/tasks.py`
- Modify: `agpair/cli/task.py`
- Modify: `tests/integration/test_task_start_and_status.py`

- [ ] **Step 1: Add failing integration test**

Add a test that starts a read-only task with a role hint and verifies the role appears in status JSON:

```python
def test_task_start_status_includes_coordination_role(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    configure_fake_antigravity_cli(tmp_path, monkeypatch)
    repo = make_repo_dir(tmp_path)

    result = CliRunner().invoke(
        app,
        [
            "task",
            "start",
            "--repo-path",
            str(repo),
            "--controller",
            "codex",
            "--executor",
            "grok-cli",
            "--authorization-profile",
            "local_readonly",
            "--completion-policy",
            "report",
            "--coordination-role",
            "thinker",
            "--no-wait",
            "--json",
            "--body",
            "Report only.",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["task"]["coordination_role"] == "thinker"
```

- [ ] **Step 2: Add public model field**

Add `coordination_role: str | None = None` to `TaskRecord` in `agpair/models.py`.

Do not add the field to `TaskAttemptRecord` in this task. Attempt-level history is a nice-to-have and would make the first implementation broader than necessary.

- [ ] **Step 3: Add SQLite columns**

Add nullable columns to `agpair/storage/schema.sql`:

```sql
coordination_role TEXT
```

Add it only to `tasks`. Do not add it to `task_attempts` or `workflow_nodes` in Phase A. Existing rows return `null`.

- [ ] **Step 3b: Add idempotent migration**

Update `agpair/storage/db.py` so existing databases get the same nullable column:

```python
task_cols = {row[1] for row in conn.execute("PRAGMA table_info(tasks)").fetchall()}
if "coordination_role" not in task_cols:
    conn.execute("ALTER TABLE tasks ADD COLUMN coordination_role TEXT")
    conn.commit()
```

Use the repository's existing explicit `PRAGMA table_info` migration style. Do not create a destructive migration.

- [ ] **Step 4: Wire storage reads/writes**

Update `agpair/storage/tasks.py` to:

- persist normalized `coordination_role` at task creation;
- read it into `TaskRecord`;
- leave task attempt writes unchanged.

- [ ] **Step 5: Add CLI option**

In `agpair/cli/task.py`, add:

```python
coordination_role: str | None = typer.Option(
    None,
    "--coordination-role",
    help="Semantic role hint for this task: thinker, worker, verifier, synthesizer, gate, or general.",
)
```

Normalize with `normalize_coordination_role()` before task persistence.

- [ ] **Step 6: Include role in JSON surfaces**

Add `coordination_role` to:

- `task start --json`;
- `task status --json`;
- `task list --json`;
- `task wait --json`;
- `task watch --json` events.

Expected JSON:

```json
{
  "coordination_role": "thinker"
}
```

Do not change existing `child_role`; it remains workflow/lane metadata for backward compatibility.

Do not compute adoption state from `coordination_role`. A `thinker` task can be blocked if it leaves no useful artifact; a `worker` task can be partial if it leaves a useful report but no safe diff.

- [ ] **Step 7: Run focused tests**

Run:

```bash
uv run pytest tests/integration/test_task_start_and_status.py -q
```

Expected:

```text
all tests pass
```

## 8. Task 3: Add Role-Aware Executor Contract

**Files:**

- Modify: `agpair/executors/task_contract.py`
- Modify: `tests/unit/test_task_contract.py`

- [ ] **Step 1: Add failing contract tests**

Add:

```python
from agpair.executors.task_contract import body_with_task_contract


def test_task_contract_includes_thinker_role_hint() -> None:
    body = body_with_task_contract(
        "TASK-ROLE",
        "Review the plan.",
        authorization_profile="local_readonly",
        completion_policy="report",
        coordination_role="thinker",
    )

    assert "Role: thinker" in body
    assert "Do not claim implementation" in body
    assert "Structured terminal receipt JSON requirements" in body


def test_task_contract_includes_verifier_acceptance_boundary() -> None:
    body = body_with_task_contract(
        "TASK-ROLE",
        "Verify the diff.",
        authorization_profile="local_readonly",
        completion_policy="report",
        coordination_role="verifier",
    )

    assert "Role: verifier" in body
    assert "controller owns final acceptance" in body
```

- [ ] **Step 2: Extend function signature**

Change `body_with_task_contract()`:

```python
def body_with_task_contract(
    task_id: str,
    body: str,
    *,
    execution_repo_path: str | None = None,
    authorization_profile: str = "local_mutating",
    authorization_summary: str | None = None,
    completion_policy: str = "auto",
    coordination_role: str | None = None,
) -> str:
```

- [ ] **Step 3: Add role addendum**

Import `role_prompt_hint` and insert this block before structured receipt requirements:

```python
role_addendum = ""
if coordination_role:
    role_addendum = f"Coordination role requirements:\n- {role_prompt_hint(coordination_role)}\n\n"
```

Then include `role_addendum` in the final contract.

- [ ] **Step 4: Update callers**

Update every call to `body_with_task_contract()` to pass `coordination_role` when available. Existing callers without role metadata must keep working.

- [ ] **Step 5: Run focused tests**

Run:

```bash
uv run pytest tests/unit/test_task_contract.py tests/unit/test_roles.py -q
```

Expected:

```text
all tests pass
```

## 9. Task 4: Add Workflow Manifest Role Semantics

**Files:**

- Modify: `agpair/workflows/schema.py`
- Modify: `tests/unit/test_workflow_manifest.py`

- [ ] **Step 1: Add manifest tests**

Add tests:

```python
def test_workflow_manifest_accepts_coordination_policy_and_roles(tmp_path) -> None:
    manifest = validate_manifest({
        "version": 1,
        "name": "role-workflow",
        "controller": "codex",
        "repo_path": str(tmp_path),
        "coordination_policy": {
            "style": "role_orchestrated",
            "expected_roles": ["thinker", "verifier"],
            "stop_rule": "controller_verifies",
            "max_coordination_turns": 5,
            "routing_basis": ["task_kind", "executor_health"],
        },
        "nodes": [
            {
                "id": "think",
                "kind": "task",
                "coordination_role": "thinker",
                "executor": "grok-cli",
                "body": "Think.",
            },
            {
                "id": "verify",
                "kind": "task",
                "coordination_role": "verifier",
                "executor": "grok-cli",
                "body": "Verify.",
                "depends_on": ["think"],
            },
        ],
    })

    nodes = manifest.nodes
    assert nodes[0]["coordination_role"] == "thinker"
    assert nodes[1]["coordination_role"] == "verifier"
    assert manifest.manifest["coordination_policy"]["stop_rule"] == "controller_verifies"


def test_workflow_manifest_rejects_unknown_coordination_role(tmp_path) -> None:
    with pytest.raises(WorkflowManifestError, match="coordination role must be one of"):
        validate_manifest({
            "version": 1,
            "name": "bad-role",
            "controller": "codex",
            "repo_path": str(tmp_path),
            "nodes": [
                {
                    "id": "judge",
                    "kind": "task",
                    "coordination_role": "judge",
                    "executor": "grok-cli",
                    "body": "Judge.",
                }
            ],
        })
```

- [ ] **Step 2: Normalize policy**

In `agpair/workflows/schema.py`, add a small validator:

```python
def _normalize_coordination_policy(raw: Any) -> dict[str, Any] | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise WorkflowManifestError("coordination_policy must be an object")
    policy = deepcopy(raw)
    style = str(policy.get("style") or "role_orchestrated").strip().lower()
    if style not in {"role_orchestrated", "fanout", "single_role"}:
        raise WorkflowManifestError("coordination_policy.style must be role_orchestrated, fanout, or single_role")
    policy["style"] = style
    policy["expected_roles"] = [normalize_coordination_role(str(item)) for item in policy.get("expected_roles", [])]
    policy["optional_roles"] = [normalize_coordination_role(str(item)) for item in policy.get("optional_roles", [])]
    policy["stop_rule"] = str(policy.get("stop_rule") or "controller_verifies").strip().lower()
    if policy["stop_rule"] not in {"controller_verifies", "budget_exhausted", "manual"}:
        raise WorkflowManifestError("coordination_policy.stop_rule must be controller_verifies, budget_exhausted, or manual")
    policy["max_coordination_turns"] = _normalize_int(
        policy.get("max_coordination_turns", 5),
        field="coordination_policy.max_coordination_turns",
        min_value=1,
        max_value=20,
    )
    routing_basis = policy.get("routing_basis", [])
    if not isinstance(routing_basis, list) or not all(isinstance(item, str) for item in routing_basis):
        raise WorkflowManifestError("coordination_policy.routing_basis must be a string array")
    policy["routing_basis"] = [item.strip() for item in routing_basis if item.strip()]
    return policy
```

Call it during `validate_manifest()` and write the normalized result back:

```python
coordination_policy = _normalize_coordination_policy(manifest.get("coordination_policy"))
if coordination_policy is not None:
    manifest["coordination_policy"] = coordination_policy
```

- [ ] **Step 3: Normalize node role**

When normalizing each node:

```python
node["coordination_role"] = normalize_coordination_role(node.get("coordination_role")) or _default_role_for_kind(kind)
```

Default mapping:

```python
def _default_role_for_kind(kind: str) -> str:
    if kind == "synthesis":
        return "synthesizer"
    if kind == "gate":
        return "gate"
    if kind == "verification":
        return "verifier"
    return "general"
```

- [ ] **Step 4: Persist workflow node role**

Do not add a `workflow_nodes.coordination_role` column in this task. Keep the semantic role in the normalized manifest and pass it into evidence/synthesis from manifest-derived node payloads. This avoids creating two database role fields next to `workflow_nodes.role`.

If status/list/watch later need role access without loading the manifest, add the column in a separate migration with tests that prove the need.

- [ ] **Step 5: Run workflow tests**

Run:

```bash
uv run pytest tests/unit/test_workflow_manifest.py -q
```

Expected:

```text
all tests pass
```

## 10. Task 5: Add Role-Aware Fanout Presets

**Files:**

- Modify: `agpair/workflows/presets.py`
- Modify: `tests/unit/test_workflow_presets.py`
- Modify: `tests/integration/test_workflow_fanout_cli.py`

- [ ] **Step 1: Add preset tests**

Add:

```python
def test_review_fanout_assigns_thinker_roles() -> None:
    manifest = build_fanout_manifest(
        controller="codex",
        mode="review",
        topic="Review adoption semantics",
        lanes=["grok-cli:primary", "antigravity-cli:adversarial"],
        repo_path="/tmp/repo",
    )

    roles = {node["id"]: node["coordination_role"] for node in manifest["nodes"]}
    assert roles["primary"] == "thinker"
    assert roles["adversarial"] == "thinker"
    assert roles["synthesis"] == "synthesizer"
    assert roles["gate"] == "gate"


def test_implementation_fanout_marks_candidate_worker_and_reviewer_verifier() -> None:
    manifest = build_fanout_manifest(
        controller="codex",
        mode="implementation",
        topic="Implement bounded change",
        lanes=["antigravity-cli:candidate", "grok-cli:reviewer"],
        repo_path="/tmp/repo",
        isolated_worktree=True,
    )

    roles = {node["id"]: node["coordination_role"] for node in manifest["nodes"]}
    assert roles["candidate"] == "worker"
    assert roles["reviewer"] == "verifier"
```

- [ ] **Step 2: Add role inference helper**

In `agpair/workflows/presets.py`:

```python
def _role_for_lane(*, mode: str, role: str) -> str:
    tokens = {part for part in re.split(r"[^a-z0-9]+", role.lower()) if part}
    if tokens & {"reviewer", "review", "verify", "verifier"}:
        return "verifier"
    if mode in {"implementation", "test-fix"}:
        return "worker"
    if mode in {"review", "research"}:
        return "thinker"
    return "general"
```

- [ ] **Step 3: Add `coordination_policy` to generated manifests**

Generated fanout manifests should include:

```json
{
  "coordination_policy": {
    "style": "role_orchestrated",
    "stop_rule": "controller_verifies",
    "max_coordination_turns": 5,
    "routing_basis": ["fanout_mode", "lane_role", "authorization_profile", "completion_policy"]
  }
}
```

- [ ] **Step 4: Add `coordination_role` to task nodes**

Update `_task_node()` to include:

```python
"coordination_role": _role_for_lane(mode=mode, role=role),
```

Update `_synthesis_node()` and `_gate_node()` to emit:

```python
"coordination_role": "synthesizer"
"coordination_role": "gate"
```

Keep the existing `"role"` field unchanged. It is the lane label. Do not replace it with `coordination_role`.

- [ ] **Step 5: Run preset and CLI tests**

Run:

```bash
uv run pytest tests/unit/test_workflow_presets.py tests/integration/test_workflow_fanout_cli.py -q
```

Expected:

```text
all tests pass
```

## 11. Task 6: Add Role Metadata To Evidence And Synthesis

**Files:**

- Modify: `agpair/workflows/synthesis.py`
- Modify: `agpair/workflows/evidence.py`
- Modify: `tests/unit/test_workflow_synthesis.py`
- Modify: `tests/integration/test_workflow_fanout_synthesis.py`
- Modify: `tests/integration/test_workflow_watch.py`

- [ ] **Step 1: Add lane card test**

Add:

```python
def test_lane_card_includes_coordination_role() -> None:
    card = build_lane_card(
        {
            "role": "primary",
            "coordination_role": "thinker",
            "task_id": "TASK-1",
            "adoption_result": {
                "adoptable_result": "partial",
                "agent_result": {"state": "needs_review", "controller_action": "use_result"},
            },
        },
        role="primary",
        executor="grok-cli",
    )

    assert card["role"] == "primary"
    assert card["coordination_role"] == "thinker"
```

- [ ] **Step 2: Update lane card shape**

In `build_lane_card()`, include:

```python
"coordination_role": _optional_str(node_payload.get("coordination_role")) or "general",
```

- [ ] **Step 3: Add role coverage to synthesis result**

Add a role summary:

```json
{
  "role_coverage": {
    "thinker": {"total": 2, "usable": 1, "partial": 1, "blocked": 0},
    "verifier": {"total": 1, "usable": 1, "partial": 0, "blocked": 0}
  }
}
```

The synthesis should not fail because a role is missing unless the manifest policy requires it.
In V3.0, missing expected roles should produce `soft_warnings=["expected_role_missing:<role>"]`, not a hard blocker.

- [ ] **Step 4: Add evidence fields**

Workflow evidence packs should include:

```json
{
  "coordination": {
    "policy": {},
    "role_coverage": {},
    "routing_basis": [],
    "stop_rule": "controller_verifies"
  }
}
```

- [ ] **Step 5: Run synthesis/watch tests**

Run:

```bash
uv run pytest tests/unit/test_workflow_synthesis.py tests/integration/test_workflow_fanout_synthesis.py tests/integration/test_workflow_watch.py -q
```

Expected:

```text
all tests pass
```

## 12. Task 7: Add Explainable Routing Metrics

**Files:**

- Modify: `agpair/workflows/evidence.py`
- Modify: `agpair/workflows/synthesis.py`
- Modify: `tests/unit/test_workflow_synthesis.py`
- Modify: `tests/integration/test_workflow_fanout_synthesis.py`

- [ ] **Step 1: Add metric expectations**

Evidence should expose metrics that answer the user's real question:

```text
Did the external agent help the controller, even if it was imperfect?
```

This task is Phase D, not part of the minimum viable role layer. Do not start it until Tasks 1-6 are implemented and used in at least one real workflow. Metrics are useful only after the role metadata is flowing through real artifacts.

Add tests for:

```json
{
  "coordination_metrics": {
    "lane_count": 3,
    "usable_lane_count": 1,
    "partial_lane_count": 1,
    "blocked_lane_count": 1,
    "role_coverage": {},
    "controller_rework": "minor",
    "fallback_recommended": false
  }
}
```

- [ ] **Step 2: Derive metrics from existing surfaces**

Use existing `agent_result`, `artifact_result`, `adoption_result`, and `recovery_decision`. Do not create a separate success model.

Metric meanings:

| Metric | Meaning |
| --- | --- |
| `lane_count` | total lanes considered |
| `usable_lane_count` | lanes with `agent_result.state=usable` |
| `partial_lane_count` | lanes with `needs_review` or partial adoption |
| `blocked_lane_count` | lanes with no safe useful artifact |
| `role_coverage` | useful/partial/blocked counts per role |
| `controller_rework` | summarized from adoption evidence |
| `fallback_recommended` | true only when recovery suggests retry/switch/repair/fallback |

- [ ] **Step 3: Keep metrics advisory**

Metrics must not:

- override `artifact_result`;
- override authorization/scope/apply-check blockers;
- mark a result accepted automatically;
- become the only reason to retry or abandon.

- [ ] **Step 4: Run focused tests**

Run:

```bash
uv run pytest tests/unit/test_workflow_synthesis.py tests/integration/test_workflow_fanout_synthesis.py -q
```

Expected:

```text
all tests pass
```

## 13. Task 8: Update Docs And Skills

**Files:**

- Modify: `docs/workflows.md`
- Modify: `docs/workflows.zh-CN.md`
- Modify: `docs/usage.md`
- Modify: `docs/usage.zh-CN.md`
- Modify: `skills/Codex/SKILL.md`
- Modify: `skills/Claude/SKILL.md`
- Modify: `skills/Hermes/SKILL.md`

- [ ] **Step 1: Add user-facing rule**

Add this principle to docs:

```text
Role hints describe intended external-agent labor. They do not make external output automatically adoptable.
```

- [ ] **Step 2: Add role mapping table**

Document:

| Role | Use when | Good artifact |
| --- | --- | --- |
| `thinker` | planning, critique, second opinion | report or stdout_salvage |
| `worker` | implementation, patch, evidence | diff, patch_or_commit, evidence |
| `verifier` | review, tests, safety check | report, evidence, blocker |
| `synthesizer` | fanout comparison | synthesis report |
| `gate` | policy/scope/adoption readiness | gate result |

- [ ] **Step 3: Update controller skills**

Skills must say:

```text
Prefer external-first when suitable, but pick the role explicitly:
- thinker for design/research/review;
- worker for bounded implementation/test-fix;
- verifier for independent check;
- synthesizer/gate inside workflows.
Controller remains final verifier and adopter.
```

- [ ] **Step 4: Run docs checks**

Run:

```bash
rg -n "coordination-role|coordination_role|thinker|worker|verifier" docs skills
git diff --check
```

Expected:

```text
role docs are present and git diff has no whitespace errors
```

## 14. Task 9: End-To-End Verification

**Files:**

- Modify only files touched by Tasks 1-8.

- [ ] **Step 1: Run focused unit tests**

Run:

```bash
uv run pytest \
  tests/unit/test_roles.py \
  tests/unit/test_task_contract.py \
  tests/unit/test_workflow_manifest.py \
  tests/unit/test_workflow_presets.py \
  tests/unit/test_workflow_synthesis.py \
  -q
```

Expected:

```text
all tests pass
```

- [ ] **Step 2: Run focused integration tests**

Run:

```bash
uv run pytest \
  tests/integration/test_task_start_and_status.py \
  tests/integration/test_workflow_fanout_cli.py \
  tests/integration/test_workflow_fanout_synthesis.py \
  tests/integration/test_workflow_watch.py \
  -q
```

Expected:

```text
all tests pass
```

- [ ] **Step 3: Run static checks**

Run:

```bash
uv run ruff check .
git diff --check
```

Expected:

```text
ruff exits 0
git diff --check exits 0
```

- [ ] **Step 4: Run privacy and secret checks before any public push**

Run:

```bash
ggshield secret scan pre-commit --json
rg -n "(/Users/[A-Za-z0-9._-]+|\\bsk-[A-Za-z0-9]{20,}|\\bxox[baprs]-|\\bghp_|\\bgithub_pat_|\\bAIza|\\bya29\\.)" .
```

Expected:

```text
no secrets
no personal local paths in staged public docs or code
```

- [ ] **Step 5: Manual QA Gate**

Run a role-aware report task:

```bash
uv run agpair task start \
  --repo-path "$PWD" \
  --controller codex \
  --executor grok-cli \
  --task-kind quick_review \
  --authorization-profile local_readonly \
  --completion-policy report \
  --coordination-role thinker \
  --wait-policy lease \
  --controller-wait-seconds 15 \
  --timeout-seconds 60 \
  --json \
  --body "Report one risk in the role orchestration design. Do not edit files."
```

Expected:

```text
The JSON output includes coordination_role=thinker.
If the executor produces useful output, artifact_result/adoption_result remains the source of adoptability.
If the executor produces only warnings or no report, the task is blocked/stuck without being counted as useful.
```

Run a role-aware fanout dry manifest:

```bash
uv run agpair workflow fanout \
  --repo-path "$PWD" \
  --controller codex \
  --mode review \
  --topic "Review role orchestration metadata" \
  --lane grok-cli:primary \
  --lane grok-cli:reviewer \
  --no-wait \
  --json
```

Expected:

```text
The generated workflow exposes coordination_policy and node coordination_role values.
The workflow still requires synthesis/gate evidence before controller adoption.
```

## 15. Rollout Strategy

### 15.1 Phase A: Metadata Only

Add role vocabulary, standalone task CLI flag, manifest validation, and JSON output.

Acceptance:

```text
Existing tasks without role hints behave exactly as before.
Role metadata appears in status and workflow evidence.
No adoption decision changes because of role alone.
```

### 15.2 Phase B: Prompt Contract

Add role-specific prompt hints.

Acceptance:

```text
Thinker tasks bias toward useful analysis.
Worker tasks bias toward concrete artifacts.
Verifier tasks state that the controller owns final acceptance.
```

### 15.3 Phase C: Workflow Presets

Make fanout presets role-aware.

Acceptance:

```text
Review/research modes produce thinker lanes plus synthesis/gate.
Implementation/test-fix modes mark candidate lanes as worker and review lanes as verifier.
```

### 15.4 Phase D: Metrics

Expose role coverage and controller-usefulness metrics.

Acceptance:

```text
The controller can tell which roles produced usable, partial, blocked, or missing evidence.
Metrics explain routing quality without becoming adoption truth.
```

## 16. Red Lines

Do not implement:

- hidden RL coordinator;
- learned model routing without an explainable routing record;
- recursive external delegation outside existing workflow limits;
- verifier-owned final acceptance;
- role metadata that weakens artifact, scope, authorization, or apply-check gates;
- protocol strictness that discards useful stdout, reports, or inspectable diffs;
- metrics that classify bootstrap warning noise as useful work;
- automatic routing based on paper benchmark scores.

## 17. Decision Record

Decided:

```text
Use TRINITY as role grammar.
Use Conductor as explainable workflow/topology planning inspiration.
Keep V2.9 artifact/adoption/action as the adoption source of truth.
Add role metadata and role-aware prompts before any learned routing.
```

Rejected:

```text
No RL conductor in V3.0.
No hidden coordinator.
No mandatory three-step task pipeline.
No external verifier auto-acceptance.
No benchmark-only executor selection.
```

Why:

```text
AGPair's real product value is controller leverage: preserving useful external work, reducing controller rework, and keeping every adoption decision inspectable.
```

## 18. Open Questions

- **(implementation discovery):** Whether a future phase should add `workflow_nodes.coordination_role`, or keep workflow semantic roles in manifest/evidence. Prefer a column only if status/list/watch need it without loading manifest/evidence.
- **(implementation discovery):** Whether role inference in `fanout` presets should be string-based initially or configured through an explicit `--role-map` later. Prefer string-based initially.
- **(implementation discovery):** Whether metrics should live only in workflow evidence or also in task attempts. Prefer workflow evidence first.

None of these questions block the Phase A implementation. They only affect later persistence breadth and metrics polish.
