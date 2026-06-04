# AGPair Workflow Orchestration V2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade AGPair from a durable single-task external executor controller into a safe declarative workflow control plane for multi-agent engineering flows.

**Architecture:** V2 implements A': AGPair owns a durable workflow state machine and validated declarative DAG manifest. Controllers may generate manifests, but AGPair never executes arbitrary model-generated Python, JavaScript, or shell scripts. V2 is allowed to start only after V1.1 has landed first-class attempts, durable artifacts, normalized `ready_for_review`, effective task policies, unified completion evaluation, and controller-aware executor policy.

**Tech Stack:** Python 3.12, Typer, SQLite, stdlib JSON/dataclasses/pathlib, existing AGPair task/attempt/artifact/receipt/watch primitives, git worktrees, pytest.

---

## 1. V2 Prerequisite Gate

Do not start V2 implementation until V1.1 proves these commands and behaviors:

```bash
pytest tests/unit/test_completion_policy.py tests/unit/test_artifacts.py tests/unit/test_executor_policy.py tests/unit/test_receipt_validation.py -q
pytest tests/integration/test_report_only_tasks.py tests/integration/test_attempt_artifacts.py tests/integration/test_policy_cli.py -q
pytest tests/integration/test_task_start_and_status.py tests/integration/test_task_wait_inline_poll.py tests/integration/test_fake_executors.py -q
```

Required V1.1 facts:

- Task success is normalized to `ready_for_review`.
- Commit is optional unless effective completion policy is `commit`.
- Read-only report tasks can finish without a commit.
- Attempt artifacts are durable and survive executor cleanup.
- `task status --json` exposes effective task policy, durable artifact paths, receipt, report, and output excerpt.
- Daemon and inline wait use the same completion evaluator.
- Controller-aware executor policy is centralized.
- Codex controller suppresses external `codex` by default.
- Claude Code controller suppresses external `claude-code` by default.

If any prerequisite is missing, stop and complete V1.1 first. V2 must not recreate or bypass the V1.1 task core.

## 2. Decision Summary

V2 adopts A', not A or B:

- Reject A: AGPair should not run arbitrary model-generated workflow scripts.
- Reject B: workflow state should not live only inside Codex or Claude Code conversation state.
- Adopt A': AGPair runs a validated declarative workflow manifest as a durable local DAG/state machine.

V2 should add workflow orchestration only:

- parent workflow records;
- child task trees;
- static DAG scheduling;
- fan-out/fan-in;
- synthesis, verification, and gate nodes;
- workflow-level budgets and concurrency limits;
- state-aware retry/reroute using V1.1 policy;
- workflow evidence pack aggregation from child receipts/artifacts;
- low-noise workflow watch;
- durable recovery after daemon restart;
- safe cancellation.

V2 should not add:

- arbitrary script execution;
- auto-merge;
- production deploy;
- credential mutation;
- direct OMX source changes;
- full distributed scheduler;
- replacement implementation of V1.1 completion, artifact, attempt, or executor policy logic.

## 3. Constraints

- Scale: local-first, single-user. Default max 4 parallel child tasks, default max 20 child tasks per workflow, hard max 1000 with explicit large-workflow override.
- Consistency: workflow, node, child task, and child attempt transitions must be idempotent and crash-safe in SQLite.
- Latency: workflow CLI/status/watch stay interactive; long work stays in child executor processes and existing wait/watch loops.
- Team: small Python codebase; use explicit data structures and migrations, not a workflow framework.
- Cost: external CLI executors first through V1.1 controller policy; native subagents remain controller-side fallback/review.
- Security: manifests may be model-generated and must be treated as untrusted input.

## 4. Core Terms

Workflow:

- Durable parent run with one manifest, one repo, one controller, limits, terminal phase, and evidence pack.

Workflow node:

- A validated DAG node. It may dispatch a normal AGPair child task, synthesize child evidence, verify results, or gate workflow completion.

Child task:

- A normal AGPair task using V1.1 task core. It has attempts, artifacts, effective policy, and terminal decision.

Manifest:

- Declarative JSON file describing nodes, dependencies, authorization, completion policy, controller, budgets, and stop rules.

Workflow evidence pack:

- Machine-readable result built from child task attempts, durable artifact paths, structured receipts, validation evidence, blocked reasons, scope violations, and node decisions.

Native subagents:

- Controller-side fallback/review lanes. They are not AGPair executor ids and are not workflow node executors.

## 5. Data Model

Create:

- `agpair/workflows/models.py`
- `agpair/workflows/store.py`
- migrations in `agpair/storage/db.py`

### 5.1 Tables

Add `workflows`:

```sql
CREATE TABLE IF NOT EXISTS workflows (
  workflow_id TEXT PRIMARY KEY,
  repo_path TEXT NOT NULL,
  controller TEXT NOT NULL,
  name TEXT NOT NULL,
  phase TEXT NOT NULL,
  manifest_json TEXT NOT NULL,
  limits_json TEXT NOT NULL,
  result_json TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  started_at TEXT,
  finished_at TEXT,
  stuck_reason TEXT
);
```

Add `workflow_nodes`:

```sql
CREATE TABLE IF NOT EXISTS workflow_nodes (
  workflow_id TEXT NOT NULL,
  node_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  role TEXT,
  phase TEXT NOT NULL,
  task_id TEXT,
  depends_on_json TEXT NOT NULL,
  authorization_profile TEXT NOT NULL,
  requested_completion_policy TEXT NOT NULL DEFAULT 'auto',
  effective_policy_json TEXT NOT NULL DEFAULT '{}',
  executor_backend TEXT,
  attempt_no INTEGER NOT NULL DEFAULT 0,
  max_retries INTEGER NOT NULL DEFAULT 0,
  allow_partial INTEGER NOT NULL DEFAULT 0,
  isolated_worktree INTEGER NOT NULL DEFAULT 0,
  result_json TEXT,
  last_error TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (workflow_id, node_id)
);
```

Add task linkage columns:

```sql
ALTER TABLE tasks ADD COLUMN workflow_id TEXT;
ALTER TABLE tasks ADD COLUMN workflow_node_id TEXT;
ALTER TABLE tasks ADD COLUMN parent_task_id TEXT;
ALTER TABLE tasks ADD COLUMN child_role TEXT;
```

Do not add workflow-specific artifact storage. Workflow evidence must reference V1.1 `task_artifacts` and child attempt artifacts.

### 5.2 Model Types

Create `agpair/workflows/models.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class WorkflowPhase(StrEnum):
    NEW = "new"
    RUNNING = "running"
    READY_FOR_REVIEW = "ready_for_review"
    BLOCKED = "blocked"
    STUCK = "stuck"
    ABANDONED = "abandoned"


class NodePhase(StrEnum):
    PENDING = "pending"
    DISPATCHING = "dispatching"
    RUNNING = "running"
    READY_FOR_REVIEW = "ready_for_review"
    BLOCKED = "blocked"
    STUCK = "stuck"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class WorkflowRecord:
    workflow_id: str
    repo_path: str
    controller: str
    name: str
    phase: WorkflowPhase
    manifest_json: str
    limits_json: str
    result_json: str | None
    created_at: str
    updated_at: str
    started_at: str | None = None
    finished_at: str | None = None
    stuck_reason: str | None = None


@dataclass(frozen=True)
class WorkflowNodeRecord:
    workflow_id: str
    node_id: str
    kind: str
    role: str | None
    phase: NodePhase
    task_id: str | None
    depends_on: list[str]
    authorization_profile: str
    requested_completion_policy: str
    effective_policy_json: str
    executor_backend: str | None
    attempt_no: int
    max_retries: int
    allow_partial: bool
    isolated_worktree: bool
    result_json: str | None
    last_error: str | None
    created_at: str
    updated_at: str
```

## 6. Manifest Schema

Create:

- `agpair/workflows/schema.py`
- `tests/unit/test_workflow_manifest.py`

### 6.1 Minimal Manifest

```json
{
  "version": 1,
  "name": "repo-wide-review",
  "controller": "codex",
  "repo_path": "/path/to/repo",
  "authorization_profile": "local_readonly",
  "completion_policy": "auto",
  "limits": {
    "max_parallel_tasks": 4,
    "max_child_tasks": 20,
    "max_retries_per_node": 1,
    "max_runtime_seconds": 14400,
    "max_watch_events": 500
  },
  "nodes": [
    {
      "id": "scan-routing",
      "kind": "task",
      "role": "explorer",
      "body": "Goal: inspect executor routing surfaces. Scope: read-only. Required changes: none. Exit criteria: return structured findings with file references.",
      "authorization_profile": "local_readonly",
      "completion_policy": "report",
      "isolated_worktree": false,
      "depends_on": []
    },
    {
      "id": "synthesize",
      "kind": "synthesis",
      "role": "synthesizer",
      "body": "Goal: synthesize scan-routing receipt into one evidence pack. Scope: no file edits. Required changes: none. Exit criteria: produce workflow evidence summary.",
      "authorization_profile": "local_readonly",
      "completion_policy": "report",
      "depends_on": ["scan-routing"]
    }
  ]
}
```

### 6.2 Validation Rules

Required:

- `version == 1`
- `name` non-empty and <= 120 chars
- `controller in {"codex", "claude-code", "generic"}`
- `repo_path` resolves to a git repo, or CLI supplies `--repo-path`
- node ids unique and match `^[A-Za-z0-9_.-]+$`
- node kinds in `task`, `synthesis`, `verification`, `gate`
- dependencies reference existing nodes
- graph is acyclic
- node body passes task body validation
- authorization profile valid
- completion policy valid: `auto`, `evidence`, `report`, `commit`
- `max_parallel_tasks` 1-16
- `max_child_tasks` 1-1000; values above 100 require `--allow-large-workflow`
- `max_retries_per_node` 0-10
- `max_runtime_seconds` 60-604800

Reject any field named:

```text
workflow_script
python
javascript
shell
command
commands
setup_commands
teardown_commands
postinstall
preinstall
```

V2.0 manifests are declarative only. Do not make exceptions for "trusted" manifests.

### 6.3 Tests

`tests/unit/test_workflow_manifest.py` must include:

Required cases:

- `test_valid_minimal_manifest_passes`: one `task` node and one `synthesis` node validate, and defaults are materialized.
- `test_rejects_cycle`: `a -> b -> a` returns a validation error naming both nodes.
- `test_rejects_unknown_dependency`: dependency on a missing node id fails before any DB write.
- `test_rejects_script_fields_at_any_depth`: forbidden keys nested under workflow, node, body, or metadata are rejected.
- `test_rejects_invalid_completion_policy`: values outside `auto`, `evidence`, `report`, and `commit` fail validation.
- `test_rejects_large_workflow_without_flag`: more than 100 child-capable nodes requires `--allow-large-workflow`.
- `test_report_nodes_keep_report_completion_policy`: read-only report nodes keep `completion_policy=report` through normalization.

## 7. Workflow State Machine

Workflow phases:

```text
new -> running -> ready_for_review
              -> blocked
              -> stuck
              -> abandoned
```

Node phases:

```text
pending -> dispatching -> running -> ready_for_review
                              -> blocked
                              -> stuck
                              -> skipped
```

Rules:

- Workflow `ready_for_review` means AGPair has an evidence pack for controller verification.
- Node `ready_for_review` means its child task or internal synthesis passed V1.1 completion evaluation.
- Child task `ready_for_review` satisfies a required task node.
- Legacy child `committed` or `evidence_ready` can satisfy a node only through V1.1 normalized status.
- Child `blocked(approval_required)` blocks the workflow unless node `allow_partial=true`.
- Child `blocked(validation_failure)` blocks required nodes and records validation evidence.
- Child `stuck` reroutes through V1.1 executor policy if retry budget remains.
- No workflow reaches `ready_for_review` without at least one gate or synthesis node, unless the manifest has exactly one task node.

## 8. Store And Repository API

Create `agpair/workflows/store.py`.

Required APIs:

```python
class WorkflowRepository:
    def create_workflow(self, *, workflow_id: str, manifest: dict, repo_path: str, controller: str) -> None:
        raise NotImplementedError

    def get_workflow(self, workflow_id: str) -> WorkflowRecord | None:
        raise NotImplementedError

    def list_workflows(self, *, repo_path: str | None = None, limit: int = 50) -> list[WorkflowRecord]:
        raise NotImplementedError

    def mark_running(self, workflow_id: str) -> None:
        raise NotImplementedError

    def mark_ready_for_review(self, workflow_id: str, *, result_json: str) -> None:
        raise NotImplementedError

    def mark_blocked(self, workflow_id: str, *, reason: str, result_json: str | None = None) -> None:
        raise NotImplementedError

    def mark_stuck(self, workflow_id: str, *, reason: str, result_json: str | None = None) -> None:
        raise NotImplementedError

    def abandon(self, workflow_id: str, *, reason: str) -> None:
        raise NotImplementedError


class WorkflowNodeRepository:
    def list_nodes(self, workflow_id: str) -> list[WorkflowNodeRecord]:
        raise NotImplementedError

    def mark_dispatching(self, workflow_id: str, node_id: str) -> None:
        raise NotImplementedError

    def mark_running(self, workflow_id: str, node_id: str, *, task_id: str) -> None:
        raise NotImplementedError

    def mark_ready_for_review(self, workflow_id: str, node_id: str, *, result_json: str) -> None:
        raise NotImplementedError

    def mark_blocked(self, workflow_id: str, node_id: str, *, error: str, result_json: str | None = None) -> None:
        raise NotImplementedError

    def mark_stuck(self, workflow_id: str, node_id: str, *, error: str, result_json: str | None = None) -> None:
        raise NotImplementedError

    def mark_skipped(self, workflow_id: str, node_id: str, *, reason: str) -> None:
        raise NotImplementedError
```

Tests:

Required cases:

- `test_create_workflow_and_nodes_transactionally`: failed node insert rolls back workflow insert.
- `test_duplicate_workflow_id_is_rejected`: second create with same id returns a deterministic conflict.
- `test_node_transition_is_idempotent_for_same_task_id`: repeated `mark_running` with same `task_id` is safe; a different `task_id` is rejected.
- `test_list_workflows_filters_by_repo`: repository filter returns only matching `repo_path` rows and honors `limit`.

## 9. Scheduler

Create `agpair/workflows/scheduler.py`.

Responsibilities:

1. Load running workflows.
2. Reconcile running nodes with child task status.
3. Mark node terminal states from child task normalized V1.1 status.
4. Find runnable pending nodes whose dependencies are satisfied.
5. Enforce workflow `max_parallel_tasks`.
6. Enforce `max_child_tasks`.
7. Dispatch runnable task nodes through a shared AGPair task creation helper, not shelling out to `agpair task start`.
8. Pass `controller`, `authorization_profile`, `completion_policy`, `workflow_id`, `workflow_node_id`, `child_role`, and idempotency key to child task creation.
9. Retry or reroute stuck nodes through V1.1 executor policy if retry budget remains.
10. Aggregate workflow evidence pack.
11. Mark workflow `ready_for_review`, `blocked`, or `stuck`.

Daemon integration:

- Add `advance_workflows(paths: RuntimePaths, current: datetime | None = None)` in `agpair/daemon/loop.py`.
- Run after receipt ingestion and before stale task marking.
- Use DB uniqueness/idempotency keys to prevent duplicate child task dispatch.

No scheduler path may:

- directly parse executor temp logs;
- decide commit/report success itself;
- bypass V1.1 `CompletionEvaluator`;
- bypass V1.1 controller-aware executor policy.

Tests:

Required cases:

- `test_scheduler_dispatches_dependency_free_nodes`: a pending root `task` node creates exactly one child task row.
- `test_scheduler_does_not_dispatch_duplicate_child_after_restart`: running scheduler twice with the same idempotency key does not create a second child task.
- `test_scheduler_marks_node_ready_from_child_ready_for_review`: normalized V1.1 child success advances the node.
- `test_scheduler_blocks_workflow_on_required_approval_block`: required `blocked(approval_required)` child blocks the workflow with retry context.
- `test_scheduler_reroutes_stuck_node_with_retry_budget`: stuck child reroutes to the next policy executor and records previous attempt context.
- `test_scheduler_marks_workflow_ready_only_after_required_nodes_and_gate`: workflow remains running until required task nodes and gate/synthesis node pass.

## 10. Workflow CLI

Create:

- `agpair/cli/workflow.py`
- register in `agpair/cli/app.py`
- tests in `tests/integration/test_workflow_cli.py`

Commands:

```bash
agpair workflow validate --file workflow.json
agpair workflow start --file workflow.json --controller codex --wait
agpair workflow status WF-123 --json
agpair workflow watch WF-123 --json
agpair workflow cancel WF-123 --reason "user requested"
agpair workflow retry-node WF-123 scan-routing --authorization-profile local_mutating
agpair workflow list --repo-path /path/to/repo --json
```

`workflow start` behavior:

- validates manifest;
- resolves repo path;
- resolves controller;
- creates workflow id `WF-<12 uppercase hex>`;
- creates pending nodes transactionally;
- runs one scheduler tick;
- if `--wait`, waits through low-noise workflow watch until terminal phase or timeout.

`workflow status --json` shape:

```json
{
  "ok": true,
  "workflow_id": "WF-123",
  "phase": "running",
  "controller": "codex",
  "repo_path": "/path/to/repo",
  "nodes": [
    {
      "node_id": "scan-routing",
      "phase": "ready_for_review",
      "task_id": "TASK-123",
      "authorization_profile": "local_readonly",
      "requested_completion_policy": "report",
      "artifact_paths": {
        "receipt": "/tmp/agpair-home/tasks/TASK-123/attempt-1/receipt.json",
        "stdout": "/tmp/agpair-home/tasks/TASK-123/attempt-1/stdout.log"
      }
    }
  ],
  "result": null
}
```

Tests:

Required cases:

- `test_workflow_validate_accepts_valid_manifest`: CLI returns zero and prints normalized manifest JSON.
- `test_workflow_validate_rejects_script_field`: CLI returns non-zero and names the forbidden field path.
- `test_workflow_start_creates_rows`: start creates one workflow row and expected node rows without launching scripts.
- `test_workflow_status_includes_child_artifact_paths`: status includes durable V1.1 child artifact paths.
- `test_workflow_cancel_marks_running_children_abandoned`: cancel updates workflow and running nodes without deleting child task rows.

## 11. Workflow Watch

Create:

- `agpair/workflows/watch.py`
- tests in `tests/integration/test_workflow_watch.py`

Event shape:

```json
{
  "schema_version": "1",
  "workflow_id": "WF-123",
  "event": "node_state_changed",
  "cursor": "node:scan-routing:attempt:1:ready_for_review",
  "node_id": "scan-routing",
  "node_phase": "ready_for_review",
  "task_id": "TASK-123",
  "summary": "Node reached ready_for_review",
  "receipt_path": "/tmp/agpair-home/tasks/TASK-123/attempt-1/receipt.json",
  "raw_log_path": "/tmp/agpair-home/tasks/TASK-123/attempt-1/stdout.log"
}
```

Rules:

- Emit state changes only.
- Include receipt/log paths, not full logs.
- Include workflow terminal events.
- Include blocked approval events.
- Include stuck/reroute events.
- Suppress unchanged heartbeats.
- Cursor must be stable enough for controller resume after compaction.

Tests:

Required cases:

- `test_workflow_watch_emits_node_state_change_once`: unchanged node state does not emit repeated events.
- `test_workflow_watch_includes_receipt_and_raw_log_paths`: events contain durable paths from child artifacts.
- `test_workflow_watch_does_not_stream_full_log_body`: large stdout is not included in event JSON.
- `test_workflow_watch_cursor_resumes_after_previous_event`: cursor replay emits only later events.

## 12. Evidence Pack

Create:

- `agpair/workflows/evidence.py`
- tests in `tests/unit/test_workflow_evidence.py`

Evidence pack shape:

```json
{
  "workflow_id": "WF-123",
  "phase": "ready_for_review",
  "required_nodes": ["scan-routing", "synthesize"],
  "completed_nodes": ["scan-routing", "synthesize"],
  "blocked_nodes": [],
  "stuck_nodes": [],
  "skipped_nodes": [],
  "changed_files": [],
  "validation": [
    {
      "node_id": "synthesize",
      "status": "passed",
      "command": "receipt validation",
      "exit_code": 0,
      "evidence": "all child receipts parsed"
    }
  ],
  "receipts": [
    {
      "node_id": "scan-routing",
      "task_id": "TASK-123",
      "attempt_no": 1,
      "receipt_path": "/tmp/agpair-home/tasks/TASK-123/attempt-1/receipt.json",
      "raw_log_path": "/tmp/agpair-home/tasks/TASK-123/attempt-1/stdout.log"
    }
  ],
  "scope_violations": [],
  "residual_risks": []
}
```

Acceptance rules:

- Natural-language executor summaries are hints only.
- Every completed node must point to a structured receipt or internally generated synthesis/gate result.
- Every child task artifact path must be durable V1.1 artifact path.
- `changed_files` is a union of child receipts.
- `scope_violations` is a union of child receipts and gate findings.
- Missing child artifact path makes the workflow `blocked(validation_failure)`.

Tests:

Required cases:

- `test_evidence_pack_aggregates_child_receipts`: changed files, validation entries, and receipt paths are unioned from child terminal receipts.
- `test_evidence_pack_rejects_missing_artifact_paths`: missing durable receipt or raw log path blocks the workflow with `validation_failure`.
- `test_evidence_pack_lists_blocked_and_stuck_nodes`: blocked/stuck required nodes appear in the evidence pack and workflow status.
- `test_evidence_pack_preserves_report_only_nodes`: report-only nodes can complete without commits and still appear in receipts.

## 13. Node Kinds

### 13.1 `task`

Dispatches a normal AGPair child task.

Uses:

- V1.1 controller policy
- V1.1 completion policy
- V1.1 authorization profile
- V1.1 attempts/artifacts

### 13.2 `synthesis`

Creates a report-only child task by default.

Default:

```json
{
  "authorization_profile": "local_readonly",
  "completion_policy": "report"
}
```

It receives child receipt summaries and durable artifact paths, not full raw logs by default.

### 13.3 `verification`

Creates a child task or internal gate that verifies previous nodes.

For V2.0, prefer child task verification through external executors. Do not add arbitrary local shell command execution in workflow manifests.

### 13.4 `gate`

Internal AGPair node that checks workflow evidence pack structure.

Allowed gate checks in V2.0:

- all required nodes terminal;
- no required node blocked/stuck;
- all completed nodes have durable receipt/artifact paths;
- no scope violations unless `allow_partial`;
- validation evidence present when required.

No model calls and no shell commands inside gate nodes.

## 14. Retry And Reroute

Workflow retry follows V1.1 semantics:

- `blocked(approval_required)` is terminal for the child attempt.
- `workflow retry-node WF node --authorization-profile local_mutating` creates a fresh child task attempt or child task.
- The new prompt includes previous node result, blocked reason, terminal receipt, artifact paths, workflow context, and new authorization profile.
- Stuck/malformed/output-missing nodes may reroute to another executor through V1.1 controller policy if retry budget remains.
- Do not retry indefinitely.

Retry budget:

```json
{
  "max_retries_per_node": 1
}
```

Tests:

Required cases:

- `test_retry_node_preserves_previous_blocked_context`: retry prompt contains previous blocker type, receipt path, raw log path, and node id.
- `test_retry_node_expands_authorization_profile`: retry with a wider authorization profile creates a new child attempt and leaves the old attempt intact.
- `test_stuck_node_reroutes_to_next_policy_executor`: reroute skips the failed executor and chooses the next healthy executor from V1.1 policy.
- `test_retry_budget_exhaustion_marks_node_stuck`: retry count above manifest budget marks node and workflow stuck with a clear reason.

## 15. Cancellation And Cleanup

`workflow cancel`:

- marks workflow `abandoned`;
- attempts to cancel running child tasks;
- records cancellation reason;
- does not delete artifacts;
- does not delete child task rows;
- does not merge or revert worktrees.

Tests:

Required cases:

- `test_cancel_workflow_marks_workflow_and_running_nodes_abandoned`: running workflow and running nodes move to `abandoned`.
- `test_cancel_preserves_child_artifacts`: receipt/stdout/stderr/report files remain readable after cancel.
- `test_cancel_does_not_delete_child_task_rows`: linked AGPair task rows and attempts remain queryable after cancel.

## 16. MCP Surface

Modify `agpair/mcp_server.py`.

Add tools:

```python
agpair_start_workflow(manifest: dict, repo_path: str | None = None, controller: str | None = None, wait: bool = False) -> dict
agpair_get_workflow(workflow_id: str) -> dict
agpair_watch_workflow(workflow_id: str, cursor: str | None = None) -> dict
agpair_cancel_workflow(workflow_id: str, reason: str = "cancelled") -> dict
agpair_retry_workflow_node(workflow_id: str, node_id: str, authorization_profile: str | None = None, executor: str | None = None) -> dict
```

Rules:

- MCP accepts manifest dicts, not script strings.
- MCP rejects script fields through the same manifest validator.
- MCP wait behavior uses low-noise workflow watch.
- MCP must expose workflow evidence pack paths, not full raw logs.

Tests:

Required cases:

- `test_mcp_start_workflow_rejects_script_fields`: MCP and CLI share the same forbidden-field validator.
- `test_mcp_start_workflow_returns_workflow_id`: valid manifest returns `workflow_id`, `phase`, and status URL/command hints.
- `test_mcp_watch_workflow_returns_cursor`: watch response includes stable cursor, terminal event summary, and artifact paths without raw log body.

## 17. Templates

Ship templates only after core runner tests pass.

Create:

- `templates/workflows/fanout-synthesize.json`
- `templates/workflows/adversarial-review.json`
- `templates/workflows/repo-wide-review.json`

Do not ship `test-fix-loop` until retry-node is stable.

Template rules:

- no absolute local paths;
- no private project names;
- no script fields;
- default read-only scan nodes use `completion_policy=report`;
- mutating nodes use isolated worktrees when parallel;
- synthesis nodes use `completion_policy=report`;
- docs say templates are examples, not trusted automation.

## 18. Docs, Skills, And Local Deployment

Update:

- `README.md`
- `README.zh-CN.md`
- `docs/usage.md`
- `docs/usage.zh-CN.md`
- `docs/getting-started.en.md`
- `docs/getting-started-zh.md`
- `docs/external-agent-first-decision-map.zh-CN.html`
- `skills/claw.json`
- `skills/Codex/SKILL.md`
- `skills/Claude/SKILL.md`

Required wording:

```text
Use normal agpair task start for ordinary work.
Use agpair workflow start for high-value multi-part, parallel, adversarial, or long-running work.
Workflow ready_for_review means AGPair has an evidence pack for controller verification, not final user-facing success.
Workflow manifests are declarative. AGPair does not execute arbitrary workflow scripts.
Workflow nodes use V1.1 task attempts, durable artifacts, completion policies, and controller-aware executor routing.
```

Local installed Codex/Claude config remains a deployment step, not a repo artifact.

Local deployment after V2 tests pass:

```bash
REPO=/path/to/agpair
agpair codex config --install --scope project --repo-path "$REPO" --dry-run
agpair claude config --install --scope project --repo-path "$REPO" --dry-run
mkdir -p "$HOME/.codex/skills/agpair" "$HOME/.claude/skills/agpair"
cp "$REPO/skills/Codex/SKILL.md" "$HOME/.codex/skills/agpair/SKILL.md"
cp "$REPO/skills/Claude/SKILL.md" "$HOME/.claude/skills/agpair/SKILL.md"
agpair codex config --install --scope project --repo-path "$REPO"
agpair claude config --install --scope project --repo-path "$REPO"
agpair doctor --repo-path "$REPO" --json
```

Do not commit user-level `~/.codex`, `~/.claude`, `~/.agpair`, raw workflow evidence, or local private manifests. Project-level `.codex/hooks.json` or `.claude/settings.json` may be committed only when sanitized and intentionally shared.

## 19. Implementation Tasks

### Task 1: Manifest Schema

**Files:**

- Create: `agpair/workflows/__init__.py`
- Create: `agpair/workflows/schema.py`
- Test: `tests/unit/test_workflow_manifest.py`

- [ ] Write tests listed in Section 6.3.
- [ ] Run `pytest tests/unit/test_workflow_manifest.py -q` and confirm failure.
- [ ] Implement manifest dataclasses and validator.
- [ ] Run `pytest tests/unit/test_workflow_manifest.py -q` and confirm pass.

### Task 2: Store And Migrations

**Files:**

- Modify: `agpair/storage/db.py`
- Modify: `agpair/storage/schema.sql`
- Create: `agpair/workflows/models.py`
- Create: `agpair/workflows/store.py`
- Test: `tests/unit/test_workflow_store.py`

- [ ] Write store tests listed in Section 8.
- [ ] Run `pytest tests/unit/test_workflow_store.py -q` and confirm failure.
- [ ] Add migrations and store APIs.
- [ ] Run `pytest tests/unit/test_workflow_store.py -q` and confirm pass.

### Task 3: Workflow CLI Validate/Start/Status

**Files:**

- Create: `agpair/cli/workflow.py`
- Modify: `agpair/cli/app.py`
- Test: `tests/integration/test_workflow_cli.py`

- [ ] Write CLI tests listed in Section 10.
- [ ] Run `pytest tests/integration/test_workflow_cli.py -q` and confirm failure.
- [ ] Implement validate/start/status/list.
- [ ] Run `pytest tests/integration/test_workflow_cli.py -q` and confirm pass.

### Task 4: Scheduler

**Files:**

- Create: `agpair/workflows/scheduler.py`
- Modify: `agpair/daemon/loop.py`
- Modify: `agpair/storage/tasks.py` only for workflow child task linkage if V1.1 did not already add helper support.
- Test: `tests/integration/test_workflow_scheduler.py`

- [ ] Write scheduler tests listed in Section 9.
- [ ] Run `pytest tests/integration/test_workflow_scheduler.py -q` and confirm failure.
- [ ] Implement scheduler using V1.1 task creation/evaluator/policy helpers.
- [ ] Run `pytest tests/integration/test_workflow_scheduler.py -q` and confirm pass.

### Task 5: Watch And Evidence Pack

**Files:**

- Create: `agpair/workflows/watch.py`
- Create: `agpair/workflows/evidence.py`
- Modify: `agpair/cli/workflow.py`
- Test: `tests/integration/test_workflow_watch.py`
- Test: `tests/unit/test_workflow_evidence.py`

- [ ] Write watch/evidence tests listed in Sections 11 and 12.
- [ ] Run `pytest tests/integration/test_workflow_watch.py tests/unit/test_workflow_evidence.py -q` and confirm failure.
- [ ] Implement watch event and evidence aggregation.
- [ ] Run `pytest tests/integration/test_workflow_watch.py tests/unit/test_workflow_evidence.py -q` and confirm pass.

### Task 6: Retry, Reroute, Cancel, Recovery

**Files:**

- Modify: `agpair/cli/workflow.py`
- Modify: `agpair/workflows/scheduler.py`
- Modify: `agpair/workflows/store.py`
- Test: `tests/integration/test_workflow_recovery.py`

- [ ] Write retry/cancel/recovery tests listed in Sections 14 and 15.
- [ ] Run `pytest tests/integration/test_workflow_recovery.py -q` and confirm failure.
- [ ] Implement retry-node, cancel, stuck reroute, idempotency recovery.
- [ ] Run `pytest tests/integration/test_workflow_recovery.py -q` and confirm pass.

### Task 7: MCP Tools

**Files:**

- Modify: `agpair/mcp_server.py`
- Test: `tests/unit/test_mcp_server.py`

- [ ] Write MCP tests listed in Section 16.
- [ ] Run `pytest tests/unit/test_mcp_server.py -q` and confirm failure.
- [ ] Implement workflow MCP tools using CLI/shared workflow helpers.
- [ ] Run `pytest tests/unit/test_mcp_server.py -q` and confirm pass.

### Task 8: Templates And Docs

**Files:**

- Create: `templates/workflows/fanout-synthesize.json`
- Create: `templates/workflows/adversarial-review.json`
- Create: `templates/workflows/repo-wide-review.json`
- Modify docs and skills listed in Section 18.

- [ ] Add templates.
- [ ] Validate every template with `agpair workflow validate`.
- [ ] Update docs/skills with required wording.
- [ ] Run stale wording grep:

```bash
rg -n "workflow script|execute arbitrary|automatic merge|all tasks commit|Gemini.*new task|Antigravity IDE.*executor" README.md README.zh-CN.md docs skills templates || true
```

Expected: no stale or misleading hits.

### Task 9: Full Verification

Run:

```bash
pytest tests/unit -q
pytest tests/integration -q
git diff --check
```

Smoke:

```bash
agpair workflow validate --file templates/workflows/fanout-synthesize.json
agpair workflow start --file templates/workflows/fanout-synthesize.json --controller codex --wait
agpair workflow status WF-ABC123DEF456 --json
agpair workflow watch WF-ABC123DEF456 --json
```

Expected smoke facts:

- workflow reaches `ready_for_review` or a clear actionable blocker;
- child read-only nodes use report completion;
- child artifacts are durable paths;
- evidence pack references child receipts and raw log paths;
- no full raw logs are streamed by watch.

## 20. Risks And Solutions

| Risk | Solution |
| --- | --- |
| V2 repeats V1.1 completion bugs | Prerequisite gate blocks V2 until V1.1 report/commit/evidence tests pass. |
| Workflow runner bypasses task evaluator | Scheduler must dispatch normal child tasks and read normalized V1.1 status only. |
| Workflow state lost after compaction/client exit | Store workflow/node/task state in SQLite. |
| Daemon crash duplicates child dispatch | Use workflow/node/attempt idempotency keys and reconcile on restart. |
| Generated script execution compromises local machine | Reject script fields at any manifest depth. |
| Workflow becomes token/resource sink | Require limits and enforce budgets. |
| Child agents conflict in one worktree | Parallel mutating nodes default to isolated worktrees. |
| Auto-merge breaks repo | No auto-merge in V2.0. |
| Executor prose is false | Evidence pack accepts structured receipts/artifacts only. |
| Untrusted read nodes trigger mutation | Require synthesis/gate before mutating nodes consume untrusted output. |
| Approval needed mid-workflow | Workflow blocks with authorization delta; retry-node opens fresh child attempt. |
| Stuck executor blocks entire workflow forever | Reroute within budget; then mark workflow stuck. |
| Large workflow hides partial failure | Evidence pack lists completed, blocked, stuck, skipped, and residual risks. |
| Secrets leak through templates or evidence | Keep templates generic; do not commit raw workflow evidence; run privacy gate. |

## 21. Privacy Gate

Before commit:

```bash
git status --short
git diff --check
git diff --stat
git diff -- . ':(exclude)tests/fixtures' | rg -n "sk-[A-Za-z0-9]|Bearer [A-Za-z0-9._-]+|api[_-]?key|access[_-]?token|refresh[_-]?token|session[_-]?id|BEGIN [A-Z ]*PRIVATE KEY|/path/to/local/user|raw logs|session transcript"
```

Do not commit:

- local workflow manifests with private paths;
- raw logs;
- private receipt/evidence packs;
- `~/.codex`, `~/.claude`, `~/.agpair`;
- local policy files;
- private repo names in templates.

## 22. Completion Criteria

V2 is complete only when:

- V1.1 prerequisite gate passes.
- `agpair workflow validate` accepts valid manifests and rejects script fields, cycles, unknown dependencies, and excessive limits.
- `agpair workflow start` creates workflow and node rows transactionally.
- Scheduler dispatches runnable nodes through V1.1 task creation and controller policy.
- Child task completion is read from V1.1 normalized status and artifacts.
- Workflow state survives daemon restart without duplicate child tasks.
- `workflow watch --json` emits low-noise state changes with durable receipt/log paths.
- Workflow evidence pack is built from structured receipts and durable artifacts, not executor prose.
- Approval-required and stuck child tasks produce actionable workflow states.
- No auto-merge occurs.
- MCP workflow tools validate manifests and reject scripts.
- Templates are sanitized and validate.
- Docs explain workflows as high-value orchestration, not default path for ordinary tasks.
- Unit and integration tests pass.
- Privacy gate passes before GitHub push.
