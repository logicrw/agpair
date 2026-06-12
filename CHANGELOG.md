# Changelog

## v3.0.0 (2026-06-12)

### Changed
- Repositioned AGPair as a native-feeling external-agent handoff layer for Codex and Claude Code.
- Made external CLI executors the first lane for non-trivial delegatable work, with native subagents kept as fallback or review lanes.
- Standardized active executors on `antigravity-cli`, `grok-cli`, `claude-code`, and `codex` with controller-aware self-executor suppression.
- Kept executor environments natural by default: AGPair owns task state, evidence, receipts, waiting, and adoption while external CLIs keep their normal skills, MCP, memory, plugins, and provider config.
- Relaxed task intake and completion around useful agent evidence: clear natural briefs are normalized, commit refs are optional unless explicitly required, and controller-facing `agent_result` drives adoption.
- Added explicit isolated-worktree adoption, apply checks, salvage recording, accept/adopt commands, low-noise wait/watch behavior, and runtime executor policy controls.
- Synchronized Codex and Claude Code skills/hooks around external-first delegation without recursive AGPair worker hooks.

## v1.1.0 (2026-04-05)

### Changed
- Removed review/approve/reject/continue flows (direct_commit only)
- Fixed session loss false-positive detection (listCascades)
- Overhauled SKILL.md for autonomous delegation

## v1.0 (2026-03-24)

Initial public release.

### Features

- CLI task lifecycle: `start`, `status`, `logs`, `retry`, `abandon`, `watch`, `wait`
- Background daemon with receipt ingestion, session continuity, and stuck detection
- `doctor` preflight checks (agent-bus, bridge health, desktop conflicts)
- Standalone `task wait` with configurable timeout/interval
- Bundled VS Code companion extension with secure HTTP bridge (auto-generated bearer token)
- Local SQLite-backed state (tasks, receipts, journals)
- macOS launchd auto-start support
- Bilingual documentation (English + Chinese)
- Optional agent skill for automatic CLI integration (works with Codex, Claude Code, etc.)
- CI/CD workflows (test on push, release on tag)
