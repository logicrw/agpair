# First-Principles Adoption Architecture V2.9 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor AGPair result adoption into a minimal three-layer architecture that preserves useful external-agent work, blocks unsafe work, and gives the controller one clear next action for both report-only and code-editing tasks.

**Architecture:** Split the current mixed adoption logic into Artifact, Adoption, and Action layers. The Artifact layer classifies every durable output (`report`, `stdout_salvage`, `diff`, `patch_or_commit`, `evidence`, `blocker`, `nothing_useful`) and assigns artifact-local verdicts only. The Adoption layer combines artifacts with task policy to produce the compatibility result (`adoptable_result`) and controller-facing `agent_result`; the Action layer maps that to `recovery_decision` without re-parsing artifacts or protocol details.

**Tech Stack:** Python 3.12, Typer, SQLite task records, AGPair terminal receipts, local CLI executors, isolated worktrees, workflow fanout evidence, pytest, ruff, real executor smoke harness, Codex / Claude Code AGPair skills.

---

## 0. Product Contract

This plan follows the first-principles rule:

```text
AGPair exists to turn unreliable external-agent labor into safe controller leverage.
```

AGPair should not optimize for perfect protocol compliance. It should optimize for:

- preserving useful external work;
- making every artifact inspectable;
- separating usable output from unsafe output;
- minimizing controller guesswork;
- keeping the controller responsible for final verification.

### 0.1 Current Problem

The current architecture has the right concepts, but the boundaries are still blurred:

- `agpair/adoption.py` classifies evidence, applies hard gates, maps to `adoptable_result`, and creates `agent_result` in one function.
- `agpair/cli/task.py` has `_no_useful_signal_agent_result`, which can still classify a task-level state without first performing the same artifact salvage logic used by terminal paths.
- `agpair/recovery.py` chooses next actions from `agent_result`, but it does not receive structured artifact evidence.
- Report-only and implementation paths share useful-result-first intent, but the code still mostly computes a single task-level decision rather than artifact-local verdicts.

The product symptom is:

```text
A run can contain useful stdout/report material while the compatibility result says no or retry.
```

The V2.9 target removes this by making artifact classification the source of truth.

### 0.2 Final Model

V2.9 has three layers:

```text
Artifact Layer
  What did the external agent leave behind?

Adoption Layer
  Which artifacts are safe/useful enough for the controller to use?

Action Layer
  What should the controller do next?
```

The layers must remain one-way:

```text
receipt/stdout/diff/evidence
  -> artifact_result
  -> adoption_result + agent_result
  -> recovery_decision
```

No lower layer may read a higher-layer decision.

### 0.3 Artifact Kinds

AGPair must classify these artifact kinds:

```text
report
stdout_salvage
diff
patch_or_commit
evidence
blocker
nothing_useful
```

Each artifact gets its own verdict:

```text
usable
needs_review
blocked
absent
```

The whole task gets a summarized `agent_result.state`:

```text
usable        At least one policy-satisfying artifact is usable, no global hard blocker.
needs_review  At least one safe useful artifact exists, but controller inspection or rework is required.
blocked       No safe useful artifact exists, or a global hard blocker prevents reuse.
```

### 0.4 Global Hard Gates vs Artifact-Local Gates

V2.9 must distinguish global blockers from artifact-local blockers.

A `BLOCKED` terminal receipt is not automatically a global hard blocker. The receipt's `blocker_type` must be classified. For example, `authorization_violation` blocks the whole attempt, while `executor_waiting_for_input` blocks completion but may still leave a safe report/stdout artifact that the controller can salvage.

Global hard blockers block the entire attempt:

```text
executor_unavailable
executor_auth_required
executor_auth_failed
approval_required
authorization_violation
authorization_profile_insufficient
secret_or_token_exposure_detected
uninspectable_artifacts
process_crash_with_no_usable_artifact
```

`secret_or_token_exposure_detected` is only valid if an existing scanner or explicit upstream signal already detected it. V2.9 must not claim to detect secrets unless this plan adds and verifies that detector.

Artifact-local blockers block only the affected artifact:

```text
report_missing
thought_only_output
stdout_salvage_not_completed_report
diff_missing
patch_missing
apply_check_failed
forbidden_changes
undeclared_changes
missing_declared_changes
validation_missing
scope_violations
commit_missing
```

Examples:

```text
apply_check_failed + useful report
  -> code artifact blocked
  -> report artifact needs_review or usable
  -> whole task needs_review/use_result or inspect_evidence

authorization_violation + useful report
  -> global blocked
  -> whole task blocked/inspect_evidence

implementation task returns only a useful report
  -> implementation artifact absent
  -> report artifact needs_review
  -> whole task needs_review/use_result
```

### 0.5 Compatibility Contract

All JSON changes must be additive.

Keep these existing fields:

```text
adoption_result
agent_result
protocol_result
controller_action
recommended_action
recovery_decision
artifact_paths
stdout_path
stderr_path
receipt_path
report_path
evidence_path
```

Add one new field:

```text
artifact_result
```

The new field must appear in:

- `task status --json`;
- `task wait --json`;
- `task watch --json` terminal events;
- workflow lane cards;
- smoke reports.

Backward compatibility mapping:

```text
agent_result.state=usable        -> adoptable_result=yes
agent_result.state=needs_review  -> adoptable_result=partial
agent_result.state=blocked       -> adoptable_result=no
```

`adoptable_result=no` must mean no safe useful artifact remains, not merely imperfect protocol output.

### 0.6 Public JSON Shape

Use this exact shape for `artifact_result`:

```json
{
  "artifact_result": {
    "state": "needs_review",
    "primary_artifact": "report",
    "artifacts": [
      {
        "kind": "report",
        "state": "usable",
        "summary": "Receipt payload contains a completed report.",
        "paths": {"report": ".../report.md", "receipt": ".../receipt.json"},
        "hard_blockers": [],
        "soft_warnings": ["wrapped_text_json"]
      },
      {
        "kind": "diff",
        "state": "blocked",
        "summary": "Patch exists but apply-check failed.",
        "paths": {"diff": ".../diff.patch"},
        "hard_blockers": ["apply_check_failed"],
        "soft_warnings": []
      }
    ],
    "global_hard_blockers": [],
    "soft_warnings": ["wrapped_text_json"]
  }
}
```

`artifact_result` must not contain `controller_action`. Controller action belongs to `agent_result` and `recovery_decision`; otherwise the Artifact layer becomes another action planner and the three-layer model collapses.

### 0.7 Policy Bridge

The Artifact layer is policy-neutral. It answers only:

```text
What safe or unsafe artifacts exist?
```

The Adoption layer is policy-aware. It answers:

```text
Given this completion policy, how useful is the best safe artifact?
```

Policy mapping:

| Effective policy | `adoptable_result=yes` | `adoptable_result=partial` | `adoptable_result=no` |
| --- | --- | --- | --- |
| `report` | A `report` artifact is usable and no global hard blocker exists. | A `stdout_salvage` report exists, or a report exists with artifact-local warnings, or a non-report safe artifact exists but the requested report is missing. | No safe report/stdout artifact exists and no other safe useful artifact exists; or a global hard blocker exists. |
| `evidence` | A safe implementation artifact exists (`diff` or verified `patch_or_commit`) with scope/apply evidence and validation or explicit validation-not-run evidence. | Any safe useful artifact exists but policy-satisfying implementation evidence is incomplete, missing validation, report-only, stdout-salvaged, or requires controller rework. | No safe useful artifact exists; or a global hard blocker exists. |
| `commit` | A commit artifact is verified present and scope-safe. | A safe diff/report/stdout artifact exists, or a commit is declared but not verified. | No safe useful artifact exists; or a global hard blocker exists. |

Important consequence:

```text
apply_check_failed + useful report + evidence policy
  -> diff artifact blocked
  -> report artifact usable or needs_review
  -> adoption_result.partial
  -> agent_result.needs_review/use_result
```

This is the core salvage-first behavior. A blocked code artifact must not erase a safe report artifact.

## 1. File Responsibility Map

Create:

- `agpair/artifact_classification.py`
  - Defines artifact kinds, artifact verdicts, artifact models, and `classify_artifacts(...)`.
  - This is the only module allowed to inspect raw report/stdout/diff path presence for adoption purposes.
- `tests/unit/test_artifact_classification.py`
  - Unit tests for artifact classification and artifact-local blocker behavior.

Do not create or repurpose `agpair/artifacts.py`. That file already exists and owns durable artifact I/O (`copy_artifact`, `write_json`, `read_excerpt`, metadata, hashing). V2.9 classification is adoption-domain logic and must live in a separate module.

Modify:

- `agpair/adoption.py`
  - Keep public `derive_adoption_decision(...)`.
  - Internally consume `ArtifactResult` instead of recomputing raw booleans.
  - Keep `AdoptionEvidence` for compatibility, but derive it from artifacts.
- `agpair/agent_result.py`
  - Keep existing public literals.
  - Add helpers for building `AgentResult` from `ArtifactResult`.
- `agpair/recovery.py`
  - Keep `choose_recovery_decision(...)`.
  - Extend `RecoveryInput` with optional `artifact_result`.
  - Prefer `agent_result.controller_action`, then use artifact global blockers and primary artifact context for recovery routing.
- `agpair/terminal_arbitration.py`
  - Keep `completed_report_text(...)`.
  - Export a small `stdout_salvage_report(...)` helper if needed by `agpair/artifact_classification.py`.
- `agpair/task_terminal.py`
  - Store `artifact_result` in evidence/adoption payloads when attempts become terminal.
- `agpair/executors/local_cli.py`
  - Stop making local ad-hoc salvage decisions that bypass artifact classification.
  - Continue producing receipts compatible with existing terminal flow.
- `agpair/cli/task.py`
  - Surface `artifact_result` in status/watch/wait payloads.
  - Replace `_no_useful_signal_agent_result(...)` with adoption-aware classification.
  - Make manual `task adopt --from-report` update `artifact_result` and `agent_result`, not only compatibility `adoptable_result`.
- `agpair/cli/wait.py`
  - Preserve and relay `artifact_result` when reporting wait output.
- `agpair/watch.py`
  - Include `artifact_result` in watched state changes.
- `agpair/workflows/evidence.py`
  - Include `artifact_result` in lane evidence cards.
- `agpair/workflows/synthesis.py`
  - Score lane usefulness from artifact verdicts, not only task-level state.
- `scripts/smoke_real_executors.py`
  - Emit artifact-level metrics.
- `README.md`
- `README.zh-CN.md`
- `docs/usage.md`
- `docs/usage.zh-CN.md`
- `docs/workflows.md`
- `docs/workflows.zh-CN.md`
- `skills/Codex/SKILL.md`
- `skills/Claude/SKILL.md`

Do not modify historical plan files except this V2.9 plan.

### 1.1 Implementation Staging

V2.9 must ship in two stages.

Core stage:

- Task 1: artifact classification model;
- Task 2: adoption policy bridge;
- Task 3: terminal persistence;
- Task 4: status fallback;
- Task 5: recovery routing.

Core stage success means:

```text
report/stdout salvage works end-to-end;
manual task adopt --from-report updates artifact_result and agent_result;
blocked code artifacts do not erase safe reports;
global hard blockers still block;
task status shows artifact_result + agent_result + recovery_decision.
```

Polish stage:

- Task 6: workflow/watch/fanout propagation;
- Task 7: local CLI duplicate-policy cleanup;
- Task 8: smoke metrics;
- Task 9: docs and skills;
- Task 10: full verification.

Do not begin the polish stage until the core stage passes focused unit and integration tests. This prevents doc churn and workflow changes from hiding mistakes in the adoption core.

## 2. Task 1: Add Artifact Classification Model

**Files:**

- Create: `agpair/artifact_classification.py`
- Create: `tests/unit/test_artifact_classification.py`

- [ ] **Step 1: Write artifact classification tests**

Create `tests/unit/test_artifact_classification.py`:

```python
from pathlib import Path

from agpair.artifact_classification import classify_artifacts


def test_report_receipt_is_usable(tmp_path: Path) -> None:
    report = tmp_path / "report.md"
    report.write_text("Recommendation: use this report", encoding="utf-8")

    result = classify_artifacts(
        receipt={"status": "EVIDENCE_PACK", "payload": {"report": "Recommendation: use this report"}},
        report_path=str(report),
        stdout_path=None,
        receipt_path=None,
        git_status_summary=None,
        scope_validation={"ok": True},
        protocol_warnings=("wrapped_text_json",),
        protocol_errors=(),
    )

    assert result.state == "usable"
    assert result.primary_artifact == "report"
    assert result.global_hard_blockers == ()
    report_artifact = result.by_kind("report")
    assert report_artifact is not None
    assert report_artifact.state == "usable"
    assert report_artifact.soft_warnings == ("wrapped_text_json",)


def test_stdout_completed_report_is_salvage(tmp_path: Path) -> None:
    stdout = tmp_path / "stdout.log"
    stdout.write_text(
        "Findings:\n- The report is useful.\n- The receipt is missing.\nConclusion: salvage it.",
        encoding="utf-8",
    )

    result = classify_artifacts(
        receipt=None,
        report_path=None,
        stdout_path=str(stdout),
        receipt_path=None,
        git_status_summary=None,
        scope_validation={"ok": True},
        protocol_warnings=(),
        protocol_errors=(),
    )

    assert result.state == "needs_review"
    assert result.primary_artifact == "stdout_salvage"
    artifact = result.by_kind("stdout_salvage")
    assert artifact is not None
    assert artifact.state == "needs_review"
    assert artifact.soft_warnings == ("terminal_receipt_missing",)


def test_thought_only_stdout_is_not_salvage(tmp_path: Path) -> None:
    stdout = tmp_path / "stdout.log"
    stdout.write_text("I need to inspect the repository before deciding.", encoding="utf-8")

    result = classify_artifacts(
        receipt=None,
        report_path=None,
        stdout_path=str(stdout),
        receipt_path=None,
        git_status_summary=None,
        scope_validation={"ok": True},
        protocol_warnings=(),
        protocol_errors=(),
    )

    assert result.state == "blocked"
    assert result.primary_artifact == "nothing_useful"
    assert "thought_only_output" in result.global_hard_blockers


def test_protocol_errors_do_not_block_useful_stdout_salvage(tmp_path: Path) -> None:
    stdout = tmp_path / "stdout.log"
    stdout.write_text(
        "Findings:\n- The report is useful despite malformed receipt.\nConclusion: salvage it.",
        encoding="utf-8",
    )

    result = classify_artifacts(
        receipt=None,
        report_path=None,
        stdout_path=str(stdout),
        receipt_path=None,
        git_status_summary=None,
        scope_validation={"ok": True},
        protocol_warnings=(),
        protocol_errors=("mixed_text_json_parse_failed",),
    )

    assert result.state == "needs_review"
    assert result.primary_artifact == "stdout_salvage"
    assert result.global_hard_blockers == ()
    assert "mixed_text_json_parse_failed" in result.soft_warnings


def test_apply_check_failure_blocks_diff_but_not_report(tmp_path: Path) -> None:
    report = tmp_path / "report.md"
    report.write_text("Recommendation: inspect the failed patch manually.", encoding="utf-8")

    result = classify_artifacts(
        receipt={
            "status": "EVIDENCE_PACK",
            "payload": {
                "report": "Recommendation: inspect the failed patch manually.",
                "worktree_diff": {
                    "has_patch": True,
                    "changed_files": ["agpair/example.py"],
                    "apply_check_ok": False,
                    "apply_check_reason": "apply_check_failed",
                },
            },
        },
        report_path=str(report),
        stdout_path=None,
        receipt_path=None,
        git_status_summary=" M agpair/example.py",
        scope_validation={"ok": True},
        protocol_warnings=(),
        protocol_errors=(),
    )

    assert result.state == "needs_review"
    assert result.primary_artifact == "report"
    diff_artifact = result.by_kind("diff")
    assert diff_artifact is not None
    assert diff_artifact.state == "blocked"
    assert diff_artifact.hard_blockers == ("apply_check_failed",)
    report_artifact = result.by_kind("report")
    assert report_artifact is not None
    assert report_artifact.state == "usable"


def test_changed_files_without_worktree_diff_still_creates_diff_artifact() -> None:
    result = classify_artifacts(
        receipt={
            "status": "EVIDENCE_PACK",
            "payload": {
                "changed_files": ["agpair/example.py"],
                "validation_not_run": "worker reported changed files without patch metadata",
            },
        },
        report_path=None,
        stdout_path=None,
        receipt_path=None,
        git_status_summary=None,
        scope_validation={"ok": True},
        protocol_warnings=(),
        protocol_errors=(),
    )

    assert result.state == "needs_review"
    assert result.primary_artifact == "diff"
    diff_artifact = result.by_kind("diff")
    assert diff_artifact is not None
    assert diff_artifact.state == "needs_review"
    assert diff_artifact.soft_warnings == ("apply_check_missing",)


def test_declared_commit_ref_is_needs_review_until_verified() -> None:
    result = classify_artifacts(
        receipt={"status": "EVIDENCE_PACK", "payload": {"commit_ref": "abc123"}},
        report_path=None,
        stdout_path=None,
        receipt_path=None,
        git_status_summary=None,
        scope_validation={"ok": True},
        protocol_warnings=(),
        protocol_errors=(),
    )

    assert result.state == "needs_review"
    assert result.primary_artifact == "patch_or_commit"
    commit_artifact = result.by_kind("patch_or_commit")
    assert commit_artifact is not None
    assert commit_artifact.state == "needs_review"
    assert commit_artifact.soft_warnings == ("commit_ref_unverified",)


def test_authorization_violation_blocks_all_artifacts(tmp_path: Path) -> None:
    report = tmp_path / "report.md"
    report.write_text("Recommendation: useful text exists.", encoding="utf-8")

    result = classify_artifacts(
        receipt={
            "status": "BLOCKED",
            "payload": {
                "blocker_type": "authorization_violation",
                "report": "Recommendation: useful text exists.",
            },
        },
        report_path=str(report),
        stdout_path=None,
        receipt_path=None,
        git_status_summary=None,
        scope_validation={"ok": False},
        protocol_warnings=(),
        protocol_errors=(),
    )

    assert result.state == "blocked"
    assert result.global_hard_blockers == ("authorization_violation",)
```

- [ ] **Step 2: Run the new tests and verify they fail for missing module**

Run:

```bash
uv run pytest -q tests/unit/test_artifact_classification.py
```

Expected:

```text
ModuleNotFoundError: No module named 'agpair.artifacts'
```

- [ ] **Step 3: Create `agpair/artifact_classification.py`**

Create `agpair/artifact_classification.py` with this implementation skeleton:

```python
from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal, Mapping

from agpair.terminal_arbitration import completed_report_text

ArtifactKind = Literal[
    "report",
    "stdout_salvage",
    "diff",
    "patch_or_commit",
    "evidence",
    "blocker",
    "nothing_useful",
]
ArtifactState = Literal["usable", "needs_review", "blocked", "absent"]

GLOBAL_HARD_BLOCKERS = frozenset(
    {
        "executor_unavailable",
        "executor_auth_required",
        "executor_auth_failed",
        "approval_required",
        "authorization_violation",
        "authorization_profile_insufficient",
        "secret_or_token_exposure_detected",
        "uninspectable_artifacts",
        "process_crash_with_no_usable_artifact",
    }
)


@dataclass(frozen=True, slots=True)
class Artifact:
    kind: ArtifactKind
    state: ArtifactState
    summary: str
    paths: dict[str, str]
    hard_blockers: tuple[str, ...] = ()
    soft_warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["hard_blockers"] = list(self.hard_blockers)
        payload["soft_warnings"] = list(self.soft_warnings)
        return payload


@dataclass(frozen=True, slots=True)
class ArtifactResult:
    state: Literal["usable", "needs_review", "blocked"]
    primary_artifact: ArtifactKind
    artifacts: tuple[Artifact, ...]
    global_hard_blockers: tuple[str, ...] = ()
    soft_warnings: tuple[str, ...] = ()

    def by_kind(self, kind: ArtifactKind) -> Artifact | None:
        return next((artifact for artifact in self.artifacts if artifact.kind == kind), None)

    def to_dict(self) -> dict[str, object]:
        return {
            "state": self.state,
            "primary_artifact": self.primary_artifact,
            "artifacts": [artifact.to_dict() for artifact in self.artifacts],
            "global_hard_blockers": list(self.global_hard_blockers),
            "soft_warnings": list(self.soft_warnings),
        }


def classify_artifacts(
    *,
    receipt: Mapping[str, Any] | None,
    report_path: str | None,
    stdout_path: str | None,
    receipt_path: str | None,
    git_status_summary: str | None,
    scope_validation: Mapping[str, Any] | None,
    protocol_warnings: tuple[str, ...],
    protocol_errors: tuple[str, ...],
) -> ArtifactResult:
    payload = receipt.get("payload") if isinstance(receipt, Mapping) else None
    if not isinstance(payload, Mapping):
        payload = {}

    warnings = _unique((*tuple(protocol_warnings), *tuple(protocol_errors)))
    global_blockers = _global_blockers(receipt=receipt, payload=payload)
    artifacts = _artifact_tuple(
        payload=payload,
        report_path=report_path,
        stdout_path=stdout_path,
        receipt_path=receipt_path,
        git_status_summary=git_status_summary,
        scope_validation=scope_validation,
        warnings=warnings,
    )

    if global_blockers:
        primary = _best_artifact(artifacts) or _nothing_useful_artifact("Global hard blocker prevents adoption.")
        return ArtifactResult(
            state="blocked",
            primary_artifact=primary.kind,
            artifacts=artifacts or (primary,),
            global_hard_blockers=global_blockers,
            soft_warnings=warnings,
        )

    primary = _best_artifact(artifacts)
    if primary is None:
        stdout_text = _read_text(stdout_path)
        blocker = "thought_only_output" if stdout_text else "no_useful_artifact"
        nothing = _nothing_useful_artifact("No safe useful report, stdout salvage, diff, patch, commit, or evidence artifact exists.")
        return ArtifactResult(
            state="blocked",
            primary_artifact="nothing_useful",
            artifacts=(nothing,),
            global_hard_blockers=(blocker,),
            soft_warnings=warnings,
        )

    state = "usable" if primary.state == "usable" and not _has_non_usable_artifact(artifacts) else "needs_review"
    return ArtifactResult(
        state=state,
        primary_artifact=primary.kind,
        artifacts=artifacts,
        global_hard_blockers=(),
        soft_warnings=warnings,
    )


def _artifact_tuple(
    *,
    payload: Mapping[str, Any],
    report_path: str | None,
    stdout_path: str | None,
    receipt_path: str | None,
    git_status_summary: str | None,
    scope_validation: Mapping[str, Any] | None,
    warnings: tuple[str, ...],
) -> tuple[Artifact, ...]:
    artifacts: list[Artifact] = []
    report = _string_value(payload.get("report"))
    if report or _path_has_content(report_path):
        artifacts.append(
            Artifact(
                kind="report",
                state="usable",
                summary="Receipt payload or report path contains a completed report.",
                paths=_paths(report=report_path, receipt=receipt_path),
                soft_warnings=warnings,
            )
        )

    stdout_candidate = _string_value(payload.get("stdout_report_candidate"))
    stdout_report = completed_report_text(stdout_candidate or _read_text(stdout_path))
    if stdout_report and not report:
        artifacts.append(
            Artifact(
                kind="stdout_salvage",
                state="needs_review",
                summary="Stdout contains a completed report but terminal receipt/report artifact is missing.",
                paths=_paths(stdout=stdout_path),
                soft_warnings=_unique(("terminal_receipt_missing", *warnings)),
            )
        )

    worktree_diff = payload.get("worktree_diff")
    if isinstance(worktree_diff, Mapping):
        diff_artifact = _diff_artifact(worktree_diff, git_status_summary=git_status_summary, scope_validation=scope_validation)
        if diff_artifact is not None:
            artifacts.append(diff_artifact)
    elif (git_status_summary or "").strip() or _changed_files(payload.get("changed_files")):
        artifacts.append(
            Artifact(
                kind="diff",
                state="needs_review",
                summary="Git status indicates changed files, but apply-check evidence is absent.",
                paths={},
                soft_warnings=("apply_check_missing",),
            )
        )

    if _string_value(payload.get("commit_ref")) or _string_value(payload.get("commit")) or _string_value(payload.get("commit_sha")):
        artifacts.append(
            Artifact(
                kind="patch_or_commit",
                state="needs_review",
                summary="Receipt declares a commit artifact, but the ref must be verified before it is usable.",
                paths={},
                soft_warnings=("commit_ref_unverified",),
            )
        )

    if payload.get("validation") or payload.get("validation_not_run"):
        artifacts.append(
            Artifact(
                kind="evidence",
                state="needs_review" if payload.get("validation_not_run") else "usable",
                summary="Receipt contains validation evidence or an explicit validation-not-run reason.",
                paths={},
                soft_warnings=("validation_not_run",) if payload.get("validation_not_run") else (),
            )
        )
    return tuple(artifacts)


def _diff_artifact(
    worktree_diff: Mapping[str, Any],
    *,
    git_status_summary: str | None,
    scope_validation: Mapping[str, Any] | None,
) -> Artifact | None:
    has_patch = bool(worktree_diff.get("has_patch")) or bool((git_status_summary or "").strip())
    changed_files = _changed_files(worktree_diff.get("changed_files"))
    if not has_patch and not changed_files:
        return None
    blockers: list[str] = []
    warnings: list[str] = []
    if worktree_diff.get("apply_check_ok") is False:
        blockers.append(_string_value(worktree_diff.get("apply_check_reason")) or "apply_check_failed")
    elif "apply_check_ok" not in worktree_diff:
        warnings.append("apply_check_missing")
    if isinstance(scope_validation, Mapping) and scope_validation.get("ok") is False:
        if scope_validation.get("forbidden_changed_files"):
            blockers.append("forbidden_changes")
        if scope_validation.get("undeclared_changed_files"):
            blockers.append("undeclared_changes")
        if scope_validation.get("missing_declared_files"):
            blockers.append("missing_declared_changes")
        if not blockers:
            blockers.append("scope_violations")
    state: ArtifactState = "blocked" if blockers else "usable" if worktree_diff.get("apply_check_ok") is True else "needs_review"
    return Artifact(
        kind="diff",
        state=state,
        summary="Worktree diff artifact was classified from receipt patch metadata.",
        paths={},
        hard_blockers=tuple(blockers),
        soft_warnings=tuple(warnings),
    )


def _global_blockers(
    *,
    receipt: Mapping[str, Any] | None,
    payload: Mapping[str, Any],
) -> tuple[str, ...]:
    blockers: list[str] = []
    blocker_type = _string_value(payload.get("blocker_type"))
    receipt_status = _string_value(receipt.get("status") if isinstance(receipt, Mapping) else None)
    if blocker_type in GLOBAL_HARD_BLOCKERS:
        blockers.append(blocker_type)
    if receipt_status and receipt_status.upper() == "BLOCKED" and blocker_type in GLOBAL_HARD_BLOCKERS:
        blockers.append(blocker_type)
    return _unique(blockers)


def _best_artifact(artifacts: tuple[Artifact, ...]) -> Artifact | None:
    priority = {
        "diff": 0,
        "patch_or_commit": 1,
        "report": 2,
        "stdout_salvage": 3,
        "evidence": 4,
        "blocker": 5,
        "nothing_useful": 6,
    }
    candidates = [artifact for artifact in artifacts if artifact.state in {"usable", "needs_review"}]
    if not candidates:
        return None
    return sorted(candidates, key=lambda item: (0 if item.state == "usable" else 1, priority[item.kind]))[0]


def _has_non_usable_artifact(artifacts: tuple[Artifact, ...]) -> bool:
    return any(artifact.state in {"needs_review", "blocked"} for artifact in artifacts)


def _nothing_useful_artifact(summary: str) -> Artifact:
    return Artifact(kind="nothing_useful", state="blocked", summary=summary, paths={})


def _paths(**values: str | None) -> dict[str, str]:
    return {key: value for key, value in values.items() if value}


def _path_has_content(path: str | None) -> bool:
    if not path:
        return False
    try:
        value = Path(path)
        return value.exists() and value.is_file() and value.stat().st_size > 0
    except OSError:
        return False


def _read_text(path: str | None) -> str | None:
    if not path:
        return None
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _string_value(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _changed_files(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        stripped = value.strip()
        return (stripped,) if stripped else ()
    if isinstance(value, list):
        return tuple(str(item) for item in value if isinstance(item, str) and item.strip())
    return ()


def _unique(values: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))
```

- [ ] **Step 4: Run artifact tests**

Run:

```bash
uv run pytest -q tests/unit/test_artifact_classification.py
```

Expected:

```text
8 passed
```

- [ ] **Step 5: Run formatting and lint for new files**

Run:

```bash
uv run ruff check agpair/artifact_classification.py tests/unit/test_artifact_classification.py
```

Expected:

```text
All checks passed!
```

- [ ] **Step 6: Commit**

Run:

```bash
git add agpair/artifact_classification.py tests/unit/test_artifact_classification.py
git commit -m "feat: add artifact classification model"
```

## 3. Task 2: Refactor Adoption To Consume Artifacts

**Files:**

- Modify: `agpair/adoption.py`
- Modify: `tests/unit/test_adoption_result.py`

- [ ] **Step 1: Add adoption tests for artifact-local salvage**

Append these tests to `tests/unit/test_adoption_result.py`:

```python
def test_evidence_policy_report_without_diff_is_partial_use_result(tmp_path: Path) -> None:
    report = tmp_path / "report.md"
    report.write_text("Recommendation: use these findings before editing.", encoding="utf-8")
    policy = resolve_effective_task_policy(
        requested_completion_policy="evidence",
        authorization_profile="local_mutating",
    )

    decision = derive_adoption_decision(
        effective_policy=policy,
        receipt={"status": "EVIDENCE_PACK", "payload": {"report": "Recommendation: use these findings before editing."}},
        report_path=str(report),
        scope_validation={"ok": True},
        controller_rework="minor",
    )

    assert decision.adoptable_result == "partial"
    assert decision.agent_result is not None
    assert decision.agent_result.state == "needs_review"
    assert decision.agent_result.controller_action == "use_result"
    assert decision.to_dict()["artifact_result"]["primary_artifact"] == "report"


def test_apply_check_failure_with_report_salvages_report_not_code(tmp_path: Path) -> None:
    report = tmp_path / "report.md"
    report.write_text("Recommendation: inspect the failed implementation direction.", encoding="utf-8")
    policy = resolve_effective_task_policy(
        requested_completion_policy="evidence",
        authorization_profile="local_mutating",
    )

    decision = derive_adoption_decision(
        effective_policy=policy,
        receipt={
            "status": "EVIDENCE_PACK",
            "payload": {
                "report": "Recommendation: inspect the failed implementation direction.",
                "worktree_diff": {
                    "has_patch": True,
                    "changed_files": ["agpair/example.py"],
                    "apply_check_ok": False,
                    "apply_check_reason": "apply_check_failed",
                },
            },
        },
        report_path=str(report),
        git_status_summary=" M agpair/example.py",
        scope_validation={"ok": True},
    )

    assert decision.adoptable_result == "partial"
    assert decision.agent_result is not None
    assert decision.agent_result.state == "needs_review"
    assert decision.agent_result.controller_action == "use_result"
    artifact_result = decision.to_dict()["artifact_result"]
    assert artifact_result["state"] == "needs_review"
    assert artifact_result["primary_artifact"] == "report"
    diff = next(item for item in artifact_result["artifacts"] if item["kind"] == "diff")
    assert diff["state"] == "blocked"
    assert diff["hard_blockers"] == ["apply_check_failed"]


def test_reviewable_diff_with_report_prefers_review_then_apply(tmp_path: Path) -> None:
    report = tmp_path / "report.md"
    report.write_text("Recommendation: review the included patch direction.", encoding="utf-8")
    policy = resolve_effective_task_policy(
        requested_completion_policy="evidence",
        authorization_profile="local_mutating",
    )

    decision = derive_adoption_decision(
        effective_policy=policy,
        receipt={
            "status": "EVIDENCE_PACK",
            "payload": {
                "report": "Recommendation: review the included patch direction.",
                "worktree_diff": {
                    "has_patch": True,
                    "changed_files": ["agpair/example.py"],
                    "apply_check_ok": True,
                    "apply_check_reason": None,
                },
                "validation_not_run": "worker did not run tests",
            },
        },
        report_path=str(report),
        git_status_summary=" M agpair/example.py",
        scope_validation={"ok": True},
    )

    assert decision.adoptable_result == "partial"
    assert decision.agent_result is not None
    assert decision.agent_result.state == "needs_review"
    assert decision.agent_result.controller_action == "review_then_apply"


def test_global_authorization_violation_stays_blocked_even_with_report(tmp_path: Path) -> None:
    report = tmp_path / "report.md"
    report.write_text("Recommendation: useful text exists.", encoding="utf-8")
    policy = resolve_effective_task_policy(
        requested_completion_policy="report",
        authorization_profile="local_readonly",
    )

    decision = derive_adoption_decision(
        effective_policy=policy,
        receipt={
            "status": "BLOCKED",
            "payload": {
                "blocker_type": "authorization_violation",
                "report": "Recommendation: useful text exists.",
            },
        },
        report_path=str(report),
        scope_validation={"ok": False},
    )

    assert decision.adoptable_result == "no"
    assert decision.agent_result is not None
    assert decision.agent_result.state == "blocked"
    assert decision.agent_result.controller_action == "inspect_evidence"
    assert decision.to_dict()["artifact_result"]["global_hard_blockers"] == ["authorization_violation"]
```

- [ ] **Step 2: Run the focused tests and verify failures**

Run:

```bash
uv run pytest -q tests/unit/test_adoption_result.py
```

Expected:

```text
FAILED tests/unit/test_adoption_result.py::test_evidence_policy_report_without_diff_is_partial_use_result
FAILED tests/unit/test_adoption_result.py::test_apply_check_failure_with_report_salvages_report_not_code
FAILED tests/unit/test_adoption_result.py::test_reviewable_diff_with_report_prefers_review_then_apply
FAILED tests/unit/test_adoption_result.py::test_global_authorization_violation_stays_blocked_even_with_report
```

- [ ] **Step 3: Extend `AdoptionDecision`**

Modify `agpair/adoption.py`:

```python
from agpair.agent_result import AgentResult, ControllerAction, unique
from agpair.artifact_classification import ArtifactResult, classify_artifacts
```

Update `AdoptionDecision`:

```python
@dataclass(frozen=True)
class AdoptionDecision:
    adoptable_result: AdoptableResult
    blockers: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    evidence: AdoptionEvidence = AdoptionEvidence()
    controller_rework: ControllerRework = "unknown"
    agent_result: AgentResult | None = None
    artifact_result: ArtifactResult | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "adoptable_result": self.adoptable_result,
            "blockers": list(self.blockers),
            "warnings": list(self.warnings),
            "evidence": self.evidence.to_dict(),
            "controller_rework": self.controller_rework,
        }
        if self.agent_result is not None:
            payload["agent_result"] = self.agent_result.to_dict()
        if self.artifact_result is not None:
            payload["artifact_result"] = self.artifact_result.to_dict()
        return payload
```

- [ ] **Step 4: Add artifact-to-agent mapping helper**

Add this helper in `agpair/adoption.py`:

```python
def _agent_result_from_artifacts(
    *,
    artifact_result: ArtifactResult,
    blockers: tuple[str, ...],
    warnings: tuple[str, ...],
    policy: str,
) -> AgentResult:
    action = _controller_action_from_artifacts(artifact_result, policy=policy)
    if artifact_result.state == "blocked":
        return AgentResult(
            state="blocked",
            controller_action=action,
            summary="No safe useful external artifact remains for automatic adoption.",
            hard_blockers=unique((*artifact_result.global_hard_blockers, *blockers)),
            soft_warnings=unique((*artifact_result.soft_warnings, *warnings)),
        )
    if artifact_result.state == "needs_review":
        return AgentResult(
            state="needs_review",
            controller_action=action,
            summary="External executor produced useful artifact evidence that needs controller review.",
            hard_blockers=unique(blockers),
            soft_warnings=unique((*artifact_result.soft_warnings, *warnings)),
        )
    return AgentResult(
        state="usable",
        controller_action=action,
        summary="External executor produced usable artifact evidence with normal controller verification.",
        hard_blockers=(),
        soft_warnings=unique((*artifact_result.soft_warnings, *warnings)),
    )


def _controller_action_from_artifacts(artifact_result: ArtifactResult, *, policy: str) -> ControllerAction:
    if artifact_result.state == "blocked":
        return "inspect_evidence" if artifact_result.global_hard_blockers else "retry_or_switch_executor"
    has_reviewable_code = any(
        item.kind in {"diff", "patch_or_commit"} and item.state in {"usable", "needs_review"}
        for item in artifact_result.artifacts
    )
    if policy in {"evidence", "commit"} and has_reviewable_code:
        return "review_then_apply"
    if artifact_result.primary_artifact in {"report", "stdout_salvage"}:
        return "use_result"
    if artifact_result.primary_artifact in {"diff", "patch_or_commit"}:
        return "review_then_apply"
    return "inspect_evidence"
```

- [ ] **Step 5: Make `derive_adoption_decision(...)` call `classify_artifacts(...)` once**

At the start of `derive_adoption_decision(...)`, after payload normalization, create:

```python
artifact_result = classify_artifacts(
    receipt=receipt,
    report_path=report_path,
    stdout_path=stdout_path,
    receipt_path=receipt_path,
    git_status_summary=git_status_summary,
    scope_validation=scope_validation,
    protocol_warnings=protocol_warnings,
    protocol_errors=protocol_errors,
)
```

Then derive compatibility booleans from artifacts:

```python
has_report = any(item.kind in {"report", "stdout_salvage"} and item.state in {"usable", "needs_review"} for item in artifact_result.artifacts)
has_diff = any(item.kind == "diff" and item.state in {"usable", "needs_review"} for item in artifact_result.artifacts)
has_commit = any(item.kind == "patch_or_commit" and item.state in {"usable", "needs_review"} for item in artifact_result.artifacts)
```

Keep `AdoptionEvidence` fields for compatibility. Stop letting `apply_check_failed` force `adoptable_result=no` when a usable report artifact exists.

- [ ] **Step 6: Implement the policy bridge**

Replace the old policy branches in `derive_adoption_decision(...)` with rules from §0.7.

Implementation requirements:

```python
if artifact_result.global_hard_blockers:
    return _make_decision("no", artifact_result.global_hard_blockers, warnings, evidence, controller_rework, policy, artifact_result)

if policy == "report":
    if has_report:
        result = "yes" if artifact_result.state == "usable" and artifact_result.primary_artifact == "report" else "partial"
        return _make_decision(result, tuple(blockers), warnings, evidence, controller_rework, policy, artifact_result)
    if artifact_result.state in {"usable", "needs_review"}:
        blockers.append("report_missing")
        return _make_decision("partial", tuple(blockers), warnings, evidence, controller_rework, policy, artifact_result)
    blockers.append("report_missing")
    return _make_decision("no", tuple(blockers), warnings, evidence, controller_rework, policy, artifact_result)

if policy == "commit":
    if has_commit and artifact_result.state == "usable":
        return _make_decision("yes", tuple(blockers), warnings, evidence, controller_rework, policy, artifact_result)
    if artifact_result.state in {"usable", "needs_review"}:
        blockers.append("commit_missing_or_unverified")
        return _make_decision("partial", tuple(blockers), warnings, evidence, controller_rework, policy, artifact_result)
    blockers.append("commit_and_diff_missing")
    return _make_decision("no", tuple(blockers), warnings, evidence, controller_rework, policy, artifact_result)

if policy == "evidence":
    if has_diff and artifact_result.state == "usable" and evidence.has_validation:
        return _make_decision("yes", tuple(blockers), warnings, evidence, controller_rework, policy, artifact_result)
    if artifact_result.state in {"usable", "needs_review"}:
        return _make_decision("partial", tuple(blockers), warnings, evidence, controller_rework, policy, artifact_result)
    blockers.append("evidence_missing")
    return _make_decision("no", tuple(blockers), warnings, evidence, controller_rework, policy, artifact_result)
```

This block is illustrative, not copy-paste final code. The final implementation must keep existing compatibility fields and all old tests that still match the new policy table. It must explicitly update tests whose old expectations conflict with salvage-first behavior, such as `apply_check_failed + useful report`.

- [ ] **Step 7: Replace `_make_decision(...)` calls with artifact-aware decisions**

Change `_make_decision(...)` to accept `artifact_result`:

```python
def _make_decision(
    adoptable_result: AdoptableResult,
    blockers: tuple[str, ...],
    warnings: tuple[str, ...],
    evidence: AdoptionEvidence,
    controller_rework: ControllerRework,
    policy: str,
    artifact_result: ArtifactResult | None,
) -> AdoptionDecision:
    normalized_blockers = unique(blockers)
    normalized_warnings = unique(warnings)
    agent_result = (
        _agent_result_from_artifacts(
            artifact_result=artifact_result,
            blockers=normalized_blockers,
            warnings=normalized_warnings,
            policy=policy,
        )
        if artifact_result is not None
        else _agent_result_for_decision(
            adoptable_result=adoptable_result,
            blockers=normalized_blockers,
            warnings=normalized_warnings,
            evidence=evidence,
            controller_rework=controller_rework,
            policy=policy,
        )
    )
    return AdoptionDecision(
        adoptable_result,
        normalized_blockers,
        normalized_warnings,
        evidence,
        controller_rework,
        agent_result,
        artifact_result,
    )
```

- [ ] **Step 8: Run focused adoption tests**

Run:

```bash
uv run pytest -q tests/unit/test_artifact_classification.py tests/unit/test_adoption_result.py
```

Expected:

```text
all tests passed
```

- [ ] **Step 9: Commit**

Run:

```bash
git add agpair/adoption.py tests/unit/test_adoption_result.py
git commit -m "refactor: derive adoption from artifact results"
```

## 4. Task 3: Make Terminal Paths Persist `artifact_result`

**Files:**

- Modify: `agpair/task_terminal.py`
- Modify: `tests/unit/test_task_terminal_report.py`
- Modify: `tests/unit/test_terminal_arbitration.py`

- [ ] **Step 1: Add terminal persistence test**

Add a test to `tests/unit/test_task_terminal_report.py` that finalizes a report-only attempt with a useful stdout report and asserts the stored evidence contains `artifact_result`.

Use this assertion block:

```python
assert evidence["adoption_result"]["artifact_result"]["primary_artifact"] in {"report", "stdout_salvage"}
assert evidence["adoption_result"]["agent_result"]["controller_action"] == "use_result"
assert evidence["adoption_result"]["adoptable_result"] in {"yes", "partial"}
```

- [ ] **Step 2: Run focused terminal tests**

Run:

```bash
uv run pytest -q tests/unit/test_task_terminal_report.py tests/unit/test_terminal_arbitration.py
```

Expected:

```text
FAILED tests/unit/test_task_terminal_report.py::<new_test_name>
```

- [ ] **Step 3: Store artifact result from adoption payload**

In `agpair/task_terminal.py`, after `derive_adoption_decision(...)` returns, ensure `adoption.to_dict()` is the single source for serialized adoption evidence:

```python
adoption_payload = adoption.to_dict()
```

When building evidence JSON, include:

```python
"adoption_result": adoption_payload,
"artifact_result": adoption_payload.get("artifact_result"),
"agent_result": adoption_payload.get("agent_result"),
```

- [ ] **Step 4: Preserve top-level artifact paths**

In the same terminal evidence payload, keep existing top-level paths and do not rename:

```python
"artifact_paths": {
    "receipt": receipt_path,
    "report": report_path,
    "evidence": evidence_path,
    "stdout": stdout_path,
    "stderr": stderr_path,
}
```

- [ ] **Step 5: Run terminal tests**

Run:

```bash
uv run pytest -q tests/unit/test_task_terminal_report.py tests/unit/test_terminal_arbitration.py
```

Expected:

```text
all tests passed
```

- [ ] **Step 6: Commit**

Run:

```bash
git add agpair/task_terminal.py tests/unit/test_task_terminal_report.py tests/unit/test_terminal_arbitration.py
git commit -m "feat: persist artifact results for terminal attempts"
```

## 5. Task 4: Replace Status No-Signal And Manual Adopt Heuristics With Adoption-Aware Classification

**Files:**

- Modify: `agpair/cli/task.py`
- Modify: `tests/integration/test_task_start_and_status.py`

- [ ] **Step 1: Add status regression test**

Add an integration test that creates an acked task with stdout containing a completed report but no receipt path. The expected status must not be `blocked/no` when stdout salvage passes.

Use these assertions:

```python
assert payload["agent_result"]["state"] == "needs_review"
assert payload["agent_result"]["controller_action"] == "use_result"
assert payload["artifact_result"]["primary_artifact"] == "stdout_salvage"
assert payload["adoption_result"]["adoptable_result"] == "partial"
```

Also add a regression test for manual salvage:

```python
result = runner.invoke(
    app,
    ["task", "adopt", task_id, "--from-report", "--adoptable-result", "partial", "--controller-rework", "minor", "--json"],
)
payload = json.loads(result.stdout)
assert payload["adoption_result"]["adoptable_result"] == "partial"
assert payload["adoption_result"]["agent_result"]["state"] == "needs_review"
assert payload["adoption_result"]["agent_result"]["controller_action"] == "use_result"
assert payload["adoption_result"]["artifact_result"]["primary_artifact"] == "stdout_salvage"
```

- [ ] **Step 2: Run focused integration test**

Run:

```bash
uv run pytest -q tests/integration/test_task_start_and_status.py -k "stdout_salvage or no_signal or manual_adopt"
```

Expected:

```text
new stdout salvage status test fails
new manual adopt salvage test fails because agent_result remains blocked
```

- [ ] **Step 3: Replace `_no_useful_signal_agent_result(...)` with adoption-layer fallback**

In `agpair/cli/task.py`, replace `_no_useful_signal_agent_result(...)` with `_adoption_result_for_signal(...)`.

The new helper must call `derive_adoption_decision(...)`, not `classify_artifacts(...)` directly. This keeps status fallback aligned with terminal adoption and prevents CLI code from inventing a second `agent_result` mapping.

```python
def _adoption_result_for_signal(paths: AppPaths, task, signal_summary, artifact_top_level: dict[str, object | None]) -> dict | None:
    controller = _controller_from_current_attempt(paths, task.task_id)
    policy = resolve_effective_task_policy(
        requested_completion_policy=task.completion_policy or "auto",
        authorization_profile=task.authorization_profile or "local_mutating",
        body="",
        controller=controller,
    )
    decision = derive_adoption_decision(
        effective_policy=policy,
        receipt=None,
        report_path=_string_path(artifact_top_level.get("report_path")),
        stdout_path=_string_path(artifact_top_level.get("stdout_path")),
        receipt_path=_string_path(artifact_top_level.get("receipt_path")),
        git_status_summary=None,
        scope_validation=None,
        protocol_warnings=(),
        protocol_errors=(),
    )
    payload = decision.to_dict()
    artifact_result = payload.get("artifact_result")
    if isinstance(artifact_result, dict) and artifact_result.get("state") != "blocked":
        payload["adoptable_result"] = "partial" if payload.get("adoptable_result") == "no" else payload.get("adoptable_result")
        return payload
    if task.phase != "acked":
        return None
    budget_exhausted = (
        signal_summary.execution_budget_remaining_seconds is not None
        and signal_summary.execution_budget_remaining_seconds <= 0
    )
    if not budget_exhausted and (not signal_summary.bootstrap_noise_only or signal_summary.stdout_bytes > 0):
        return None
    return payload
```

Add:

```python
def _string_path(value: object | None) -> str | None:
    return value if isinstance(value, str) and value else None
```

- [ ] **Step 4: Use adoption payload directly**

When `_adoption_result_for_signal(...)` returns a payload, use it directly:

```python
adoption_result = fallback_payload
agent_result = fallback_payload.get("agent_result")
artifact_result = fallback_payload.get("artifact_result")
```

When the fallback payload is blocked because no useful artifact exists, preserve the previous no-signal hard blockers in `agent_result.hard_blockers`.

- [ ] **Step 5: Recompute manual adopt payloads through adoption layer**

In the `task adopt` command path in `agpair/cli/task.py`, when `--from-report` is passed and the current attempt has a stdout/report path, call the same adoption-layer helper used by status fallback. The stored adoption payload must include:

```python
{
    "adoptable_result": "partial",
    "agent_result": {
        "state": "needs_review",
        "controller_action": "use_result",
    },
    "artifact_result": {
        "primary_artifact": "stdout_salvage",
    },
}
```

Do not leave a stale `agent_result.state=blocked` when manual salvage succeeds.

- [ ] **Step 6: Run status and manual adopt tests**

Run:

```bash
uv run pytest -q tests/integration/test_task_start_and_status.py
```

Expected:

```text
all tests passed
```

- [ ] **Step 7: Commit**

Run:

```bash
git add agpair/cli/task.py tests/integration/test_task_start_and_status.py
git commit -m "fix: classify status salvage through artifact results"
```

## 6. Task 5: Make Recovery Decisions Consume Artifact Context

**Files:**

- Modify: `agpair/recovery.py`
- Modify: `tests/unit/test_recovery_decision.py`

- [ ] **Step 1: Add recovery tests**

Add these tests to `tests/unit/test_recovery_decision.py`:

```python
def test_recovery_uses_result_when_stdout_salvage_is_available() -> None:
    decision = choose_recovery_decision(
        RecoveryInput(
            task_id="TASK-1",
            controller="codex",
            current_executor="grok-cli",
            requested_executor=None,
            agent_result={
                "state": "needs_review",
                "controller_action": "use_result",
                "hard_blockers": [],
                "soft_warnings": ["terminal_receipt_missing"],
            },
            liveness_state=None,
            wait_outcome=None,
            execution_budget_exhausted=False,
            artifact_result={
                "state": "needs_review",
                "primary_artifact": "stdout_salvage",
                "global_hard_blockers": [],
            },
            next_eligible_executor="claude-code",
        )
    )

    assert decision.action == "use_result"
    assert decision.next_executor is None


def test_recovery_inspects_evidence_for_global_authorization_blocker() -> None:
    decision = choose_recovery_decision(
        RecoveryInput(
            task_id="TASK-1",
            controller="codex",
            current_executor="grok-cli",
            requested_executor=None,
            agent_result={
                "state": "blocked",
                "controller_action": "inspect_evidence",
                "hard_blockers": ["authorization_violation"],
                "soft_warnings": [],
            },
            liveness_state=None,
            wait_outcome=None,
            execution_budget_exhausted=False,
            artifact_result={
                "state": "blocked",
                "primary_artifact": "report",
                "global_hard_blockers": ["authorization_violation"],
            },
            next_eligible_executor="claude-code",
        )
    )

    assert decision.action == "inspect_evidence"
```

- [ ] **Step 2: Run recovery tests**

Run:

```bash
uv run pytest -q tests/unit/test_recovery_decision.py
```

Expected:

```text
new authorization blocker test fails until inspect_evidence branch is adjusted
```

- [ ] **Step 3: Update `choose_recovery_decision(...)` ordering**

In `agpair/recovery.py`, add `artifact_result` to `RecoveryInput`:

```python
artifact_result: Mapping[str, Any] | None = None
```

Then handle `inspect_evidence` before retry/switch blockers:

```python
if controller_action == "inspect_evidence":
    return RecoveryDecision(
        action="inspect_evidence",
        reason="External executor produced artifacts or blockers that require controller inspection.",
        next_executor=data.next_eligible_executor,
    )
```

Keep `use_result` and `review_then_apply` as the first two fast paths.

- [ ] **Step 4: Run recovery and adoption tests**

Run:

```bash
uv run pytest -q tests/unit/test_recovery_decision.py tests/unit/test_adoption_result.py
```

Expected:

```text
all tests passed
```

- [ ] **Step 5: Commit**

Run:

```bash
git add agpair/recovery.py tests/unit/test_recovery_decision.py
git commit -m "fix: route recovery from artifact-aware actions"
```

## 7. Task 6: Surface `artifact_result` Across CLI, Watch, And Workflows

**Files:**

- Modify: `agpair/cli/task.py`
- Modify: `agpair/cli/wait.py`
- Modify: `agpair/watch.py`
- Modify: `agpair/workflows/evidence.py`
- Modify: `agpair/workflows/watch.py`
- Modify: `agpair/workflows/synthesis.py`
- Modify: `tests/integration/test_workflow_watch.py`
- Modify: `tests/unit/test_workflow_synthesis.py`
- Modify: `tests/integration/test_workflow_fanout_synthesis.py`

- [ ] **Step 1: Add surface tests**

Add assertions wherever terminal task payloads are checked:

```python
assert "artifact_result" in payload
assert payload["artifact_result"]["state"] in {"usable", "needs_review", "blocked"}
assert isinstance(payload["artifact_result"]["artifacts"], list)
```

For workflow lane cards, assert:

```python
assert "artifact_result" in lane_card
assert lane_card["agent_result"]["state"] in {"usable", "needs_review", "blocked"}
```

- [ ] **Step 2: Run workflow/status tests**

Run:

```bash
uv run pytest -q tests/integration/test_workflow_watch.py tests/unit/test_workflow_synthesis.py tests/integration/test_workflow_fanout_synthesis.py
```

Expected:

```text
new artifact_result assertions fail
```

- [ ] **Step 3: Thread artifact result through task payload builders**

In `agpair/cli/task.py`, when `adoption_result` is loaded:

```python
artifact_result = adoption.get("artifact_result") if isinstance(adoption, dict) else None
```

Add it to the JSON payload:

```python
"artifact_result": artifact_result,
```

- [ ] **Step 4: Thread artifact result through wait and watch**

In `agpair/cli/wait.py` and `agpair/watch.py`, include `artifact_result` in the same places that already include `agent_result`.

Watch state comparison should treat artifact changes as meaningful:

```python
or previous.artifact_result != current.artifact_result
```

- [ ] **Step 5: Thread artifact result through workflow evidence**

In `agpair/workflows/evidence.py`, include:

```python
"artifact_result": artifact_result,
```

In `agpair/workflows/synthesis.py`, prefer artifact result when computing lane state:

```python
artifact_result = _dict_value(lane.get("artifact_result"))
state = str(artifact_result.get("state") or agent_result.get("state") or "needs_review")
```

Also update panel aggregation so artifact-local blocked items do not force the whole lane to blocked when another safe artifact is usable:

```python
global_blockers = _string_list(artifact_result.get("global_hard_blockers"))
if global_blockers:
    blocked += 1
elif state == "usable":
    usable += 1
elif state == "needs_review":
    partial += 1
```

Add a synthesis test where one lane has `artifact_result.state=needs_review`, primary `report`, and a blocked `diff` artifact. Expected: synthesis may cite the report, but must not recommend applying the diff.

- [ ] **Step 6: Run surface tests**

Run:

```bash
uv run pytest -q tests/integration/test_task_start_and_status.py tests/integration/test_workflow_watch.py tests/unit/test_workflow_synthesis.py tests/integration/test_workflow_fanout_synthesis.py
```

Expected:

```text
all tests passed
```

- [ ] **Step 7: Commit**

Run:

```bash
git add agpair/cli/task.py agpair/cli/wait.py agpair/watch.py agpair/workflows/evidence.py agpair/workflows/watch.py agpair/workflows/synthesis.py tests/integration/test_workflow_watch.py tests/unit/test_workflow_synthesis.py tests/integration/test_workflow_fanout_synthesis.py tests/integration/test_task_start_and_status.py
git commit -m "feat: expose artifact results across controller surfaces"
```

## 8. Task 7: Update Executor Terminal Salvage To Avoid Duplicate Policy

**Files:**

- Modify: `agpair/executors/local_cli.py`
- Modify: `tests/unit/test_terminal_arbitration.py`
- Modify: `tests/unit/test_task_terminal_report.py`

- [ ] **Step 1: Add duplicate-policy regression test**

Add a test proving local CLI stdout salvage and task terminal adoption produce the same `artifact_result.primary_artifact`.

Use assertions:

```python
assert receipt["payload"]["report"]
assert evidence["adoption_result"]["artifact_result"]["primary_artifact"] in {"report", "stdout_salvage"}
assert evidence["adoption_result"]["agent_result"]["controller_action"] == "use_result"
```

- [ ] **Step 2: Run local terminal tests**

Run:

```bash
uv run pytest -q tests/unit/test_terminal_arbitration.py tests/unit/test_task_terminal_report.py
```

Expected:

```text
new duplicate-policy regression test fails or exposes mismatched artifact kind
```

- [ ] **Step 3: Keep local CLI arbitration minimal**

In `agpair/executors/local_cli.py`, remove final salvage eligibility decisions from local CLI. Local CLI may detect and preserve a `stdout_report_candidate`, but it must not decide whether that candidate is adoptable.

When stdout report text is detected, receipt payload should contain:

```python
{
    "stdout_report_candidate": stdout_report,
    "changed_files": [],
    "scope_violations": [],
    "validation_not_run": "stdout report candidate captured after zero exit",
    "arbitration": "stdout_report_candidate_after_zero_exit",
}
```

The final adoption decision must still come from `derive_adoption_decision(...)` and `classify_artifacts(...)`.

- [ ] **Step 4: Run local terminal tests**

Run:

```bash
uv run pytest -q tests/unit/test_terminal_arbitration.py tests/unit/test_task_terminal_report.py
```

Expected:

```text
all tests passed
```

- [ ] **Step 5: Commit**

Run:

```bash
git add agpair/executors/local_cli.py tests/unit/test_terminal_arbitration.py tests/unit/test_task_terminal_report.py
git commit -m "refactor: keep local cli salvage policy artifact-driven"
```

## 9. Task 8: Add Product Metrics For Artifact Outcomes

**Files:**

- Modify: `scripts/smoke_real_executors.py`
- Modify: `tests/integration/test_real_executor_smoke_harness.py`

- [ ] **Step 1: Add smoke metrics tests**

In `tests/integration/test_real_executor_smoke_harness.py`, assert the smoke summary includes:

```python
assert "artifact_result_rate" in summary["summary_metrics"]
assert "artifact_state_counts" in summary["summary_metrics"]
assert set(summary["summary_metrics"]["artifact_state_counts"]).issubset({"usable", "needs_review", "blocked", "unknown"})
```

- [ ] **Step 2: Run smoke harness tests**

Run:

```bash
uv run pytest -q tests/integration/test_real_executor_smoke_harness.py
```

Expected:

```text
new artifact metric assertions fail
```

- [ ] **Step 3: Emit artifact metrics**

In `scripts/smoke_real_executors.py`, when consuming task status JSON:

```python
artifact_result = payload.get("artifact_result") if isinstance(payload.get("artifact_result"), dict) else {}
artifact_state = str(artifact_result.get("state") or "unknown")
```

Add summary metrics:

```python
"artifact_result_rate": artifact_result_count / total if total else 0.0,
"artifact_state_counts": artifact_state_counts,
```

- [ ] **Step 4: Run smoke harness tests**

Run:

```bash
uv run pytest -q tests/integration/test_real_executor_smoke_harness.py
```

Expected:

```text
all tests passed
```

- [ ] **Step 5: Commit**

Run:

```bash
git add scripts/smoke_real_executors.py tests/integration/test_real_executor_smoke_harness.py
git commit -m "feat: report artifact outcome metrics in smoke harness"
```

## 10. Task 9: Update Docs And Skills

**Files:**

- Modify: `README.md`
- Modify: `README.zh-CN.md`
- Modify: `docs/usage.md`
- Modify: `docs/usage.zh-CN.md`
- Modify: `docs/workflows.md`
- Modify: `docs/workflows.zh-CN.md`
- Modify: `skills/Codex/SKILL.md`
- Modify: `skills/Claude/SKILL.md`

- [ ] **Step 1: Update user-facing result model**

In `README.md` and `README.zh-CN.md`, replace any wording that implies a single protocol result is the final success signal with this contract:

```markdown
AGPair reports three controller-facing layers:

- `artifact_result`: what the external executor produced, with artifact-local states.
- `agent_result`: whether the controller can use the best safe artifact.
- `recovery_decision`: the next controller action.

Use `agent_result.controller_action` for normal operation. Read `artifact_result` when a result is partial, malformed, or surprising. Use raw logs as evidence, not as the usual control plane.
```

- [ ] **Step 2: Update workflow docs**

In `docs/workflows.md` and `docs/workflows.zh-CN.md`, add:

```markdown
Workflow lane cards preserve artifact-level verdicts. A lane can have a blocked code diff and a usable report at the same time; synthesis should cite the usable report while keeping the blocked diff out of automatic application.
```

- [ ] **Step 3: Update AGPair skills**

In `skills/Codex/SKILL.md` and `skills/Claude/SKILL.md`, add controller guidance:

```markdown
When status JSON contains `artifact_result`, treat it as the evidence map:

- `report` or `stdout_salvage` plus `use_result`: read and incorporate the report.
- `diff` or `patch_or_commit` plus `review_then_apply`: inspect, apply-check, test, then adopt.
- `blocked` artifact with another usable artifact: salvage the usable artifact and do not apply the blocked artifact.
- global hard blockers: repair, retry, switch executor, or fall back natively.
```

- [ ] **Step 4: Run docs search**

Run:

```bash
rg -n "adoptable_result=no|retry_or_switch_executor|artifact_result|agent_result|recovery_decision|stdout salvage|stdout_salvage" README.md README.zh-CN.md docs skills
```

Expected:

```text
Active docs mention artifact_result and do not tell controllers to discard useful stdout/report solely because protocol parsing was imperfect.
```

- [ ] **Step 5: Commit**

Run:

```bash
git add README.md README.zh-CN.md docs/usage.md docs/usage.zh-CN.md docs/workflows.md docs/workflows.zh-CN.md skills/Codex/SKILL.md skills/Claude/SKILL.md
git commit -m "docs: explain artifact-level adoption model"
```

## 11. Task 10: Final Verification

**Files:**

- Verify all changed files.

- [ ] **Step 1: Run focused unit tests**

Run:

```bash
uv run pytest -q tests/unit/test_artifact_classification.py tests/unit/test_adoption_result.py tests/unit/test_recovery_decision.py tests/unit/test_terminal_arbitration.py tests/unit/test_task_terminal_report.py tests/unit/test_workflow_synthesis.py
```

Expected:

```text
all tests passed
```

- [ ] **Step 2: Run focused integration tests**

Run:

```bash
uv run pytest -q tests/integration/test_task_start_and_status.py tests/integration/test_workflow_watch.py tests/integration/test_workflow_fanout_synthesis.py tests/integration/test_real_executor_smoke_harness.py
```

Expected:

```text
all tests passed
```

- [ ] **Step 3: Run lint**

Run:

```bash
uv run ruff check .
```

Expected:

```text
All checks passed!
```

- [ ] **Step 4: Run full test suite**

Run:

```bash
uv run pytest -q
```

Expected:

```text
all tests passed
```

- [ ] **Step 5: Run git diff check**

Run:

```bash
git diff --check
```

Expected:

```text
no output
```

- [ ] **Step 6: Run real executor smoke for report and implementation lanes**

Run:

```bash
uv run python scripts/smoke_real_executors.py --controllers codex --task-kinds quick_review implementation --json
```

Expected:

```text
Each healthy executor result includes artifact_result.state in usable|needs_review, agent_result.controller_action, recovery_decision.action, and artifact_result_rate in summary_metrics.
```

- [ ] **Step 7: Verify status JSON manually**

Start one report-only task:

```bash
agpair task start \
  --repo-path /path/to/agpair \
  --executor grok-cli \
  --controller codex \
  --completion-policy report \
  --authorization-profile local_readonly \
  --task-kind quick_review \
  --wait-policy terminal \
  --execution-budget-seconds 120 \
  --timeout-seconds 180 \
  --json \
  --body "Report only. Review AGPair artifact_result status output. Do not edit files."
```

Then inspect:

```bash
agpair task status TASK-ID --json
```

Expected:

```text
status JSON contains artifact_result, adoption_result, agent_result, recovery_decision, and report/stdout artifact paths.
```

- [ ] **Step 8: Accept the smoke task**

Run:

```bash
agpair task accept TASK-ID --adoptable-result yes --controller-rework none --json
```

Expected:

```text
ok=true and is_approved=true
```

## 12. Stop Rule

This plan is complete only when:

- `artifact_result` exists for terminal attempts and status fallback paths;
- manual `task adopt --from-report` does not leave stale blocked `agent_result`;
- artifact-local blocked states do not discard other usable artifacts;
- `adoptable_result=no` means no safe useful artifact remains or a global hard blocker applies;
- `agent_result.controller_action` is derived from the best safe artifact;
- `recovery_decision` remains the only next-action planner;
- report-only and code-editing tasks use the same three-layer model;
- focused tests, integration tests, ruff, full pytest, and one real executor smoke run pass.

## 13. Self-Review Checklist

- [ ] **Spec coverage:** Every first-principles requirement maps to a task:
  - preserve useful external work: Tasks 1, 2, 3, 4;
  - block unsafe work: Tasks 1, 2, 5;
  - one controller next action: Tasks 5, 6;
  - manual salvage consistency: Task 4;
  - report-only and code-editing coverage: Tasks 2, 7, 10;
  - docs and skills: Task 9.
- [ ] **Placeholder scan:** Run:

```bash
rg -n 'TB[D]|TO[D]O|implement[ ]later|fill[ ]in|appropriate[ ]error handling|similar[ ]to Task' docs/superpowers/plans/2026-06-21-first-principles-adoption-architecture-v2-9.md
```

Expected:

```text
no output
```

- [ ] **Interface consistency:** Confirm these names are used consistently:
  - `Artifact`
  - `ArtifactResult`
  - `classify_artifacts`
  - `artifact_result`
  - `agent_result`
  - `recovery_decision`

Run:

```bash
rg -n "ArtifactResult|classify_artifacts|artifact_result" agpair tests scripts docs README.md README.zh-CN.md skills
```

Expected:

```text
Only the V2.9 implementation files, tests, docs, and skills use the new artifact_result vocabulary.
```
