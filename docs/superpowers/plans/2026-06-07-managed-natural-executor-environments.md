# Managed-Natural Executor Environments Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make AGPair default to normal, full-capability external-agent execution while retaining explicit restricted/isolated fallback modes for evidence-backed retries and diagnostics.

**Architecture:** AGPair is the task control plane, not the skill/MCP sandbox. The first attempt should run each external CLI close to how the user runs it directly, inheriting that CLI's normal skills, memory, plugins, MCP, and provider config. AGPair still owns task boundaries: repo guardrails, authorization profiles, completion policy, stdout/stderr/artifact capture, terminal receipt parsing, wait/watch, retry, and controller verification.

**Tech Stack:** Python 3.12, Typer, SQLite, existing AGPair executor registry and `LocalCLIExecutor`, terminal receipt parser, task start/retry/status/watch commands, pytest, real executor smoke harness.

---

## 1. Decision Summary

### 1.1 What `managed-natural` Means

`managed-natural` means:

- AGPair launches the external CLI through its adapter.
- AGPair passes the task contract, repo path, completion policy, authorization summary, and noninteractive output settings.
- The external CLI keeps its normal config home, skills, memory, plugins, MCP, provider selection, and user-installed behavior.
- AGPair captures stdout/stderr, report, receipt, evidence, and git status.
- AGPair does not default-disable memory, subagents, web search, skills, plugins, MCP, or local provider config.

It is "managed" because AGPair still controls lifecycle and evidence. It is "natural" because the executor keeps the same capabilities it has when the user runs it directly.

### 1.2 What `skill_policy=inherit` Means

`skill_policy=inherit` means AGPair does not construct a curated skill bundle for the executor by default.

If the user has configured skills for `grok`, `claude`, `agy`, or `codex`, those CLIs may use them normally. AGPair should not second-guess or filter them during the default attempt.

### 1.3 What `mcp_policy=inherit` Means

`mcp_policy=inherit` means AGPair does not disable, rewrite, or replace MCP configuration by default.

If an executor normally sees MCP servers when invoked directly, AGPair's default mode lets it see the same MCP surface. If MCP causes noise, hangs, or broken pipes, that becomes evidence for a restricted retry or explicit diagnostic mode, not a reason to sandbox all first attempts.

### 1.4 What Restricted Or Isolated Modes Mean

Restricted/isolated modes are fallback or diagnostic modes.

They are not default. They are selected only when:

- the controller explicitly asks for them; or
- a previous attempt produced evidence that the natural environment is causing failure; or
- the task itself has a hard constraint such as no network, no memory, no MCP, or reproducible minimal environment.

Examples:

| Situation | First attempt | Fallback / diagnostic attempt |
| --- | --- | --- |
| Grok completes normally | `managed-natural` | none |
| Grok loops through memory/subagents/web | `managed-natural` | `managed-restricted` with `--no-memory --no-subagents --disable-web-search` |
| Antigravity emits useful mixed text + JSON | `managed-natural` | no environment change; fix receipt extraction |
| Claude Code natural mode is silent or MCP-noisy | `managed-natural` | `isolated-bare` |
| Codex worker inherits too much local config | `managed-isolated` by default | no broader inheritance unless explicitly diagnostic |
| Read-only/no-network audit | explicit restricted mode from dispatch | no automatic widening |

## 2. Updated Executor Defaults

### 2.1 `antigravity-cli`

Default:

```text
environment_mode = managed-natural
skill_policy = inherit
mcp_policy = inherit
```

Behavior:

- Keep Antigravity CLI as the first executor for normal implementation work.
- Do not isolate Antigravity by default.
- Do not disable its normal context or tools by default.
- Fix AGPair receipt parsing for mixed prose + JSON output instead of blaming executor environment.

Fallback:

- Usually no environment fallback.
- If Antigravity output is malformed, retry with stronger receipt instructions before changing environment.
- If binary/preflight fails, route to next executor.

### 2.2 `grok-cli`

Default:

```text
environment_mode = managed-natural
skill_policy = inherit
mcp_policy = inherit
```

Default command should keep:

```text
grok --cwd <repo> --output-format json --always-approve --max-turns <N> --single <body>
```

Default command should not include:

```text
--no-memory
--no-subagents
--disable-web-search
```

Restricted fallback command may include those flags only when requested:

```text
grok --cwd <repo> --output-format json --always-approve --max-turns <N> \
  --no-memory --no-subagents --disable-web-search --single <body>
```

Fallback trigger examples:

- `no_progress`
- repeated tool loop
- output never reaches receipt/report
- controller marks result unusable because Grok relied on stale memory or irrelevant subagent output
- explicit `--environment-mode managed-restricted`

### 2.3 `claude-code`

Default for Codex controller:

```text
environment_mode = managed-natural
skill_policy = inherit
mcp_policy = inherit
auth_mode = auto
```

Auth behavior:

- Try valid Claude Code OAuth/subscription first.
- If OAuth live probe fails, use the current Claude provider from CC Switch.
- AGPair should not require a separate Claude API key setup for normal use.
- If neither auth path works, preflight blocks with `executor_auth_required`.

Default command direction:

- Prefer normal noninteractive Claude Code print/json mode with inherited config.
- Inject CC Switch provider env when `auth_mode=ccswitch`.
- Do not default to `--bare` purely because CC Switch is used.

Bare fallback:

- `isolated-bare` is a fallback or diagnostic mode.
- Use it when natural mode is silent, plugin/MCP startup prevents progress, or the user explicitly requests isolated diagnosis.
- If isolated auth for bare mode is missing, preflight blocks rather than launching a silent task.

### 2.4 `codex`

Default:

```text
environment_mode = managed-isolated
skill_policy = isolated
mcp_policy = isolated
```

Reason:

- External Codex worker is mainly useful to Claude Code controllers.
- Codex controller should normally use native Codex subagents instead of AGPair-managed external `codex`.
- Real tests show external Codex worker can work but is heavier and more likely to inherit local skill noise.

Fallback:

- Keep external `codex` suppressed for Codex controllers unless `--allow-self-executor` is explicit.
- Claude Code controller may use external `codex` as its cross-controller fallback.

## 3. When Fallback Modes Are Chosen

### 3.1 First Attempt

First attempt uses the executor profile default:

```text
antigravity-cli -> managed-natural
grok-cli        -> managed-natural
claude-code     -> managed-natural when auth is healthy
codex           -> managed-isolated
```

AGPair should not infer "maybe plugins are noisy" before the executor has actually failed. It should collect evidence first.

### 3.2 Explicit Dispatch Override

Controller may specify environment mode at dispatch:

```bash
agpair task start \
  --repo-path "$REPO" \
  --executor grok-cli \
  --environment-mode managed-restricted \
  --authorization-profile local_readonly \
  --completion-policy report \
  --body "$BRIEF"
```

Use explicit override for:

- no-network work;
- no-memory work;
- minimal reproduction;
- debugging plugin/MCP startup behavior;
- comparing natural vs restricted executor quality;
- tasks where user explicitly forbids external context.

### 3.3 Retry Override

The most important mode switch happens on retry:

```bash
agpair task retry TASK-123 \
  --from-block \
  --environment-mode managed-restricted
```

Retry context must include:

- original brief;
- previous attempt id;
- previous environment mode;
- blocked/stuck reason;
- stdout/stderr path and tail excerpt;
- terminal receipt or malformed receipt excerpt;
- git status and diff;
- new environment mode;
- new authorization profile, if changed.

### 3.4 Automatic Suggested Fallback

AGPair may suggest a fallback mode in status, but should not silently launch a different environment unless the retry command or workflow policy explicitly allows it.

Status should expose:

```json
{
  "environment_mode": "managed-natural",
  "fallback_environment_mode": "managed-restricted",
  "fallback_recommended": true,
  "fallback_reason": "no_progress_with_tool_loop",
  "retry_command": "agpair task retry TASK-123 --from-block --environment-mode managed-restricted"
}
```

## 4. Required Data Model And Status Fields

Add environment metadata to task attempts.

Minimum fields:

```json
{
  "environment_mode": "managed-natural",
  "environment_mode_source": "executor_default",
  "skill_policy": "inherit",
  "mcp_policy": "inherit",
  "fallback_environment_mode": null,
  "fallback_reason": null
}
```

Allowed values:

```text
environment_mode:
  managed-natural
  managed-restricted
  managed-isolated
  isolated-bare
  diagnostic-natural

environment_mode_source:
  executor_default
  task_start_override
  retry_override
  workflow_policy

skill_policy:
  inherit
  restricted
  isolated

mcp_policy:
  inherit
  restricted
  isolated
```

Do not call this `capability_profile`. The point is not to build a capability grant system. The point is to record how naturally or restrictively the external process was launched.

## 5. Files To Modify

### Core policy and profile

- Modify: `agpair/executors/policy.py`
  - Add default environment fields to `ExecutorSpec`.
  - Replace isolation-first Grok metadata with managed-natural defaults.
  - Keep controller suppression unchanged.

- Modify: `agpair/executors/grok_cli.py`
  - Remove restricted flags from default command.
  - Add restricted mode flag path.
  - Read mode from task/adapter context or env fallback.

- Modify: `agpair/executors/claude_code.py`
  - Stop treating `ccswitch` as automatic `--bare`.
  - Add explicit `isolated-bare` mode.
  - Keep auth preflight.

- Modify: `agpair/executors/codex_cli.py` or current Codex adapter file
  - Mark Codex default as managed-isolated.
  - Keep controller suppression unchanged.

- Modify: `agpair/executors/local_cli.py`
  - Pass selected environment mode to command builders.
  - Record environment metadata in state/artifacts.

### CLI and persistence

- Modify: `agpair/cli/task.py`
  - Add `--environment-mode`.
  - Validate values.
  - Store selected mode on task/attempt.

- Modify: retry command in `agpair/cli/task.py`
  - Add `--environment-mode`.
  - Include previous and new environment modes in retry-from-block context.

- Modify task storage models/repositories as needed:
  - Store attempt environment mode.
  - Preserve compatibility when old tasks have no environment metadata.

### Receipt and status

- Modify: `agpair/terminal_receipts.py`
  - Keep current tolerant parsing.
  - Add Antigravity mixed prose + JSON extraction if not already covered.

- Modify status/watch surfaces:
  - `task status --json`
  - `task watch --json`
  - human status where useful

Expose:

```json
{
  "environment_mode": "managed-natural",
  "skill_policy": "inherit",
  "mcp_policy": "inherit",
  "fallback_environment_mode": "managed-restricted",
  "fallback_reason": "no_progress"
}
```

### Docs and skills

- Modify: `README.md`
- Modify: `README.zh-CN.md`
- Modify: `docs/usage.md`
- Modify: `docs/usage.zh-CN.md`
- Modify: `docs/executor-lifecycle.md`
- Modify: `skills/Codex/SKILL.md`
- Modify: `skills/Claude/SKILL.md`

Docs must say current behavior only:

- Default is managed-natural/inherit for Antigravity, Grok, and healthy Claude Code.
- Restricted/isolated modes are fallback or explicit diagnostic.
- External `codex` remains isolated and cross-controller-only by default.
- Do not present skill/MCP filtering as the primary architecture.

## 6. Implementation Tasks

### Task 1: Add Environment Mode Contract

**Files:**

- Modify: `agpair/executors/policy.py`
- Add or modify tests: `tests/unit/test_executor_onboarding.py`

- [ ] Add fields to `ExecutorSpec`:

```python
default_environment_mode: str = "managed-natural"
default_skill_policy: str = "inherit"
default_mcp_policy: str = "inherit"
fallback_environment_modes: tuple[str, ...] = ()
```

- [ ] Add validation constants:

```python
ENVIRONMENT_MODES = {
    "managed-natural",
    "managed-restricted",
    "managed-isolated",
    "isolated-bare",
    "diagnostic-natural",
}
SKILL_POLICIES = {"inherit", "restricted", "isolated"}
MCP_POLICIES = {"inherit", "restricted", "isolated"}
```

- [ ] Update `ExecutorSpec.to_dict()` so profile output includes these fields.

- [ ] Update executor defaults:

```python
"antigravity-cli": default_environment_mode="managed-natural"
"grok-cli": default_environment_mode="managed-natural", fallback_environment_modes=("managed-restricted",)
"claude-code": default_environment_mode="managed-natural", fallback_environment_modes=("isolated-bare",)
"codex": default_environment_mode="managed-isolated", default_skill_policy="isolated", default_mcp_policy="isolated"
```

- [ ] Run:

```bash
PYTHONPATH=. pytest -q tests/unit/test_executor_onboarding.py
```

Expected: onboarding tests prove every executor profile has valid environment mode and policy values.

### Task 2: Make Grok Default Managed-Natural

**Files:**

- Modify: `agpair/executors/grok_cli.py`
- Modify: `tests/unit/test_grok_cli_executor.py`
- Modify: `agpair/executors/policy.py`

- [ ] Write failing test:

```python
def test_grok_cli_default_does_not_disable_memory_subagents_or_web() -> None:
    executor = GrokCLIExecutor(grok_bin="fake-grok")

    cmd = executor._build_grok_cmd(
        "Goal: inspect",
        "/tmp/repo",
        pathlib.Path("/tmp/agpair"),
    )

    assert "--cwd" in cmd
    assert "--output-format" in cmd
    assert "--max-turns" in cmd
    assert "--no-memory" not in cmd
    assert "--no-subagents" not in cmd
    assert "--disable-web-search" not in cmd
```

- [ ] Write restricted mode test:

```python
def test_grok_cli_restricted_mode_disables_memory_subagents_and_web(monkeypatch) -> None:
    monkeypatch.setenv("AGPAIR_GROK_ENVIRONMENT_MODE", "managed-restricted")
    executor = GrokCLIExecutor(grok_bin="fake-grok")

    cmd = executor._build_grok_cmd("Goal: inspect", "/tmp/repo", pathlib.Path("/tmp/agpair"))

    assert "--no-memory" in cmd
    assert "--no-subagents" in cmd
    assert "--disable-web-search" in cmd
```

- [ ] Implement helper:

```python
def _environment_mode() -> str:
    value = os.environ.get("AGPAIR_GROK_ENVIRONMENT_MODE", "managed-natural").strip()
    if value not in {"managed-natural", "managed-restricted"}:
        raise ValueError("Unsupported AGPAIR_GROK_ENVIRONMENT_MODE; use managed-natural or managed-restricted")
    return value
```

- [ ] Add restricted flags only when `_environment_mode() == "managed-restricted"`.

- [ ] Update `grok-cli` profile `noninteractive_flags` to list default flags only, and add a separate profile key:

```python
"restricted_flags": ["--no-memory", "--no-subagents", "--disable-web-search"]
```

- [ ] Run:

```bash
PYTHONPATH=. pytest -q tests/unit/test_grok_cli_executor.py tests/unit/test_executor_onboarding.py
```

### Task 3: Add CLI Environment Mode Override

**Files:**

- Modify: `agpair/cli/task.py`
- Modify storage models/repositories that persist task attempt metadata
- Add tests in existing task start/retry integration test files

- [ ] Add `--environment-mode` to `task start`.

Valid values:

```text
managed-natural
managed-restricted
managed-isolated
isolated-bare
diagnostic-natural
```

- [ ] Add `--environment-mode` to `task retry`.

- [ ] Persist selected environment mode on the attempt.

- [ ] If unset, use executor profile default.

- [ ] If set to a mode not supported by the executor, fail before dispatch:

```text
executor grok-cli does not support environment mode isolated-bare
```

- [ ] Add test:

```python
def test_task_start_stores_environment_mode_override(...):
    # start with --executor grok-cli --environment-mode managed-restricted --no-wait
    # assert stored task/attempt exposes managed-restricted
```

- [ ] Add retry test:

```python
def test_retry_from_block_can_change_environment_mode(...):
    # seed blocked task with managed-natural
    # retry --from-block --environment-mode managed-restricted
    # assert new attempt has managed-restricted and retry body includes previous mode
```

- [ ] Run:

```bash
PYTHONPATH=. pytest -q tests/integration/test_task_start_and_status.py tests/integration/test_task_retry_from_block.py
```

### Task 4: Expose Environment Mode In Status, Watch, Receipt Artifacts

**Files:**

- Modify status/watch serializers.
- Modify `agpair/executors/local_cli.py`.
- Modify tests for status/watch/task artifacts.

- [ ] Add current attempt environment metadata to `state.json`:

```json
{
  "environment_mode": "managed-natural",
  "skill_policy": "inherit",
  "mcp_policy": "inherit",
  "environment_mode_source": "executor_default"
}
```

- [ ] Add same metadata to task status JSON.

- [ ] Add same metadata to watch events when an attempt is created or reaches terminal phase.

- [ ] Add `fallback_environment_mode`, `fallback_reason`, and `retry_command` when an attempt fails with a reason that maps to an environment fallback.

- [ ] Add tests:

```python
def test_status_reports_environment_mode_for_active_attempt(...):
    ...

def test_watch_reports_environment_mode_on_terminal_event(...):
    ...
```

- [ ] Run:

```bash
PYTHONPATH=. pytest -q tests/integration/test_task_start_and_status.py tests/integration/test_task_watch_events.py
```

### Task 5: Fix Antigravity Mixed Text + JSON Receipt Extraction

**Files:**

- Modify: `agpair/terminal_receipts.py`
- Modify or add tests: `tests/unit/test_receipt_validation.py` or `tests/unit/test_local_cli_executor.py`

- [ ] Add failing test for Antigravity-style output:

```python
def test_receipt_parser_extracts_mixed_text_then_json_receipt():
    raw = '''
    I inspected the repository and found no issues.

    Final receipt:
    {
      "schema_version": "1",
      "task_id": "TASK-AGY-MIXED",
      "attempt_no": 1,
      "review_round": 0,
      "status": "ready_for_review",
      "summary": "Review complete",
      "payload": {
        "changed_files": [],
        "validation_not_run": "read-only review",
        "scope_violations": [],
        "raw_log_path": "stdout.log",
        "receipt_path": "receipt.json",
        "report": "No issues found"
      }
    }
    '''

    receipt = parse_structured_terminal_receipt(raw, expected_task_id="TASK-AGY-MIXED")

    assert receipt is not None
    assert receipt.status == "EVIDENCE_PACK"
    assert receipt.payload["report"] == "No issues found"
```

- [ ] Implement balanced JSON object extraction from raw text.

Rules:

- Prefer exact `task_id` match.
- Prefer objects containing `schema_version`, `task_id`, `status`, and `payload`.
- Do not accept arbitrary JSON snippets without required receipt fields.
- Preserve strict payload evidence validation.

- [ ] Run:

```bash
PYTHONPATH=. pytest -q tests/unit/test_receipt_validation.py tests/unit/test_local_cli_executor.py
```

### Task 6: Make Claude Code Natural Mode The Normal Healthy Path

**Files:**

- Modify: `agpair/executors/claude_code.py`
- Modify: `agpair/executors/claude_auth.py`
- Modify: `tests/unit/test_claude_code_executor.py`
- Modify: `tests/unit/test_executor_lifecycle.py`

- [ ] Add tests proving default healthy `auto` uses managed-natural flags, not `--bare`.

Expected default command should include:

```text
claude --output-format json --print <body>
```

Expected default command should not include by default:

```text
--bare
--strict-mcp-config
--mcp-config {"mcpServers":{}}
--disable-slash-commands
--no-chrome
```

- [ ] Keep auth env injection for CC Switch provider.

- [ ] Add explicit `AGPAIR_CLAUDE_CODE_ENVIRONMENT_MODE=isolated-bare` test proving `--bare` is added only in that mode.

- [ ] Keep preflight auth blocker:

```text
executor_auth_required
```

when neither OAuth nor CC Switch provider is usable.

- [ ] Run:

```bash
PYTHONPATH=. pytest -q tests/unit/test_claude_code_executor.py tests/unit/test_executor_lifecycle.py
```

### Task 7: Update Documentation And Skills

**Files:**

- Modify: `README.md`
- Modify: `README.zh-CN.md`
- Modify: `docs/usage.md`
- Modify: `docs/usage.zh-CN.md`
- Modify: `docs/executor-lifecycle.md`
- Modify: `skills/Codex/SKILL.md`
- Modify: `skills/Claude/SKILL.md`

- [ ] Replace isolation-first language with managed-natural language.

- [ ] Document the default matrix:

| Executor | Default mode | Skills/MCP | Fallback mode |
| --- | --- | --- | --- |
| `antigravity-cli` | `managed-natural` | inherit | receipt instruction/parser retry |
| `grok-cli` | `managed-natural` | inherit | `managed-restricted` |
| `claude-code` | `managed-natural` when auth healthy | inherit | `isolated-bare` |
| `codex` | `managed-isolated` | isolated | diagnostic natural only by explicit override |

- [ ] Document when fallback modes are selected:

```text
manual --environment-mode override
retry --from-block --environment-mode ...
workflow policy
status-suggested fallback after no_progress/malformed_receipt/tool_loop/noise
```

- [ ] Sync local skills after repo docs are updated:

```bash
cp skills/Codex/SKILL.md ~/.codex/skills/agpair/SKILL.md
cp skills/Codex/SKILL.md ~/.codex/skills/agpair-codex/SKILL.md
cp skills/Claude/SKILL.md ~/.claude/skills/agpair/SKILL.md
```

- [ ] Verify:

```bash
cmp -s skills/Codex/SKILL.md ~/.codex/skills/agpair/SKILL.md
cmp -s skills/Codex/SKILL.md ~/.codex/skills/agpair-codex/SKILL.md
cmp -s skills/Claude/SKILL.md ~/.claude/skills/agpair/SKILL.md
```

### Task 8: Real Executor Smoke Matrix

**Files:**

- Modify or use: `scripts/smoke_real_executors.py`
- Do not commit raw smoke logs.

- [ ] Run Codex-controller smoke:

```bash
PYTHONPATH=. python scripts/smoke_real_executors.py \
  --repo-path /Users/chenrongwei/Projects/agpair \
  --controller codex \
  --executors antigravity-cli,grok-cli,claude-code
```

Expected:

- `antigravity-cli`: `ready_for_review` or precise blocker; if blocked only for mixed receipt, fix parser.
- `grok-cli`: `ready_for_review` in managed-natural.
- `claude-code`: `ready_for_review` when OAuth or CC Switch is healthy.

- [ ] Run Claude-controller smoke:

```bash
PYTHONPATH=. python scripts/smoke_real_executors.py \
  --repo-path /Users/chenrongwei/Projects/agpair \
  --controller claude-code \
  --executors antigravity-cli,grok-cli,codex
```

Expected:

- `codex` is tested only for Claude Code controller, not as Codex self-worker default.

- [ ] Run Grok comparison:

```bash
AGPAIR_GROK_ENVIRONMENT_MODE=managed-natural \
PYTHONPATH=. python scripts/smoke_real_executors.py \
  --repo-path /Users/chenrongwei/Projects/agpair \
  --controller codex \
  --executors grok-cli

AGPAIR_GROK_ENVIRONMENT_MODE=managed-restricted \
PYTHONPATH=. python scripts/smoke_real_executors.py \
  --repo-path /Users/chenrongwei/Projects/agpair \
  --controller codex \
  --executors grok-cli
```

Expected:

- Both modes are runnable.
- Default docs and profile choose managed-natural unless restricted mode shows clear quality/stability advantage in repeated measurements.

## 7. Acceptance Criteria

The implementation is complete only when all are true:

- Grok default command no longer includes `--no-memory`, `--no-subagents`, or `--disable-web-search`.
- Grok restricted mode still supports those flags through explicit mode selection.
- Antigravity mixed prose + JSON output parses into a valid `EVIDENCE_PACK` when required fields are present.
- Claude Code healthy `auto` path uses inherited/natural config by default and can use CC Switch provider env without a separate AGPair API config.
- Claude Code `isolated-bare` remains available as explicit fallback/diagnostic.
- Status JSON exposes `environment_mode`, `skill_policy`, and `mcp_policy`.
- Retry-from-block can change environment mode and records both old and new mode.
- External `codex` remains suppressed for Codex controllers by default.
- Docs and local skills describe managed-natural defaults and fallback timing clearly.
- Full test suite passes:

```bash
PYTHONPATH=. pytest -q
```

- Real executor smoke is run and summarized without committing raw logs or secrets.

## 8. Privacy And GitHub Safety

Do not commit:

- `~/.agpair/tasks/...`
- raw stdout/stderr logs;
- real API keys;
- CC Switch database contents;
- Claude/Codex/Grok private config;
- real smoke temp directories;
- `/tmp/agpair-env-experiment-*`.

Before commit:

```bash
git diff --check
git diff -U0 | rg -n 'sk-[A-Za-z0-9_-]{20,}|Bearer [A-Za-z0-9._-]+|api[_-]?key\s*[:=]\s*["'\"'\"'][A-Za-z0-9._-]{16,}|ANTHROPIC_AUTH_TOKEN\s*[:=]' || true
git status --short
```

Commit only repo source, docs, and tests.

## 9. Decision Record

Decided:

- Default external executor mode should be natural/inherited for Antigravity, Grok, and healthy Claude Code.
- AGPair controls task boundaries and result verification, not the executor's normal skills/MCP surface.
- Restricted/isolated modes are explicit or evidence-backed retry modes.
- `environment_mode` is a launch-mode record, not a capability grant system.

Rejected:

- Default full isolation for every executor.
- Default skill/MCP filtering.
- Building a capability bundle system before real executor evidence proves it is necessary.
- Treating Grok plugin noise as a reason to disable memory/subagents/web by default.
- Treating Claude Code's earlier auth/silent failure as proof that natural mode is inherently unusable.

Open questions:

- (implementation discovery) Whether Claude Code natural mode with CC Switch env is consistently faster and less noisy than isolated-bare across 3-5 repeated runs.
- (implementation discovery) Whether Antigravity needs additional receipt prompt wording after parser tolerance is fixed.
- (implementation discovery) Whether workflow-level automatic fallback should launch retries automatically or only suggest retry commands. The safer V1 choice is suggestion-only.
