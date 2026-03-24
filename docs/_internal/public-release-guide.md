# Publishing agpair v1.0 — Public Release Guide

This guide describes how to publish a clean public v1.0 release of agpair without carrying prior private commit history into the public repository.

## Recommended Strategy: Fresh Public Repo from Clean Snapshot

This approach creates a new public repository with a single initial commit from the current cleaned tree. Your private working repo remains untouched.

### Why this is safer than rewriting history

| Approach | Risk |
|----------|------|
| **Fresh public repo** (recommended) | Zero risk to private repo. Clean public history. Easy to maintain going forward. |
| `git filter-branch` / `git filter-repo` | Rewrites all SHAs. Breaks any existing references, tags, CI links. Risk of accidentally leaving sensitive data in reflogs. |
| Orphan branch in same repo | Cleaner than filter-branch but still shares the same `.git` directory with private history. Accidental push of wrong branch exposes everything. |

### Step-by-step

#### 1. Prepare the private repo

Make sure your working tree is clean and all doc changes are committed:

```bash
cd /path/to/your/private/agpair
git status          # should be clean
```

#### 2. Final privacy audit

Scan for personal paths, usernames, tokens, or secrets:

```bash
# Check public docs for your actual local username/home path plus obvious secret markers
rg -n "$USER|$HOME|PRIVATE|SECRET|TOKEN" \
  README.md README.zh-CN.md docs/*.md companion-extension/

# Check Python source for hardcoded paths
rg -n "$HOME" agpair/ tests/
```

Fix anything found before proceeding.

#### 3. Create the public repo

```bash
# Create a new directory for the public repo
mkdir ../agpair-public
cd ../agpair-public
git init

# Copy the cleaned source tree (exclude .git and local state)
rsync -av --exclude='.git' \
          --exclude='.venv' \
          --exclude='.pytest_cache' \
          --exclude='__pycache__' \
          --exclude='.supervisor' \
          --exclude='.agpair' \
          --exclude='companion-extension/node_modules' \
          --exclude='companion-extension/dist' \
          --exclude='companion-extension/*.vsix' \
          /path/to/your/private/agpair/ .

# Review what will be committed
git add -A
git status
```

#### 4. Make the initial public commit and tag

```bash
git commit -m "feat: initial public release of agpair v1.0

agpair is a CLI-first pairing tool connecting Codex chat to Antigravity
executors. This release includes task dispatch, auto-wait, receipt
ingestion, stuck detection, doctor preflight checks, and continuation
flow (continue/approve/reject/retry)."

git tag -a v1.0 -m "agpair v1.0 — first public release"
```

#### 5. Push to the public remote

```bash
git remote add origin <your-public-repo-url>
git push -u origin main
git push origin v1.0
```

#### 6. Update pyproject.toml version (if not already done)

In the public repo, update the version field:

```toml
[project]
version = "1.0"
```

Commit and push:

```bash
git add pyproject.toml
git commit -m "chore: set version to 1.0"
git push origin main
```

### Ongoing maintenance

After the initial public release, you have two options:

1. **Dual-repo model**: Continue developing in the private repo, periodically snapshot clean changes into the public repo. Simple but manual.

2. **Single public repo going forward**: Switch primary development to the public repo. Archive the private repo. Cleaner long-term, but requires discipline about what gets committed.

Choose based on whether you need to keep private-only features or experimental branches separate.

## Alternative: Orphan Branch (higher risk)

If you prefer to keep everything in one repository:

```bash
cd /path/to/your/private/agpair
git checkout --orphan public-release
git add -A
git commit -m "feat: initial public release of agpair v1.0"
git tag -a v1.0 -m "agpair v1.0 — first public release"
```

Then push only the `public-release` branch to a public remote:

```bash
git remote add public <your-public-repo-url>
git push public public-release:main
git push public v1.0
```

> **Warning**: This keeps private history in the same `.git` directory. An accidental `git push public main` (wrong branch) would expose everything. The fresh-repo approach is strictly safer.

## Pre-release Checklist

- [ ] All public-facing docs scrubbed of personal paths, usernames, tokens
- [ ] `rg -n "$USER|$HOME" README.md README.zh-CN.md docs/*.md companion-extension/` returns no results
- [ ] `agpair doctor` and `agpair --help` work from a clean install
- [ ] All Python tests pass: `python3 -m pytest tests/`
- [ ] Companion extension builds: `cd companion-extension && npm install && npm run build && npm test`
- [ ] `pyproject.toml` version set appropriately
- [ ] MIT license committed
- [ ] README answers: what is it, why use it, how to start
