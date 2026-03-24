# Agpair CLI Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first standalone `agpair` repository so Codex chat can reliably drive Antigravity execution through a CLI plus a light daemon, without recreating a second Supervisor.

**Architecture:** `agpair` is a separate Python repository under `~/Projects/agpair`. It owns a tiny SQLite-backed task/session journal, a CLI for semantic control (`start/continue/approve/reject/retry`), and a light daemon for receipt ingestion, deduplication, stuck detection, and health bookkeeping. It reuses the existing Antigravity companion's public transport semantics but does not import this repository's runtime modules or use `~/.supervisor/*` as its primary state.

**Tech Stack:** Python 3.12+, Typer, sqlite3, pytest, standard-library subprocess/file locking/logging

---

## File Map

**New repository root:** `~/Projects/agpair`

**Core package**
- Create: `agpair/__init__.py`
- Create: `agpair/config.py`
  - Resolve `~/.agpair/` paths, default DB path, `agent-bus` executable path, pid/status paths.
- Create: `agpair/models.py`
  - Task/receipt/daemon health dataclasses or typed dicts.
- Create: `agpair/storage/schema.sql`
  - Minimal tables for tasks, receipts, journal, daemon health.
- Create: `agpair/storage/db.py`
  - SQLite connection helpers, migrations bootstrap.
- Create: `agpair/storage/tasks.py`
  - CRUD for task/session mapping and retry flags.
- Create: `agpair/storage/receipts.py`
  - Receipt dedupe and latest-receipt lookup.
- Create: `agpair/storage/journal.py`
  - Append-only task journal helpers.

**Transport layer**
- Create: `agpair/transport/bus.py`
  - Implement the minimal public `agent-bus` command adapter without importing current repo modules or reading its databases directly.
- Create: `agpair/transport/messages.py`
  - Public message/status mapping (`TASK`, `ACK`, `EVIDENCE_PACK`, `BLOCKED`, `COMMITTED`, `REVIEW`, `REVIEW_DELTA`, `APPROVED`).

**CLI**
- Create: `agpair/cli/__init__.py`
- Create: `agpair/cli/app.py`
  - Typer entrypoint.
- Create: `agpair/cli/task.py`
  - `task start/status/continue/approve/reject/retry/logs`.
- Create: `agpair/cli/daemon.py`
  - `daemon run/start/stop/status`.
- Create: `agpair/cli/doctor.py`
  - Environment and transport diagnostics.

**Daemon**
- Create: `agpair/daemon/loop.py`
- Create: `agpair/daemon/process.py`

**Tests**
- Create: `tests/integration/test_cli_help.py`
- Create: `tests/integration/test_task_start_and_status.py`
- Create: `tests/integration/test_task_continue_and_approval.py`
- Create: `tests/integration/test_daemon_receipts.py`
- Create: `tests/integration/test_daemon_stuck_detection.py`
- Create: `tests/integration/test_failure_modes.py`
- Create: `tests/integration/test_doctor.py`
- Create: `tests/unit/test_transport_adapter.py`
- Create: `tests/unit/test_daemon_boundaries.py`
- Create: `tests/fixtures/fake_agent_bus.py`

**Docs**
- Create: `README.md`
- Create: `docs/usage.md`

## Chunk 1: Repository Skeleton And Minimal Persistence

### Task 1: Bootstrap the standalone repository and CLI shell

**Files:**
- Create: `~/Projects/agpair/pyproject.toml`
- Create: `~/Projects/agpair/README.md`
- Create: `~/Projects/agpair/agpair/__init__.py`
- Create: `~/Projects/agpair/agpair/cli/app.py`
- Test: `~/Projects/agpair/tests/integration/test_cli_help.py`

- [ ] **Step 1: Write the failing CLI smoke test**

```python
from typer.testing import CliRunner

from agpair.cli.app import app


def test_cli_help_lists_top_level_groups() -> None:
    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "task" in result.stdout
    assert "daemon" in result.stdout
    assert "doctor" in result.stdout
```

- [ ] **Step 2: Run the test to verify the shell is missing**

Run: `cd ~/Projects/agpair && python -m pytest tests/integration/test_cli_help.py -q`
Expected: FAIL because `agpair.cli.app` or `app` does not exist yet.

- [ ] **Step 3: Add the minimal package and Typer shell**

```python
import typer

app = typer.Typer(no_args_is_help=True)
task_app = typer.Typer(no_args_is_help=True)
daemon_app = typer.Typer(no_args_is_help=True)
doctor_app = typer.Typer(no_args_is_help=True)

app.add_typer(task_app, name="task")
app.add_typer(daemon_app, name="daemon")
app.add_typer(doctor_app, name="doctor")
```

- [ ] **Step 4: Re-run the shell test**

Run: `cd ~/Projects/agpair && python -m pytest tests/integration/test_cli_help.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd ~/Projects/agpair
git add pyproject.toml README.md agpair/__init__.py agpair/cli/app.py tests/integration/test_cli_help.py
git commit -m "chore: bootstrap agpair cli shell"
```

### Task 2: Add config and SQLite bootstrap

**Files:**
- Create: `~/Projects/agpair/agpair/config.py`
- Create: `~/Projects/agpair/agpair/storage/schema.sql`
- Create: `~/Projects/agpair/agpair/storage/db.py`
- Test: `~/Projects/agpair/tests/integration/test_task_start_and_status.py`

- [ ] **Step 1: Write the failing DB bootstrap test**

```python
from agpair.config import AppPaths
from agpair.storage.db import ensure_database


def test_ensure_database_creates_sqlite_schema(tmp_path) -> None:
    paths = AppPaths.from_root(tmp_path)
    ensure_database(paths.db_path)
    assert paths.db_path.exists()
```

- [ ] **Step 2: Run the test to verify bootstrap is missing**

Run: `cd ~/Projects/agpair && python -m pytest tests/integration/test_task_start_and_status.py -k ensure_database -q`
Expected: FAIL because config/storage bootstrap does not exist.

- [ ] **Step 3: Implement config paths and schema bootstrap**

```python
@dataclass(frozen=True)
class AppPaths:
    root: Path
    db_path: Path
    status_path: Path
    pid_path: Path
    agent_bus_bin: str

    @classmethod
    def from_root(cls, root: Path) -> "AppPaths":
        ...
```

```python
def ensure_database(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.executescript(SCHEMA_SQL)
```

- [ ] **Step 4: Re-run the bootstrap test**

Run: `cd ~/Projects/agpair && python -m pytest tests/integration/test_task_start_and_status.py -k ensure_database -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd ~/Projects/agpair
git add agpair/config.py agpair/storage/schema.sql agpair/storage/db.py tests/integration/test_task_start_and_status.py
git commit -m "feat: add agpair sqlite bootstrap"
```

### Task 3: Add minimal task, receipt, and journal repositories

**Files:**
- Create: `~/Projects/agpair/agpair/models.py`
- Create: `~/Projects/agpair/agpair/storage/tasks.py`
- Create: `~/Projects/agpair/agpair/storage/receipts.py`
- Create: `~/Projects/agpair/agpair/storage/journal.py`
- Test: `~/Projects/agpair/tests/integration/test_task_start_and_status.py`

- [ ] **Step 1: Write failing repository tests for task lifecycle primitives**

```python
def test_task_repository_persists_session_mapping(tmp_path) -> None:
    repo = make_task_repo(tmp_path)
    repo.create_task(task_id="TASK-1", repo_path="/tmp/repo")
    repo.mark_acked(task_id="TASK-1", session_id="session-123")
    task = repo.get_task("TASK-1")
    assert task["phase"] == "acked"
    assert task["antigravity_session_id"] == "session-123"
```

```python
def test_receipt_repository_deduplicates_by_message_id(tmp_path) -> None:
    receipts = make_receipt_repo(tmp_path)
    assert receipts.record("msg-1", "TASK-1", "ACK") is True
    assert receipts.record("msg-1", "TASK-1", "ACK") is False
```

- [ ] **Step 2: Run the repository tests to confirm they fail**

Run: `cd ~/Projects/agpair && python -m pytest tests/integration/test_task_start_and_status.py -k "session_mapping or deduplicates" -q`
Expected: FAIL because repositories are missing.

- [ ] **Step 3: Implement the minimal repositories**

```python
class TaskRepository:
    def create_task(...): ...
    def mark_acked(...): ...
    def mark_evidence_ready(...): ...
    def mark_blocked(...): ...
    def mark_committed(...): ...
    def mark_stuck(...): ...
    def recommend_retry(...): ...
    def get_task(...): ...
```

```python
class ReceiptRepository:
    def record(self, message_id: str, task_id: str, status: str) -> bool: ...
```

```python
class JournalRepository:
    def append(self, task_id: str, event: str, body: str) -> None: ...
```

- [ ] **Step 4: Re-run the repository tests**

Run: `cd ~/Projects/agpair && python -m pytest tests/integration/test_task_start_and_status.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd ~/Projects/agpair
git add agpair/models.py agpair/storage/tasks.py agpair/storage/receipts.py agpair/storage/journal.py tests/integration/test_task_start_and_status.py
git commit -m "feat: add agpair task and receipt persistence"
```

## Chunk 2: Transport And Task CLI

### Task 4: Implement the public transport adapter against the shared bus

**Files:**
- Create: `~/Projects/agpair/agpair/transport/messages.py`
- Create: `~/Projects/agpair/agpair/transport/bus.py`
- Create: `~/Projects/agpair/tests/fixtures/fake_agent_bus.py`
- Test: `~/Projects/agpair/tests/unit/test_transport_adapter.py`
- Test: `~/Projects/agpair/tests/integration/test_task_start_and_status.py`

- [ ] **Step 1: Write the failing transport tests**

```python
def test_send_task_shells_out_to_agent_bus_cli(tmp_path) -> None:
    bus = make_bus(tmp_path, fake_agent_bus_bin)
    bus.send_task(task_id="TASK-1", body="Goal: test")
    recorded = read_fake_agent_bus_calls(tmp_path)
    assert recorded[-1]["argv"][:4] == ["agent-bus", "send", "--sender", "desktop"]
    assert recorded[-1]["body_contains"] == "Goal: test"
```

```python
def test_pull_receipts_shells_out_to_agent_bus_cli(tmp_path) -> None:
    bus = make_bus(tmp_path, fake_agent_bus_bin)
    receipts = bus.pull_receipts()
    assert receipts[0]["status"] == "ACK"
```

- [ ] **Step 2: Run the transport tests to confirm they fail**

Run: `cd ~/Projects/agpair && python -m pytest tests/unit/test_transport_adapter.py -q`
Expected: FAIL because the adapter does not exist yet.

- [ ] **Step 3: Implement the adapter against the public CLI contract**

```python
class AgentBusClient:
    def send_task(self, *, task_id: str, body: str, repo_path: str) -> int: ...
    def send_review(self, *, task_id: str, body: str) -> int: ...
    def send_approved(self, *, task_id: str, body: str) -> int: ...
    def pull_receipts(self) -> list[dict]: ...
```

The implementation must shell out to `agent-bus` as an external command and only depend on the public message envelope and status semantics. It must not open or mutate the shared bus SQLite file directly.

- [ ] **Step 4: Re-run the transport tests**

Run: `cd ~/Projects/agpair && python -m pytest tests/unit/test_transport_adapter.py tests/integration/test_task_start_and_status.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd ~/Projects/agpair
git add agpair/transport/messages.py agpair/transport/bus.py tests/fixtures/fake_agent_bus.py tests/unit/test_transport_adapter.py tests/integration/test_task_start_and_status.py
git commit -m "feat: add agpair bus transport adapter"
```

### Task 5: Implement `task start`, `task status`, and `task logs`

**Files:**
- Modify: `~/Projects/agpair/agpair/cli/app.py`
- Create: `~/Projects/agpair/agpair/cli/task.py`
- Test: `~/Projects/agpair/tests/integration/test_task_start_and_status.py`

- [ ] **Step 1: Write failing CLI tests for start/status/logs**

```python
def test_task_start_creates_local_record_and_sends_task(tmp_path) -> None:
    runner = make_runner(tmp_path)
    result = runner.invoke(app, ["task", "start", "--repo-path", "/tmp/repo", "--body", "Goal: fix it"])
    assert result.exit_code == 0
    assert "TASK-" in result.stdout
```

```python
def test_task_status_shows_phase_and_session(tmp_path) -> None:
    runner = make_runner_with_seeded_task(tmp_path, phase="acked", session_id="session-123")
    result = runner.invoke(app, ["task", "status", "TASK-1"])
    assert "acked" in result.stdout
    assert "session-123" in result.stdout
```

```python
def test_task_logs_prints_recent_journal_entries(tmp_path) -> None:
    ...
```

- [ ] **Step 2: Run the CLI tests to confirm they fail**

Run: `cd ~/Projects/agpair && python -m pytest tests/integration/test_task_start_and_status.py -q`
Expected: FAIL because the task commands are not implemented.

- [ ] **Step 3: Implement the read/write task commands**

```python
@task_app.command("start")
def start_task(repo_path: str, body: str, task_id: str | None = None) -> None: ...

@task_app.command("status")
def task_status(task_id: str) -> None: ...

@task_app.command("logs")
def task_logs(task_id: str, limit: int = 20) -> None: ...
```

- [ ] **Step 4: Re-run the CLI tests**

Run: `cd ~/Projects/agpair && python -m pytest tests/integration/test_task_start_and_status.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd ~/Projects/agpair
git add agpair/cli/app.py agpair/cli/task.py tests/integration/test_task_start_and_status.py
git commit -m "feat: add agpair task start status and logs"
```

## Chunk 3: Continuation Controls And Light Daemon

### Task 6: Implement `task continue`, `approve`, `reject`, and `retry`

**Files:**
- Modify: `~/Projects/agpair/agpair/cli/task.py`
- Test: `~/Projects/agpair/tests/integration/test_task_continue_and_approval.py`

- [ ] **Step 1: Write failing continuation tests**

```python
def test_task_continue_sends_review_for_existing_session(tmp_path) -> None:
    runner = make_runner_with_seeded_task(tmp_path, phase="evidence_ready", session_id="session-123")
    result = runner.invoke(app, ["task", "continue", "TASK-1", "--body", "Please fix edge case"])
    assert result.exit_code == 0
    message = read_latest_code_message(tmp_path)
    assert message["status"] in {"REVIEW", "REVIEW_DELTA"}
```

```python
def test_task_approve_sends_approved(tmp_path) -> None:
    ...
```

```python
def test_task_reject_routes_back_as_review_feedback(tmp_path) -> None:
    ...
```

```python
def test_task_retry_creates_fresh_attempt_and_new_task_message(tmp_path) -> None:
    ...
```

- [ ] **Step 2: Run the continuation tests**

Run: `cd ~/Projects/agpair && python -m pytest tests/integration/test_task_continue_and_approval.py -q`
Expected: FAIL because commands are not implemented.

- [ ] **Step 3: Implement the continuation commands**

```python
@task_app.command("continue")
def continue_task(task_id: str, body: str) -> None: ...

@task_app.command("approve")
def approve_task(task_id: str, body: str = "Approved") -> None: ...

@task_app.command("reject")
def reject_task(task_id: str, body: str) -> None: ...

@task_app.command("retry")
def retry_task(task_id: str, body: str | None = None) -> None: ...
```

- [ ] **Step 4: Re-run the continuation tests**

Run: `cd ~/Projects/agpair && python -m pytest tests/integration/test_task_continue_and_approval.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd ~/Projects/agpair
git add agpair/cli/task.py tests/integration/test_task_continue_and_approval.py
git commit -m "feat: add agpair continuation and retry commands"
```

### Task 7: Implement the light daemon for receipt ingestion and stuck detection

**Files:**
- Create: `~/Projects/agpair/agpair/daemon/loop.py`
- Create: `~/Projects/agpair/agpair/daemon/process.py`
- Create: `~/Projects/agpair/agpair/cli/daemon.py`
- Test: `~/Projects/agpair/tests/integration/test_daemon_receipts.py`
- Test: `~/Projects/agpair/tests/integration/test_daemon_stuck_detection.py`
- Test: `~/Projects/agpair/tests/unit/test_daemon_boundaries.py`

- [ ] **Step 1: Write failing daemon tests**

```python
def test_daemon_ingests_ack_and_updates_session_mapping(tmp_path) -> None:
    seed_task(tmp_path, task_id="TASK-1")
    seed_receipt(tmp_path, task_id="TASK-1", status="ACK", session_id="session-123")
    run_single_daemon_tick(tmp_path)
    task = load_task(tmp_path, "TASK-1")
    assert task["phase"] == "acked"
    assert task["antigravity_session_id"] == "session-123"
```

```python
def test_daemon_marks_stuck_and_retry_recommended_after_timeout(tmp_path) -> None:
    seed_acked_task_with_old_activity(tmp_path, task_id="TASK-1")
    run_single_daemon_tick(tmp_path, now=...)
    task = load_task(tmp_path, "TASK-1")
    assert task["phase"] == "stuck"
    assert task["retry_recommended"] is True
```

```python
def test_daemon_does_not_send_semantic_messages(tmp_path) -> None:
    seed_stale_task(tmp_path, task_id="TASK-1")
    fake_bus = make_fake_bus(tmp_path)
    run_single_daemon_tick(tmp_path, bus=fake_bus)
    assert fake_bus.sent_messages == []
```

```python
def test_daemon_does_not_create_fresh_retry_attempt(tmp_path) -> None:
    seed_stale_task(tmp_path, task_id="TASK-1")
    run_single_daemon_tick(tmp_path)
    assert count_attempts(tmp_path, "TASK-1") == 1
```

- [ ] **Step 2: Run the daemon tests**

Run: `cd ~/Projects/agpair && python -m pytest tests/integration/test_daemon_receipts.py tests/integration/test_daemon_stuck_detection.py tests/unit/test_daemon_boundaries.py -q`
Expected: FAIL because daemon logic does not exist.

- [ ] **Step 3: Implement one daemon tick and process helpers**

```python
def run_once(paths: AppPaths, now: datetime | None = None) -> None:
    ingest_new_receipts(...)
    refresh_daemon_health(...)
    mark_stuck_tasks(...)
```

```python
def start_background_daemon(...) -> int: ...
def stop_background_daemon(...) -> None: ...
def read_daemon_status(...) -> dict: ...
```

- [ ] **Step 4: Wire `daemon run/start/stop/status`**

Run: `cd ~/Projects/agpair && python -m pytest tests/integration/test_daemon_receipts.py tests/integration/test_daemon_stuck_detection.py tests/unit/test_daemon_boundaries.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd ~/Projects/agpair
git add agpair/daemon/loop.py agpair/daemon/process.py agpair/cli/daemon.py tests/integration/test_daemon_receipts.py tests/integration/test_daemon_stuck_detection.py tests/unit/test_daemon_boundaries.py
git commit -m "feat: add agpair light daemon"
```

## Chunk 4: Diagnostics, Docs, And End-to-End Proof

### Task 8: Add the failure matrix the spec requires

**Files:**
- Create: `~/Projects/agpair/tests/integration/test_failure_modes.py`

- [ ] **Step 1: Write the failing failure-posture tests**

```python
def test_duplicate_receipt_is_ignored(tmp_path) -> None:
    ...
```

```python
def test_stale_receipt_is_ignored(tmp_path) -> None:
    ...
```

```python
def test_continue_requires_known_session_mapping(tmp_path) -> None:
    ...
```

```python
def test_invalid_continuation_target_fails_closed(tmp_path) -> None:
    ...
```

```python
def test_retry_exhaustion_stops_automatic_recovery(tmp_path) -> None:
    ...
```

- [ ] **Step 2: Run the failure matrix tests**

Run: `cd ~/Projects/agpair && python -m pytest tests/integration/test_failure_modes.py -q`
Expected: FAIL because the failure posture is not fully implemented yet.

- [ ] **Step 3: Implement only the missing guards**

The implementation must fail closed and preserve journal evidence instead of inventing recovery.

- [ ] **Step 4: Re-run the failure matrix tests**

Run: `cd ~/Projects/agpair && python -m pytest tests/integration/test_failure_modes.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd ~/Projects/agpair
git add tests/integration/test_failure_modes.py
git commit -m "test: lock agpair failure posture"
```

### Task 9: Implement `doctor` and operator-facing docs

**Files:**
- Create: `~/Projects/agpair/agpair/cli/doctor.py`
- Test: `~/Projects/agpair/tests/integration/test_doctor.py`
- Create: `~/Projects/agpair/docs/usage.md`
- Modify: `~/Projects/agpair/README.md`

- [ ] **Step 1: Write the failing doctor tests**

```python
def test_doctor_reports_missing_bus_or_database_paths(tmp_path) -> None:
    runner = make_runner(tmp_path)
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0
    assert "bus" in result.stdout.lower()
```

- [ ] **Step 2: Run the doctor test**

Run: `cd ~/Projects/agpair && python -m pytest tests/integration/test_doctor.py -q`
Expected: FAIL because doctor command does not exist.

- [ ] **Step 3: Implement doctor diagnostics and docs**

`doctor` should report at minimum:
- config root
- DB existence
- `agent-bus` binary availability
- daemon pid/status visibility
- companion receipt freshness if available

- [ ] **Step 4: Re-run doctor tests**

Run: `cd ~/Projects/agpair && python -m pytest tests/integration/test_doctor.py -q`
Expected: PASS

- [ ] **Step 5: Write docs for the first real operator flow**

Document:
- install
- start daemon
- `task start`
- wait for `ACK` / `EVIDENCE_PACK`
- use `task continue/approve/reject/retry`
- inspect `task status` and `task logs`

- [ ] **Step 6: Commit**

```bash
cd ~/Projects/agpair
git add agpair/cli/doctor.py tests/integration/test_doctor.py README.md docs/usage.md
git commit -m "docs: add agpair operator guide and doctor command"
```

### Task 10: Run one local end-to-end pairing proof

**Files:**
- No new source files required if earlier tasks are complete.
- Optionally create: `~/Projects/agpair/scripts/e2e_smoke.sh`

- [ ] **Step 1: Start the daemon**

Run: `cd ~/Projects/agpair && python -m agpair.cli.app daemon run`
Expected: daemon starts and writes status/health.

- [ ] **Step 2: Dispatch a real smoke task**

Run: `cd ~/Projects/agpair && python -m agpair.cli.app task start --repo-path /absolute/path/to/test-repo --body "Goal: add a no-op smoke edit and prove continuation"`
Expected: prints a `TASK_ID`.

- [ ] **Step 3: Confirm the task reaches `ACK` and captures the session**

Run: `cd ~/Projects/agpair && python -m agpair.cli.app task status <TASK_ID>`
Expected: `phase=acked` and `antigravity_session_id` present.

- [ ] **Step 4: Confirm receipt ingestion reaches `evidence_ready` or `blocked`**

Run: `cd ~/Projects/agpair && python -m agpair.cli.app task logs <TASK_ID>`
Expected: journal shows companion receipts, no duplicates.

- [ ] **Step 5: Drive one continuation from Codex via CLI**

Run: `cd ~/Projects/agpair && python -m agpair.cli.app task continue <TASK_ID> --body "Please tighten the final proof and rerun the smoke test."`
Expected: same task is continued without losing the session mapping.

- [ ] **Step 6: Approve or retry explicitly**

Run one of:
- `cd ~/Projects/agpair && python -m agpair.cli.app task approve <TASK_ID>`
- `cd ~/Projects/agpair && python -m agpair.cli.app task retry <TASK_ID>`

Expected: transport uses the public contract correctly and the task reaches a fresh terminal receipt.

- [ ] **Step 7: Run the full test suite**

Run: `cd ~/Projects/agpair && python -m pytest -q`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
cd ~/Projects/agpair
git add .
git commit -m "test: prove agpair end-to-end pairing flow"
```
