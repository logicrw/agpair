# Contributing to agpair

Thanks for your interest in contributing!

## Getting Started

```bash
git clone https://github.com/logicrw/agpair.git && cd agpair
python3 -m venv .venv && source .venv/bin/activate
python3 -m pip install -e '.[dev]'
```

## Running Tests

```bash
# Python tests
python -m pytest -q

# Companion extension tests
cd companion-extension && npm install && npm test
```

## Submitting Changes

1. Fork the repo and create a feature branch
2. Make your changes with clear commit messages
3. Ensure all tests pass
4. Open a pull request against `main`

## Release Privacy Checklist

Before publishing a PR or release, run:

```bash
git status --short
git diff --check
rg -n "([A]NTHROPIC_API_KEY|[O]PENAI_API_KEY|[X]AI_API_KEY|[A]GPAIR_.*TOKEN|[Aa]uthorization:[[:space:]]+Bearer|sk-[A-Za-z0-9_-]{20,}|xox[baprs]-|gh[pousr]_[A-Za-z0-9]|/(Users|home)/[[:alnum:]_.-]+/|[a]iapi\\.ulucky\\.cn|[s]uper-secret|[c]onfigured-secret)" .
git ls-files --others --exclude-standard
```

Expected: no real API keys, bearer tokens, OAuth tokens, local absolute user paths, private proxy endpoints, raw logs, session transcripts, or generated local config are staged. Test fixture strings are allowed only when clearly fake and covered by tests.

## Code Style

- Python: follow existing conventions in the codebase
- TypeScript: strict mode, no `any` where avoidable

## Reporting Issues

Open an issue on [GitHub](https://github.com/logicrw/agpair/issues) with:
- Steps to reproduce
- Expected vs actual behavior
- OS and Python/Node version

## License

By contributing, you agree that your contributions will be licensed under the MIT License.
