# AGPair Core Task Model And Controller Policy V1.1 Implementation Plan

> Historical archive: this plan preserves implementation context from an earlier
> phase. It is not the current behavior contract. Use `README.md`,
> `docs/usage.md`, and `docs/executor-lifecycle.md` for current executor
> routing and lifecycle rules.

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor AGPair's single-task core so Codex and Claude Code can delegate to external CLI executors first while AGPair correctly handles commit, report, and evidence-based completion without hardcoding every successful task as a commit.

**Architecture:** V1.1 is a core-model cleanup, not a workflow engine. Add first-class attempts, durable artifacts, normalized `ready_for_review`, effective task policy, a unified completion evaluator, and one controller-aware executor policy resolver. All daemon, wait, CLI, hooks, docs, and tests must consume these core primitives; no feature may exist only as wording in docs or hook text.

**Tech Stack:** Python 3.12, Typer, SQLite, stdlib dataclasses/JSON/pathlib/hashlib, local CLI executors, pytest.

---

## 1. Decision Summary

V1.1 fixes the mismatch between the original external-agent-first design and the current code:

- The original design said external work ends in `ready_for_review` with evidence; commit is optional unless required.
- The current model still defaults to `direct_commit`, so read-only and report tasks are blocked for missing commits.
- The original design said AGPair owns raw log paths, receipts, retry context, and watch cursors; current local CLI artifacts can be deleted after terminal cleanup.
- The original design said controller-specific executor routing should be centralized; current routing, binary health, hooks, and task start still duplicate decisions.

V1.1 must make these concepts real in code:

1. `Task`: stable user intent and controller-visible current state.
2. `Attempt`: one concrete executor run with its own authorization, completion policy, session, terminal decision, and timestamps.
3. `Artifact`: durable stdout/stderr/receipt/report/evidence paths under AGPair state, never transient executor temp paths.
4. `Receipt`: structured executor claim, always validated before it can satisfy a task.
5. `EffectiveTaskPolicy`: derived from authorization profile, completion policy, brief hints, and controller context.
6. `CompletionDecision`: one result produced by a unified evaluator and applied by daemon, wait, and local CLI paths.
7. `ControllerPolicy`: one resolver for Codex, Claude Code, generic controller, executor suppression, and health-aware selection.

Do not implement workflow DAGs in V1.1. V2 depends on this plan and must not reimplement these primitives.

## 2. Constraints

- Scale: local single-user workstation, normally 1-10 active tasks and at most a few attempts per task.
- Consistency: attempts and artifacts must be durable before terminal cleanup. A retry must not overwrite or erase the previous attempt's evidence.
- Latency: `task start`, `task status`, hooks, and `policy show` stay interactive. Long work stays in executor processes and `wait/watch`.
- Team: small Python codebase. Prefer explicit modules and tests over framework abstractions.
- Cost: external CLI executors first; native subagents remain controller-side fallback or review.

## 3. Non-Goals

- No workflow/DAG engine. That is V2.
- No runtime approval/resume state machine.
- No arbitrary generated script execution.
- No full benchmark-driven executor scoring system.
- No OMX source-code changes.
- No production deploy, git push, credential mutation, or destructive cleanup feature.
- No automatic merge or automatic user-facing completion. Controller remains verifier.

## 4. Core Model Changes

### 4.1 Normalized Phases

Keep legacy values readable, but normalize new successful terminal work to `ready_for_review`.

Canonical phases:

```text
new -> acked -> ready_for_review
             -> blocked
             -> stuck
             -> abandoned
```

Legacy/read aliases:

- `committed` remains readable and maps to controller state `ready_for_review` when the receipt is valid.
- `evidence_ready` remains readable and maps to controller state `ready_for_review` when the evidence pack is valid.
- Wire receipt status `COMMITTED` remains accepted for backwards compatibility, but it must not imply that a git commit is mandatory.
- Wire receipt status `EVIDENCE_PACK` remains accepted and should normally become `ready_for_review`.

### 4.2 Completion Policies

Replace commit-only completion with these values:

```text
auto
evidence
report
commit
```

Legacy aliases:

- `direct_commit` -> `commit`
- `evidence_ready` -> `evidence`

Resolution:

| Input | Effective policy |
| --- | --- |
| `--completion-policy commit` | `commit` |
| `--completion-policy report` | `report` |
| `--completion-policy evidence` | `evidence` |
| `--completion-policy auto` + `authorization_profile=local_readonly` | `report` |
| `--completion-policy auto` + body says `Required changes: none`, `Required changes: 无`, `禁止写入`, or `read-only` | `report` |
| `--completion-policy auto` + explicit commit wording such as `must commit`, `commit required`, `提交 commit` | `commit` |
| `--completion-policy auto` otherwise | `evidence` |

Policy semantics:

- `commit`: a verified task-specific commit is required.
- `evidence`: commit is optional; AGPair needs valid structured evidence such as changed files, validation, scope checks, report, diff, or receipt paths.
- `report`: commit and file mutation are not required; AGPair needs a captured report artifact or valid structured report receipt.
- `auto`: never stored as final effective policy on attempts. Store both requested policy and resolved effective policy.

### 4.3 Effective Task Policy

Expose this in `task status --json`:

```json
{
  "effective_task_policy": {
    "requested_completion_policy": "auto",
    "effective_completion_policy": "report",
    "authorization_profile": "local_readonly",
    "allows_file_edits": false,
    "allows_commit": false,
    "requires_commit": false,
    "requires_report": true,
    "requires_machine_evidence": true,
    "report_only": true,
    "source": "authorization_profile"
  },
  "effective_task_safety": {
    "is_mutating": false,
    "is_concurrency_safe": true
  },
  "backend_safety_metadata": {
    "is_mutating": true,
    "is_concurrency_safe": false,
    "requires_human_interaction": false
  }
}
```

Do not remove backend safety metadata; rename it away from task safety.

### 4.4 Durable Attempt And Artifact Layout

Every attempt gets a durable directory:

```text
~/.agpair/tasks/TASK-123/attempt-1/
  stdout.log
  stderr.log
  receipt.json
  report.md
  evidence.json
  metadata.json
```

Rules:

- Executor temp directories may still exist during runtime.
- Before terminal cleanup, copy stdout/stderr and any parsed receipt/report into the durable attempt directory.
- All receipt payload paths must point to durable paths, not temp paths.
- `task logs` should keep journal output.
- `task logs --include-executor-output` may read durable stdout/stderr excerpts.
- `task status --json` must expose top-level `artifact_paths`, `stdout_path`, `stderr_path`, `receipt_path`, `report_path`, and `executor_output_excerpt` when present.

### 4.5 Low-Noise Wait And Watch Contract

Codex and Claude Code should be able to start external work and wait for completion without spending model turns on repeated polling.

Required CLI behavior:

```bash
agpair task start --body-file task.md --controller codex --wait --json
agpair task wait TASK-123 --json --timeout-seconds 1800
agpair task watch TASK-123 --json --cursor CURSOR-1
```

Rules:

- `--wait` and `task wait` block in the tool process until terminal state or timeout.
- The blocking command may poll SQLite/process state internally; it must not require the controller model to ask status repeatedly.
- `task watch --json` emits state changes and terminal receipts, not unchanged heartbeats.
- Watch events include cursor, phase, blocker type, summary, receipt path, stdout/stderr/report paths, and output excerpt when safe.
- Watch events do not stream full raw logs by default.
- Stop hooks should block only actionable terminal states such as `ready_for_review` and `blocked(approval_required)`.
- Claude Code integration should keep using deterministic command hooks for this logic; do not move wait/watch control into prompt hooks or model-invoking hooks.
- Codex App thread automations may be used manually for special follow-up workflows, but they are not the default AGPair wait mechanism because they create model turns.
- `SubagentStart` hooks are advisory only. They may inject context, but they must not be treated as a reliable hard veto against native subagents.

Acceptance:

- `tests/integration/test_task_wait_inline_poll.py` proves a fake long-running executor can be started with `--wait` and returns one terminal JSON payload.
- `tests/integration/test_task_watch.py` proves watch cursors resume without replaying unchanged events and do not include full stdout bodies.

### 4.6 Structured Block And Retry Context

V1.1 keeps the simple approval model:

```text
new -> acked -> ready_for_review
             -> blocked(approval_required)
             -> stuck
```

`blocked(approval_required)` is terminal for the current attempt. Do not implement runtime pause/approve/resume in V1.1.

Retry must make the new attempt feel like continuation:

```bash
agpair task retry TASK-123 --from-block --authorization-profile local_mutating
```

`--from-block` must build the retry prompt from:

- original brief;
- previous executor id and actual binary name;
- previous blocker type and blocker message;
- previous terminal receipt;
- durable stdout/stderr/report paths and safe excerpts;
- current `git status --short`;
- current diff/commit summary when available;
- previous authorization profile and new authorization profile;
- effective completion policy and controller policy decision.

Rules:

- A retry creates a new attempt row.
- Previous attempts and artifacts remain immutable.
- Retry may widen authorization only when explicitly requested.
- Retry may change executor through controller policy or explicit `--executor`.
- Retry never mutates the original terminal receipt.

### 4.7 No Project Target List

Do not require `agpair target list` or per-project registration for delegation.

AGPair task routing should work for any valid `--repo-path` or current working directory. Executor choice is based on:

- controller id;
- explicit executor override;
- controller-aware suppression;
- binary health;
- authorization profile;
- completion policy;
- repository/worktree safety checks.

Project-local configuration may install hooks or skills, but it must not be a prerequisite for starting tasks in a repo.

## 5. File Structure

Modify existing files:

- `agpair/models.py`: phase constants, completion policy constants, new dataclasses.
- `agpair/storage/schema.sql`: add `task_attempts` and `task_artifacts`; adjust task defaults.
- `agpair/storage/db.py`: migrations for attempts, artifacts, ready_for_review compatibility, completion policy defaults.
- `agpair/storage/tasks.py`: create attempts, current attempt view, mark_ready_for_review, retry handling.
- `agpair/storage/journal.py`: no schema change expected; may add helper for terminal events if useful.
- `agpair/executors/base.py`: extend `TaskState`, `executor_session_id`, and poll signatures carefully.
- `agpair/executors/local_cli.py`: persist artifacts, stop hardcoding missing commit as universal blocker.
- `agpair/executors/routing.py`: static executor ids remain here.
- `agpair/executors/health.py`: keep eligibility helper; feed it real binary health.
- `agpair/terminal_receipts.py`: validate success/report/blocked receipts and normalize legacy statuses.
- `agpair/daemon/loop.py`: consume terminal receipts through the completion evaluator.
- `agpair/cli/wait.py`: consume inline local CLI terminal results through the completion evaluator.
- `agpair/cli/task.py`: start/retry/status/logs/watch integration.
- `agpair/cli/codex.py`: hooks use shared policy and ready_for_review semantics.
- `agpair/cli/claude.py`: hooks use shared policy and ready_for_review semantics.
- `agpair/cli/doctor.py`: executor specs, policy, binary health, effective config.
- `agpair/cli/app.py`: register new `policy` command.
Create new files:

- `agpair/completion.py`: completion policy resolution and terminal decision evaluator.
- `agpair/artifacts.py`: durable artifact directory, copy, hashing, excerpts, path normalization.
- `agpair/executors/specs.py`: executor id, default binary, env var, display label, controller suppression label.
- `agpair/executors/policy.py`: controller-aware resolver.
- `agpair/cli/policy.py`: `agpair policy` CLI.
- `tests/unit/test_completion_policy.py`
- `tests/unit/test_artifacts.py`
- `tests/unit/test_executor_policy.py`
- `tests/integration/test_report_only_tasks.py`
- `tests/integration/test_attempt_artifacts.py`
- `tests/integration/test_policy_cli.py`
- `tests/integration/test_task_watch.py`
- `tests/integration/test_retry_from_block.py`
- `tests/integration/test_config_install.py`

Update docs and skills:

- `README.md`
- `README.zh-CN.md`
- `docs/usage.md`
- `docs/usage.zh-CN.md`
- `docs/getting-started.en.md`
- `docs/getting-started-zh.md`
- `docs/claude-code-integration.zh-CN.md`
- `docs/external-agent-first-decision-map.zh-CN.html`
- `skills/claw.json`
- `skills/Codex/SKILL.md`
- `skills/Claude/SKILL.md`

Local deployment targets, never committed unless sanitized and intentionally project-scoped:

- `~/.codex/skills/agpair/SKILL.md` or the AGPair-managed Codex skill path reported by the installer.
- `~/.codex/hooks.json`
- `<repo>/.codex/hooks.json`
- `~/.claude/skills/agpair/SKILL.md`
- `~/.claude/settings.json`
- `<repo>/.claude/settings.json`
- marker-bounded AGPair blocks in `AGENTS.md` or `CLAUDE.md` only when the installer already owns that marker.

Do not scatter routing logic into `AGENTS.md` or `CLAUDE.md`. They may contain a short pointer to AGPair skills/hooks, but the source of truth is `agpair/executors/policy.py`, `skills/Codex/SKILL.md`, `skills/Claude/SKILL.md`, and the managed hook config.

## 6. Implementation Tasks

### Task 1: Completion Policy Resolver

**Files:**

- Create: `agpair/completion.py`
- Modify: `agpair/models.py`
- Test: `tests/unit/test_completion_policy.py`

- [ ] **Step 1: Add failing tests for policy aliases and auto resolution**

Create `tests/unit/test_completion_policy.py` with these tests:

```python
from agpair.completion import (
    CompletionPolicy,
    EffectiveTaskPolicy,
    normalize_completion_policy,
    resolve_effective_task_policy,
)


def test_normalizes_legacy_completion_policy_aliases() -> None:
    assert normalize_completion_policy("direct_commit") == CompletionPolicy.COMMIT
    assert normalize_completion_policy("evidence_ready") == CompletionPolicy.EVIDENCE
    assert normalize_completion_policy("report") == CompletionPolicy.REPORT


def test_local_readonly_auto_resolves_to_report() -> None:
    policy = resolve_effective_task_policy(
        requested_completion_policy="auto",
        authorization_profile="local_readonly",
        body="Goal: review\nScope: read only\nRequired changes: none\nExit criteria: report findings",
    )
    assert policy.effective_completion_policy == CompletionPolicy.REPORT
    assert policy.report_only is True
    assert policy.requires_report is True
    assert policy.requires_commit is False
    assert policy.allows_file_edits is False
    assert policy.allows_commit is False
    assert policy.source == "authorization_profile"


def test_auto_required_changes_none_resolves_to_report_even_with_mutating_profile() -> None:
    policy = resolve_effective_task_policy(
        requested_completion_policy="auto",
        authorization_profile="local_mutating",
        body="Goal: inspect\nScope: repo\nRequired changes: 无，禁止写入。\nExit criteria: 中文审查结论",
    )
    assert policy.effective_completion_policy == CompletionPolicy.REPORT
    assert policy.source == "brief"


def test_auto_commit_wording_resolves_to_commit() -> None:
    policy = resolve_effective_task_policy(
        requested_completion_policy="auto",
        authorization_profile="local_mutating",
        body="Goal: fix bug\nScope: repo\nRequired changes: edit files\nExit criteria: must commit with task id",
    )
    assert policy.effective_completion_policy == CompletionPolicy.COMMIT
    assert policy.requires_commit is True
    assert policy.allows_commit is True


def test_auto_mutating_without_commit_requirement_resolves_to_evidence() -> None:
    policy = resolve_effective_task_policy(
        requested_completion_policy="auto",
        authorization_profile="local_mutating",
        body="Goal: fix bug\nScope: repo\nRequired changes: edit files\nExit criteria: tests pass and diff ready",
    )
    assert policy.effective_completion_policy == CompletionPolicy.EVIDENCE
    assert policy.requires_commit is False
    assert policy.requires_machine_evidence is True
```

- [ ] **Step 2: Run tests and confirm failure**

```bash
pytest tests/unit/test_completion_policy.py -q
```

Expected: import errors for `agpair.completion`.

- [ ] **Step 3: Implement resolver**

Create `agpair/completion.py`:

```python
from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
import re
from typing import Any, Mapping

from agpair.models import validate_authorization_profile


class CompletionPolicy(StrEnum):
    AUTO = "auto"
    EVIDENCE = "evidence"
    REPORT = "report"
    COMMIT = "commit"


_ALIASES = {
    "direct_commit": CompletionPolicy.COMMIT,
    "evidence_ready": CompletionPolicy.EVIDENCE,
    "ready_for_review": CompletionPolicy.EVIDENCE,
}


@dataclass(frozen=True)
class EffectiveTaskPolicy:
    requested_completion_policy: CompletionPolicy
    effective_completion_policy: CompletionPolicy
    authorization_profile: str
    allows_file_edits: bool
    allows_commit: bool
    requires_commit: bool
    requires_report: bool
    requires_machine_evidence: bool
    report_only: bool
    source: str

    def to_json_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["requested_completion_policy"] = self.requested_completion_policy.value
        payload["effective_completion_policy"] = self.effective_completion_policy.value
        return payload


def normalize_completion_policy(value: str | None) -> CompletionPolicy:
    normalized = (value or CompletionPolicy.AUTO.value).strip().lower()
    if normalized in _ALIASES:
        return _ALIASES[normalized]
    try:
        return CompletionPolicy(normalized)
    except ValueError as exc:
        allowed = ", ".join(policy.value for policy in CompletionPolicy)
        raise ValueError(f"completion policy must be one of: {allowed}") from exc


def resolve_effective_task_policy(
    *,
    requested_completion_policy: str | None,
    authorization_profile: str,
    body: str,
) -> EffectiveTaskPolicy:
    requested = normalize_completion_policy(requested_completion_policy)
    profile = validate_authorization_profile(authorization_profile)
    allows_file_edits = profile in {"local_mutating", "local_test_heavy", "external_network"}
    allows_commit = allows_file_edits

    effective = requested
    source = "explicit"
    if requested is CompletionPolicy.AUTO:
        effective, source = _infer_auto_policy(profile=profile, body=body)

    requires_commit = effective is CompletionPolicy.COMMIT
    requires_report = effective is CompletionPolicy.REPORT
    return EffectiveTaskPolicy(
        requested_completion_policy=requested,
        effective_completion_policy=effective,
        authorization_profile=profile,
        allows_file_edits=allows_file_edits,
        allows_commit=allows_commit,
        requires_commit=requires_commit,
        requires_report=requires_report,
        requires_machine_evidence=True,
        report_only=effective is CompletionPolicy.REPORT,
        source=source,
    )


def _infer_auto_policy(*, profile: str, body: str) -> tuple[CompletionPolicy, str]:
    if profile == "local_readonly":
        return CompletionPolicy.REPORT, "authorization_profile"
    lowered = body.lower()
    if re.search(r"required changes\s*:\s*(none|no changes|n/a)", lowered):
        return CompletionPolicy.REPORT, "brief"
    if "required changes: 无" in body or "禁止写入" in body or "read-only" in lowered or "readonly" in lowered:
        return CompletionPolicy.REPORT, "brief"
    if "must commit" in lowered or "commit required" in lowered or "提交 commit" in body:
        return CompletionPolicy.COMMIT, "brief"
    return CompletionPolicy.EVIDENCE, "default"
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/unit/test_completion_policy.py -q
```

Expected: pass.

### Task 2: First-Class Attempts And Durable Artifacts

**Files:**

- Modify: `agpair/storage/schema.sql`
- Modify: `agpair/storage/db.py`
- Modify: `agpair/models.py`
- Modify: `agpair/storage/tasks.py`
- Create: `agpair/artifacts.py`
- Test: `tests/unit/test_artifacts.py`
- Test: `tests/integration/test_attempt_artifacts.py`

- [ ] **Step 1: Add artifact tests**

Create `tests/unit/test_artifacts.py`:

```python
from pathlib import Path

from agpair.artifacts import attempt_artifact_dir, copy_attempt_artifact, read_excerpt
from agpair.config import AppPaths


def test_attempt_artifact_dir_is_stable_under_agpair_home(tmp_path: Path) -> None:
    paths = AppPaths.from_root(tmp_path / ".agpair")
    artifact_dir = attempt_artifact_dir(paths, "TASK-ABC", 2)
    assert artifact_dir == paths.root / "tasks" / "TASK-ABC" / "attempt-2"


def test_copy_attempt_artifact_creates_durable_file(tmp_path: Path) -> None:
    paths = AppPaths.from_root(tmp_path / ".agpair")
    source = tmp_path / "stdout.log"
    source.write_text("hello\nworld\n", encoding="utf-8")
    copied = copy_attempt_artifact(
        paths,
        task_id="TASK-ABC",
        attempt_no=1,
        source_path=source,
        kind="stdout",
        filename="stdout.log",
    )
    assert copied.path.exists()
    assert copied.kind == "stdout"
    assert copied.size_bytes == len("hello\nworld\n".encode())
    assert copied.sha256
    assert str(copied.path).endswith(".agpair/tasks/TASK-ABC/attempt-1/stdout.log")


def test_read_excerpt_limits_large_output(tmp_path: Path) -> None:
    log = tmp_path / "stdout.log"
    log.write_text("a" * 5000, encoding="utf-8")
    excerpt = read_excerpt(log, max_chars=80)
    assert len(excerpt) <= 80
```

- [ ] **Step 2: Add attempt persistence tests**

Create `tests/integration/test_attempt_artifacts.py`:

```python
from pathlib import Path

from agpair.config import AppPaths
from agpair.storage.db import connect, ensure_database
from agpair.storage.tasks import TaskRepository


def test_create_task_creates_initial_attempt(tmp_path: Path) -> None:
    paths = AppPaths.from_root(tmp_path / ".agpair")
    ensure_database(paths.db_path)
    repo = TaskRepository(paths.db_path)
    repo.create_task(
        task_id="TASK-A",
        repo_path=str(tmp_path),
        executor_backend="grok-cli",
        authorization_profile="local_readonly",
        completion_policy="auto",
        effective_policy_json='{"effective_completion_policy":"report"}',
    )
    with connect(paths.db_path) as conn:
        row = conn.execute("SELECT task_id, attempt_no, executor_backend FROM task_attempts").fetchone()
    assert row["task_id"] == "TASK-A"
    assert row["attempt_no"] == 1
    assert row["executor_backend"] == "grok-cli"


def test_retry_creates_new_attempt_without_erasing_old_attempt(tmp_path: Path) -> None:
    paths = AppPaths.from_root(tmp_path / ".agpair")
    ensure_database(paths.db_path)
    repo = TaskRepository(paths.db_path)
    repo.create_task(
        task_id="TASK-A",
        repo_path=str(tmp_path),
        executor_backend="grok-cli",
        completion_policy="auto",
        effective_policy_json="{}",
    )
    repo.mark_blocked(task_id="TASK-A", reason="Need more context")
    repo.apply_retry_dispatch(
        task_id="TASK-A",
        executor_backend="claude-code",
        completion_policy="auto",
        effective_policy_json="{}",
    )
    with connect(paths.db_path) as conn:
        rows = conn.execute(
            "SELECT attempt_no, executor_backend FROM task_attempts WHERE task_id=? ORDER BY attempt_no",
            ("TASK-A",),
        ).fetchall()
    assert [(row["attempt_no"], row["executor_backend"]) for row in rows] == [
        (1, "grok-cli"),
        (2, "claude-code"),
    ]
```

- [ ] **Step 3: Run tests and confirm failure**

```bash
pytest tests/unit/test_artifacts.py tests/integration/test_attempt_artifacts.py -q
```

Expected: import/signature/schema failures.

- [ ] **Step 4: Add artifact helper**

Create `agpair/artifacts.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import shutil

from agpair.config import AppPaths


@dataclass(frozen=True)
class ArtifactRecord:
    kind: str
    path: Path
    size_bytes: int
    sha256: str


def attempt_artifact_dir(paths: AppPaths, task_id: str, attempt_no: int) -> Path:
    return paths.root / "tasks" / task_id / f"attempt-{attempt_no}"


def copy_attempt_artifact(
    paths: AppPaths,
    *,
    task_id: str,
    attempt_no: int,
    source_path: Path,
    kind: str,
    filename: str,
) -> ArtifactRecord:
    target_dir = attempt_artifact_dir(paths, task_id, attempt_no)
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / filename
    shutil.copyfile(source_path, target_path)
    body = target_path.read_bytes()
    return ArtifactRecord(
        kind=kind,
        path=target_path,
        size_bytes=len(body),
        sha256=hashlib.sha256(body).hexdigest(),
    )


def write_attempt_artifact(
    paths: AppPaths,
    *,
    task_id: str,
    attempt_no: int,
    kind: str,
    filename: str,
    body: str,
) -> ArtifactRecord:
    target_dir = attempt_artifact_dir(paths, task_id, attempt_no)
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / filename
    target_path.write_text(body, encoding="utf-8")
    data = body.encode("utf-8")
    return ArtifactRecord(kind=kind, path=target_path, size_bytes=len(data), sha256=hashlib.sha256(data).hexdigest())


def read_excerpt(path: Path, *, max_chars: int = 4000) -> str:
    if max_chars <= 0 or not path.exists():
        return ""
    body = path.read_text(encoding="utf-8", errors="replace")
    if len(body) <= max_chars:
        return body
    return body[-max_chars:]
```

- [ ] **Step 5: Add schema and migrations**

Add to `agpair/storage/schema.sql`:

```sql
CREATE TABLE IF NOT EXISTS task_attempts (
  attempt_id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL,
  attempt_no INTEGER NOT NULL,
  executor_backend TEXT,
  phase TEXT NOT NULL,
  requested_completion_policy TEXT NOT NULL DEFAULT 'auto',
  effective_completion_policy TEXT NOT NULL DEFAULT 'evidence',
  effective_policy_json TEXT NOT NULL DEFAULT '{}',
  authorization_profile TEXT NOT NULL DEFAULT 'local_mutating',
  authorization_summary TEXT,
  session_id TEXT,
  execution_repo_path TEXT,
  terminal_receipt_json TEXT,
  terminal_source TEXT,
  failure_context_json TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  started_at TEXT,
  finished_at TEXT,
  UNIQUE(task_id, attempt_no)
);

CREATE TABLE IF NOT EXISTS task_artifacts (
  artifact_id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL,
  attempt_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  path TEXT NOT NULL,
  size_bytes INTEGER NOT NULL DEFAULT 0,
  sha256 TEXT,
  excerpt TEXT,
  created_at TEXT NOT NULL
);
```

Add matching idempotent migrations in `agpair/storage/db.py`.

- [ ] **Step 6: Update task repository APIs**

Update `TaskRepository.create_task` to accept:

```python
completion_policy: str = "auto"
effective_policy_json: str = "{}"
```

Update insert default so new tasks store requested completion policy `auto`, not `direct_commit`.

Add repository helpers:

```python
def create_attempt_for_task(
    self,
    *,
    task_id: str,
    executor_backend: str | None,
    requested_completion_policy: str,
    effective_policy_json: str,
) -> TaskAttemptRecord

def current_attempt(self, task_id: str) -> TaskAttemptRecord | None

def record_attempt_terminal(
    self,
    *,
    attempt_id: str,
    terminal_status: str,
    terminal_source: str,
    terminal_receipt_json: str,
) -> None

def record_artifact(
    self,
    *,
    task_id: str,
    attempt_id: str,
    kind: str,
    path: str,
    size_bytes: int,
    sha256: str | None,
    excerpt: str | None,
) -> TaskArtifactRecord

def list_artifacts(
    self,
    *,
    task_id: str,
    attempt_no: int | None = None,
) -> list[TaskArtifactRecord]
```

`apply_retry_dispatch` must create a new `task_attempts` row before dispatching and must not delete previous attempt rows or artifacts.

- [ ] **Step 7: Run tests**

```bash
pytest tests/unit/test_artifacts.py tests/integration/test_attempt_artifacts.py -q
```

Expected: pass.

### Task 3: Ready-For-Review State And Status Payload

**Files:**

- Modify: `agpair/models.py`
- Modify: `agpair/storage/tasks.py`
- Modify: `agpair/cli/task.py`
- Test: `tests/integration/test_task_start_and_status.py`

- [ ] **Step 1: Add tests**

Add tests asserting:

- `test_task_status_exposes_ready_for_review_and_artifact_paths` creates a task with a terminal attempt, stdout/stderr/receipt/report artifacts, then asserts `phase=ready_for_review` and all durable artifact paths are present.
- `test_committed_legacy_phase_maps_to_ready_for_review_state_hint` inserts a legacy `committed` task row without an attempt and asserts status preserves the stored phase while exposing normalized success semantics.
- `test_status_splits_backend_safety_from_effective_task_safety` uses a mutating-capable backend with `local_readonly` effective policy and asserts the two metadata objects do not contradict each other.
- `test_status_reads_terminal_receipt_from_attempt_not_only_journal_event_name` writes different terminal payloads to the journal and attempt row, then asserts the attempt receipt wins.

Expected status JSON keys:

```json
{
  "phase": "ready_for_review",
  "stored_phase": "ready_for_review",
  "legacy_phase": null,
  "a2a_state_hint": "input-required",
  "terminal_receipt": {"schema_version": "1"},
  "artifact_paths": {
    "stdout": "/tmp/agpair-home/tasks/TASK-123/attempt-1/stdout.log",
    "stderr": "/tmp/agpair-home/tasks/TASK-123/attempt-1/stderr.log",
    "receipt": "/tmp/agpair-home/tasks/TASK-123/attempt-1/receipt.json",
    "report": "/tmp/agpair-home/tasks/TASK-123/attempt-1/report.md"
  },
  "executor_output_excerpt": "Chinese review conclusion captured from executor stdout"
}
```

- [ ] **Step 2: Implement phase constants**

`TERMINAL_PHASES` must include `ready_for_review`.

`SUCCESS_REVIEW_PHASES` should include `ready_for_review`, `evidence_ready`, and `committed` for backwards compatibility.

`a2a_state_hint_from_phase("ready_for_review")` should return `input-required`, because the controller must verify before final user success.

- [ ] **Step 3: Add repository transition**

Add `TaskRepository.mark_ready_for_review`:

```python
def mark_ready_for_review(
    self,
    *,
    task_id: str,
    last_receipt_id: str | None = None,
    terminal_source: str | None = None,
    terminal_receipt_json: str | None = None,
) -> None:
    now = utc_now_iso()
    with self._conn:
        row_count = self._conn.execute(
            """
            UPDATE tasks
               SET phase = ?,
                   last_receipt_id = COALESCE(?, last_receipt_id),
                   terminal_source = COALESCE(?, terminal_source),
                   terminal_receipt_json = COALESCE(?, terminal_receipt_json),
                   updated_at = ?
             WHERE task_id = ?
               AND phase IN (?, ?, ?)
            """,
            (
                "ready_for_review",
                last_receipt_id,
                terminal_source,
                terminal_receipt_json,
                now,
                task_id,
                "acked",
                "evidence_ready",
                "committed",
            ),
        ).rowcount
        if row_count != 1:
            raise InvalidTaskTransition(task_id, "ready_for_review")
```

Valid source phases: `acked`, `evidence_ready`, `committed` for migration compatibility.

- [ ] **Step 4: Build status from attempts/artifacts first**

Update `build_task_payload` in `agpair/cli/task.py`:

1. Load current attempt.
2. Load current attempt artifacts.
3. Use attempt `terminal_receipt_json` first.
4. Fall back to journal only for legacy rows.
5. Emit `backend_safety_metadata`.
6. Emit `effective_task_safety`.
7. Emit `effective_task_policy`.
8. Emit `executor_session_id` for all executor backends.
9. Keep any legacy physical storage field such as `antigravity_session_id` internal-only; new status, watch, doctor, and docs must not expose non-Antigravity sessions as Antigravity sessions.

- [ ] **Step 5: Run tests**

```bash
pytest tests/integration/test_task_start_and_status.py -q
```

Expected: pass.

### Task 4: Unified Completion Evaluator

**Files:**

- Modify: `agpair/completion.py`
- Modify: `agpair/terminal_receipts.py`
- Modify: `agpair/executors/base.py`
- Modify: `agpair/executors/local_cli.py`
- Modify: `agpair/daemon/loop.py`
- Modify: `agpair/cli/wait.py`
- Test: `tests/unit/test_completion_policy.py`
- Test: `tests/unit/test_local_cli_executor.py`
- Test: `tests/integration/test_report_only_tasks.py`
- Test: `tests/integration/test_completion_policy.py`

- [ ] **Step 1: Add report-only integration tests**

Create `tests/integration/test_report_only_tasks.py`:

Required cases:

- `test_readonly_task_exit_zero_with_stdout_becomes_ready_for_review`: fake executor exits 0, writes stdout, makes no commit, uses `local_readonly`; assert `phase=ready_for_review`, `artifact_paths.stdout` exists, and `commit_ref` is absent.
- `test_readonly_task_exit_zero_without_stdout_blocks_output_missing`: fake executor exits 0 and writes no stdout/report/receipt; assert `phase=blocked` and `blocker_type=output_missing`.
- `test_report_policy_does_not_require_commit`: explicit `--completion-policy report` with no commit must not produce `missing_commit`.
- `test_commit_policy_still_requires_commit`: explicit `--completion-policy commit` with no commit must produce `missing_commit_for_commit_policy`.
- `test_malformed_success_receipt_blocks_validation_failure`: malformed `COMMITTED` or `EVIDENCE_PACK` receipt must produce `blocked(validation_failure)` and keep raw artifact paths.

Expected blocker types:

- `output_missing`
- `missing_commit_for_commit_policy`
- `validation_failure`
- `executor_unavailable`

- [ ] **Step 2: Implement neutral evidence input**

In `agpair/completion.py`, add:

```python
@dataclass(frozen=True)
class ExecutionEvidence:
    exit_code: int | None
    has_commit: bool
    commit_ref: str | None
    worktree_dirty: bool
    changed_files: list[str]
    stdout_path: str | None
    stderr_path: str | None
    receipt_path: str | None
    report_path: str | None
    stdout_excerpt: str
    structured_receipt: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class CompletionDecision:
    phase: str
    terminal_status: str
    summary: str
    payload: dict[str, Any]
    terminal_source: str
    blocker_type: str | None = None
```

- [ ] **Step 3: Implement evaluator**

Rules:

```python
def evaluate_completion(*, task_id: str, attempt_no: int, policy: EffectiveTaskPolicy, evidence: ExecutionEvidence) -> CompletionDecision:
    if evidence.structured_receipt:
        return evaluate_structured_receipt(
            task_id=task_id,
            attempt_no=attempt_no,
            policy=policy,
            evidence=evidence,
            receipt=evidence.structured_receipt,
        )
    if evidence.exit_code not in (None, 0):
        return blocked("execution_error")
    if policy.requires_commit and not evidence.has_commit:
        return blocked("missing_commit_for_commit_policy")
    if policy.requires_report:
        if evidence.report_path or evidence.stdout_excerpt.strip():
            return ready_for_review("Report captured", claimed_state="ready_for_review")
        return blocked("output_missing")
    if evidence.has_commit or evidence.changed_files or evidence.stdout_excerpt.strip():
        return ready_for_review("Evidence captured", claimed_state="ready_for_review")
    return blocked("output_missing")
```

Structured receipt rules:

- `BLOCKED` stays blocked and preserves blocker type.
- `COMMITTED` and `EVIDENCE_PACK` can become `ready_for_review` only after `validate_terminal_receipt_payload` passes.
- Non-empty `scope_violations` becomes `blocked(validation_failure)`.
- Missing `raw_log_path` or `receipt_path` in success receipts becomes `blocked(validation_failure)`.
- `commit_ref` is optional unless policy requires commit.

- [ ] **Step 4: Stop local CLI from hardcoding commit-only success**

Update `LocalCLIExecutor.poll` so it receives enough context:

```python
def poll(
    self,
    task_id: str,
    session_id: str,
    attempt_no: int = 1,
    effective_policy: EffectiveTaskPolicy | None = None,
) -> TaskState | None:
    raw_state = self._poll_process(task_id=task_id, session_id=session_id)
    if raw_state is None:
        return None
    return self._state_from_raw_poll(
        task_id=task_id,
        session_id=session_id,
        attempt_no=attempt_no,
        effective_policy=effective_policy,
        raw_state=raw_state,
    )
```

If changing the protocol is too broad, the wait/daemon path may call `evaluate_completion` after `LocalCLIExecutor` returns raw evidence. Do not leave `exit_code=0 and no commit` hardcoded as `missing_commit` for all policies.

- [ ] **Step 5: Apply evaluator in both terminal paths**

Use the same evaluator in:

- daemon receipt ingestion
- inline wait local CLI polling
- repo evidence auto-close

No terminal path may call `mark_committed`, `mark_evidence_ready`, or `mark_blocked` from raw executor output without passing through the evaluator, except legacy migration fallback with an explicit comment.

- [ ] **Step 6: Run tests**

```bash
pytest tests/unit/test_completion_policy.py tests/unit/test_local_cli_executor.py -q
pytest tests/integration/test_report_only_tasks.py tests/integration/test_completion_policy.py tests/integration/test_task_wait_inline_poll.py -q
```

Expected: pass.

### Task 5: Durable Log Capture Before Cleanup

**Files:**

- Modify: `agpair/executors/local_cli.py`
- Modify: `agpair/daemon/loop.py`
- Modify: `agpair/cli/wait.py`
- Modify: `agpair/cli/task.py`
- Test: `tests/integration/test_attempt_artifacts.py`
- Test: `tests/integration/test_task_wait_inline_poll.py`

- [ ] **Step 1: Add tests**

Tests must prove:

- after terminal inline wait, stdout/stderr durable files still exist;
- status exposes durable paths;
- raw temp executor directory may be deleted;
- `task logs --include-executor-output` can show an excerpt from durable output;
- inline `inline_poll_closed` receipts appear in `task status --json`.

- [ ] **Step 2: Persist artifacts before cleanup**

Before `_cleanup_local_cli_session` or `executor.cleanup`, copy:

- `stdout.log`
- `stderr.log`
- parsed terminal receipt as `receipt.json`
- report body as `report.md` for report policy
- `evidence.json` containing the completion decision payload

Use durable paths in the receipt payload.

- [ ] **Step 3: Extend logs CLI**

Add options:

```text
agpair task logs TASK --include-executor-output
agpair task logs TASK --raw stdout
agpair task logs TASK --raw stderr
```

Rules:

- default output remains lifecycle journal only;
- raw modes read durable artifact files only;
- missing artifacts return a clear error;
- never read transient temp dirs in status/logs after terminal cleanup.

- [ ] **Step 4: Run tests**

```bash
pytest tests/integration/test_attempt_artifacts.py tests/integration/test_task_wait_inline_poll.py tests/integration/test_task_start_and_status.py -q
```

Expected: pass.

### Task 6: Executor Specs, Binary Preflight, And Controller Policy

**Files:**

- Create: `agpair/executors/specs.py`
- Create: `agpair/executors/policy.py`
- Create: `agpair/cli/policy.py`
- Modify: `agpair/executors/routing.py`
- Modify: `agpair/executors/health.py`
- Modify: `agpair/cli/task.py`
- Modify: `agpair/cli/doctor.py`
- Modify: `agpair/cli/app.py`
- Test: `tests/unit/test_executor_policy.py`
- Test: `tests/integration/test_policy_cli.py`
- Test: `tests/integration/test_doctor.py`

- [ ] **Step 1: Add tests**

Create `tests/unit/test_executor_policy.py`:

```python
from agpair.executors.policy import resolve_executor_policy
from agpair.executors.health import ExecutorHealth


def test_codex_controller_suppresses_external_codex_executor() -> None:
    decision = resolve_executor_policy(
        controller="codex",
        health_by_executor={
            "antigravity-cli": ExecutorHealth("antigravity-cli", available=True),
            "codex": ExecutorHealth("codex", available=True),
        },
    )
    assert decision.selected_executor == "antigravity-cli"
    assert "codex" in decision.suppressed_executors


def test_claude_controller_suppresses_external_claude_code_executor() -> None:
    decision = resolve_executor_policy(
        controller="claude-code",
        health_by_executor={
            "claude-code": ExecutorHealth("claude-code", available=True),
            "codex": ExecutorHealth("codex", available=True),
        },
    )
    assert decision.selected_executor == "codex"
    assert "claude-code" in decision.suppressed_executors


def test_unavailable_default_executor_falls_to_next_healthy_executor() -> None:
    decision = resolve_executor_policy(
        controller="codex",
        health_by_executor={
            "antigravity-cli": ExecutorHealth("antigravity-cli", available=False, last_error_excerpt="missing binary"),
            "grok-cli": ExecutorHealth("grok-cli", available=True),
        },
    )
    assert decision.selected_executor == "grok-cli"


def test_recent_stuck_or_malformed_receipt_marks_executor_ineligible() -> None:
    decision = resolve_executor_policy(
        controller="codex",
        health_by_executor={
            "antigravity-cli": ExecutorHealth(
                "antigravity-cli",
                available=True,
                recent_failure_count=0,
                consecutive_stuck_count=3,
                malformed_receipt_count=0,
            ),
            "grok-cli": ExecutorHealth("grok-cli", available=True),
        },
    )
    assert decision.selected_executor == "grok-cli"
    assert "antigravity-cli" in decision.ineligible_executors


def test_explicit_suppressed_executor_requires_allow_self_executor() -> None:
    decision = resolve_executor_policy(
        controller="codex",
        explicit_executor="codex",
        allow_self_executor=False,
        health_by_executor={"codex": ExecutorHealth("codex", available=True)},
    )
    assert decision.selected_executor is None
    assert any("suppressed" in warning for warning in decision.warnings)
```

- [ ] **Step 2: Implement executor specs**

Create `agpair/executors/specs.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
import os
import shutil


@dataclass(frozen=True)
class ExecutorSpec:
    executor_id: str
    env_var: str
    default_binary: str
    display_name: str

    def configured_binary(self) -> str:
        return os.environ.get(self.env_var) or self.default_binary

    def available(self) -> bool:
        binary = self.configured_binary()
        if "/" in binary:
            return os.path.exists(binary) and os.access(binary, os.X_OK)
        return shutil.which(binary) is not None


EXECUTOR_SPECS: dict[str, ExecutorSpec] = {
    "antigravity-cli": ExecutorSpec("antigravity-cli", "AGPAIR_ANTIGRAVITY_CLI", "antigravity", "Antigravity CLI"),
    "grok-cli": ExecutorSpec("grok-cli", "AGPAIR_GROK_CLI", "grok", "Grok CLI"),
    "claude-code": ExecutorSpec("claude-code", "AGPAIR_CLAUDE_CODE_CLI", "claude", "Claude Code CLI"),
    "codex": ExecutorSpec("codex", "AGPAIR_CODEX_CLI", "codex", "Codex CLI"),
}
```

`doctor.py`, `task start`, and docs must use this map. Do not duplicate executor binary/env var tables.

Minimum executor health model:

```text
executor_id
configured_binary
available
last_error_excerpt
recent_failure_count
consecutive_stuck_count
malformed_receipt_count
eligible
ineligible_reason
checked_at
```

Health stays deliberately simple. Do not add a full scoring/ranking framework in V1.1; only skip clearly unavailable or recently bad executors before falling back to the next configured executor.

- [ ] **Step 3: Implement policy resolver**

Create `agpair/executors/policy.py` using the existing v1.1 controller matrix:

| Controller | Implicit order | Suppressed | Native fallback |
| --- | --- | --- | --- |
| `codex` | `antigravity-cli`, `grok-cli`, `claude-code` | `codex` | `codex-native-subagents` |
| `claude-code` | `antigravity-cli`, `grok-cli`, `codex` | `claude-code` | `claude-code-native-subagents` |
| `generic` | `antigravity-cli`, `grok-cli`, `claude-code`, `codex` | none | none |

Expose:

```text
resolve_executor_policy inputs:
- controller: codex | claude-code | generic
- explicit_executor: optional executor id from CLI
- allow_self_executor: boolean
- executor_health: availability map from executor specs
- configured_overrides: optional config object

resolve_executor_policy output:
- ordered_executors: list of usable executor ids
- suppressed_executors: list of executor ids skipped because they equal the controller
- unavailable_executors: list with binary/env diagnostics
- native_fallback: codex-native-subagents | claude-code-native-subagents | null
- decision_reason: short machine-readable reason

render_policy_context input:
- controller and resolved policy decision

render_policy_context output:
- compact Markdown for skills/hooks explaining executor order, suppressed self executor, and native fallback wording
```

- [ ] **Step 4: Add task start/retry options**

Add to `agpair task start` and `agpair task retry`:

```text
--controller codex|claude-code|generic
--allow-self-executor
--completion-policy auto|evidence|report|commit
--from-block
```

Rules:

- `--executor` is still allowed.
- explicit self executor fails unless `--allow-self-executor` is set.
- implicit selection skips unavailable binaries before dispatch.
- explicit unavailable binary fails before dispatch with `blocker_type=executor_unavailable`.
- no launch may happen after preflight fails.
- `--from-block` is accepted only by `task retry`, never by `task start`.
- `task retry --from-block` includes original brief, previous blocker, terminal receipt, artifact paths, safe output excerpts, git status/diff summary, previous authorization, and new authorization in the new attempt body.
- `task retry --from-block` creates a new attempt and leaves old artifacts immutable.

- [ ] **Step 5: Add policy CLI**

Commands:

```bash
agpair policy show --controller codex --json
agpair policy show --controller claude-code --json
agpair policy validate
agpair policy paths
```

- [ ] **Step 6: Run tests**

```bash
pytest tests/unit/test_executor_policy.py tests/unit/test_executor_health.py -q
pytest tests/integration/test_policy_cli.py tests/integration/test_doctor.py tests/integration/test_task_start_and_status.py -q
pytest tests/integration/test_retry_from_block.py -q
```

Expected: pass.

### Task 7: Receipt Validation As A Single Gate

**Files:**

- Modify: `agpair/terminal_receipts.py`
- Modify: `agpair/completion.py`
- Modify: `agpair/daemon/loop.py`
- Modify: `agpair/cli/wait.py`
- Test: `tests/unit/test_receipt_validation.py`
- Test: `tests/integration/test_daemon_receipts.py`
- Test: `tests/integration/test_fake_executors.py`

- [ ] **Step 1: Add tests**

Tests must assert:

- malformed success receipt never becomes `ready_for_review`;
- success receipt with missing durable `raw_log_path` fails validation;
- success receipt with missing `receipt_path` fails validation;
- success receipt with non-empty `scope_violations` becomes `blocked(validation_failure)`;
- `approval_required` without authorization delta fails validation;
- daemon and wait paths behave identically for the same receipt.

- [ ] **Step 2: Normalize receipt statuses**

Add helper:

```python
def normalize_terminal_success_status(status: str, payload: Mapping[str, Any]) -> str:
    if status in {"COMMITTED", "EVIDENCE_PACK"}:
        return "ready_for_review"
    if payload.get("claimed_state") == "ready_for_review":
        return "ready_for_review"
    return status.lower()
```

- [ ] **Step 3: Remove direct daemon success marking**

`agpair/daemon/loop.py` must not directly call `mark_committed` for a `COMMITTED` receipt. It should:

1. parse structured receipt;
2. load current attempt and effective policy;
3. call `evaluate_completion`;
4. persist artifacts/receipt;
5. apply `mark_ready_for_review` or `mark_blocked`.

- [ ] **Step 4: Run tests**

```bash
pytest tests/unit/test_receipt_validation.py -q
pytest tests/integration/test_daemon_receipts.py tests/integration/test_fake_executors.py -q
```

Expected: pass.

### Task 8: Body Validation And Report UX

**Files:**

- Modify: `agpair/cli/task.py`
- Test: `tests/integration/test_task_start_and_status.py`

- [ ] **Step 1: Add tests**

Tests:

- `test_task_start_rejects_missing_sections_with_template`: missing `Exit criteria` exits non-zero and prints a copyable minimum template.
- `test_readonly_report_brief_accepts_required_changes_none`: English body with `Required changes: none` and `local_readonly` starts successfully with report policy.
- `test_chinese_required_changes_none_hint_resolves_report_policy`: Chinese body containing `Required changes: 无，禁止写入。` starts successfully and resolves effective policy to report-only.

- [ ] **Step 2: Improve error**

When task body is missing sections, print a copyable template:

```text
Refused: task body is missing key structural sections: exit criteria

Minimum template:
Goal: Review the supplied diagnostics and return a concise Chinese conclusion.
Scope: Read-only. Inspect only the files or text explicitly referenced by the task.
Required changes: none
Exit criteria: Return the conclusion in stdout or a report receipt; do not write files.

For read-only review/report tasks use:
Required changes: none
Completion policy: report
```

- [ ] **Step 3: Keep schema simple**

Do not introduce a large `--kind` system in V1.1. Use `--completion-policy` and effective policy inference.

- [ ] **Step 4: Run tests**

```bash
pytest tests/integration/test_task_start_and_status.py -q
```

Expected: pass.

### Task 9: Hooks, Skills, And Docs Use The Core Model

**Files:**

- Modify: `agpair/cli/codex.py`
- Modify: `agpair/cli/claude.py`
- Modify: `agpair/cli/doctor.py`
- Modify: `skills/Codex/SKILL.md`
- Modify: `skills/Claude/SKILL.md`
- Modify: docs listed in Section 5
- Test: `tests/integration/test_codex_cli.py`
- Test: `tests/integration/test_claude_cli.py`

- [ ] **Step 1: Hook tests**

Tests must assert:

- hooks render policy-derived executor order;
- Codex config payload includes AGPair-managed `UserPromptSubmit` and `Stop` command hooks;
- Codex `SubagentStart`, if installed, is advisory-only and never a hard block;
- Claude Code config payload includes AGPair-managed `statusLine`, `SessionStart`, `PreCompact`, `UserPromptSubmit`, `Stop`, `SubagentStart`, `SubagentStop`, `TaskCreated`, and `TaskCompleted` command hooks;
- Claude Code `SubagentStart` is advisory-only;
- Claude Code `TaskCreated` and `TaskCompleted` are observability-only in V1.1 and must not mutate AGPair task terminal state unless a future explicit task-linking protocol exists;
- hooks say `codex` means AGPair-launched Codex CLI executor;
- hooks say native subagents are controller-side fallback/review;
- hooks say `task start --wait`, `task wait`, and `task watch --json` are the low-token wait surfaces;
- Stop blocks `ready_for_review`;
- Stop blocks `blocked(approval_required)`;
- Stop does not block inactive or non-actionable states;
- hooks fail open when AGPair state cannot be read.

Config installer tests must assert:

- `agpair codex config --install --scope project --dry-run --repo-path REPO` prints a diff for `<repo>/.codex/hooks.json` and writes nothing.
- `agpair codex config --install --scope project --repo-path REPO` preserves unrelated hooks and installs only AGPair-managed commands.
- `agpair codex config --uninstall` removes only AGPair-managed commands.
- `agpair claude config --install --scope project --dry-run --repo-path REPO` prints a diff for `<repo>/.claude/settings.json` and writes nothing.
- `agpair claude config --install --scope project --repo-path REPO` preserves unrelated hooks, statusline, permissions, plugins, and user settings.
- `agpair claude config --uninstall` removes only AGPair-managed commands and statusline entries.
- `agpair doctor --repo-path REPO --json` reports Codex and Claude hook install status.
- Installer commands do not edit `AGENTS.md` or `CLAUDE.md` unless an AGPair-managed marker block already exists and the command explicitly owns marker updates.

- [ ] **Step 2: Update docs and skills**

Before updating docs and skills, refresh current official docs for:

- Codex hooks/config behavior relevant to `UserPromptSubmit`, `Stop`, and advisory native subagent context.
- Claude Code settings scopes, skills, command hooks, statusLine, `SessionStart`, `PreCompact`, `UserPromptSubmit`, `Stop`, `SubagentStart`, `SubagentStop`, `TaskCreated`, and `TaskCompleted`.

If official docs add or remove lifecycle events, adjust tests and managed config deliberately. Do not add model-invoking prompt/agent hooks for AGPair control unless the docs prove they are low-cost and necessary.

Required wording:

```text
AGPair task success means ready_for_review with evidence. A commit_ref is optional unless the task explicitly requires a commit.
codex means the Codex CLI executor launched by AGPair, not Codex native subagents.
claude-code means the Claude Code CLI executor launched by AGPair, not Claude Code native subagents.
Use --completion-policy report for read-only review/report tasks.
Use --completion-policy commit only when a commit is required.
Use agpair task start --wait, agpair task wait, or agpair task watch --json instead of model-turn polling.
OMX source does not need changes; when AGPair hooks are installed and healthy, they inject external-first guidance. When AGPair is unavailable, hooks fail open and OMX/native behavior remains unchanged.
Codex App automations and Claude Code Monitor/background-task surfaces are optional observation aids, not AGPair's source of truth and not the default wait path.
SubagentStart is advisory only. Native subagents remain available as fallback/review when external AGPair executors are unavailable or not good enough.
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/integration/test_codex_cli.py tests/integration/test_claude_cli.py -q
pytest tests/integration/test_config_install.py tests/integration/test_doctor.py -q
```

Expected: pass.

### Task 10: Local Deployment And Privacy-Safe Release Gate

**Files:**

- Modify only source/docs/tests unless explicitly syncing local config after tests.

- [ ] **Step 1: Run targeted suite**

```bash
pytest tests/unit/test_completion_policy.py tests/unit/test_artifacts.py tests/unit/test_executor_policy.py tests/unit/test_receipt_validation.py -q
pytest tests/integration/test_report_only_tasks.py tests/integration/test_attempt_artifacts.py tests/integration/test_policy_cli.py -q
pytest tests/integration/test_task_start_and_status.py tests/integration/test_task_wait_inline_poll.py tests/integration/test_fake_executors.py -q
pytest tests/integration/test_codex_cli.py tests/integration/test_claude_cli.py tests/integration/test_doctor.py -q
```

- [ ] **Step 2: Run full suite**

```bash
pytest tests/unit -q
pytest tests/integration -q
git diff --check
```

- [ ] **Step 3: Smoke report-only path with fake executor**

Use a fake executor in tests or a local temp repo. The expected behavior is:

```text
authorization_profile=local_readonly
completion_policy=auto
effective_completion_policy=report
phase=ready_for_review
commit_ref absent
stdout_path durable
receipt_path durable
report_path or executor_output_excerpt present
```

- [ ] **Step 4: Optional local config sync**

Only after tests pass:

```bash
REPO=/path/to/agpair

agpair codex config --install --scope user --dry-run
agpair claude config --install --scope user --dry-run
agpair codex config --install --scope project --repo-path "$REPO" --dry-run
agpair claude config --install --scope project --repo-path "$REPO" --dry-run

mkdir -p "$HOME/.codex/skills/agpair" "$HOME/.claude/skills/agpair"
cp "$REPO/skills/Codex/SKILL.md" "$HOME/.codex/skills/agpair/SKILL.md"
cp "$REPO/skills/Claude/SKILL.md" "$HOME/.claude/skills/agpair/SKILL.md"

agpair codex config --install --scope user
agpair claude config --install --scope user
agpair codex config --install --scope project --repo-path "$REPO"
agpair claude config --install --scope project --repo-path "$REPO"

cmp "$REPO/skills/Codex/SKILL.md" "$HOME/.codex/skills/agpair/SKILL.md"
cmp "$REPO/skills/Claude/SKILL.md" "$HOME/.claude/skills/agpair/SKILL.md"
agpair doctor --repo-path "$REPO" --json
```

Inspect `agpair doctor` output and confirm:

- project Codex hook installed;
- project Claude hook installed;
- executor health reports canonical executor ids only: `antigravity-cli`, `grok-cli`, `claude-code`, `codex`;
- no Gemini executor is advertised for new tasks;
- no Antigravity IDE executor is advertised for new tasks.

- [ ] **Step 5: Privacy gate**

```bash
git status --short
git diff --check
git diff --stat
git diff -- . ':(exclude)tests/fixtures' | rg -n "sk-[A-Za-z0-9]|Bearer [A-Za-z0-9._-]+|api[_-]?key|access[_-]?token|refresh[_-]?token|session[_-]?id|BEGIN [A-Z ]*PRIVATE KEY|/path/to/local/user|raw logs|session transcript"
```

Inspect every hit. Do not commit:

- local config under `~/.codex`, `~/.claude`, `~/.agpair`;
- raw executor logs;
- private receipt artifacts;
- session transcripts;
- real credentials;
- private local workflow/report paths except sanitized docs examples.

## 7. Anti-Regression Requirements

These are not optional. V1.1 is incomplete if any item is missing from code or tests.

- `local_readonly` + `completion_policy=auto` resolves to report and does not require commit.
- `Required changes: none` and `Required changes: 无，禁止写入` resolve to report.
- `--completion-policy commit` still requires a task-specific commit.
- `ready_for_review` exists as a core phase, not just hook wording.
- `committed` and `evidence_ready` remain readable for legacy rows.
- Terminal artifacts survive cleanup.
- `task status --json` exposes durable artifact paths.
- Inline wait receipts are visible in status.
- Daemon terminal receipts and inline wait terminal receipts use the same evaluator.
- Low-noise `task wait` and `task watch --json` are the default wait surfaces; no normal path requires model-turn polling.
- Malformed success receipts do not become `ready_for_review`.
- Scope violations do not become successful readiness.
- `executor_unavailable` is detected before dispatch for implicit and explicit local CLI executors.
- Recently stuck or malformed-receipt executors can be marked ineligible without adding a scoring framework.
- `backend_safety_metadata` and `effective_task_safety` are separate.
- Status/watch/doctor expose neutral `executor_session_id`, not Antigravity-specific session wording for every backend.
- Codex controller does not implicitly select external `codex`.
- Claude Code controller does not implicitly select external `claude-code`.
- Codex `SubagentStart` and Claude Code `SubagentStart` are advisory-only, not hard vetoes.
- Claude Code `TaskCreated` and `TaskCompleted` are observability-only in V1.1.
- Gemini is not accepted for new start/retry/policy paths.
- Docs and skills describe current behavior only; they must not say all successful tasks commit.
- `skills/claw.json` metadata matches current executor positioning and does not advertise Gemini or Antigravity IDE as the recommended path.

## 8. Risks And Solutions

| Risk | Solution |
| --- | --- |
| Read-only tasks are still blocked for missing commit | Completion evaluator tests must cover readonly report success and commit-required failure separately. |
| Report tasks succeed without output | `report` policy requires stdout/report/receipt artifact; otherwise `blocked(output_missing)`. |
| Artifacts disappear after cleanup | Copy stdout/stderr/receipt/report to durable attempt directory before cleanup and test file existence after terminal cleanup. |
| `ready_for_review` only appears in docs | Add `mark_ready_for_review`, phase constants, status tests, hook tests, and wait/daemon tests. |
| Retry erases old attempt evidence | Add `task_attempts` and `task_artifacts`; test old attempt rows remain after retry. |
| Two terminal paths disagree | Daemon and wait must call the same evaluator. |
| Executor binary missing fails late | Use `ExecutorSpec` preflight before dispatch; status blocker type is `executor_unavailable`. |
| Unhealthy executor is retried forever | Track recent failure, stuck, and malformed receipt counters; mark clearly bad executors ineligible until health recovers. |
| Backend mutating metadata confuses readonly tasks | Split backend metadata from effective task safety in status. |
| Policy override breaks hooks | Hooks fail open; executing commands fail before dispatch. |
| External executor prose is trusted | Only structured receipt/artifact/evaluator output can mark `ready_for_review`. |
| Hooks become hidden model-token sink | Use deterministic command hooks; prompt/agent hooks are disallowed for V1.1 control. |
| Native subagents are accidentally disabled | `SubagentStart` is advisory-only and native subagents remain fallback/review. |
| Commit optional becomes too lax | `commit` policy remains strict and has tests. |
| Large refactor breaks legacy rows | Keep legacy aliases and fallback journal parsing tests. |

## 9. Completion Criteria

V1.1 is complete only when all of the following are true:

- New tasks default to `completion_policy=auto`, not `direct_commit`.
- Effective completion policy is stored on attempts and shown in status.
- Read-only report tasks can reach `ready_for_review` without commits.
- Commit-required tasks still block without commit.
- Durable attempt artifacts exist after terminal cleanup.
- `task status --json` surfaces receipt/report/log paths and output excerpt.
- `task logs --include-executor-output` reads durable artifacts.
- `task wait` and `task watch --json` provide low-noise completion waiting without model-turn polling.
- Controller-aware executor policy is centralized and used by task start, retry, hooks, doctor, and docs.
- `agpair policy show --controller codex --json` suppresses external `codex`.
- `agpair policy show --controller claude-code --json` suppresses external `claude-code`.
- Binary preflight prevents `command not found` late failures for local CLI executors.
- Executor health reports binary availability plus recent failure/stuck/malformed receipt signals.
- Hook config payloads match the current documented Codex and Claude Code lifecycle surfaces.
- `SubagentStart` is advisory-only and task lifecycle hooks are observability-only unless future explicit linking is added.
- Unit and integration suites pass.
- Docs and skills are updated to current target behavior.
- Optional local Codex/Claude config sync is verified when requested.
- Privacy gate passes before GitHub push.
