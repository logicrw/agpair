# Changelog

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
