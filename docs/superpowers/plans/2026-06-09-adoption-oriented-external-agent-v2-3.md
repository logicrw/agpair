# Adoption-Oriented External Agent V2.3 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make AGPair behave like a practical external subagent layer: external CLI workers can produce review or code results, AGPair preserves and classifies those results accurately, and Codex / Claude Code can verify and adopt them with minimal controller rework.

**Architecture:** Keep AGPair as a CLI control plane, not an MCP system and not a semantic controller. The core model changes from "terminal phase equals value" to three separate outcomes: executor output, protocol normalization, and controller adoption. Mutating external work becomes a bounded implementation slice with isolated worktree evidence by default, while report-only work remains lightweight and cannot be blocked solely by optional commit or receipt-format quirks.

**Tech Stack:** Python 3.12, Typer, SQLite, local CLI executors, git worktrees, pytest, AGPair task artifacts, Codex / Claude Code skills and hooks, real executor smoke harness.

---

## 0. This Plan's Contract

This document extends `2026-06-08-practical-external-agent-first-v2-2.md` with the latest real-use findings:

- Antigravity can produce useful reports, but AGPair still misclassifies real-world terminal receipts such as `schema_version: "1.0.0"` and `status: "ready_for_review"`.
- `blocked(validation_failure)` currently mixes external work failure with AGPair protocol failure.
- Codex can salvage useful stdout/report artifacts manually, but that controller rework means AGPair has not reached native-subagent-like ergonomics.
- Real AGPair usage is still biased toward readonly review/report tasks. Implementation work needs a first-class bounded mutating workflow, not just documentation saying "external-first".
- A bounded external readonly review during this planning pass (`TASK-9FB38DE9950E`) also identified dirty worktree synchronization, independent changed-file validation, recursion guardrails, interactive prompt hangs, and isolated-worktree liveness as required implementation-slice hardening.

This plan is executable only if every task has code, tests, docs, and verification. A README-only change is not progress.

## 1. Target Product Behavior

### 1.1 Result Layers

Every attempt must expose three layers:

```text
executor_result  = did the external process produce useful artifacts?
protocol_result  = did AGPair parse and normalize the worker receipt?
adoption_result  = can the controller adopt the result with bounded rework?
```

Examples:

| Situation | Correct AGPair behavior |
| --- | --- |
| Antigravity prints a good report plus receipt with `schema_version: "1.0.0"` | `ready_for_review`, `protocol_warnings=["schema_version_alias"]`, `adoptable_result=partial` or `yes` |
| Worker exits 0, report-only task, no report/stdout receipt | `blocked`, `blocker_type=report_output_missing` |
| Worker edits files in isolated worktree and returns changed files + validation | `ready_for_review`, `adoptable_result=yes` |
| Worker is alive but only emits plugin/MCP/bootstrap noise for the threshold window | `stuck` or `blocked`, `blocker_type=no_progress_timeout` |
| Worker needs write authorization under `local_readonly` | `blocked`, `blocker_type=approval_required`, retryable via `task retry --from-block` |

### 1.2 Phase Semantics

Keep the existing external phase vocabulary, but make it stricter:

- `ready_for_review`: AGPair has enough report/evidence artifacts for controller verification, even if the receipt needed safe normalization.
- `blocked`: the controller cannot use the result without retry, auth repair, authorization expansion, or manual salvage.
- `stuck`: the worker made no useful progress or liveness became invalid.
- `abandoned`: controller or harness intentionally stopped the task.

Do not use `blocked` for low-risk receipt variants when usable report/evidence exists.

### 1.3 Implementation Delegation Semantics

For non-trivial implementation/refactor/test-fix tasks, Codex / Claude Code should first dispatch a bounded mutating slice:

```bash
agpair task start \
  --repo-path "$REPO" \
  --controller codex \
  --executor antigravity-cli \
  --authorization-profile local_mutating \
  --completion-policy evidence \
  --isolated-worktree \
  --body "$BRIEF"
```

The external worker may edit only the bounded scope. It returns changed files, validation, scope violations, and report/evidence paths. The controller owns final integration into the user's active worktree.

## 2. Files And Responsibilities

### Core Result Model

- `agpair/terminal_receipts.py`: parse and normalize structured receipts, wrapper envelopes, and mixed text + JSON.
- `agpair/completion.py`: evaluate effective completion policy from normalized receipt + artifacts.
- `agpair/adoption.py`: new module for adoption evidence, adoption blockers, controller rework classification, and result labels.
- `agpair/artifacts.py`: artifact metadata, excerpts, redaction integration, and durable paths.
- `agpair/task_terminal.py`: terminal arbitration glue between local CLI process outcome, parser result, completion decision, and task repository writes.

### Persistence

- `agpair/storage/schema.sql`: fresh DB schema for protocol/adoption attempt metadata.
- `agpair/storage/db.py`: migrations for existing databases.
- `agpair/storage/tasks.py`: repository methods to persist protocol warnings and adoption status.
- `agpair/models.py`: dataclasses for new persisted fields.

### CLI / User Surfaces

- `agpair/cli/task.py`: `status`, `list`, `logs`, `accept`, and optional adoption marking.
- `agpair/cli/workflow.py`: workflow child task status should preserve protocol/adoption summaries.
- `agpair/cli/wait.py`: low-token wait and no-progress terminal behavior.
- `agpair/watch.py`: throttled status events with artifact growth and protocol/adoption metadata.
- `skills/Codex/SKILL.md`: Codex controller external-first workflow, including mutating implementation.
- `skills/Claude/SKILL.md`: Claude Code controller external-first workflow, including mutating implementation.
- `README.md`, `README.zh-CN.md`, `docs/usage.md`, `docs/usage.zh-CN.md`: current behavior only.

### Real Executor Verification

- `scripts/smoke_real_executors.py`: report-only and tiny mutating smoke, controller matrix, adoption metrics.
- `tests/integration/test_real_executor_smoke_harness.py`: fake executor coverage for smoke behavior.

### Tests

- `tests/unit/test_receipt_validation.py`: parser and normalizer.
- `tests/unit/test_completion_policy_v1_1.py`: report/evidence/commit policy decisions.
- `tests/unit/test_adoption_result.py`: new adoption result model.
- `tests/unit/test_local_cli_executor.py`: malformed receipt salvage and process terminal cases.
- `tests/integration/test_task_start_and_status.py`: status JSON fields and body template errors.
- `tests/integration/test_task_wait.py`: no-progress and waiter release.
- `tests/integration/test_daemon_codex_lifecycle.py`: daemon/poller lifecycle under external Codex worker and liveness changes.
- `tests/integration/test_codex_cli.py`: Codex hook / accept behavior.
- `tests/integration/test_claude_cli.py`: Claude hook / accept behavior.

## 3. Task 1: Capture Real Failure Fixtures First

**Files:**

- Create: `tests/fixtures/terminal_receipts/antigravity_ready_for_review_1_0_0_mixed.txt`
- Create: `tests/fixtures/terminal_receipts/antigravity_report_with_nested_receipt.json`
- Create: `tests/fixtures/terminal_receipts/bootstrap_noise_only.stderr`
- Modify: `tests/unit/test_receipt_validation.py`
- Modify: `tests/unit/test_local_cli_executor.py`

- [ ] **Step 1: Create the Antigravity mixed-output fixture**

Add a fixture that mirrors the observed failure shape: human report first, final JSON last, `schema_version: "1.0.0"`, `status: "ready_for_review"`, report payload, `changed_files: []`, and missing or external raw paths.

Required fixture content shape:

```text
# Graphiti Lite 架构评估报告

本报告确认现有客户端 guidance 可以主动使用 Graphiti Lite，但中央契约应减少客户端枚举，并将 source anchor 规则保持为服务端无关约束。

{"schema_version":"1.0.0","task_id":"TASK-AGY-REAL","attempt_no":1,"review_round":1,"status":"ready_for_review","summary":"Completed read-only review","payload":{"report":"中文报告","changed_files":[],"validation_not_run":"report-only task","scope_violations":[],"raw_log_path":null,"receipt_path":null}}
```

- [ ] **Step 2: Create the nested envelope fixture**

Add a JSON fixture where the receipt is inside one of the known wrapper text fields:

```json
{
  "type": "result",
  "status": "success",
  "result": "human text\n{\"schema_version\":\"1.0.0\",\"task_id\":\"TASK-WRAPPED-REAL\",\"attempt_no\":1,\"review_round\":0,\"status\":\"completed\",\"summary\":\"Done\",\"payload\":{\"report\":\"usable\",\"changed_files\":[],\"validation_not_run\":\"report-only\",\"scope_violations\":[],\"raw_log_path\":\"stdout.log\",\"receipt_path\":\"receipt.json\"}}"
}
```

- [ ] **Step 3: Create the bootstrap-noise fixture**

Add stderr content that must not count as useful progress:

```text
plugin manifest warning: skipped invalid manifest
mcp-debugger initialization failed: Broken pipe
session registry sync 404 Not Found
grep timed out timeout_secs=60
```

- [ ] **Step 4: Write parser tests against the fixtures**

Add tests:

```python
def test_parse_real_antigravity_schema_1_0_0_mixed_fixture() -> None:
    raw = Path("tests/fixtures/terminal_receipts/antigravity_ready_for_review_1_0_0_mixed.txt").read_text(encoding="utf-8")

    parsed = parse_structured_terminal_receipt(raw, expected_task_id="TASK-AGY-REAL")

    assert parsed is not None
    assert parsed.schema_version == "1"
    assert parsed.status == "EVIDENCE_PACK"
    assert parsed.payload["report"] == "中文报告"
```

Expected first run before implementation: fail because `"1.0.0"` is not accepted.

- [ ] **Step 5: Run the focused failing tests**

Run:

```bash
PYTHONPATH=. pytest tests/unit/test_receipt_validation.py::test_parse_real_antigravity_schema_1_0_0_mixed_fixture -q
```

Expected before implementation: fail with `parsed is None`.

## 4. Task 2: Introduce Receipt Normalization As A First-Class Result

**Files:**

- Modify: `agpair/terminal_receipts.py`
- Modify: `tests/unit/test_receipt_validation.py`

- [ ] **Step 1: Add normalization dataclasses**

Add these dataclasses near the existing receipt dataclasses:

```python
@dataclass(frozen=True)
class ReceiptProtocolResult:
    receipt: StructuredTerminalReceipt | None
    ok: bool
    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    raw_body: str = ""

    @property
    def has_usable_receipt(self) -> bool:
        return self.receipt is not None and not self.errors
```

Keep `parse_structured_terminal_receipt()` for backward compatibility, but implement it through a new `normalize_terminal_receipt()` function.

- [ ] **Step 2: Accept schema aliases**

Update `validate_structured_receipt_dict()`:

```python
schema_version = str(parsed.get("schema_version", "")).strip()
if schema_version not in {"1", "1.0", "1.0.0"}:
    return None
```

The returned `StructuredTerminalReceipt.schema_version` remains `"1"`.

- [ ] **Step 3: Preserve protocol warnings**

`normalize_terminal_receipt()` must add warnings such as:

- `schema_version_alias` when input is `"1.0"` or `"1.0.0"`.
- `status_alias` when input status is not canonical.
- `mixed_text_json` when the parser extracted a balanced JSON object from non-JSON text.
- `wrapped_text_json` when the parser found the receipt inside a wrapper field.
- `artifact_path_missing` when `raw_log_path` or `receipt_path` is empty and AGPair must backfill later.

- [ ] **Step 4: Keep strict validation for hard errors**

These remain hard parse failures:

- no JSON object can be found;
- task id mismatch;
- `attempt_no` or `review_round` is not an integer;
- payload is not a dict;
- status cannot be normalized.

- [ ] **Step 5: Run parser tests**

Run:

```bash
PYTHONPATH=. pytest tests/unit/test_receipt_validation.py -q
```

Expected: all parser tests pass, including old wrapper tests and the new `"1.0.0"` fixture.

## 5. Task 3: Add Adoption Result Model

**Files:**

- Create: `agpair/adoption.py`
- Modify: `agpair/models.py`
- Modify: `agpair/storage/schema.sql`
- Modify: `agpair/storage/db.py`
- Modify: `agpair/storage/tasks.py`
- Create: `tests/unit/test_adoption_result.py`

- [ ] **Step 1: Implement `agpair/adoption.py`**

Add:

```python
from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal, Mapping

AdoptableResult = Literal["yes", "partial", "no", "unknown"]


@dataclass(frozen=True)
class AdoptionEvidence:
    has_report: bool = False
    has_receipt: bool = False
    has_changed_files: bool = False
    changed_files_present: bool = False
    has_validation: bool = False
    has_scope_violations: bool = False
    controller_rework_required: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AdoptionDecision:
    adoptable_result: AdoptableResult
    blockers: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    evidence: AdoptionEvidence = AdoptionEvidence()

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["blockers"] = list(self.blockers)
        payload["warnings"] = list(self.warnings)
        payload["evidence"] = self.evidence.to_dict()
        return payload
```

- [ ] **Step 2: Implement `derive_adoption_decision()`**

Rules:

```text
report policy:
  yes      = report exists and receipt or stdout exists
  partial  = report exists but receipt malformed/normalized with warnings
  no       = no report artifact and no payload report

evidence policy:
  yes      = receipt exists, changed_files declared, changed files exist or diff exists, validation exists, no scope violations
  partial  = changed files exist but validation missing or receipt warning exists
  no       = no changed files/evidence and no report

commit policy:
  yes      = commit_ref exists and validates
  partial  = diff exists but commit missing
  no       = no commit and no diff
```

The function must accept the effective policy, terminal receipt payload, artifact paths, optional git status summary, and protocol warnings.

- [ ] **Step 3: Persist adoption metadata**

Fresh schema additions in `task_attempts`:

```sql
protocol_warnings_json TEXT NOT NULL DEFAULT '[]',
protocol_errors_json TEXT NOT NULL DEFAULT '[]',
adoptable_result TEXT NOT NULL DEFAULT 'unknown',
adoption_evidence_json TEXT NOT NULL DEFAULT '{}',
controller_rework_json TEXT NOT NULL DEFAULT '{}'
```

Migration in `agpair/storage/db.py`:

```python
attempt_defaults = {
    "environment_mode": "TEXT NOT NULL DEFAULT 'managed-natural'",
    "environment_mode_source": "TEXT NOT NULL DEFAULT 'executor_default'",
    "skill_policy": "TEXT NOT NULL DEFAULT 'inherit'",
    "mcp_policy": "TEXT NOT NULL DEFAULT 'inherit'",
    "protocol_warnings_json": "TEXT NOT NULL DEFAULT '[]'",
    "protocol_errors_json": "TEXT NOT NULL DEFAULT '[]'",
    "adoptable_result": "TEXT NOT NULL DEFAULT 'unknown'",
    "adoption_evidence_json": "TEXT NOT NULL DEFAULT '{}'",
    "controller_rework_json": "TEXT NOT NULL DEFAULT '{}'",
}
```

- [ ] **Step 4: Extend `TaskAttemptRecord`**

Add matching fields in `agpair/models.py` and row hydration in `agpair/storage/tasks.py`.

- [ ] **Step 5: Add repository writer**

Add a method:

```python
def update_attempt_adoption(
    self,
    *,
    task_id: str,
    attempt_no: int,
    protocol_warnings_json: str,
    protocol_errors_json: str,
    adoptable_result: str,
    adoption_evidence_json: str,
    controller_rework_json: str = "{}",
) -> None:
    now = utcnow_iso()
    with connect(self.db_path) as conn:
        conn.execute(
            """
            UPDATE task_attempts
            SET protocol_warnings_json=?,
                protocol_errors_json=?,
                adoptable_result=?,
                adoption_evidence_json=?,
                controller_rework_json=?,
                updated_at=?
            WHERE task_id=? AND attempt_no=?
            """,
            (
                protocol_warnings_json,
                protocol_errors_json,
                adoptable_result,
                adoption_evidence_json,
                controller_rework_json,
                now,
                task_id,
                attempt_no,
            ),
        )
        conn.commit()
```

- [ ] **Step 6: Unit test adoption decisions**

Tests:

```python
def test_report_with_protocol_warning_is_partial_adoptable(tmp_path: Path) -> None:
    report = tmp_path / "report.md"
    report.write_text("usable", encoding="utf-8")
    decision = derive_adoption_decision(
        effective_policy=report_policy,
        receipt={"status": "EVIDENCE_PACK", "payload": {"report": "usable"}},
        report_path=str(report),
        protocol_warnings=("schema_version_alias",),
    )
    assert decision.adoptable_result == "partial"
    assert "schema_version_alias" in decision.warnings
```

Run:

```bash
PYTHONPATH=. pytest tests/unit/test_adoption_result.py -q
```

Expected: pass.

## 6. Task 4: Make Completion Policy Use Normalized Protocol And Adoption

**Files:**

- Modify: `agpair/completion.py`
- Modify: `agpair/task_terminal.py`
- Modify: `agpair/executors/local_cli.py`
- Modify: `tests/unit/test_completion_policy_v1_1.py`
- Modify: `tests/unit/test_local_cli_executor.py`

- [ ] **Step 1: Keep policy semantics strict**

`evaluate_completion()` remains policy-aware:

- `report`: requires report text or report artifact.
- `evidence`: requires machine-checkable evidence, not necessarily commit.
- `commit`: requires commit evidence.

- [ ] **Step 2: Do not hard-block low-risk receipt variants**

When receipt normalization produces a receipt with warnings and report/evidence exists:

```text
phase = ready_for_review
ok = true
reason_code = None
protocol_warnings = ["schema_version_alias", "status_alias"]
adoptable_result = partial or yes
```

When receipt cannot be normalized but report artifact exists:

```text
phase = ready_for_review
ok = true
reason_code = "malformed_terminal_receipt"
protocol_errors = ["malformed_terminal_receipt"]
adoptable_result = partial
```

Only use `blocked(validation_failure)` if required policy evidence is missing.

- [ ] **Step 3: Rename misleading blockers**

Map process exits by effective policy:

| Effective policy | Missing evidence blocker |
| --- | --- |
| `report` | `report_output_missing` |
| `evidence` | `evidence_output_missing` |
| `commit` | `missing_commit_for_commit_policy` |

No report-only path may emit "without committing".

- [ ] **Step 4: Add regression test for the observed Antigravity case**

Test shape:

```python
def test_report_policy_keeps_report_when_terminal_receipt_has_safe_aliases(tmp_path: Path) -> None:
    stdout = tmp_path / "stdout.log"
    stdout.write_text(Path("tests/fixtures/terminal_receipts/antigravity_ready_for_review_1_0_0_mixed.txt").read_text(encoding="utf-8"), encoding="utf-8")
    report = tmp_path / "report.md"
    report.write_text("中文报告", encoding="utf-8")

    # Use the local CLI terminal arbitration helper, not only the parser.
    decision = evaluate_completion(
        effective_policy=policy,
        receipt=normalized_receipt,
        evidence=ExecutionEvidence(
            stdout_path=str(stdout),
            report_path=str(report),
            receipt_valid=True,
            structured_status="EVIDENCE_PACK",
        ),
        process_returncode=0,
    )

    assert decision.phase == "ready_for_review"
    assert decision.reason_code is None
```

Run:

```bash
PYTHONPATH=. pytest tests/unit/test_completion_policy_v1_1.py tests/unit/test_local_cli_executor.py -q
```

Expected: pass.

## 7. Task 5: Surface Protocol And Adoption In CLI Status

**Files:**

- Modify: `agpair/cli/task.py`
- Modify: `agpair/cli/workflow.py`
- Modify: `agpair/workflows/evidence.py`
- Modify: `tests/integration/test_task_start_and_status.py`

- [ ] **Step 1: Add status JSON fields**

`agpair task status TASK --json` must include:

```json
{
  "protocol_result": {
    "ok": true,
    "warnings": ["schema_version_alias"],
    "errors": []
  },
  "adoption_result": {
    "adoptable_result": "partial",
    "blockers": [],
    "warnings": ["schema_version_alias"],
    "evidence": {
      "has_report": true,
      "has_receipt": true,
      "has_changed_files": false,
      "has_validation": true
    }
  }
}
```

- [ ] **Step 2: Keep existing artifact paths**

Do not remove:

- `stdout_path`
- `stderr_path`
- `receipt_path`
- `report_path`
- `evidence_path`
- `executor_output_excerpt`
- `active_attempt_artifacts`

- [ ] **Step 3: Add human status lines**

Non-JSON status should show:

```text
protocol_warnings: schema_version_alias,status_alias
adoptable_result: partial
adoption_blockers: none
```

- [ ] **Step 4: Integration test**

Use a fake executor that emits the `"1.0.0"` fixture and assert:

```python
assert payload["phase"] == "ready_for_review"
assert payload["protocol_result"]["warnings"]
assert payload["adoption_result"]["adoptable_result"] in {"yes", "partial"}
```

Run:

```bash
PYTHONPATH=. pytest tests/integration/test_task_start_and_status.py -q
```

- [ ] **Step 5: Preserve protocol/adoption summaries in workflow child status**

When a workflow node points at a task, workflow evidence must include:

```json
{
  "task_id": "TASK-123",
  "phase": "ready_for_review",
  "protocol_result": {
    "warnings": ["schema_version_alias"],
    "errors": []
  },
  "adoption_result": {
    "adoptable_result": "partial",
    "blockers": []
  }
}
```

Run:

```bash
PYTHONPATH=. pytest tests/integration/test_workflow_cli.py tests/integration/test_workflow_watch.py -q
```

## 8. Task 6: Add Controller Adoption Commands

**Files:**

- Modify: `agpair/cli/task.py`
- Modify: `agpair/storage/tasks.py`
- Modify: `tests/integration/test_codex_cli.py`
- Modify: `tests/integration/test_claude_cli.py`
- Modify: `README.md`
- Modify: `README.zh-CN.md`

- [ ] **Step 1: Keep `task accept` as the normal success closeout**

Existing command remains:

```bash
agpair task accept TASK-123
```

It sets `is_approved=true`.

- [ ] **Step 2: Add explicit adoption metadata options**

Extend accept:

```bash
agpair task accept TASK-123 \
  --adoptable-result yes \
  --controller-rework none \
  --note "report used in final answer"
```

Allowed `--adoptable-result`:

```text
yes
partial
no
unknown
```

Allowed `--controller-rework`:

```text
none
minor
major
redone
```

- [ ] **Step 3: Add salvage command for malformed protocol cases**

Add:

```bash
agpair task adopt TASK-123 --from-report --adoptable-result partial
```

Behavior:

- Requires existing `report_path` or non-empty `stdout_path`.
- Does not change a genuinely failed executor into success silently.
- Sets `is_approved=true`.
- Records adoption metadata and a journal event `controller_adopted_report`.

- [ ] **Step 4: Hook regression**

Stop hooks must not repeatedly block when:

```text
phase=ready_for_review and is_approved=true
```

Nor when:

```text
phase=blocked and adoptable_result=partial and is_approved=true
```

Run:

```bash
PYTHONPATH=. pytest tests/integration/test_codex_cli.py tests/integration/test_claude_cli.py -q
```

## 9. Task 7: Make Bounded Implementation The Default Mutating Delegation Pattern

**Files:**

- Modify: `skills/Codex/SKILL.md`
- Modify: `skills/Claude/SKILL.md`
- Modify: `README.md`
- Modify: `README.zh-CN.md`
- Modify: `docs/usage.md`
- Modify: `docs/usage.zh-CN.md`
- Modify: `scripts/smoke_real_executors.py`
- Modify: `tests/integration/test_real_executor_smoke_harness.py`

- [ ] **Step 1: Define the bounded implementation brief**

Add this template to both controller skills:

```text
Goal:
Implement one bounded code slice that the controller can verify and integrate.

Scope:
- Allowed files:
  - agpair/terminal_receipts.py
  - tests/unit/test_receipt_validation.py
- Forbidden files:
  - ~/.agpair
  - ~/.codex
  - ~/.claude
- Use an isolated worktree. Do not touch unrelated files.

Required changes:
- Accept `schema_version: "1.0.0"` as a safe receipt schema alias and normalize it to `"1"`.
- Return changed_files and scope_violations in the terminal receipt.

Exit criteria:
- Run `PYTHONPATH=. pytest tests/unit/test_receipt_validation.py -q`.
- Return terminal receipt with changed_files, validation or validation_not_run, scope_violations, report, raw_log_path, receipt_path.
- Do not push. Commit only if explicitly requested.
```

- [ ] **Step 2: Add the dispatch rule to skills**

Codex skill rule:

```text
For non-trivial implementation/refactor/test-fix, first dispatch one bounded implementation slice through AGPair unless the task is tiny, sensitive, external executors are unhealthy, or prior external result was low quality.
```

Claude skill mirrors the same rule with its controller matrix.

- [ ] **Step 3: Use existing CLI flags instead of inventing a new kind system**

The recommended command is:

```bash
agpair task start \
  --repo-path "$REPO" \
  --controller codex \
  --executor antigravity-cli \
  --authorization-profile local_mutating \
  --completion-policy evidence \
  --isolated-worktree \
  --body "$BRIEF"
```

Do not add `--kind` or `--task-profile` in this phase. The current CLI already has the needed policy knobs.

- [ ] **Step 4: Add smoke validation for mutating adoption**

The smoke harness already creates a tiny file. Extend its report to make mutating adoption explicit:

```json
{
  "mode": "tiny_mutating",
  "completion_policy": "evidence",
  "isolated_worktree": true,
  "adoptable_result": true,
  "changed_files_present": true,
  "validation_present": true
}
```

- [ ] **Step 5: Add controller matrix smoke expectations**

Tests must assert:

- Codex controller tests `antigravity-cli,grok-cli,claude-code`.
- Claude Code controller tests `antigravity-cli,grok-cli,codex`.
- Codex controller suppresses external `codex` unless explicitly allowed.
- Claude Code controller suppresses external `claude-code` unless explicitly allowed.

Run:

```bash
PYTHONPATH=. pytest tests/integration/test_real_executor_smoke_harness.py -q
```

## 10. Task 8: Make Isolated Mutating Work Adoptable

**Files:**

- Create: `agpair/scope_validation.py`
- Create: `agpair/delegation_guard.py`
- Modify: `agpair/executors/local_cli.py`
- Modify: `agpair/runtime_liveness.py`
- Modify: `agpair/cli/task.py`
- Modify: `agpair/storage/schema.sql`
- Modify: `agpair/storage/db.py`
- Modify: `agpair/storage/tasks.py`
- Create: `tests/unit/test_scope_validation.py`
- Create: `tests/unit/test_delegation_guard.py`
- Modify: `tests/integration/test_task_start_and_status.py`
- Modify: `tests/integration/test_task_wait.py`

- [ ] **Step 1: Add dirty snapshot metadata to attempts**

Fresh schema additions in `task_attempts`:

```sql
dirty_snapshot_mode TEXT NOT NULL DEFAULT 'off',
dirty_snapshot_json TEXT NOT NULL DEFAULT '{}',
dirty_snapshot_applied INTEGER NOT NULL DEFAULT 0
```

Migration additions in `agpair/storage/db.py`:

```python
attempt_defaults = {
    "dirty_snapshot_mode": "TEXT NOT NULL DEFAULT 'off'",
    "dirty_snapshot_json": "TEXT NOT NULL DEFAULT '{}'",
    "dirty_snapshot_applied": "INTEGER NOT NULL DEFAULT 0",
}
```

- [ ] **Step 2: Add task start option for dirty snapshots**

Extend `agpair task start`:

```bash
--dirty-snapshot [off|tracked]
```

Default resolution:

```text
off     = report-only, readonly, or non-isolated task
tracked = isolated local_mutating/evidence task
```

Do not copy ignored files or untracked files by default. The controller can include untracked content in the body only when it has verified it is not secret.

- [ ] **Step 3: Capture and apply tracked dirty state**

In `agpair/executors/local_cli.py`, before creating the isolated worktree:

```bash
git status --porcelain=v1 --untracked-files=all
git diff --binary > attempt/context/unstaged.diff
git diff --cached --binary > attempt/context/staged.diff
```

After `git worktree add`, apply tracked diffs:

```bash
git apply --3way --whitespace=nowarn attempt/context/staged.diff
git apply --3way --whitespace=nowarn attempt/context/unstaged.diff
```

If apply fails, mark:

```text
phase = blocked
blocker_type = dirty_snapshot_apply_failed
recoverable = true
recommended_next_action = retry_without_dirty_snapshot_or_clean_worktree
```

- [ ] **Step 4: Validate receipt changed files against actual worktree changes**

Implement `agpair/scope_validation.py`:

```python
@dataclass(frozen=True)
class ScopeValidationResult:
    ok: bool
    declared_changed_files: tuple[str, ...]
    actual_changed_files: tuple[str, ...]
    undeclared_changed_files: tuple[str, ...]
    missing_declared_files: tuple[str, ...]
    forbidden_changed_files: tuple[str, ...]

    @property
    def blockers(self) -> tuple[str, ...]:
        result: list[str] = []
        if self.undeclared_changed_files:
            result.append("undeclared_changed_files")
        if self.missing_declared_files:
            result.append("changed_files_not_present")
        if self.forbidden_changed_files:
            result.append("forbidden_path_modified")
        return tuple(result)
```

Default forbidden paths:

```text
.agpair/
.git/
~/.agpair
~/.codex
~/.claude
```

For V2.3, machine validation must verify actual changed files are declared and do not hit default forbidden paths. Full allowed-path semantics remain controller-verified from the task brief until a future machine-readable scope field is added.

- [ ] **Step 5: Feed scope validation into adoption**

For `completion_policy=evidence`, adoption is `yes` only when:

- receipt has `changed_files`;
- actual changed files match or are a subset of declared changed files;
- no default forbidden path was modified;
- validation or validation_not_run is present;
- no non-empty `scope_violations` were reported.

If files changed but validation is missing:

```text
adoptable_result = partial
blockers = ["validation_missing"]
```

If forbidden files changed:

```text
adoptable_result = no
blockers = ["forbidden_path_modified"]
```

- [ ] **Step 6: Add recursive delegation guard**

Create `agpair/delegation_guard.py`:

```python
def current_delegation_depth(env: Mapping[str, str] | None = None) -> int:
    value = (env or os.environ).get("AGPAIR_DELEGATION_DEPTH", "0")
    try:
        return max(0, int(value))
    except ValueError:
        return 0


def next_delegation_env(task_id: str, env: Mapping[str, str] | None = None) -> dict[str, str]:
    depth = current_delegation_depth(env)
    return {
        "AGPAIR_PARENT_TASK_ID": task_id,
        "AGPAIR_DELEGATION_DEPTH": str(depth + 1),
        "AGPAIR_NONINTERACTIVE": "1",
    }
```

`agpair task start` refuses nested delegation when `AGPAIR_DELEGATION_DEPTH >= 1` unless the user passes:

```bash
--allow-nested-delegation
```

Failure:

```text
error = nested_delegation_blocked
```

- [ ] **Step 7: Make local CLI execution non-interactive**

In `agpair/executors/local_cli.py`:

- set `stdin=subprocess.DEVNULL`;
- set `CI=1`;
- set `AGPAIR_NONINTERACTIVE=1`;
- set `AGPAIR_PARENT_TASK_ID`;
- set `AGPAIR_DELEGATION_DEPTH`.

Prompt patterns in stdout/stderr:

```text
Press enter to continue
Do you want to proceed
Approve
Continue?
Waiting for input
```

If prompt pattern appears and no useful artifacts are produced before threshold:

```text
blocker_type = executor_waiting_for_input
recommended_next_action = switch_executor_or_retry_with_noninteractive_flags
```

- [ ] **Step 8: Fix isolated worktree liveness path**

All workspace activity and git status checks for an active isolated task must use:

```text
execution_repo_path
```

not the controller's original `repo_path`, unless `execution_repo_path` is empty.

Regression:

```python
def test_liveness_uses_execution_repo_path_for_isolated_worktree(tmp_path: Path) -> None:
    original = tmp_path / "repo"
    worktree = tmp_path / "repo" / ".agpair" / "worktrees" / "TASK-123"
    # Create a change only in worktree.
    # Assert status sees activity from worktree, not original repo.
```

- [ ] **Step 9: Tests**

Run:

```bash
PYTHONPATH=. pytest \
  tests/unit/test_scope_validation.py \
  tests/unit/test_delegation_guard.py \
  tests/integration/test_task_start_and_status.py \
  tests/integration/test_task_wait.py \
  -q
```

Expected: dirty snapshot metadata appears in status, scope validation detects undeclared changes, nested delegation is blocked by default, and isolated liveness uses `execution_repo_path`.

## 11. Task 9: Improve No-Progress And Low-Quality Detection

**Files:**

- Modify: `agpair/daemon/loop.py`
- Modify: `agpair/runtime_liveness.py`
- Modify: `agpair/cli/wait.py`
- Modify: `agpair/cli/task.py`
- Modify: `agpair/watch.py`
- Modify: `tests/integration/test_task_wait.py`
- Modify: `tests/integration/test_task_watch_events.py`
- Modify: `tests/integration/test_daemon_codex_lifecycle.py`

- [ ] **Step 1: Define useful progress**

Useful progress is any of:

- non-empty report artifact;
- receipt/evidence artifact;
- stdout containing report content, terminal receipt, or explicit tool progress;
- git diff/status change in the execution worktree;
- validation output attached to receipt/evidence.

Not useful progress:

- plugin discovery warnings;
- MCP broken pipe;
- auth warnings;
- registry sync warnings;
- repeated "thinking" logs without artifacts;
- stderr bootstrap logs only.

- [ ] **Step 2: Implement bootstrap-noise classifier**

Add a function:

```python
def is_bootstrap_noise(text: str) -> bool:
    lowered = text.lower()
    markers = (
        "plugin manifest",
        "mcp",
        "broken pipe",
        "registry sync",
        "grep timed out",
        "initializing",
    )
    return bool(text.strip()) and any(marker in lowered for marker in markers)
```

Use this only for no-progress classification. Do not hide the logs.

- [ ] **Step 3: Use profile-driven thresholds**

Effective defaults:

| Task shape | Diagnostic threshold | Timeout threshold |
| --- | ---: | ---: |
| readonly/report | 60s | 180s |
| tiny mutating smoke | 60s | 240s |
| bounded implementation | 120s | 600s |
| explicit long-running | from user/task timeout | from user/task timeout |

- [ ] **Step 4: Make wait/watch show the evidence**

`task watch --json` should emit small events:

```json
{
  "event": "artifact_progress",
  "stdout_size": 1200,
  "stderr_size": 9200,
  "useful_progress": false,
  "last_output_excerpt": "plugin manifest warning: skipped invalid manifest"
}
```

- [ ] **Step 5: Tests**

Add a fake executor that emits only the bootstrap fixture and sleeps. Assert:

```python
assert payload["phase"] in {"blocked", "stuck", "abandoned"}
assert payload["failure_context"]["blocker_type"] == "no_progress_timeout"
```

Run:

```bash
PYTHONPATH=. pytest tests/integration/test_task_wait.py tests/integration/test_task_watch_events.py -q
```

Also run the daemon lifecycle regression:

```bash
PYTHONPATH=. pytest tests/integration/test_daemon_codex_lifecycle.py -q
```

## 12. Task 10: Keep Executor Equality, But Improve Health

**Files:**

- Modify: `agpair/executors/policy.py`
- Modify: `agpair/executors/registry.py`
- Modify: `agpair/executors/health.py`
- Modify: `agpair/cli/doctor.py`
- Modify: `tests/unit/test_executor_onboarding.py`
- Modify: `tests/unit/test_executor_health.py`

- [ ] **Step 1: Preserve active executor defaults**

All active executors remain:

```text
environment_mode = managed-natural
skill_policy = inherit
mcp_policy = inherit
```

This applies to:

- `antigravity-cli`
- `grok-cli`
- `claude-code`
- `codex`

- [ ] **Step 2: Keep self-executor suppression in controller routing**

Do not special-case executor launch behavior for self-workers. The routing layer decides:

| Controller | Suppressed by default |
| --- | --- |
| Codex | external `codex` |
| Claude Code | external `claude-code` |

- [ ] **Step 3: Health must show why a worker should not be used**

Health snapshot fields:

```json
{
  "executor_id": "claude-code",
  "binary_available": true,
  "launch_probe": "ok",
  "auth_state": "executor_auth_required",
  "auth_source": "ccswitch",
  "environment_mode": "managed-natural",
  "skill_policy": "inherit",
  "mcp_policy": "inherit",
  "last_failure_type": "Invalid Authentication"
}
```

- [ ] **Step 4: Tests**

Run:

```bash
PYTHONPATH=. pytest tests/unit/test_executor_onboarding.py tests/unit/test_executor_health.py tests/unit/test_executor_routing.py -q
```

## 13. Task 11: Document Controller Rework And External Value Metrics

**Files:**

- Modify: `scripts/smoke_real_executors.py`
- Modify: `agpair/cli/task.py`
- Modify: `README.md`
- Modify: `README.zh-CN.md`
- Modify: `docs/usage.md`
- Modify: `docs/usage.zh-CN.md`

- [ ] **Step 1: Add value metrics to smoke output**

Every smoke result includes:

```json
{
  "adoptable_result": "yes",
  "time_to_first_useful_signal_seconds": 18.4,
  "fallback_suggestion": null,
  "controller_rework": "none",
  "protocol_warnings": [],
  "failure_class": null
}
```

- [ ] **Step 2: Add task list summary**

`agpair task list --json` should include enough fields to compute recent value:

```json
{
  "task_id": "TASK-123",
  "executor": "antigravity-cli",
  "phase": "ready_for_review",
  "effective_completion_policy": "evidence",
  "adoptable_result": "yes",
  "blocker_type": null,
  "protocol_warnings": []
}
```

- [ ] **Step 3: Add docs section "How to judge AGPair value"**

Document these metrics:

- completion rate;
- adoptable-result rate;
- time-to-first-useful-signal;
- fallback rate;
- controller rework rate;
- abandoned/no-progress rate.

- [ ] **Step 4: Verify docs do not equate dispatch success with value**

Run:

```bash
rg -n "dispatch success|task start.*success|process alive.*success|phase success" README.md README.zh-CN.md docs skills || true
```

Expected: no wording that says start/process/phase alone proves success.

## 14. Task 12: Local Config And Skills Sync

**Files:**

- Modify: `agpair/cli/codex.py`
- Modify: `agpair/cli/claude.py`
- Modify: `agpair/cli/skill_sync.py`
- Modify: `skills/Codex/SKILL.md`
- Modify: `skills/Claude/SKILL.md`
- Modify: `tests/integration/test_codex_cli.py`
- Modify: `tests/integration/test_claude_cli.py`

- [ ] **Step 1: Ensure repo skills include the V2.3 workflow**

Both skills must include:

1. dispatch;
2. wait/watch;
3. status JSON;
4. read report/receipt/stdout/stderr;
5. verify diff/tests/evidence;
6. accept/adopt;
7. fallback when low quality;
8. bounded implementation dispatch for non-trivial code tasks.

- [ ] **Step 2: Make skill sync explicit**

If existing commands are retained:

```bash
agpair codex config --dry-run
agpair codex config --apply
agpair claude config --dry-run
agpair claude config --apply
```

they must sync skills, not only hooks/settings.

If they cannot remain clear, add:

```bash
agpair skills sync --controller codex --dry-run
agpair skills sync --controller codex --apply
agpair skills sync --controller claude-code --dry-run
agpair skills sync --controller claude-code --apply
```

- [ ] **Step 3: Preserve user-owned config**

Merge-not-overwrite:

- keep non-AGPair `statusLine`;
- keep unrelated hooks;
- keep unrelated Claude/Codex settings;
- write backup path before apply;
- uninstall removes only AGPair-managed entries.

- [ ] **Step 4: Verify local sync manually after implementation**

Commands:

```bash
cmp skills/Codex/SKILL.md ~/.codex/skills/agpair-codex/SKILL.md
cmp skills/Claude/SKILL.md ~/.claude/skills/agpair/SKILL.md
```

Expected: both commands exit 0 after explicit apply.

## 15. Task 13: Verification Matrix

**Files:**

- No new production files. This task verifies the complete patch.

- [ ] **Step 1: Run focused tests first**

```bash
PYTHONPATH=. pytest \
  tests/unit/test_receipt_validation.py \
  tests/unit/test_completion_policy_v1_1.py \
  tests/unit/test_adoption_result.py \
  tests/unit/test_local_cli_executor.py \
  tests/unit/test_executor_onboarding.py \
  tests/unit/test_executor_health.py \
  -q
```

Expected: pass.

- [ ] **Step 2: Run integration tests for user-facing flows**

```bash
PYTHONPATH=. pytest \
  tests/integration/test_task_start_and_status.py \
  tests/integration/test_task_wait.py \
  tests/integration/test_task_watch_events.py \
  tests/integration/test_codex_cli.py \
  tests/integration/test_claude_cli.py \
  tests/integration/test_real_executor_smoke_harness.py \
  -q
```

Expected: pass.

- [ ] **Step 3: Run full suite**

```bash
PYTHONPATH=. pytest -q
```

Expected: pass.

- [ ] **Step 4: Run syntax and doc wording guard**

```bash
git diff --check
rg -n -- "--repo\\b|--title\\b|--prompt\\b|managed-restricted|isolated-bare|managed-isolated|AGPAIR_CODEX_IGNORE_USER_CONFIG|Antigravity IDE.*executor|Gemini.*new" README.md README.zh-CN.md docs skills agpair tests || true
```

Expected:

- `git diff --check` exits 0.
- `rg` output contains only historical/archive references or intentionally documented deprecated surfaces.

- [ ] **Step 5: Run real executor smoke**

Codex controller matrix:

```bash
PYTHONPATH=. python scripts/smoke_real_executors.py \
  --repo-path <repo> \
  --controller codex \
  --executors antigravity-cli,grok-cli,claude-code \
  --timeout-seconds 300 \
  --no-progress-seconds 120
```

Claude Code controller matrix:

```bash
PYTHONPATH=. python scripts/smoke_real_executors.py \
  --repo-path <repo> \
  --controller claude-code \
  --executors antigravity-cli,grok-cli,codex \
  --timeout-seconds 300 \
  --no-progress-seconds 120
```

Diagnostic matrix:

```bash
PYTHONPATH=. python scripts/smoke_real_executors.py \
  --repo-path <repo> \
  --controller diagnostic \
  --all-registered \
  --allow-self-executor \
  --timeout-seconds 300 \
  --no-progress-seconds 120
```

Expected:

- smoke report records `adoptable_result`;
- no smoke result is counted successful on phase alone;
- failures are classified as auth/unavailable/no-progress/malformed-receipt/report-missing/low-quality;
- worktrees are cleaned;
- raw smoke artifacts remain ignored and uncommitted.

## 16. Implementation Order And Stop Rules

### Phase A: Parser And Protocol

- [ ] Task 1 fixtures.
- [ ] Task 2 normalizer.
- [ ] Task 4 completion salvage for safe variants.

Stop if the Antigravity `"1.0.0"` mixed fixture still becomes `blocked(validation_failure)` when report artifacts exist.

### Phase B: Adoption Model

- [ ] Task 3 adoption module and persistence.
- [ ] Task 5 status fields.
- [ ] Task 6 accept/adopt commands.

Stop if status cannot tell these apart:

```text
external worker produced no useful output
external worker produced useful output but receipt was non-canonical
external worker produced adoptable implementation evidence
```

### Phase C: Mutating External Work

- [ ] Task 7 bounded implementation workflow.
- [ ] Task 8 isolated mutating work adoption.
- [ ] Task 11 value metrics.
- [ ] Update skills and docs.

Stop if non-trivial implementation can still complete without either an external bounded attempt or an explicit controller-side reason for skipping AGPair.

### Phase D: Liveness And Health

- [ ] Task 9 no-progress.
- [ ] Task 10 executor equality and health.

Stop if `acked + silent` still requires manual `ps`, `ls`, or raw log inspection to decide whether to abandon.

### Phase E: Deployment And Verification

- [ ] Task 12 local config/skills sync.
- [ ] Task 13 complete verification.

Stop if repo changes pass but local Codex / Claude Code skills still use old behavior.

## 17. Definition Of Done

The V2.3 implementation is complete only when all statements are true:

- `schema_version: "1.0.0"` and `status: "ready_for_review"` receipts are normalized safely.
- Malformed but useful report output is preserved and surfaced as adoptable or partial, not discarded as generic blocked.
- `blocked` no longer conflates executor failure with AGPair protocol warning.
- `task status --json` exposes protocol and adoption result fields.
- `task accept` records whether the controller adopted the result.
- `task adopt --from-report` can close a useful report that had receipt problems without lying about protocol quality.
- Non-trivial implementation/refactor/test-fix guidance tells Codex / Claude Code to dispatch bounded mutating slices first.
- Mutating external tasks use `local_mutating`, `completion-policy=evidence`, and isolated worktrees by default in skills and smoke.
- Isolated mutating tasks either apply tracked dirty snapshots safely or expose why they could not.
- AGPair independently compares receipt `changed_files` with actual execution worktree changes.
- Nested AGPair delegation is blocked by default to prevent external workers recursively spawning more workers.
- Interactive prompt hangs become `executor_waiting_for_input` or no-progress failures instead of silent waits.
- Liveness and workspace activity use `execution_repo_path` for isolated worktrees.
- Real executor smoke verifies tiny mutating output, not only readonly reports.
- AGPair value is measured by adoptable result and controller rework, not task dispatch.
- No active executor is made special by hidden launch isolation or default capability suppression.
- No docs imply Gemini or Antigravity IDE are active new-task executors.
- Local `~/.codex` and `~/.claude` skills/hooks/settings are synced through explicit dry-run/apply commands.
- Full tests and real executor smoke have either passed or produced precise blockers with no privacy leakage.

## 18. What This Plan Does Not Do

- It does not make AGPair an MCP runtime.
- It does not add a default capability-bundle system.
- It does not disable skills/MCP by default.
- It does not require live in-process approval/resume.
- It does not remove Codex or Claude Code native subagents.
- It does not make external workers trusted. The controller still verifies every artifact, diff, and test result.
- It does not claim all code work should be external-only. It makes external bounded implementation the default first attempt for non-trivial delegatable work, with direct/native fallback when evidence says AGPair is unsuitable.
