---
name: agpair
description: "Hand off coding tasks to Antigravity — Google's agent-first IDE that autonomously edits, verifies, and commits code. Covers: delegate/outsource/assign work to Antigravity, check progress, review results, follow-up actions (continue, approve, reject, retry), or when user names Antigravity or implies 'let something else write this'. Also invoke PROACTIVELY for mechanical multi-file edits, boilerplate generation, schema migrations, or bulk refactors that can be precisely specified — dispatch immediately with transparency, no approval needed."
---

# agpair

## Overview

Use this skill when your AI coding agent is the reviewer/controller and Antigravity is the executor.

`agpair` is the control surface for:

- preflight health checks
- task dispatch
- terminal-phase polling
- evidence review (exceptional path — rare when using Rule 5)
- semantic follow-up (`continue`, `approve`, `reject`, `retry`)

It is not a second orchestrator and it is not the semantic decision-maker.

## Triggering

This skill is intended to trigger when the user asks their AI agent to:

- send or delegate work to Antigravity
- use `agpair`
- inspect `doctor`, `daemon`, `task status`, or `task logs`
- approve, reject, continue, or retry a delegated task

For the strongest activation, the user can explicitly say `use agpair` or `send this to Antigravity via agpair`.

**Proactive trigger (agent-initiated, no user prompt needed):** When the agent assesses that the current task is mechanical, well-specified, and requires consistent edits across multiple files — dispatch directly without asking. Announce the task body to the user immediately before running the dispatch command.

**Do NOT dispatch proactively when:**
- The task requires interactive decisions or user input mid-way
- The change is trivial (1 file, 1–2 lines)
- The task description is ambiguous and needs exploration before it can be specified
- The agent hasn't read enough of the codebase to write a precise task body

## Workflow

### 1. Preflight first

Before any semantic action, check:

- `agpair doctor --repo-path <absolute-repo-path>`
- `agpair daemon status`

Do not continue if the target repo is unhealthy:

- `desktop_reader_conflict=true`
- `repo_bridge_session_ready=false`

### 2. Dispatch with `--no-wait` and poll

**Always dispatch with `--no-wait`:**

```bash
agpair task start --repo-path <path> --body "<task body>" --no-wait
```

This prints the TASK_ID and returns immediately.

#### Standard task body template

Every task body sent to Antigravity **must** begin with the following execution rules block. Adapt the language to match the project's working language (e.g. translate to Chinese if the codebase and team use Chinese), but keep all five rules intact:

```
## Execution Rules (highest priority)
1. Wrap every shell command with timeout: timeout 15 <command>
2. Syntax checks only — never import project modules or start services:
   Python:  timeout 10 python3 -m py_compile <file>
   Node.js: timeout 10 node --check <file>
   Go:      timeout 10 go vet ./...
   Other:   skip syntax checks entirely — do not improvise
3. Do not run integration tests or start services
4. If timeout fires (exit code 124), skip that step and continue — do not retry
5. After all work is done, git commit directly — no external approval needed
```

**Why this matters:**
- Rules 1–4 prevent shell commands from hanging indefinitely. `python3 -c "from app.xxx import ..."` can silently block when a module opens a database connection at import time. Language-specific syntax checkers are pure static analysis — no side effects, no IO.
- Rule 5 makes Antigravity commit directly, so the task reaches `committed` without going through `evidence_ready`. This eliminates the risk of approve not being consumed after the session dies.

**Task body shell escaping:** If the task body contains backticks or special characters, write it to a temp file and pass via `$(cat /tmp/task.txt)`:

```bash
cat > /tmp/task.txt << 'EOF'
## Execution Rules (highest priority)
...
EOF
agpair task start --repo-path <path> --no-wait --body "$(cat /tmp/task.txt)"
```

**Then poll in a loop** until a terminal phase is reached:

```bash
agpair task status <TASK_ID>
```

Poll every **exactly 60 seconds**. Use a fixed interval — do NOT increase it over time (no exponential backoff). Antigravity tasks typically take 5–15 minutes for medium tasks, longer for large ones.

**How to poll without blocking:** Each poll is a single Bash call. After getting a non-terminal result, wait 60 seconds and issue another poll call. Repeat until a terminal phase appears — do not stop early.

Two patterns work:
- `sleep 60 && agpair task status <TASK_ID>` — blocks for 60 s then prints status; issue another call after reading the output
- `agpair task status <TASK_ID>` with `run_in_background: true` — returns immediately; you are notified when it completes; issue the next poll after being notified

Never use `--wait` (see below). Terminal phases are:

| Phase | Meaning | Action |
|-------|---------|--------|
| `evidence_ready` | Antigravity produced an evidence pack | Review it (see §3) |
| `committed` | Work committed successfully | Verify with `git log --oneline -3`, announce completion to user |
| `blocked` | Antigravity could not proceed | Run `agpair task retry <TASK_ID> --no-wait` and resume polling |
| `stuck` | Daemon watchdog flagged the task as stale | Wait for auto-recovery; if it transitions to `blocked`, retry |
| `abandoned` | Task was abandoned | Start a fresh task with `agpair task start --no-wait` if work still needed |

**While polling:**

- `acked` means accepted, NOT completed — keep polling patiently
- **Never stop polling before a terminal phase.** Even if the task looks stuck, keep polling every 60 seconds.
- After **10 minutes** of `liveness_state: silent` (no heartbeat, no workspace activity): check logs with `agpair task logs <TASK_ID> --limit 5` (quick sanity check — not a full evidence review), briefly inform the user of the situation, then **continue polling**. Do NOT stop and wait for user input. Antigravity may still be loading context, planning, or executing early steps.
- If `retry_recommended=true` appears in task status (set by daemon after ~15 min), run `agpair task retry <TASK_ID> --no-wait` and resume polling the new attempt.
- Report phase transitions to the user as they happen

**Why not `--wait`?** The built-in `--wait` blocks for up to 60 minutes, but AI agent Bash tools typically have a 2-minute timeout. The command gets killed and the waiter becomes orphaned. Polling keeps the agent in control.

### 3. Review evidence when `evidence_ready` (fallback path)

**This phase should rarely occur** when using the standard task body template with Rule 5 (direct commit). If you see `evidence_ready`, it means the task was dispatched without Rule 5, or Rule 5 was ignored.

When phase becomes `evidence_ready`:

1. **Read the evidence pack**: `agpair task logs <TASK_ID> --limit 50` (use `--limit 50` here — this is a full evidence review, not a quick sanity check)
2. **Check what was changed**: look for diff stat, key files, test results in the logs
3. **Spot-check the code**: read 2-3 key files from the working tree to verify quality
4. **Decide**:
   - `approve` — evidence is solid, tests pass, code looks good
   - `reject` — output is not acceptable and needs to be redone in the same session (use when the current output should be discarded or substantially revised)
   - `continue` — output is acceptable as far as it goes, but additional work is needed in the same session (e.g. add tests, implement a missing piece)

Do not choose a semantic action until you have read both status and logs.

### 4. Guard against premature intervention

If `agpair task active-waits` shows the task, or `task status` shows `waiter_state=waiting`:

- do not send another semantic action on the same task
- do not abandon/retry the task
- only use `agpair task abandon --force <TASK_ID>` if the waiter is clearly orphaned (e.g. approve was sent several minutes ago and the session is confirmed dead)

### 5. Pick one semantic action

Choose exactly one:

- `continue` — current output is **acceptable** but **incomplete**: Antigravity should keep going in the same session (e.g. add tests, implement a missing piece)
- `approve` — output is solid and ready to finalize; triggers commit
- `reject` — current output is **not acceptable** and needs to be substantially redone or discarded in the same session
- `retry` — session is stale or has too much accumulated context; start fresh with a clean session

All semantic commands also default to `--wait`. **Use `--no-wait` and poll** for these too:

```bash
agpair task continue <TASK_ID> --body "<feedback>" --no-wait
agpair task approve  <TASK_ID> --body "<message>"  --no-wait
agpair task reject   <TASK_ID> --body "<feedback>" --no-wait
agpair task retry    <TASK_ID> --no-wait
```

**What to write in `--body`:**

- `approve`: summarize what you reviewed, suggest commit message
- `reject`: be specific — which file, which issue, what to fix. Antigravity reads this in the same session and continues working.
- `continue`: describe the additional work needed (e.g. "add tests for X", "also implement Y")

Do NOT send empty `--body`. Antigravity uses the feedback to guide its next actions.

Then resume polling `task status` until the next terminal phase.

## Required gates

Before claiming completion:

- health was checked
- current task status was checked
- latest logs were checked (or `git log --oneline -3` if phase reached `committed`)
- polling continued until a terminal phase was observed
- no same-task semantic action was sent while an active waiter existed

## Recovery: stuck session

Antigravity has **built-in auto-recovery**: the `DelegationReceiptWatcher` detects stale tasks (no receipt after timeout), automatically creates a new background session, and retries up to 2 times before falling back to BLOCKED.

### When a task is stuck during execution (no evidence produced)

1. **Wait for auto-recovery.** The companion extension will detect the stale task and attempt session recovery automatically.
2. If `phase` changes to `blocked` after auto-recovery exhaustion, use `agpair task retry <TASK_ID> --no-wait` to start a fresh attempt.

### When approve/continue is not consumed (evidence_ready but session dead)

This happens when Antigravity produced evidence but the session died before consuming semantic actions. Auto-recovery does NOT help here — the session that should process the approve is gone.

**Prevention (preferred):** Use the standard task body template with Rule 5 ("commit directly — no external approval needed"). When Antigravity commits on its own, the phase goes directly to `committed` — `evidence_ready` is never reached and this failure mode cannot occur.

**Recovery when it does happen:**

1. **Check if work was already committed** — `git log --oneline -3`. If Antigravity committed before the session died, no action needed.
2. **If not committed, commit locally** — the code is already in the working tree:
   ```bash
   git add <files>
   git commit -m "..."
   ```
3. **Abandon the stuck task:**
   ```bash
   agpair task abandon <TASK_ID>
   ```
4. **Proceed to the next planned task** — dispatch it with `agpair task start --no-wait`. (This is the next task in your work sequence, not a retry of the current one.) The companion extension will automatically terminate the old session and clear the lock — no manual window reload needed.

### Last resort: manual window reload

Only if `agpair doctor` shows `repo_bridge_session_ready=false` AND dispatching a new task fails, ask the user to reload the Antigravity desktop window.

## Multi-task: sequencing and auto-clear

**Normal flow:** After sending `approve`, poll until `committed` before dispatching the next task. The approve triggers Antigravity to commit, and the session needs time to finish. Do NOT dispatch the next task immediately after approve — the old session is still alive and working.

```
approve → poll 60s → committed → dispatch next task
```

**Stuck session auto-clear:** Since companion extension `949111b`, dispatching a new task when the old session is truly dead/stuck will automatically terminate the old session and clear the lock. This only applies when:
- The old task's session has died (approve not consumed for several minutes)
- You committed locally and need to move on

Do NOT rely on auto-clear as a shortcut to skip waiting for `committed`.

## Reducing Antigravity errors

Antigravity occasionally fails with "error unknown" when its AI backend is overloaded or the context is too large. These practices reduce failure rate:

- **Keep tasks small.** Each task should touch 2–5 files. If a plan has 10+ files, split it into multiple sequential tasks. Smaller context = fewer API errors.
- **Leave a gap between tasks.** After `committed`, wait 5–10 seconds before dispatching the next task. Don't rapid-fire tasks back-to-back.
- **Prefer retry over long continue chains.** If a task has gone through 3+ rounds of `continue`/`reject`, the conversation context is bloated. Use `agpair task retry` to start fresh with a clean session instead of another `continue`.

## Anti-patterns

- Do not use `--wait` (default) — always pass `--no-wait` and poll instead.
- Do not treat `ACK` as proof of progress.
- Do not stop polling before a terminal phase is reached.
- Do not jump straight to `continue` without checking current task status and logs first.
- Do not hide `desktop_reader_conflict` or `repo_bridge_session_ready=false`.
- Do not invent commands or transport paths outside the real `agpair` CLI.
- Do not keep sending `approve`/`continue` to a dead session — abandon and reload instead.
- Do not ask Antigravity to run integration tests, start services, or import project modules (e.g. `python3 -c "from app.xxx import ..."`) — these can block indefinitely. For syntax checks use only the language-specific static checkers in Rule 2 (`py_compile`, `node --check`, `go vet`); for other languages skip syntax checks entirely.
- Do not send task body with unescaped backticks in shell — write body to a temp file and use `$(cat /tmp/task.txt)` instead.
- Do not omit the standard execution rules block from task body — always prepend it to prevent hangs and ensure direct commit.
- Do not dispatch proactively when the task requires interactive decisions, is trivial (1 file, 1–2 lines), or cannot yet be fully specified without further exploration.
