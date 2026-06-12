# External Executor Lifecycle

AGPair treats every external CLI executor as a registered module. Add, disable,
deprecate, or remove executors through the shared profile contract, not through
task-state-machine branches.

## Profile Contract

Each active executor must define one profile entry with:

- executor id and display name
- binary name, primary env var, and env aliases
- default authorization profile and safety metadata
- supported completion policies and receipt capability
- noninteractive command shape owned by the adapter
- default environment mode, skill policy, and MCP policy
- isolation profile and health probe metadata
- controller suppression rules
- lifecycle status and replacement guidance

The profile is executable documentation, not decorative metadata. If a profile
declares noninteractive flags or auth modes, the adapter command must emit the
matching flags for the default mode, and unit tests must prove that parity for
every active executor.

For code-writing work, the lifecycle contract includes adoption, not just
launch. An active mutating executor must be able to run in an isolated worktree,
produce a worker diff, pass `agpair task apply TASK --check` against the
controller repository, and leave final acceptance to the controller.

Default modes keep executors natural. Failed attempts should recover by retrying
the same natural executor, switching to another external executor, or handing
back to the controller's native subagents:

- `antigravity-cli`: `managed-natural`, skills/MCP inherit.
- `grok-cli`: `managed-natural`, skills/MCP inherit.
- `claude-code`: `managed-natural`, skills/MCP inherit when auth is healthy.
- `codex`: `managed-natural`, skills/MCP inherit.

Do not introduce a new capability-bundle system just to add an executor. AGPair
records launch mode and owns evidence capture; the external CLI owns its normal
skills, MCP, provider config, and model behavior in `managed-natural` mode.

## Runtime Policy Overlay

`ExecutorSpec` is the compiled-in registry. User and controller preferences live
in one runtime overlay at `~/.agpair/executors.json`; do not duplicate executor
definitions there.

The overlay may contain:

- global disabled executors;
- controller-specific disabled executors;
- controller-specific priority order.

The resolver combines the static registry and overlay in this order:

1. normalize executor ids and reject removed aliases such as Gemini;
2. apply lifecycle and static enabled state;
3. apply controller self-suppression;
4. apply global and controller disabled entries;
5. apply controller priority order;
6. optionally apply binary/auth launch availability checks.

If a task directly requests an executor and that executor is disabled,
suppressed, inactive, or unavailable, dispatch fails before launch with a
machine-readable blocker. Direct requests must never silently fall through to a
different executor. If no executor is requested, AGPair selects the first
eligible executor after applying the policy.

Use these commands to manage the overlay:

```bash
agpair policy list --controller codex
agpair policy disable claude-code --controller codex
agpair policy enable claude-code --controller codex
agpair policy priority --controller codex antigravity-cli grok-cli claude-code
agpair policy reset --controller codex
```

Use `--global` with `disable`, `enable`, or `reset` only when every controller
should share the same change.

`agpair doctor --fresh` reports the same resolved controller policies as
`agpair policy list`, so a controller can explain why an executor was selected,
skipped, suppressed, or disabled without reading the overlay by hand.

If an executor changes its authentication source, declare the required
OAuth login, auth environment, or settings source in the profile. Health checks
and dispatch preflight must then report `executor_auth_required` before launch
instead of letting the process sit silently until a no-progress timeout.

Current active executor ids are `antigravity-cli`, `grok-cli`, `claude-code`,
and `codex`. `codex` means an external Codex CLI worker; it is different from
Codex native subagents. `claude-code` means an external Claude Code worker; it
is different from Claude Code native subagents.

## Onboarding

1. Add one adapter file or thin `LocalCLIExecutor` subclass.
2. Add one `ExecutorSpec` profile entry.
3. Add command, binary/env, environment mode, isolation, safety, routing, health, and lifecycle
   tests.
4. Make `doctor --fresh` report the executor through the same schema as all
   others.
5. Add fake smoke coverage that requires no private credentials.
6. Add real smoke eligibility for the controller matrix.
7. Update retained docs and skills with current behavior only.

Do not add provider-specific branches to task start, retry, wait/watch,
completion arbitration, artifact capture, workflow scheduling, or doctor
formatting. If a provider needs custom flags, environment cleanup, receipt
instructions, or output parsing, keep that behavior inside the adapter or the
shared profile fields consumed by the adapter.

Minimum verification for a new active executor:

- unit tests for registry/profile fields and routing eligibility;
- unit tests for command construction with a fake binary;
- unit tests proving declared profile isolation and noninteractive flags match
  the adapter's default command;
- unit tests proving default `managed-natural` commands and health probes do
  not disable skills, memory, subagents, web, plugins, or MCP;
- unit tests proving isolated auth requirements produce a pre-dispatch blocker
  when the required env/settings source is absent;
- doctor test proving binary and launch probes surface through the common schema;
- task dispatch test proving completion policies are honored;
- smoke harness entry proving the executor can produce an adoptable result in a
  disposable worktree;
- implementation smoke proving `task diff` is available and `task apply --check`
  succeeds without mutating the real repo;
- docs/skills update naming the executor only in the shared routing order.

## Offboarding

Use profile lifecycle status:

- `active`: eligible for new tasks when healthy.
- `disabled`: intentionally unavailable for new tasks.
- `deprecated`: readable for old tasks, refused for default new dispatch.
- `removed`: no new dispatch; historical tasks and receipts remain readable.

Offboarding should require the profile status/replacement update plus tests and
docs. It must not require deleting historical task rows, receipts, or logs.

Temporary offboarding is normally a policy overlay change, not a code change:

```bash
agpair policy disable claude-code --controller codex
```

Permanent offboarding changes the profile lifecycle status. Historical tasks
must stay inspectable either way.

Offboarding checklist:

1. Change the profile lifecycle status and replacement guidance.
2. Update routing tests so default selection skips the executor.
3. Keep historical status/log/receipt inspection readable.
4. Update doctor output expectations.
5. Update docs and skills so the executor is not advertised for new work.
6. Run fake smoke and, when the binary is still installed, diagnostic smoke to
   prove the refusal reason is precise.

## Routing

Controller suppression belongs in the profile:

- Codex controller normally uses `antigravity-cli`, `grok-cli`, and
  `claude-code`; it suppresses external `codex`.
- Claude Code controller normally uses `antigravity-cli`, `grok-cli`, and
  external `codex`; it suppresses external `claude-code`.
- Diagnostic mode may include self executors only when explicitly allowed.

Direct selection of a disabled, deprecated, removed, or locally unavailable
executor must fail before dispatch with a precise reason. It must never silently
fall through to another executor.

## Smoke Matrix

Normal controller smoke covers the executors each controller should actually
use:

- Codex controller: `antigravity-cli`, `grok-cli`, `claude-code`.
- Claude Code controller: `antigravity-cli`, `grok-cli`, `codex`.

Diagnostic all-registered smoke may include self executors with an explicit
allow-self flag. It should report every active executor as `adoptable_result`,
`skipped`, or a precise blocker such as `executor_unavailable`,
`executor_quota_exhausted`, `no_progress_budget_exceeded`, or
`report_output_missing`.

Smoke reports are local evidence. Do not commit raw smoke reports, executor
logs, receipts, or worktrees.
