# Agpair CLI Design

Status: proposed  
Date: 2026-03-21  
Audience: local operator, Codex reviewer, maintainer  
Scope: a new standalone project for Codex ↔ Antigravity pairing

## 1. Goal

Create a **new standalone project** that lets the user keep using the Codex chat window as the main review/control surface while giving Codex a stable way to drive Antigravity execution without turning the tool into a second orchestrator.

The new project should:

- live in a new repository under `~/Projects`
- be independent from `codex-antigravity-supervisor`
- be `CLI-first`
- include a **light daemon** for mechanical background work
- reuse the existing Antigravity companion/extension protocol instead of rebuilding the executor side

The intended user experience is:

- the user talks to Codex in the chat window
- Codex uses the new CLI to send work to Antigravity
- Antigravity executes and returns receipts
- the light daemon handles receipt ingestion, session continuity, and health bookkeeping
- the user participates as little as possible in mechanical coordination, while still keeping review/control authority

## 2. Non-goals

This new project does **not** aim to:

- replace `codex-antigravity-supervisor`
- become a second full `Supervisor`
- own workflow truth for general project orchestration
- rebuild the Antigravity companion extension
- provide Telegram control
- provide MCP as the primary interface
- provide a GUI or menu bar app in v1
- support multi-machine or team-shared deployment in v1
- support same-path multi-window tenancy in v1
- fully automate Codex-side semantic review/approval decisions

This is a focused pairing tool, not a general autonomous coding platform.

## 3. Why A New Project Exists

The current repository has grown into a broader system:

- workflow truth
- bridge-first runtime
- autopilot
- sidecar diagnostics
- roadmap-driven productization layers

That is appropriate for `codex-antigravity-supervisor`, but it is heavier than what is needed for the user's actual preferred workflow.

The user's preferred workflow is simpler:

- keep the Codex chat window as the main place for planning and review
- let Antigravity handle execution
- minimize human involvement in mechanical coordination
- avoid introducing another large orchestration stack

Therefore the new project should extract only the **pair-programming control layer**, not the entire supervisor system.

## 4. Product Positioning

The new project is best understood as:

> A local pairing tool that gives the Codex chat window a stable way to drive Antigravity execution through a reusable CLI and a small background assistant.

It is **not**:

- a no-touch autopilot platform
- a replacement for human review
- a long-lived AI chat controller that stores all project memory in conversation history

## 5. Architecture Choice

We explicitly reject three extremes:

### 5.1 Not “just a few thin scripts”

A purely stateless wrapper is too fragile for long-running tasks because it cannot reliably preserve:

- `task -> session` continuity
- duplicate receipt protection
- retry visibility
- continuation targeting

### 5.2 Not a second full Supervisor

A heavy state machine, automated semantic decisions, or self-owned retry workflow would recreate the complexity of the existing repository and defeat the purpose of a simpler standalone product.

### 5.3 Not a permanent Codex chat controller

We do not want a design that assumes a single long-lived Codex AI chat session is the system of record.

Reasons:

- chat context drifts
- recovery is harder
- state is less inspectable
- the product becomes too coupled to one interaction surface

## 6. Recommended Architecture

The recommended architecture is:

1. **Light persistent core**
2. **CLI as the public interface**
3. **Light daemon for receipt ingestion and health**
4. **Antigravity companion reused as-is**
5. **Codex chat window remains the main brain**

In shorthand:

```text
Codex chat window = planner/reviewer/operator
agpair CLI        = tool surface Codex calls
agpair daemon     = mechanical assistant
Antigravity       = executor
```

## 7. Core Design Principles

### 7.1 Human-led, Codex-assisted

The user and Codex remain responsible for:

- task intent
- review judgment
- scope decisions
- approval/rejection semantics

### 7.2 Mechanical automation only

The daemon automates only mechanical coordination, such as:

- reading receipts
- mapping tasks to executor sessions
- marking tasks as stuck when no progress is observed
- surfacing retry recommendations and health signals

It must not become a second semantic reviewer.

### 7.3 Minimal durable state

The project should persist only the smallest state needed to avoid broken chains and session confusion.

It should not invent a large workflow truth model.

### 7.4 Standalone product boundaries

The project must be independent from `codex-antigravity-supervisor`.

That means:

- separate repository
- separate package name
- separate state directory
- separate SQLite file
- no hidden runtime dependency on supervisor internals

### 7.5 Public contract reuse only

The new project may reuse the **publicly observable transport contract** already proven in the current repository, but must not import or depend on its internal Python modules or databases.

Allowed reuse:

- `agent-bus` message envelope and sender/receiver convention
- Antigravity companion's task/session/receipt behavior
- receipt status vocabulary already in use by the companion

Forbidden reuse:

- `supervisor.db`
- current repository Python packages as runtime dependencies
- `~/.supervisor/*` state directories as the new product's primary state

## 8. Repository And Runtime Separation

The new project must be created in a separate repository under `~/Projects`.

Recommended provisional repository name:

- `agpair`

Recommended local state directory:

- `~/.agpair/`

Recommended local SQLite file:

- `~/.agpair/agpair.db`

This explicitly avoids mixing with:

- `~/.supervisor/`
- `supervisor.db`
- the current repository's runtime directories

## 9. Public Interface Choice

The primary interface should be **CLI-first**.

Reasons:

- local process control is easier to debug through CLI
- SQLite/log/daemon behavior is easier to inspect locally
- the Codex chat window can call CLI commands directly
- the project avoids MCP transport complexity in v1

MCP may be added later as a thin adapter if needed, but it must not be the primary control path in v1.

## 10. Main Components

## 10.1 `agpair core`

Responsibilities:

- task registry
- session registry
- receipt ingestion
- journal persistence
- retry metadata

This is the durable minimum state layer.

It should expose a narrow API to the CLI and daemon.

## 10.2 `agpair cli`

Responsibilities:

- public user/Codex command surface
- task creation
- task inspection
- continuation and review actions
- daemon lifecycle control
- diagnostics

The CLI is the intended interface Codex will call from the chat window.

## 10.3 `agpair daemon`

Responsibilities:

- watch for Antigravity receipts
- update local task/session state
- mark tasks as stuck after timeout
- expose retry recommendation and daemon health
- record journal and operational health

The daemon is explicitly not allowed to:

- send semantic continuation (`continue/approve/reject`) on its own
- create a fresh retry attempt on its own in v1
- make high-level semantic decisions

## 10.4 Antigravity companion reuse

The new project will reuse the existing Antigravity companion/extension **public contract**, not the current repository's internals:

- TASK ingress
- ACK
- EVIDENCE_PACK
- BLOCKED
- COMMITTED
- continuation into the same task session through the same public message/status semantics already understood by the companion
- receipt file / receipt watcher behavior

This avoids dual-end reinvention.

## 11. Minimal Persistent State Model

Each task should persist only the minimum fields needed for reliable continuity.

Recommended task record:

- `task_id`
- `repo_path`
- `phase`
- `antigravity_session_id`
- `attempt_no`
- `retry_count`
- `last_receipt_id`
- `stuck_reason`
- `retry_recommended`
- `last_activity_at`
- `created_at`
- `updated_at`

Recommended journal record:

- `task_id`
- `kind`
- `body`
- `source`
- `created_at`

Recommended phase values:

- `new`
- `acked`
- `evidence_ready`
- `awaiting_codex`
- `committed`
- `blocked`
- `stuck`

This is intentionally smaller than the full supervisor state machine.
It tracks executor continuity and local operator posture, not a full review workflow.

## 12. CLI Commands For MVP

The first version should support the following commands.

### Task commands

- `agpair task start`
- `agpair task status <task_id>`
- `agpair task continue <task_id>`
- `agpair task approve <task_id>`
- `agpair task reject <task_id>`
- `agpair task retry <task_id>`
- `agpair task logs <task_id>`

### Daemon commands

- `agpair daemon start`
- `agpair daemon stop`
- `agpair daemon status`

### Diagnostics

- `agpair doctor`

## 13. CLI Versus Daemon Control Boundary

This boundary is a hard rule for v1.

### CLI owns semantic control messages

Only the CLI may send:

- continue / review feedback
- approve
- reject
- retry

The Codex chat window remains the place where those decisions are made.

### daemon owns passive mechanical bookkeeping

The daemon may only:

- read receipts
- deduplicate receipts
- update task/session mapping
- mark a task as stuck after timeout
- set `retry_recommended=true`
- write journal and health state

The daemon does **not** own a queue of pending human/Codex intentions.

## 14. Transport Contract For V1

The new project should explicitly adopt the current proven public transport semantics:

- `TASK` for initial task dispatch
- `ACK` from Antigravity
- `EVIDENCE_PACK` from Antigravity
- `BLOCKED` from Antigravity
- `COMMITTED` from Antigravity
- `REVIEW` / `REVIEW_DELTA` for Codex-side continuation and rejection-like feedback
- `APPROVED` for Codex-side approval

CLI command mapping:

- `task continue` -> `REVIEW` or `REVIEW_DELTA`
- `task reject` -> `REVIEW` or `REVIEW_DELTA`
- `task approve` -> `APPROVED`
- `task retry` -> new `TASK` on the next attempt number

This avoids inventing a similar-but-different protocol in v1.

## 15. Daemon Automation Contract

The daemon should automatically perform these mechanical actions by default:

- consume new Antigravity receipts
- update `task -> session` mapping
- deduplicate receipts
- detect stuck/timeout tasks
- set retry recommendation for stuck tasks

The daemon must **not**:

- auto-review code changes
- auto-approve or auto-reject semantic work
- auto-send continuation
- auto-create fresh retry attempts in v1
- rewrite user scope
- invent new tasks

## 16. Data Flow

### 14.1 Task start

1. Codex calls `agpair task start`
2. CLI creates a new local task record
3. CLI sends TASK to Antigravity using the compatible task ingress path
4. daemon later sees `ACK`
5. daemon stores `antigravity_session_id`

### 14.2 Execution and evidence

1. Antigravity executes the task
2. Antigravity emits `EVIDENCE_PACK` or `BLOCKED`
3. daemon ingests the receipt
4. task state becomes inspectable through `task status` and `task logs`
5. Codex reads the result and decides whether to continue, approve, reject, or retry

### 14.3 Continuation

1. Codex calls `agpair task continue <task_id>` or `approve/reject`
2. CLI resolves the stored `antigravity_session_id`
3. CLI sends the continuation/approval message using the public transport contract
4. daemon later watches for the next receipt

### 14.4 Recovery

1. daemon observes no progress for too long
2. daemon marks current attempt as `stuck`
3. daemon sets `retry_recommended=true` and records the reason
4. Codex may call `agpair task retry <task_id>`
5. CLI creates the next attempt and starts a fresh session

## 17. Error Handling

The system should explicitly handle:

- duplicate receipts
- stale receipts
- missing session mapping
- continuation to a non-existent session
- stuck executor session
- repeated retry exhaustion
- missing companion availability
- invalid continuation target

Recommended failure posture:

- fail closed
- preserve journal trail
- require explicit Codex action for retry in v1
- require explicit Codex action after retry budget is exhausted

## 18. Why This Will Not Drift Too Much

This design deliberately keeps semantic control in the Codex chat window.

It reduces drift in two ways:

1. it does **not** ask the daemon to make semantic decisions
2. it does persist just enough local truth to avoid broken continuation chains
3. it uses the same public transport semantics already proven with the current companion

So the system avoids both extremes:

- not totally stateless and fragile
- not a heavy autonomous second orchestrator

## 19. Technology Choice

Recommended v1 stack:

- Python
- Typer for CLI
- SQLite for local durable state
- lightweight daemon loop in Python
- pytest for testing

Reasons:

- local process control is straightforward
- existing implementation experience already exists in Python
- state/log/daemon behavior is easy to inspect
- fastest path to a usable standalone product

## 20. Testing Strategy

The MVP should prove:

### Unit level

- task/session persistence
- receipt deduplication
- continuation routing
- retry budget logic

### Integration level

- `task start -> ACK`
- `ACK -> session mapping persisted`
- `EVIDENCE_PACK -> status/logs visible`
- `continue -> same session`
- `stuck -> retry_recommended`
- `task retry -> fresh session`
- `approve/reject -> correct continuation routing`

### Failure tests

- duplicate receipt ignored
- stale receipt ignored
- missing session returns explicit error
- retry exhaustion stops automatic recovery

## 21. What MVP Explicitly Does Not Include

The first version must not include:

- Telegram
- MCP
- GUI / menu bar app
- generalized workflow orchestration
- complex multi-window tenancy
- team-shared server mode
- fully autonomous Codex-side reviewer logic
- daemon-owned retry workflow
- daemon-owned semantic continuation queue

## 22. Rollout Plan

Recommended rollout order:

1. create standalone repository under `~/Projects`
2. build core persistence and CLI shell
3. add daemon and receipt ingestion
4. wire continuation/session mapping
5. add stuck detection and retry recommendation
6. add explicit CLI retry path
7. validate with one real local pairing flow

## 23. Open Questions Resolved In This Spec

This spec resolves the following design decisions:

- standalone repo: yes
- reuse Antigravity companion: yes
- interface style: CLI-first
- MCP in v1: no
- Telegram in v1: no
- daemon in v1: yes
- daemon scope: receipt ingestion, mapping, and health only
- primary reviewer brain: Codex chat window

## 24. Final Definition

The new project should be built as:

> A standalone Python CLI + light daemon that lets the Codex chat window reliably drive Antigravity execution, while keeping semantic review in the chat window and limiting the daemon to receipt ingestion, session continuity, and health bookkeeping.
