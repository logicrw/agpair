# Client Hook Boundary V2.4 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make AGPair-controlled external workers and health probes immune to AGPair controller hooks, while preserving each external agent's normal skills, MCP, plugins, memory, and provider configuration.

**Architecture:** Split AGPair into two explicit runtime surfaces: the controller surface, where Codex / Claude Code hooks inject external-first routing and block unreviewed results, and the internal executor/probe surface, where those same AGPair hooks must no-op. Keep executor environments `managed-natural` by default; do not fix Claude Code by disabling its normal capabilities. The fix is a control-plane boundary, not a capability downgrade.

**Tech Stack:** Python 3.12, Typer, SQLite task state, Claude Code CLI, Codex CLI, local executor environment variables, pytest, AGPair hooks, AGPair doctor/status/smoke harness.

---

## 0. Why This Plan Exists

The V2.3 implementation made external results adoptable, but real Claude Code worker testing exposed a boundary bug:

- `claude` exists and launches: `/opt/homebrew/bin/claude`, version `2.1.150`.
- The active CC Switch provider is usable in a neutral noninteractive probe when `stdin=subprocess.DEVNULL` and `cwd=/tmp`.
- The same `claude --print --output-format json` probe can time out from a hook-heavy repo cwd.
- Debug logs show Claude Code loads user/project hooks, plugins, MCP, and skills. AGPair's Claude `UserPromptSubmit` hook injects external-first controller guidance into the Claude worker/probe. AGPair's Claude `Stop` hook can also block the probe when a task is `ready_for_review`.
- The probe then stops being a probe. It becomes a full controller-like Claude Code session that tries to inspect AGPair tasks, call Bash, obey ready-for-review stop gates, and sometimes recursively route through AGPair.

This is control-plane contamination:

```text
human controller session
  -> AGPair hooks should inject external-first guidance
  -> AGPair stop hook should block until receipts are reviewed

AGPair-started executor/probe
  -> AGPair hooks must not inject controller guidance
  -> AGPair stop hook must not block the worker/probe
  -> the external agent should execute the assigned brief directly
```

Codex worker did not expose the same failure because its background CLI path is lighter and did not recursively reinterpret the health probe as an AGPair controller workflow. The architecture must still protect Codex, Grok, Antigravity, and future executors with the same boundary.

## 1. Target Behavior

### 1.1 Controller Surface

When a human-facing Codex or Claude Code session starts in a project where AGPair is installed:

- `UserPromptSubmit` may inject external-first guidance.
- `Stop` may block on unreviewed `ready_for_review` / `evidence_ready` AGPair tasks.
- statusline may show AGPair state.
- subagent/task lifecycle hooks may record controller activity.

### 1.2 Internal Executor / Probe Surface

When AGPair itself starts a process for a health probe, real executor, smoke run, or retry attempt:

- AGPair client hooks must no-op.
- The external CLI should still inherit its normal user skills, MCP, plugins, memory, and provider configuration unless the user explicitly selected a diagnostic isolation mode.
- AGPair must set noninteractive and delegation-depth environment markers.
- Nested AGPair delegation remains blocked unless `--allow-nested-delegation` is explicit.
- Health probe failures must be classified by actual failure type, not collapsed into `executor_auth_required`.

Correct Claude Code outcomes after this plan:

| Scenario | Expected behavior |
| --- | --- |
| Active CC Switch provider works in neutral cwd | `doctor --fresh` reports `claude-code.available=true` or at least `auth_state=ok` |
| Claude Code provider returns 401 / invalid key | `executor_auth_required` |
| Claude Code probe exceeds timeout with no auth error | `executor_probe_timeout` |
| Claude Code probe/debug logs show AGPair hook recursion or ready_for_review stop gate | `executor_hook_interference` |
| Claude Code worker task runs from AGPair | AGPair hooks no-op, worker follows assigned task, returns receipt/report/evidence |
| Claude Code worker tries `agpair task start` inside executor | blocked by delegation guard unless explicit nested delegation is allowed |

## 2. Non-Goals

- Do not default Claude Code to `--bare`.
- Do not disable skills, MCP, plugins, or CC Switch for normal Claude Code worker runs.
- Do not add a new MCP server or reintroduce `agpair-mcp`.
- Do not special-case Claude Code in a way that bypasses the shared executor contract.
- Do not hide failures by increasing timeouts alone.
- Do not remove the controller-side AGPair hooks; they remain valuable in human controller sessions.

## 3. Files And Responsibilities

### New Shared Runtime Boundary Module

- Create: `agpair/internal_context.py`
  - Owns AGPair internal role environment variables.
  - Builds environment overlays for probes and executors.
  - Detects when client hooks should no-op.
  - Keeps this logic out of executor-specific modules.

### Hook Surfaces

- Modify: `agpair/cli/claude.py`
  - No-op all Claude hooks when AGPair marks the process as internal.
  - Keep human controller hooks unchanged.
  - Make Stop hook no-op for executor/probe role.

- Modify: `agpair/cli/codex.py`
  - Add the same no-op behavior for Codex hooks.
  - Codex currently works, but the boundary must be shared.

### Executor / Probe Launch

- Modify: `agpair/executors/local_cli.py`
  - Add `AGPAIR_INTERNAL_ROLE=executor`.
  - Add `AGPAIR_SUPPRESS_CLIENT_HOOKS=1`.
  - Preserve existing `AGPAIR_PARENT_TASK_ID`, `AGPAIR_DELEGATION_DEPTH`, `AGPAIR_NONINTERACTIVE`, and `CI`.

- Modify: `agpair/executors/claude_auth.py`
  - Run Claude live auth probes with internal probe env.
  - Run auth/provider probes from a neutral cwd.
  - Preserve `stdin=subprocess.DEVNULL`.
  - Classify timeout and hook interference separately from auth failure.

- Modify: `agpair/executors/policy.py`
  - Surface the new failure classes in executor health.
  - Keep controller suppression behavior unchanged.

### CLI Surfaces

- Modify: `agpair/cli/doctor.py`
  - Show probe cwd, client-hook suppression state, auth source, auth state, and last failure type.

- Modify: `agpair/cli/task.py`
  - Ensure task start preflight reports precise blocker types from executor health.

### Tests

- Modify: `tests/unit/test_delegation_guard.py`
- Create: `tests/unit/test_internal_context.py`
- Modify: `tests/unit/test_executor_lifecycle.py`
- Modify: `tests/unit/test_executor_onboarding.py`
- Modify: `tests/integration/test_claude_cli.py`
- Modify: `tests/integration/test_codex_cli.py`
- Modify: `tests/integration/test_doctor.py`
- Modify: `tests/integration/test_real_executor_smoke_harness.py`

### Docs / Skills

- Modify: `skills/Codex/SKILL.md`
- Modify: `skills/Claude/SKILL.md`
- Modify: `docs/usage.md`
- Modify: `docs/usage.zh-CN.md`
- Modify: `README.md`
- Modify: `README.zh-CN.md`

## 4. Task 1: Add Shared Internal Context

**Files:**

- Create: `agpair/internal_context.py`
- Create: `tests/unit/test_internal_context.py`
- Modify: `agpair/delegation_guard.py`

- [ ] **Step 1: Write failing tests for internal role detection**

Create `tests/unit/test_internal_context.py`:

```python
from agpair.internal_context import (
    INTERNAL_ROLE_ENV,
    NONINTERACTIVE_ENV,
    SUPPRESS_CLIENT_HOOKS_ENV,
    build_internal_executor_env,
    build_internal_probe_env,
    client_hooks_suppressed,
    internal_role,
)


def test_client_hooks_suppressed_by_explicit_flag() -> None:
    assert client_hooks_suppressed({SUPPRESS_CLIENT_HOOKS_ENV: "1"}) is True
    assert client_hooks_suppressed({SUPPRESS_CLIENT_HOOKS_ENV: "true"}) is True
    assert client_hooks_suppressed({SUPPRESS_CLIENT_HOOKS_ENV: "0"}) is False


def test_client_hooks_suppressed_for_internal_roles() -> None:
    assert client_hooks_suppressed({INTERNAL_ROLE_ENV: "probe"}) is True
    assert client_hooks_suppressed({INTERNAL_ROLE_ENV: "executor"}) is True
    assert client_hooks_suppressed({INTERNAL_ROLE_ENV: "controller"}) is False


def test_build_internal_probe_env_marks_noninteractive_probe() -> None:
    env = build_internal_probe_env({"PATH": "/bin", "AGPAIR_DELEGATION_DEPTH": "9"})

    assert env["PATH"] == "/bin"
    assert env[INTERNAL_ROLE_ENV] == "probe"
    assert env[SUPPRESS_CLIENT_HOOKS_ENV] == "1"
    assert env[NONINTERACTIVE_ENV] == "1"
    assert env["CI"] == "1"
    assert env["AGPAIR_DELEGATION_DEPTH"] == "1"


def test_build_internal_executor_env_increments_depth_and_records_parent() -> None:
    env = build_internal_executor_env("TASK-123", {"AGPAIR_DELEGATION_DEPTH": "2"})

    assert env[INTERNAL_ROLE_ENV] == "executor"
    assert env[SUPPRESS_CLIENT_HOOKS_ENV] == "1"
    assert env[NONINTERACTIVE_ENV] == "1"
    assert env["AGPAIR_PARENT_TASK_ID"] == "TASK-123"
    assert env["AGPAIR_DELEGATION_DEPTH"] == "3"
    assert internal_role(env) == "executor"
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run:

```bash
PYTHONPATH=. pytest tests/unit/test_internal_context.py -q
```

Expected:

```text
ModuleNotFoundError: No module named 'agpair.internal_context'
```

- [ ] **Step 3: Implement the module**

Create `agpair/internal_context.py`:

```python
from __future__ import annotations

import os
from collections.abc import Mapping

from agpair.delegation_guard import DELEGATION_DEPTH_ENV, next_delegation_env

INTERNAL_ROLE_ENV = "AGPAIR_INTERNAL_ROLE"
SUPPRESS_CLIENT_HOOKS_ENV = "AGPAIR_SUPPRESS_CLIENT_HOOKS"
NONINTERACTIVE_ENV = "AGPAIR_NONINTERACTIVE"

INTERNAL_ROLE_PROBE = "probe"
INTERNAL_ROLE_EXECUTOR = "executor"
INTERNAL_ROLE_SMOKE = "smoke"
INTERNAL_ROLES = frozenset({INTERNAL_ROLE_PROBE, INTERNAL_ROLE_EXECUTOR, INTERNAL_ROLE_SMOKE})
TRUE_ENV_VALUES = frozenset({"1", "true", "yes", "on"})


def _source(env: Mapping[str, str] | None = None) -> Mapping[str, str]:
    return os.environ if env is None else env


def internal_role(env: Mapping[str, str] | None = None) -> str | None:
    role = _source(env).get(INTERNAL_ROLE_ENV, "").strip().lower()
    return role or None


def client_hooks_suppressed(env: Mapping[str, str] | None = None) -> bool:
    source = _source(env)
    explicit = source.get(SUPPRESS_CLIENT_HOOKS_ENV, "").strip().lower()
    if explicit in TRUE_ENV_VALUES:
        return True
    return internal_role(source) in INTERNAL_ROLES


def build_internal_probe_env(base_env: Mapping[str, str] | None = None) -> dict[str, str]:
    env = dict(os.environ if base_env is None else base_env)
    env.update(
        {
            INTERNAL_ROLE_ENV: INTERNAL_ROLE_PROBE,
            SUPPRESS_CLIENT_HOOKS_ENV: "1",
            NONINTERACTIVE_ENV: "1",
            "CI": "1",
            DELEGATION_DEPTH_ENV: "1",
        }
    )
    return env


def build_internal_executor_env(task_id: str, base_env: Mapping[str, str] | None = None) -> dict[str, str]:
    env = dict(os.environ if base_env is None else base_env)
    env.update(next_delegation_env(task_id, env))
    env.update(
        {
            INTERNAL_ROLE_ENV: INTERNAL_ROLE_EXECUTOR,
            SUPPRESS_CLIENT_HOOKS_ENV: "1",
            NONINTERACTIVE_ENV: "1",
            "CI": "1",
        }
    )
    return env
```

- [ ] **Step 4: Run the tests and confirm they pass**

Run:

```bash
PYTHONPATH=. pytest tests/unit/test_internal_context.py tests/unit/test_delegation_guard.py -q
```

Expected:

```text
passed
```

## 5. Task 2: Make Claude And Codex Hooks No-Op For Internal Processes

**Files:**

- Modify: `agpair/cli/claude.py`
- Modify: `agpair/cli/codex.py`
- Modify: `tests/integration/test_claude_cli.py`
- Modify: `tests/integration/test_codex_cli.py`

- [ ] **Step 1: Add hook no-op tests for Claude**

Add tests to `tests/integration/test_claude_cli.py`:

```python
def test_claude_user_prompt_hook_noops_for_internal_executor(monkeypatch, cli_runner):
    monkeypatch.setenv("AGPAIR_INTERNAL_ROLE", "executor")
    result = cli_runner.invoke(claude_app, ["hook", "user-prompt-submit"], input='{"cwd":"/tmp"}')

    assert result.exit_code == 0
    assert result.output.strip() in {"", '{"continue":true,"suppressOutput":true}'}
    assert "AGPair external-first routing" not in result.output


def test_claude_stop_hook_noops_for_internal_probe(monkeypatch, cli_runner):
    monkeypatch.setenv("AGPAIR_INTERNAL_ROLE", "probe")
    result = cli_runner.invoke(claude_app, ["hook", "stop"], input='{"cwd":"/tmp"}')

    assert result.exit_code == 0
    assert "ready_for_review" not in result.output
    assert '"decision":"block"' not in result.output.replace(" ", "")
```

Use the actual fixture/runner names already present in `tests/integration/test_claude_cli.py`. If that file uses `CliRunner()` directly, mirror the local style instead of introducing a new fixture.

- [ ] **Step 2: Add hook no-op tests for Codex**

Add equivalent tests to `tests/integration/test_codex_cli.py`:

```python
def test_codex_user_prompt_hook_noops_for_internal_executor(monkeypatch, cli_runner):
    monkeypatch.setenv("AGPAIR_INTERNAL_ROLE", "executor")
    result = cli_runner.invoke(codex_app, ["hook", "user-prompt-submit"], input='{"cwd":"/tmp"}')

    assert result.exit_code == 0
    assert "AGPair external-first routing" not in result.output


def test_codex_stop_hook_noops_for_internal_probe(monkeypatch, cli_runner):
    monkeypatch.setenv("AGPAIR_INTERNAL_ROLE", "probe")
    result = cli_runner.invoke(codex_app, ["hook", "stop"], input='{"cwd":"/tmp"}')

    assert result.exit_code == 0
    assert '"decision":"block"' not in result.output.replace(" ", "")
```

- [ ] **Step 3: Run tests and confirm they fail**

Run:

```bash
PYTHONPATH=. pytest tests/integration/test_claude_cli.py tests/integration/test_codex_cli.py -q
```

Expected:

```text
at least the new no-op tests fail because hooks still inject controller context
```

- [ ] **Step 4: Implement shared no-op helper in Claude CLI**

In `agpair/cli/claude.py`:

```python
from agpair.internal_context import client_hooks_suppressed
```

Add a helper near `_emit_json` / hook helpers:

```python
def _emit_noop_hook() -> None:
    _emit_json({"continue": True, "suppressOutput": True})


def _should_noop_client_hook() -> bool:
    return client_hooks_suppressed()
```

At the top of every hook command body, before reading task state or injecting context:

```python
if _should_noop_client_hook():
    _emit_noop_hook()
    return
```

Apply this to:

- `hook_session_start`
- `hook_precompact`
- `hook_user_prompt_submit`
- `hook_stop`
- `hook_subagent_start`
- `hook_subagent_stop`
- `hook_task_created`
- `hook_task_completed`

The most important ones are `hook_user_prompt_submit` and `hook_stop`, but partial coverage is a bug because future Claude Code updates can add more lifecycle events to noninteractive runs.

- [ ] **Step 5: Implement the same boundary in Codex CLI**

In `agpair/cli/codex.py`, import `client_hooks_suppressed()` and add the same no-op helper.

Apply it to:

- `hook_user_prompt_submit`
- `hook_stop`
- `hook_subagent_start`
- any other AGPair-managed Codex hook command in the file

Codex currently passes real smoke, but parity prevents future recursion when Codex CLI behavior changes.

- [ ] **Step 6: Run hook tests**

Run:

```bash
PYTHONPATH=. pytest tests/integration/test_claude_cli.py tests/integration/test_codex_cli.py -q
```

Expected:

```text
passed
```

## 6. Task 3: Mark All AGPair Internal Executor Launches

**Files:**

- Modify: `agpair/executors/local_cli.py`
- Modify: `tests/unit/test_local_cli_executor.py`
- Modify: `tests/unit/test_local_cli_executor_isolated.py`

- [ ] **Step 1: Write failing executor env tests**

Extend the existing env injection test in `tests/unit/test_local_cli_executor.py`:

```python
assert mock_popen.call_args.kwargs["env"]["AGPAIR_INTERNAL_ROLE"] == "executor"
assert mock_popen.call_args.kwargs["env"]["AGPAIR_SUPPRESS_CLIENT_HOOKS"] == "1"
```

Add the same assertion to an isolated-worktree dispatch test in `tests/unit/test_local_cli_executor_isolated.py`, so both normal and isolated executor paths are covered.

- [ ] **Step 2: Run targeted tests and confirm they fail**

Run:

```bash
PYTHONPATH=. pytest tests/unit/test_local_cli_executor.py tests/unit/test_local_cli_executor_isolated.py -q
```

Expected:

```text
failures showing missing AGPAIR_INTERNAL_ROLE / AGPAIR_SUPPRESS_CLIENT_HOOKS
```

- [ ] **Step 3: Implement executor env overlay**

In `agpair/executors/local_cli.py`, replace ad hoc environment updates for AGPair parent/depth/noninteractive with `build_internal_executor_env()`.

Required shape:

```python
from agpair.internal_context import build_internal_executor_env

...

process_env = os.environ.copy()
process_env.update(self.executor_env(task_id=task_id, session_dir=session_dir))
process_env = build_internal_executor_env(task_id, process_env)
```

Preserve existing executor-specific env injection and secret redaction. `cmd.json` must not persist secrets or full env.

- [ ] **Step 4: Verify env tests**

Run:

```bash
PYTHONPATH=. pytest tests/unit/test_local_cli_executor.py tests/unit/test_local_cli_executor_isolated.py tests/unit/test_delegation_guard.py tests/unit/test_internal_context.py -q
```

Expected:

```text
passed
```

## 7. Task 4: Make Claude Health Probes Neutral, Tagged, And Correctly Classified

**Files:**

- Modify: `agpair/executors/claude_auth.py`
- Modify: `agpair/executors/policy.py`
- Modify: `tests/unit/test_executor_lifecycle.py`
- Modify: `tests/unit/test_executor_onboarding.py`
- Modify: `tests/integration/test_doctor.py`

- [ ] **Step 1: Add probe cwd/env tests**

In `tests/unit/test_executor_lifecycle.py`, add:

```python
def test_claude_probe_runs_with_internal_env_and_neutral_cwd(monkeypatch):
    captured = {}

    class FakeProcess:
        pid = 123
        returncode = 0

        def communicate(self, timeout=None):
            return ('{"type":"result","result":"agpair-ccswitch-health-ok"}', "")

    def fake_popen(cmd, **kwargs):
        captured.update(kwargs)
        return FakeProcess()

    monkeypatch.setattr("agpair.executors.claude_auth.subprocess.Popen", fake_popen)

    proc = _run_probe(
        "/opt/homebrew/bin/claude",
        ["--print", "Return exactly: agpair-ccswitch-health-ok"],
        timeout_seconds=1.0,
    )

    assert proc.returncode == 0
    assert captured["stdin"] is subprocess.DEVNULL
    assert captured["cwd"] == tempfile.gettempdir()
    assert captured["env"]["AGPAIR_INTERNAL_ROLE"] == "probe"
    assert captured["env"]["AGPAIR_SUPPRESS_CLIENT_HOOKS"] == "1"
```

Adjust imports for `subprocess` and `tempfile`.

- [ ] **Step 2: Add failure classification tests**

Add tests:

```python
def test_claude_timeout_is_probe_timeout_not_auth_required(monkeypatch):
    def fake_run_probe(*args, **kwargs):
        raise subprocess.TimeoutExpired(["claude"], 30, output="", stderr="command timed out after 30s")

    monkeypatch.setattr("agpair.executors.claude_auth._run_probe", fake_run_probe)

    error = claude_ccswitch_error("/opt/homebrew/bin/claude", timeout_seconds=30)

    assert error is not None
    assert error.failure_type == "executor_probe_timeout"


def test_claude_401_is_auth_required(monkeypatch):
    def fake_run_probe(*args, **kwargs):
        return subprocess.CompletedProcess(
            ["claude"],
            1,
            '{"type":"result","is_error":true,"api_error_status":401,"result":"Invalid authentication credentials"}',
            "",
        )

    monkeypatch.setattr("agpair.executors.claude_auth._run_probe", fake_run_probe)

    error = claude_ccswitch_error("/opt/homebrew/bin/claude", timeout_seconds=30)

    assert error is not None
    assert error.failure_type == "executor_auth_required"
```

If `claude_ccswitch_error()` currently returns a plain string, first introduce a tiny dataclass:

```python
@dataclass(frozen=True)
class ClaudeProbeError:
    message: str
    failure_type: str
```

Update callers to use `.message` and `.failure_type`.

- [ ] **Step 3: Add hook interference classification test**

Add:

```python
def test_claude_probe_detects_agpair_hook_interference(monkeypatch):
    def fake_run_probe(*args, **kwargs):
        return subprocess.CompletedProcess(
            ["claude"],
            1,
            "",
            "AGPair task TASK-123 reached ready_for_review. Inspect git status, diff/commits, receipt",
        )

    monkeypatch.setattr("agpair.executors.claude_auth._run_probe", fake_run_probe)

    error = claude_ccswitch_error("/opt/homebrew/bin/claude", timeout_seconds=30)

    assert error is not None
    assert error.failure_type == "executor_hook_interference"
```

- [ ] **Step 4: Run lifecycle tests and confirm failures**

Run:

```bash
PYTHONPATH=. pytest tests/unit/test_executor_lifecycle.py -q
```

Expected:

```text
new tests fail until _run_probe/env/cwd/classification are implemented
```

- [ ] **Step 5: Implement neutral probe cwd and internal env**

In `agpair/executors/claude_auth.py`:

```python
import tempfile
from dataclasses import dataclass

from agpair.internal_context import build_internal_probe_env
```

Change `_run_probe` signature:

```python
def _run_probe(
    binary_path: str,
    args: list[str],
    *,
    env: dict[str, str] | None = None,
    timeout_seconds: float,
    cwd: str | None = None,
) -> subprocess.CompletedProcess[str]:
```

Build env and cwd inside `_run_probe`:

```python
probe_env = build_internal_probe_env(env)
probe_cwd = cwd or os.environ.get("AGPAIR_CLAUDE_CODE_PROBE_CWD") or tempfile.gettempdir()
```

Pass both to `subprocess.Popen`:

```python
process = subprocess.Popen(
    [binary_path, *args],
    cwd=probe_cwd,
    stdin=subprocess.DEVNULL,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True,
    env=probe_env,
    start_new_session=True,
)
```

Do not remove `stdin=subprocess.DEVNULL`; it is required. Without it, Claude `--print` can read parent stdin and hallucinate that it must execute the caller's Python/shell script.

- [ ] **Step 6: Implement precise failure classes**

Add:

```python
@dataclass(frozen=True)
class ClaudeProbeError:
    message: str
    failure_type: str
```

Classification rules:

```python
def _classify_probe_failure(stdout: str, stderr: str, exc: BaseException | None = None) -> str:
    combined = f"{stdout}\n{stderr}".lower()
    if isinstance(exc, subprocess.TimeoutExpired):
        if "agpair task" in combined or "ready_for_review" in combined:
            return "executor_hook_interference"
        return "executor_probe_timeout"
    if "api_error_status\":401" in combined or "invalid authentication" in combined:
        return "executor_auth_required"
    if "agpair task" in combined or "ready_for_review" in combined or "agpair external-first routing" in combined:
        return "executor_hook_interference"
    return "executor_noninteractive_failed"
```

Use these failure types in `claude_oauth_error()`, `claude_ccswitch_error()`, and `claude_api_error()` instead of returning only a string.

- [ ] **Step 7: Update policy health mapping**

In `agpair/executors/policy.py`, when Claude auth resolution returns a probe error:

```python
last_failure_type = resolution.failure_type
auth_state = "ok" if auth_satisfied else resolution.failure_type
```

Keep `executor_auth_required` only for actual auth failures.

The `available` boolean remains:

```python
available = binary_available and launch_clean and auth_satisfied
```

So `executor_probe_timeout` still makes the executor unavailable, but the reason is accurate.

- [ ] **Step 8: Update doctor output tests**

In `tests/integration/test_doctor.py`, assert Claude health includes:

```python
assert health["auth_probe_environment_mode"] == "managed-natural"
assert health["last_failure_type"] in {
    None,
    "executor_auth_required",
    "executor_probe_timeout",
    "executor_hook_interference",
    "executor_noninteractive_failed",
}
```

Add one mocked doctor test where timeout becomes `executor_probe_timeout`.

- [ ] **Step 9: Run lifecycle / onboarding / doctor tests**

Run:

```bash
PYTHONPATH=. pytest tests/unit/test_executor_lifecycle.py tests/unit/test_executor_onboarding.py tests/integration/test_doctor.py -q
```

Expected:

```text
passed
```

## 8. Task 5: Ensure Task Start Preflight Uses The New Blockers

**Files:**

- Modify: `agpair/cli/task.py`
- Modify: `tests/integration/test_task_start_and_status.py`
- Modify: `tests/integration/test_real_executor_smoke_harness.py`

- [ ] **Step 1: Add task start blocker tests**

In `tests/integration/test_task_start_and_status.py`, add a mocked Claude health case:

```python
def test_task_start_reports_claude_probe_timeout_not_auth_required(monkeypatch, tmp_path, runner):
    monkeypatch.setattr(
        "agpair.executors.policy.executor_health_snapshot",
        lambda *args, **kwargs: {
            "claude-code": {
                "executor_id": "claude-code",
                "available": False,
                "binary_available": True,
                "launch_clean": True,
                "auth_state": "executor_probe_timeout",
                "last_failure_type": "executor_probe_timeout",
                "last_error_excerpt": "command timed out after 30s",
            }
        },
    )

    result = runner.invoke(
        task_app,
        [
            "start",
            "--repo-path",
            str(tmp_path),
            "--controller",
            "codex",
            "--executor",
            "claude-code",
            "--authorization-profile",
            "local_readonly",
            "--completion-policy",
            "report",
            "--body",
            "Goal: x\nScope: x\nRequired changes: none\nExit criteria: x",
        ],
    )

    assert result.exit_code != 0
    assert "executor_probe_timeout" in result.output
    assert "executor_auth_required" not in result.output
```

Mirror existing helper style if the test file uses a different runner fixture.

- [ ] **Step 2: Run targeted tests and confirm failure**

Run:

```bash
PYTHONPATH=. pytest tests/integration/test_task_start_and_status.py -q
```

Expected:

```text
new blocker assertion fails until task start surfaces last_failure_type
```

- [ ] **Step 3: Propagate precise blocker**

In `agpair/cli/task.py`, when requested executor preflight rejects an executor, choose blocker in this order:

```python
blocker_type = (
    health.get("last_failure_type")
    or health.get("auth_state")
    or "executor_unavailable"
)
```

Make sure status/log payloads preserve:

- `executor_id`
- `binary_path`
- `auth_state`
- `last_failure_type`
- `last_error_excerpt`
- `fallback_suggestion`

- [ ] **Step 4: Run task tests**

Run:

```bash
PYTHONPATH=. pytest tests/integration/test_task_start_and_status.py tests/integration/test_real_executor_smoke_harness.py -q
```

Expected:

```text
passed
```

## 9. Task 6: Real Claude Code Worker Smoke

**Files:**

- Modify: `scripts/smoke_real_executors.py`
- Modify: `tests/integration/test_real_executor_smoke_harness.py`
- No source edits during real smoke unless a failure requires a tested fix.

- [ ] **Step 1: Add a Claude-specific smoke note to the harness output**

When executor is `claude-code`, include:

```json
{
  "internal_role_expected": "executor",
  "client_hooks_suppressed_expected": true,
  "auth_source": "...",
  "auth_state": "..."
}
```

This is metadata in the smoke report, not a separate execution mode.

- [ ] **Step 2: Run neutral provider probe manually**

Run:

```bash
python - <<'PY'
import json, subprocess, tempfile, time

cmd = [
    "/opt/homebrew/bin/claude",
    "--permission-mode", "default",
    "--no-session-persistence",
    "--output-format", "json",
    "--print", "Return exactly: agpair-manual-probe-ok",
]
t = time.time()
cp = subprocess.run(
    cmd,
    cwd=tempfile.gettempdir(),
    stdin=subprocess.DEVNULL,
    capture_output=True,
    text=True,
    timeout=60,
)
print("exit", cp.returncode, "seconds", round(time.time() - t, 2))
data = json.loads(cp.stdout)
print("result", data.get("result"))
PY
```

Expected:

```text
exit 0
result agpair-manual-probe-ok
```

- [ ] **Step 3: Run AGPair doctor**

Run:

```bash
PYTHONPATH=. python -m agpair.cli.app doctor --fresh > /tmp/agpair-doctor-v2-4.json
python - <<'PY'
import json
h=json.load(open("/tmp/agpair-doctor-v2-4.json"))["executor_cli_health"]["claude-code"]
for k in ["available","auth_state","auth_source","ccswitch_provider","auth_satisfied","last_failure_type","last_error_excerpt"]:
    print(k, h.get(k))
PY
```

Expected on the current machine after this fix:

```text
available True
auth_state ok
auth_source ccswitch
ccswitch_provider <active CC Switch provider>
auth_satisfied True
last_failure_type None
```

If this still fails, the failure must be one of:

- `executor_auth_required`: provider/key really failed.
- `executor_probe_timeout`: provider did not complete within timeout even from neutral cwd and suppressed hooks.
- `executor_hook_interference`: a hook still ran inside the probe, which means Task 2 or Task 4 is incomplete.
- `executor_noninteractive_failed`: inspect excerpts and add a focused regression test before changing behavior.

- [ ] **Step 4: Run real Codex-controller Claude worker smoke**

Run:

```bash
PYTHONPATH=. python scripts/smoke_real_executors.py \
  --repo-path "$PWD" \
  --controller codex \
  --executors claude-code \
  --timeout-seconds 360 \
  --interval-seconds 5 \
  --no-progress-seconds 120
```

Expected:

```text
all_success=true
claude-code outcome=ready_for_review
scope_ok=true
adoptable=true
worktree cleanup removed=true
```

- [ ] **Step 5: Run controller matrices**

Run:

```bash
PYTHONPATH=. python scripts/smoke_real_executors.py \
  --repo-path "$PWD" \
  --controller codex \
  --executors antigravity-cli,grok-cli,claude-code \
  --timeout-seconds 420 \
  --interval-seconds 5 \
  --no-progress-seconds 120
```

Run:

```bash
PYTHONPATH=. python scripts/smoke_real_executors.py \
  --repo-path "$PWD" \
  --controller claude-code \
  --executors antigravity-cli,grok-cli,codex \
  --timeout-seconds 420 \
  --interval-seconds 5 \
  --no-progress-seconds 120
```

Expected:

```text
Codex controller: antigravity-cli, grok-cli, claude-code pass.
Claude Code controller: antigravity-cli, grok-cli, codex pass.
External codex remains suppressed for Codex controller.
External claude-code remains suppressed for Claude Code controller.
```

If Claude Code is unavailable due real provider failure, record the exact blocker from doctor and smoke. Do not mark that as AGPair success unless the blocker type is accurate and actionable.

## 10. Task 7: Docs, Skills, And Local Config Sync

**Files:**

- Modify: `skills/Codex/SKILL.md`
- Modify: `skills/Claude/SKILL.md`
- Modify: `docs/usage.md`
- Modify: `docs/usage.zh-CN.md`
- Modify: `README.md`
- Modify: `README.zh-CN.md`

- [ ] **Step 1: Update skills**

Both Codex and Claude skills must say:

```text
AGPair external-first routing applies to controller sessions.
AGPair-started executor/probe processes suppress AGPair client hooks to avoid recursive delegation.
External workers still inherit their normal CLI capabilities, skills, MCP, plugins, memory, and provider config unless an explicit diagnostic mode says otherwise.
```

Claude-specific wording:

```text
Claude Code worker auth mode is auto: OAuth/subscription first, then CC Switch provider.
Probe timeout is not the same as auth failure; check doctor last_failure_type.
```

- [ ] **Step 2: Update usage docs**

Document these environment variables:

```text
AGPAIR_INTERNAL_ROLE=probe|executor|smoke
AGPAIR_SUPPRESS_CLIENT_HOOKS=1
AGPAIR_NONINTERACTIVE=1
AGPAIR_CLAUDE_CODE_PROBE_CWD=/tmp-like-neutral-path
```

Mark the first three as AGPair-internal. Users normally should not set them manually except for diagnostics.

- [ ] **Step 3: Update README current-positioning**

Keep wording concise:

```text
AGPair is a CLI control plane for external local agent executors. Controller hooks help Codex and Claude Code delegate work; AGPair suppresses those hooks inside its own executors and probes so external agents behave like normal standalone CLIs rather than nested controllers.
```

Do not explain old architecture unless needed for migration.

- [ ] **Step 4: Sync local configs**

Run:

```bash
PYTHONPATH=. python -m agpair.cli.app codex config --install --scope user
PYTHONPATH=. python -m agpair.cli.app claude config --install --scope user
cmp -s skills/Codex/SKILL.md ~/.codex/skills/agpair-codex/SKILL.md && echo codex_skill_ok
cmp -s skills/Claude/SKILL.md ~/.claude/skills/agpair/SKILL.md && echo claude_skill_ok
```

Expected:

```text
codex_skill_ok
claude_skill_ok
```

## 11. Task 8: Final Verification And Privacy-Safe Commit

**Files:**

- No source changes unless verification finds a bug.

- [ ] **Step 1: Run focused tests**

Run:

```bash
PYTHONPATH=. pytest \
  tests/unit/test_internal_context.py \
  tests/unit/test_delegation_guard.py \
  tests/unit/test_local_cli_executor.py \
  tests/unit/test_local_cli_executor_isolated.py \
  tests/unit/test_executor_lifecycle.py \
  tests/unit/test_executor_onboarding.py \
  tests/integration/test_claude_cli.py \
  tests/integration/test_codex_cli.py \
  tests/integration/test_doctor.py \
  tests/integration/test_task_start_and_status.py \
  tests/integration/test_real_executor_smoke_harness.py \
  -q
```

Expected:

```text
passed
```

- [ ] **Step 2: Run full suite**

Run:

```bash
PYTHONPATH=. pytest -q
```

Expected:

```text
all tests passed
```

- [ ] **Step 3: Run static checks**

Run:

```bash
PYTHONPATH=. python -m compileall -q agpair scripts tests
git diff --check
```

Expected:

```text
no output from git diff --check
```

- [ ] **Step 4: Run real smoke**

Run the smoke commands from Task 6 and summarize the latest report paths:

```bash
python - <<'PY'
import glob, json, os
for pattern in [
    ".agpair/smoke/reports/smoke-codex-*.json",
    ".agpair/smoke/reports/smoke-claude-code-*.json",
]:
    path=max(glob.glob(pattern), key=os.path.getmtime)
    data=json.load(open(path))
    print(path, data["controller"], data["all_success"])
    for item in data["results"]:
        print(item["executor_id"], item.get("outcome"), item.get("adoptable_result"), item.get("failure_class"))
PY
```

Expected:

```text
Codex matrix succeeds, including claude-code if provider is currently healthy.
Claude Code matrix succeeds.
Any failure has a precise blocker that is not mislabeled as auth unless auth truly failed.
```

- [ ] **Step 5: Privacy scan staged diff**

Run:

```bash
git diff --cached --color=never | rg -n "sk-[A-Za-z0-9_-]{16,}|api[_-]?key\s*[:=]|token\s*[:=]|secret\s*[:=]|Authorization:|Bearer [A-Za-z0-9._-]{16,}|ANTHROPIC_API_KEY|ANTHROPIC_AUTH_TOKEN|OPENAI_API_KEY|XAI_API_KEY"
```

Expected:

```text
no real secrets; env var names and test fake values are acceptable only after inspection
```

- [ ] **Step 6: Commit**

Use the Lore Commit Protocol:

```bash
git add agpair tests docs skills README.md README.zh-CN.md
git commit
```

Commit message intent line:

```text
Keep AGPair controller hooks out of internal workers
```

Required trailers:

```text
Constraint: Claude Code worker must reuse OAuth/CC Switch/provider config without defaulting to bare or disabled skills/MCP.
Rejected: Disable Claude Code skills/MCP by default | fixes the symptom by making workers less capable.
Rejected: Increase probe timeout only | hides hook recursion and still misclassifies failures.
Confidence: high
Scope-risk: moderate
Directive: Any future client hook must no-op when AGPAIR_SUPPRESS_CLIENT_HOOKS=1 or AGPAIR_INTERNAL_ROLE is probe/executor/smoke.
Tested: <focused tests>
Tested: <full suite>
Tested: <real smoke summary>
Not-tested: <only real provider limitations, if any>
```

## 12. Required Design Decisions

### 12.1 Why No Default `--bare`

`--bare` skips OAuth/keychain and only reads API key or explicit settings. That conflicts with the project goal: Claude Code worker should reuse the user's existing Claude Code / CC Switch setup. It also strips normal Claude Code capabilities that users expect.

Use `--bare` only as a diagnostic mode, not as the default AGPair worker mode.

### 12.2 Why No Default MCP/Skill Filtering

The user's intent is that an AGPair external worker should be close to directly asking that external CLI. Disabling skills/MCP by default makes the worker less useful and diverges from direct use.

The problem is not "Claude has too many capabilities." The problem is "AGPair's controller hooks are being applied inside AGPair's executor/probe."

### 12.3 Why Neutral CWD For Auth Probe

Auth/provider verification should answer one question:

```text
Can this CLI complete a minimal noninteractive model request with the current provider?
```

It should not load a project's AGPair hooks, ready-for-review gates, or worktree context. Real project execution is verified separately by smoke tasks.

### 12.4 Why Hooks Must No-Op Instead Of Being Removed

Controller hooks are still correct for human-facing Codex and Claude Code sessions. Removing them would regress external-first routing. The boundary is conditional:

```text
normal controller session -> hooks active
AGPair internal process -> hooks no-op
```

### 12.5 Why Failure Types Matter

`executor_auth_required` should mean the user must fix credentials. A timeout caused by hook recursion or provider latency requires a different action. Mislabeling wastes debugging time and makes AGPair look unreliable.

## 13. Final Acceptance Criteria

This plan is complete only when all of the following are true:

- `agpair doctor --fresh` does not classify Claude Code probe timeouts as `executor_auth_required`.
- AGPair client hooks no-op under `AGPAIR_INTERNAL_ROLE=probe|executor|smoke`.
- AGPair executor processes carry `AGPAIR_INTERNAL_ROLE=executor` and `AGPAIR_SUPPRESS_CLIENT_HOOKS=1`.
- Claude live probes carry `AGPAIR_INTERNAL_ROLE=probe`, `AGPAIR_SUPPRESS_CLIENT_HOOKS=1`, `AGPAIR_NONINTERACTIVE=1`, `CI=1`, and use a neutral cwd by default.
- Codex hooks have the same suppression boundary even though Codex worker already passes smoke.
- Claude Code worker can be real-smoked from Codex controller when the current provider is healthy.
- If Claude Code cannot be real-smoked, the blocker is precise and actionable.
- Local Codex and Claude AGPair skills/configs are synced after the repo changes.
- Full test suite passes.
- Real smoke reports are retained locally but not committed.
- No secrets or local raw artifacts are committed.

## 14. Common Pitfalls During Implementation

- Do not set `AGPAIR_SUPPRESS_CLIENT_HOOKS=1` globally in user shells. It is an internal launch marker.
- Do not let `--tools` or other variadic Claude CLI options consume the prompt. If testing those flags, verify argument parsing with `claude --help` and a real command.
- Do not remove `stdin=subprocess.DEVNULL` from probes. Claude `--print` can read inherited stdin when stdout is not a TTY.
- Do not treat `cwd=/tmp` auth success as full worker success. It proves provider availability only; real worker smoke still matters.
- Do not leave `ready_for_review` tasks unaccepted after manual AGPair review smoke. They can correctly block human controller sessions.
- Do not special-case only Claude hooks. Codex hooks need the same boundary to keep the architecture symmetric.
- Do not call the external Codex worker from Codex controller by default, or external Claude Code worker from Claude Code controller by default. Controller suppression remains unchanged.
