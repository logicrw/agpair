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
