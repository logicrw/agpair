# Fusion-Style Fanout Synthesis V2.7 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Productize AGPair's external-agent fanout so multiple cheap or diverse external executors can run in parallel, produce comparable evidence, and be synthesized into a controller-ready result without weakening AGPair's safety, receipt, and adoption gates.

**Architecture:** Reuse the existing workflow model (`task`, `synthesis`, `verification`, `gate`) instead of adding a separate orchestration stack. Add a Fusion-style synthesis contract on top of current task receipts and workflow evidence packs: panel lanes produce normal AGPair task evidence, a synthesis node summarizes consensus, contradictions, unique insights, blind spots, and controller action, and a gate node verifies safety and adoption readiness. AGPair remains an auditable local control plane, not a black-box multi-model API.

**Tech Stack:** Python 3.12, Typer, SQLite, AGPair workflow scheduler, local CLI executors, terminal receipts, workflow evidence packs, pytest, JSON manifests, Codex / Claude Code AGPair skills.

---

## 0. Plan Contract

This plan follows `2026-06-12-agent-native-handoff-v2-6.md`. V2.6 focuses on making a single external handoff feel native by relaxing protocol friction around useful evidence. V2.7 focuses on the next layer: making multiple external agents work together in a predictable, cheap, auditable way.

The OpenRouter Fusion lesson to adopt:

```text
panel lanes run independently
judge/synthesis compares their output
final answer is grounded in consensus, contradictions, blind spots, and unique insights
benchmark/rubric work validates the panel offline
domain/source blocking prevents evaluation leakage
```

The AGPair-specific boundary:

```text
AGPair is not a hosted model router.
AGPair is not an LLM judge that declares work complete.
AGPair is a local evidence and workflow control plane.
Controller still verifies and accepts/adopts the result.
```

Hard requirements:

- Keep active executor ids unchanged: `grok-cli`, `antigravity-cli`, `claude-code`, `codex`.
- Keep controller-aware suppression: Codex does not default to external `codex`; Claude Code does not default to external `claude-code`.
- Keep `managed-natural` executor environments and inherited skills/MCP/provider config.
- Do not reintroduce `managed-restricted`, `isolated-bare`, hidden launch modes, or per-executor special privileges.
- Do not use benchmark scores for online automatic executor routing.
- Do not hide panel lanes from the controller; every lane must remain inspectable through task status, logs, artifacts, and receipts.
- Do not treat dispatch count, running process count, or phase alone as success.
- Do not let synthesis override authorization, scope, forbidden-file, apply-check, or approval-required gates.
- Do not mark raw model thought, bootstrap logs, or cancellation output as a completed report.
- Do not force every synthesis result to be perfect JSON if there is useful stdout, but capture salvage explicitly and require controller review.

The product test for V2.7 is:

```text
Can Codex or Claude Code start a multi-agent panel, wait cheaply, inspect one synthesis evidence pack, and decide whether to use, apply, retry, switch, or fall back without manually reading every raw log first?
```

## 1. Current Evidence And Motivation

### 1.1 What Fusion Proves

OpenRouter Fusion reports that model panels can beat single-model runs on deep research because different models explore different reasoning paths and source selections. The mechanism that matters is not simply "call more models"; it is:

- parallel panel execution;
- a judge/synthesis layer that surfaces agreement and disagreement;
- structured final answer generation;
- cost-aware panel composition;
- benchmark/rubric validation;
- source blocking during evaluation to reduce contamination.

### 1.2 What AGPair Already Has

AGPair already has the foundation:

- `agpair workflow` supports manifests, scheduler ticks, `task`, `synthesis`, `verification`, and `gate` nodes.
- `agpair/workflows/evidence.py` aggregates task artifacts, receipts, protocol result, adoption result, changed files, validation, and residual risks.
- `agpair/adoption.py` emits `agent_result` and controller actions.
- Codex and Claude skills already require routing budgets and role-based fanout.
- `task wait` / `workflow watch` avoid controller polling loops.

### 1.3 What Still Needs To Change

The current system still relies too much on the controller manually composing the panel result. V2.7 must close these gaps:

1. There is no canonical synthesis schema for `consensus`, `contradictions`, `unique_insights`, `blind_spots`, and `recommended_controller_action`.
2. Workflow evidence packs do not expose panel-level synthesis quality or synthesis-derived controller action.
3. Synthesis nodes receive dependency evidence, but not a compact lane card designed for comparison.
4. Gate nodes only check dependency phases; they do not verify synthesis completeness or panel adoption readiness.
5. A real external report can appear in stdout without a terminal receipt. Example from this planning pass: `TASK-034BACA03B8E` produced a useful `grok-cli` stdout report, but task status remained `acked` with no `receipt_path` or `report_path`. That must become explicit salvage evidence, not an invisible half-result.
6. There is no first-class preset for common fanout shapes such as budget review, adversarial review, implementation candidates, or test-fix panel.
7. Metrics still emphasize per-task outcome more than panel outcome: synthesis usefulness, controller rework reduction, and lane contribution are not visible enough.
8. Documentation says fanout is allowed, but does not yet provide a concrete, repeatable "AGPair Fusion" workflow pattern.

## 2. Target User Experience

### 2.1 Research / Review Panel

Controller command:

```bash
agpair workflow fanout \
  --repo-path "$REPO" \
  --controller codex \
  --mode review \
  --topic "Review terminal receipt salvage and workflow synthesis risks" \
  --lane grok-cli:primary \
  --lane grok-cli:adversarial \
  --lane antigravity-cli:second-opinion \
  --wait
```

Expected high-level status:

```json
{
  "workflow_id": "WF-...",
  "phase": "ready_for_review",
  "panel_result": {
    "state": "usable",
    "controller_action": "use_result",
    "consensus_count": 4,
    "contradiction_count": 1,
    "blind_spot_count": 2,
    "lane_count": 3,
    "usable_lane_count": 2
  },
  "evidence_path": ".../.agpair/workflows/WF-.../evidence.json"
}
```

### 2.2 Implementation Candidate Panel

Controller command:

```bash
agpair workflow fanout \
  --repo-path "$REPO" \
  --controller codex \
  --mode implementation \
  --topic "Implement stdout salvage for wrapped text JSON receipts" \
  --scope "agpair/terminal_receipts.py, agpair/executors/local_cli.py, tests/unit/test_receipt_validation.py" \
  --lane grok-cli:candidate-a \
  --lane grok-cli:candidate-b \
  --lane claude-code:reviewer \
  --isolated-worktree \
  --wait
```

Expected controller action:

```text
review_then_apply
```

The controller still inspects each candidate diff, applies at most one, runs tests, and accepts/adopts explicitly.

### 2.3 Partial Evidence Salvage

If a lane prints a useful report to stdout but misses the receipt format, status must show:

```json
{
  "lane_result": {
    "state": "needs_review",
    "controller_action": "inspect_evidence",
    "soft_warnings": ["terminal_receipt_missing", "stdout_report_salvaged"],
    "evidence_paths": {
      "stdout": ".../stdout.log"
    }
  }
}
```

This is not success. It is usable panel evidence that a synthesis node may cite as partial, while the final gate keeps the workflow in `needs_review` or `blocked` unless the synthesis and safety checks pass.

## 3. Data Model And Result Vocabulary

### 3.1 Lane Card

Create a compact lane card that every synthesis node receives:

```json
{
  "node_id": "review-primary",
  "role": "primary",
  "executor": "grok-cli",
  "task_id": "TASK-...",
  "phase": "ready_for_review",
  "agent_result": {
    "state": "usable",
    "controller_action": "use_result",
    "hard_blockers": [],
    "soft_warnings": []
  },
  "artifacts": {
    "report": ".../report.md",
    "receipt": ".../receipt.json",
    "stdout": ".../stdout.log",
    "stderr": ".../stderr.log",
    "diff": null
  },
  "summary_excerpt": "The worker found that ...",
  "changed_files": [],
  "scope_violations": [],
  "adoptable_result": "yes"
}
```

### 3.2 Synthesis Result

Create a canonical synthesis result:

```json
{
  "schema_version": "1",
  "workflow_id": "WF-...",
  "synthesis_version": "1.0.0",
  "panel": {
    "lane_count": 3,
    "usable_lane_count": 2,
    "partial_lane_count": 1,
    "blocked_lane_count": 0
  },
  "consensus": [
    {
      "claim": "Receipt parsing should salvage JSON in wrapped text fields.",
      "supporting_nodes": ["review-primary", "review-adversarial"]
    }
  ],
  "contradictions": [
    {
      "topic": "Whether synthesis should be an LLM node or internal rule node.",
      "positions": [
        {"node_id": "review-primary", "position": "Use an external synthesis node."},
        {"node_id": "review-adversarial", "position": "Keep synthesis internal for small panels."}
      ],
      "controller_resolution": "Use external synthesis for high-value panels; keep internal validation in gate."
    }
  ],
  "unique_insights": [
    {
      "node_id": "second-opinion",
      "insight": "Expose lane contribution metrics in workflow evidence."
    }
  ],
  "blind_spots": [
    {
      "topic": "No real smoke yet for antigravity-cli panel role.",
      "recommended_followup": "Add smoke manifest covering grok-cli plus antigravity-cli."
    }
  ],
  "recommended_controller_action": "use_result",
  "controller_summary": "Use the report after checking source evidence.",
  "evidence_paths": {
    "workflow_evidence": ".../evidence.json"
  }
}
```

Allowed `recommended_controller_action` values:

```text
use_result
review_then_apply
inspect_evidence
retry_or_switch_executor
native_fallback
wait_background
```

### 3.3 Panel Result

Expose panel status in `workflow status --json`:

```json
{
  "panel_result": {
    "state": "usable",
    "controller_action": "use_result",
    "summary": "Synthesis report is usable; controller should inspect cited lane evidence.",
    "hard_blockers": [],
    "soft_warnings": ["one_lane_partial"],
    "metrics": {
      "lane_count": 3,
      "usable_lane_count": 2,
      "partial_lane_count": 1,
      "blocked_lane_count": 0,
      "contradiction_count": 1,
      "blind_spot_count": 2
    }
  }
}
```

## 4. File And Responsibility Plan

### Create

- `agpair/workflows/synthesis.py`
  - Own lane card construction, synthesis result validation, and panel result derivation.
- `agpair/workflows/presets.py`
  - Build validated workflow manifests for common fanout modes without hard-coding executor special cases.
- `tests/unit/test_workflow_synthesis.py`
  - Unit tests for lane cards, synthesis validation, partial stdout evidence, and panel result derivation.
- `tests/unit/test_workflow_presets.py`
  - Unit tests for fanout manifest generation and validation.
- `tests/integration/test_workflow_fanout_synthesis.py`
  - Integration tests using fake/local executor artifacts and no real API dependency.
- `docs/examples/fanout-synthesis-review.json`
  - Minimal review panel manifest.
- `docs/examples/fanout-synthesis-implementation.json`
  - Isolated implementation panel manifest.

### Modify

- `agpair/workflows/schema.py`
  - Preserve current node kinds.
  - Add validation for optional `mode`, `panel`, `lane_role`, `synthesis_contract`, and `source_policy` fields.
- `agpair/workflows/scheduler.py`
  - Feed lane cards into `synthesis` nodes.
  - Let gate nodes validate synthesis result completeness and panel blockers.
- `agpair/workflows/evidence.py`
  - Include lane cards, synthesis result, panel result, and stdout salvage metadata in workflow evidence packs.
- `agpair/workflows/watch.py`
  - Surface compact `panel_result` and synthesis summary in status/watch payloads.
- `agpair/cli/workflow.py`
  - Add `workflow fanout` command.
  - Add JSON output for generated manifest and started workflow.
- `agpair/terminal_receipts.py`
  - Ensure wrapped `.text` JSON receipts and fenced JSON terminal receipts can be recovered when safe.
- `agpair/executors/local_cli.py`
  - Ensure report-only stdout salvage writes durable artifacts when terminal receipt is absent.
- `README.md`, `README.zh-CN.md`, `docs/usage.md`, `docs/usage.zh-CN.md`
  - Document Fusion-style fanout and controller gate boundaries.
- `skills/Codex/SKILL.md`, `skills/Claude/SKILL.md`
  - Add concrete fanout-synthesis guidance and avoid duplicate-lane ceremony.

## 5. Task 1: Add Workflow Synthesis Unit Model

**Files:**

- Create: `agpair/workflows/synthesis.py`
- Create: `tests/unit/test_workflow_synthesis.py`

- [ ] **Step 1: Write tests for lane card construction**

Create `tests/unit/test_workflow_synthesis.py` with:

```python
from agpair.workflows.synthesis import (
    SynthesisValidationError,
    build_lane_card,
    derive_panel_result,
    validate_synthesis_result,
)


def test_build_lane_card_preserves_agent_result_and_artifacts() -> None:
    node_payload = {
        "node_id": "review-primary",
        "kind": "task",
        "phase": "ready_for_review",
        "task_id": "TASK-1",
        "artifacts": [
            {"type": "report", "path": "/tmp/report.md", "size_bytes": 100, "sha256": "abc"},
            {"type": "stdout", "path": "/tmp/stdout.log", "size_bytes": 200, "sha256": "def"},
        ],
        "adoption_result": {
            "adoptable_result": "yes",
            "agent_result": {
                "state": "usable",
                "controller_action": "use_result",
                "summary": "Report can be used.",
                "hard_blockers": [],
                "soft_warnings": [],
            },
        },
        "terminal_receipt": {
            "payload": {
                "report": "Useful report body",
                "changed_files": [],
                "scope_violations": [],
            }
        },
    }

    card = build_lane_card(node_payload, role="primary", executor="grok-cli")

    assert card["node_id"] == "review-primary"
    assert card["role"] == "primary"
    assert card["executor"] == "grok-cli"
    assert card["agent_result"]["state"] == "usable"
    assert card["artifacts"]["report"] == "/tmp/report.md"
    assert card["summary_excerpt"] == "Useful report body"
```

- [ ] **Step 2: Write tests for stdout salvage lane cards**

Add:

```python
def test_build_lane_card_marks_stdout_salvage_as_needs_review() -> None:
    node_payload = {
        "node_id": "review-salvage",
        "kind": "task",
        "phase": "running",
        "task_id": "TASK-SALVAGE",
        "artifacts": [
            {"type": "stdout", "path": "/tmp/stdout.log", "size_bytes": 500, "sha256": "abc"},
        ],
        "stdout_salvage": {
            "has_report": True,
            "report_excerpt": "The worker produced useful analysis but no terminal receipt.",
        },
        "adoption_result": {
            "adoptable_result": "unknown",
            "agent_result": {
                "state": "needs_review",
                "controller_action": "inspect_evidence",
                "summary": "Compatibility fallback.",
                "hard_blockers": [],
                "soft_warnings": [],
            },
        },
    }

    card = build_lane_card(node_payload, role="partial", executor="grok-cli")

    assert card["agent_result"]["state"] == "needs_review"
    assert card["agent_result"]["controller_action"] == "inspect_evidence"
    assert "stdout_report_salvaged" in card["agent_result"]["soft_warnings"]
    assert card["summary_excerpt"].startswith("The worker produced useful analysis")
```

- [ ] **Step 3: Write tests for synthesis result validation**

Add:

```python
def test_validate_synthesis_result_requires_core_sections() -> None:
    result = {
        "schema_version": "1",
        "workflow_id": "WF-1",
        "synthesis_version": "1.0.0",
        "panel": {"lane_count": 2, "usable_lane_count": 2, "partial_lane_count": 0, "blocked_lane_count": 0},
        "consensus": [{"claim": "Both lanes agree.", "supporting_nodes": ["a", "b"]}],
        "contradictions": [],
        "unique_insights": [],
        "blind_spots": [],
        "recommended_controller_action": "use_result",
        "controller_summary": "Use the synthesized report.",
        "evidence_paths": {"workflow_evidence": "/tmp/evidence.json"},
    }

    normalized = validate_synthesis_result(result, workflow_id="WF-1")

    assert normalized["recommended_controller_action"] == "use_result"
    assert normalized["panel"]["lane_count"] == 2


def test_validate_synthesis_result_rejects_missing_blind_spots() -> None:
    result = {
        "schema_version": "1",
        "workflow_id": "WF-1",
        "synthesis_version": "1.0.0",
        "panel": {"lane_count": 1, "usable_lane_count": 1, "partial_lane_count": 0, "blocked_lane_count": 0},
        "consensus": [],
        "contradictions": [],
        "unique_insights": [],
        "recommended_controller_action": "use_result",
        "controller_summary": "Missing section.",
        "evidence_paths": {"workflow_evidence": "/tmp/evidence.json"},
    }

    try:
        validate_synthesis_result(result, workflow_id="WF-1")
    except SynthesisValidationError as exc:
        assert "blind_spots" in str(exc)
    else:
        raise AssertionError("expected SynthesisValidationError")
```

- [ ] **Step 4: Write tests for panel result derivation**

Add:

```python
def test_derive_panel_result_uses_synthesis_action_and_lane_counts() -> None:
    synthesis = {
        "recommended_controller_action": "use_result",
        "controller_summary": "Two lanes agree and one lane is partial.",
        "consensus": [{"claim": "Agree", "supporting_nodes": ["a", "b"]}],
        "contradictions": [{"topic": "Risk", "positions": [], "controller_resolution": "Review manually."}],
        "blind_spots": [{"topic": "Smoke", "recommended_followup": "Run real executor smoke."}],
    }
    lane_cards = [
        {"agent_result": {"state": "usable", "hard_blockers": [], "soft_warnings": []}},
        {"agent_result": {"state": "usable", "hard_blockers": [], "soft_warnings": []}},
        {"agent_result": {"state": "needs_review", "hard_blockers": [], "soft_warnings": ["stdout_report_salvaged"]}},
    ]

    panel = derive_panel_result(synthesis, lane_cards)

    assert panel["state"] == "usable"
    assert panel["controller_action"] == "use_result"
    assert panel["metrics"]["lane_count"] == 3
    assert panel["metrics"]["partial_lane_count"] == 1
    assert panel["metrics"]["contradiction_count"] == 1
    assert "one_lane_partial" in panel["soft_warnings"]
```

- [ ] **Step 5: Run tests and verify failure**

Run:

```bash
PYTHONPATH=. pytest -q tests/unit/test_workflow_synthesis.py
```

Expected:

```text
ModuleNotFoundError: No module named 'agpair.workflows.synthesis'
```

- [ ] **Step 6: Implement `agpair/workflows/synthesis.py`**

Create `agpair/workflows/synthesis.py`:

```python
from __future__ import annotations

from typing import Any, Mapping

VALID_CONTROLLER_ACTIONS = frozenset({
    "use_result",
    "review_then_apply",
    "inspect_evidence",
    "retry_or_switch_executor",
    "native_fallback",
    "wait_background",
})
REQUIRED_SYNTHESIS_FIELDS = (
    "schema_version",
    "workflow_id",
    "synthesis_version",
    "panel",
    "consensus",
    "contradictions",
    "unique_insights",
    "blind_spots",
    "recommended_controller_action",
    "controller_summary",
    "evidence_paths",
)


class SynthesisValidationError(ValueError):
    pass


def build_lane_card(node_payload: Mapping[str, Any], *, role: str | None = None, executor: str | None = None) -> dict[str, Any]:
    artifacts = {
        str(item.get("type")): str(item.get("path"))
        for item in node_payload.get("artifacts", [])
        if isinstance(item, Mapping) and item.get("type") and item.get("path")
    }
    adoption = node_payload.get("adoption_result") if isinstance(node_payload.get("adoption_result"), Mapping) else {}
    agent_result = adoption.get("agent_result") if isinstance(adoption, Mapping) and isinstance(adoption.get("agent_result"), Mapping) else {}
    terminal_receipt = node_payload.get("terminal_receipt") if isinstance(node_payload.get("terminal_receipt"), Mapping) else {}
    payload = terminal_receipt.get("payload") if isinstance(terminal_receipt.get("payload"), Mapping) else {}
    stdout_salvage = node_payload.get("stdout_salvage") if isinstance(node_payload.get("stdout_salvage"), Mapping) else {}
    soft_warnings = list(agent_result.get("soft_warnings") or [])
    if stdout_salvage.get("has_report") and "stdout_report_salvaged" not in soft_warnings:
        soft_warnings.append("stdout_report_salvaged")
    normalized_agent_result = {
        "state": str(agent_result.get("state") or "needs_review"),
        "controller_action": str(agent_result.get("controller_action") or "inspect_evidence"),
        "summary": str(agent_result.get("summary") or "Lane evidence requires controller inspection."),
        "hard_blockers": list(agent_result.get("hard_blockers") or []),
        "soft_warnings": soft_warnings,
    }
    return {
        "node_id": node_payload.get("node_id"),
        "role": role,
        "executor": executor,
        "task_id": node_payload.get("task_id"),
        "phase": node_payload.get("phase"),
        "agent_result": normalized_agent_result,
        "artifacts": {
            "report": artifacts.get("report"),
            "receipt": artifacts.get("receipt"),
            "stdout": artifacts.get("stdout"),
            "stderr": artifacts.get("stderr"),
            "diff": artifacts.get("diff") or artifacts.get("patch"),
        },
        "summary_excerpt": _summary_excerpt(payload, stdout_salvage),
        "changed_files": list(payload.get("changed_files") or []),
        "scope_violations": list(payload.get("scope_violations") or []),
        "adoptable_result": adoption.get("adoptable_result", "unknown") if isinstance(adoption, Mapping) else "unknown",
    }


def validate_synthesis_result(result: Mapping[str, Any], *, workflow_id: str) -> dict[str, Any]:
    missing = [field for field in REQUIRED_SYNTHESIS_FIELDS if field not in result]
    if missing:
        raise SynthesisValidationError("synthesis result missing required fields: " + ", ".join(missing))
    if str(result.get("schema_version")) != "1":
        raise SynthesisValidationError("synthesis schema_version must be 1")
    if str(result.get("workflow_id")) != workflow_id:
        raise SynthesisValidationError("synthesis workflow_id mismatch")
    action = str(result.get("recommended_controller_action"))
    if action not in VALID_CONTROLLER_ACTIONS:
        raise SynthesisValidationError("invalid recommended_controller_action: " + action)
    for list_field in ("consensus", "contradictions", "unique_insights", "blind_spots"):
        if not isinstance(result.get(list_field), list):
            raise SynthesisValidationError(f"synthesis {list_field} must be an array")
    panel = result.get("panel")
    if not isinstance(panel, Mapping):
        raise SynthesisValidationError("synthesis panel must be an object")
    return dict(result)


def derive_panel_result(synthesis: Mapping[str, Any] | None, lane_cards: list[Mapping[str, Any]]) -> dict[str, Any]:
    synthesis = synthesis or {}
    usable = sum(1 for card in lane_cards if _lane_state(card) == "usable")
    partial = sum(1 for card in lane_cards if _lane_state(card) == "needs_review")
    blocked = sum(1 for card in lane_cards if _lane_state(card) == "blocked")
    hard_blockers: list[str] = []
    soft_warnings: list[str] = []
    for card in lane_cards:
        agent_result = card.get("agent_result") if isinstance(card.get("agent_result"), Mapping) else {}
        hard_blockers.extend(str(item) for item in agent_result.get("hard_blockers") or [])
        soft_warnings.extend(str(item) for item in agent_result.get("soft_warnings") or [])
    if partial:
        soft_warnings.append("one_lane_partial" if partial == 1 else "multiple_lanes_partial")
    if blocked:
        soft_warnings.append("one_lane_blocked" if blocked == 1 else "multiple_lanes_blocked")
    action = str(synthesis.get("recommended_controller_action") or "inspect_evidence")
    state = "blocked" if hard_blockers and not usable else "needs_review" if partial or blocked or action == "inspect_evidence" else "usable"
    return {
        "state": state,
        "controller_action": action if action in VALID_CONTROLLER_ACTIONS else "inspect_evidence",
        "summary": str(synthesis.get("controller_summary") or "Panel evidence requires controller inspection."),
        "hard_blockers": _unique(hard_blockers),
        "soft_warnings": _unique(soft_warnings),
        "metrics": {
            "lane_count": len(lane_cards),
            "usable_lane_count": usable,
            "partial_lane_count": partial,
            "blocked_lane_count": blocked,
            "consensus_count": len(synthesis.get("consensus") or []),
            "contradiction_count": len(synthesis.get("contradictions") or []),
            "blind_spot_count": len(synthesis.get("blind_spots") or []),
        },
    }


def _lane_state(card: Mapping[str, Any]) -> str:
    agent_result = card.get("agent_result")
    if isinstance(agent_result, Mapping):
        return str(agent_result.get("state") or "needs_review")
    return "needs_review"


def _summary_excerpt(payload: Mapping[str, Any], stdout_salvage: Mapping[str, Any]) -> str:
    report = payload.get("report")
    if isinstance(report, str) and report.strip():
        return report.strip()[:1200]
    excerpt = stdout_salvage.get("report_excerpt")
    if isinstance(excerpt, str) and excerpt.strip():
        return excerpt.strip()[:1200]
    return ""


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(item for item in values if item))
```

- [ ] **Step 7: Run tests and verify pass**

Run:

```bash
PYTHONPATH=. pytest -q tests/unit/test_workflow_synthesis.py
```

Expected:

```text
4 passed
```

## 6. Task 2: Add Fanout Presets Without Executor Specialization

**Files:**

- Create: `agpair/workflows/presets.py`
- Create: `tests/unit/test_workflow_presets.py`
- Modify: `agpair/workflows/schema.py`

- [ ] **Step 1: Write preset tests**

Create `tests/unit/test_workflow_presets.py`:

```python
from agpair.workflows.presets import build_fanout_manifest
from agpair.workflows.schema import validate_manifest


def test_review_fanout_manifest_has_parallel_lanes_synthesis_and_gate() -> None:
    manifest = build_fanout_manifest(
        name="Receipt salvage review",
        repo_path="/tmp/repo",
        controller="codex",
        mode="review",
        topic="Review stdout salvage",
        scope="agpair/terminal_receipts.py",
        lanes=[
            ("grok-cli", "primary"),
            ("grok-cli", "adversarial"),
            ("antigravity-cli", "second-opinion"),
        ],
        isolated_worktree=False,
    )

    validated = validate_manifest(manifest, require_repo_path=True)

    assert validated.name == "Receipt salvage review"
    assert [node["kind"] for node in validated.nodes] == ["task", "task", "task", "synthesis", "gate"]
    assert validated.nodes[0]["role"] == "primary"
    assert validated.nodes[3]["kind"] == "synthesis"
    assert sorted(validated.nodes[3]["depends_on"]) == ["lane-1-primary", "lane-2-adversarial", "lane-3-second-opinion"]
    assert validated.nodes[4]["kind"] == "gate"


def test_implementation_fanout_requires_isolated_mutating_task_lanes() -> None:
    manifest = build_fanout_manifest(
        name="Implementation candidates",
        repo_path="/tmp/repo",
        controller="codex",
        mode="implementation",
        topic="Implement synthesis parser",
        scope="agpair/workflows/synthesis.py",
        lanes=[("grok-cli", "candidate-a"), ("grok-cli", "candidate-b")],
        isolated_worktree=True,
    )

    task_nodes = [node for node in manifest["nodes"] if node["kind"] == "task"]

    assert all(node["isolated_worktree"] is True for node in task_nodes)
    assert all(node["authorization_profile"] == "local_mutating" for node in task_nodes)
    assert all(node["completion_policy"] == "evidence" for node in task_nodes)
```

- [ ] **Step 2: Verify tests fail**

Run:

```bash
PYTHONPATH=. pytest -q tests/unit/test_workflow_presets.py
```

Expected:

```text
ModuleNotFoundError: No module named 'agpair.workflows.presets'
```

- [ ] **Step 3: Implement `build_fanout_manifest`**

Create `agpair/workflows/presets.py`:

```python
from __future__ import annotations

import re
from typing import Literal

FanoutMode = Literal["review", "research", "implementation", "test-fix"]


def build_fanout_manifest(
    *,
    name: str,
    repo_path: str,
    controller: str,
    mode: FanoutMode,
    topic: str,
    scope: str,
    lanes: list[tuple[str, str]],
    isolated_worktree: bool,
) -> dict:
    if not lanes:
        raise ValueError("fanout requires at least one lane")
    if mode in {"implementation", "test-fix"} and not isolated_worktree:
        raise ValueError("implementation and test-fix fanout require --isolated-worktree")
    auth = "local_mutating" if mode in {"implementation", "test-fix"} else "local_readonly"
    completion = "evidence" if mode in {"implementation", "test-fix"} else "report"
    task_nodes = []
    dep_ids = []
    for index, (executor, role) in enumerate(lanes, start=1):
        node_id = f"lane-{index}-{_slug(role)}"
        dep_ids.append(node_id)
        task_nodes.append({
            "id": node_id,
            "kind": "task",
            "role": role,
            "executor": executor,
            "authorization_profile": auth,
            "completion_policy": completion,
            "isolated_worktree": bool(isolated_worktree and mode in {"implementation", "test-fix"}),
            "body": _lane_body(mode=mode, role=role, topic=topic, scope=scope),
        })
    synthesis = {
        "id": "synthesis",
        "kind": "synthesis",
        "role": "synthesizer",
        "authorization_profile": "local_readonly",
        "completion_policy": "report",
        "depends_on": dep_ids,
        "body": _synthesis_body(mode=mode, topic=topic, scope=scope),
        "synthesis_contract": "fusion_v1",
    }
    gate = {
        "id": "gate",
        "kind": "gate",
        "role": "controller-gate",
        "depends_on": ["synthesis"],
        "authorization_profile": "local_readonly",
        "completion_policy": "report",
    }
    return {
        "version": 1,
        "name": name,
        "repo_path": repo_path,
        "controller": controller,
        "mode": mode,
        "limits": {
            "max_parallel_tasks": min(max(len(lanes), 1), 8),
            "max_child_tasks": len(lanes) + 2,
            "max_retries_per_node": 1,
            "max_runtime_seconds": 14400,
            "max_watch_events": 500,
        },
        "nodes": [*task_nodes, synthesis, gate],
    }


def _lane_body(*, mode: str, role: str, topic: str, scope: str) -> str:
    return (
        f"Goal: {topic}\n"
        f"Role: {role}\n"
        f"Mode: {mode}\n"
        f"Scope: {scope}\n"
        "Required changes: "
        + ("Produce an isolated evidence diff only within scope." if mode in {"implementation", "test-fix"} else "None. Report only. Do not modify files.")
        + "\n"
        "Exit criteria: Return AGPair terminal evidence with clear report, cited files, risks, and controller recommendation.\n"
    )


def _synthesis_body(*, mode: str, topic: str, scope: str) -> str:
    return (
        f"Goal: Synthesize the AGPair fanout panel for: {topic}\n"
        f"Mode: {mode}\n"
        f"Scope: {scope}\n"
        "Required changes: None. Report only. Do not modify files.\n"
        "Exit criteria: Return a synthesis JSON object with schema_version, workflow_id, synthesis_version, panel, "
        "consensus, contradictions, unique_insights, blind_spots, recommended_controller_action, controller_summary, and evidence_paths. "
        "Use dependency lane evidence only; do not invent missing evidence.\n"
    )


def _slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip()).strip("-")
    return slug or "lane"
```

- [ ] **Step 4: Allow optional manifest metadata**

Modify `agpair/workflows/schema.py` so node metadata survives validation:

```python
if isinstance(node.get("role"), str):
    node["role"] = node["role"].strip()[:80]
if isinstance(node.get("synthesis_contract"), str):
    node["synthesis_contract"] = node["synthesis_contract"].strip()
if isinstance(manifest.get("mode"), str):
    manifest["mode"] = manifest["mode"].strip()
if isinstance(manifest.get("source_policy"), dict):
    manifest["source_policy"] = manifest["source_policy"]
```

Place the node-level block after `node["kind"] = kind`. Place the manifest-level block after limits normalization.

- [ ] **Step 5: Run tests**

Run:

```bash
PYTHONPATH=. pytest -q tests/unit/test_workflow_presets.py tests/unit/test_workflow_store.py
```

Expected:

```text
all tests pass
```

## 7. Task 3: Preserve Partial Evidence In Workflow Evidence Packs

**Files:**

- Modify: `agpair/workflows/evidence.py`
- Modify: `tests/unit/test_workflow_synthesis.py`
- Create: `tests/unit/test_workflow_evidence_salvage.py`

- [ ] **Step 1: Add tests for stdout salvage extraction**

Create `tests/unit/test_workflow_evidence_salvage.py`:

```python
from pathlib import Path

from agpair.workflows.evidence import extract_stdout_salvage


def test_extract_stdout_salvage_from_grok_text_wrapper(tmp_path: Path) -> None:
    stdout = tmp_path / "stdout.log"
    stdout.write_text(
        '{"text":"# Review Report\\n\\nFindings:\\n- Useful external report.\\n\\n```json\\n'
        '{\\"schema_version\\":\\"1.0\\",\\"task_id\\":\\"TASK-1\\",\\"attempt_no\\":1,\\"review_round\\":0,'
        '\\"status\\":\\"completed\\",\\"summary\\":\\"done\\",\\"payload\\":{\\"report\\":\\"Useful external report.\\",'
        '\\"raw_log_path\\":\\"stdout.log\\",\\"receipt_path\\":\\"receipt.json\\"}}\\n```\\n"}',
        encoding="utf-8",
    )

    salvage = extract_stdout_salvage(str(stdout))

    assert salvage["has_report"] is True
    assert "Useful external report" in salvage["report_excerpt"]
    assert salvage["has_embedded_receipt"] is True


def test_extract_stdout_salvage_rejects_thought_only_output(tmp_path: Path) -> None:
    stdout = tmp_path / "stdout.log"
    stdout.write_text(
        '{"text":"Reviewing files before answering.\\n","thought":"I need to continue reading.","stopReason":"Cancelled"}',
        encoding="utf-8",
    )

    salvage = extract_stdout_salvage(str(stdout))

    assert salvage["has_report"] is False
    assert salvage["has_embedded_receipt"] is False
```

- [ ] **Step 2: Verify tests fail**

Run:

```bash
PYTHONPATH=. pytest -q tests/unit/test_workflow_evidence_salvage.py
```

Expected:

```text
ImportError: cannot import name 'extract_stdout_salvage'
```

- [ ] **Step 3: Implement salvage extraction**

In `agpair/workflows/evidence.py`, add:

```python
def extract_stdout_salvage(stdout_path: str | None) -> dict[str, Any]:
    if not stdout_path:
        return {"has_report": False, "has_embedded_receipt": False}
    try:
        text = Path(stdout_path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {"has_report": False, "has_embedded_receipt": False}
    material = _unwrap_text_field(text)
    if _looks_thought_only(material, text):
        return {"has_report": False, "has_embedded_receipt": False}
    embedded_receipt = _extract_embedded_receipt(material)
    has_report = _looks_like_report(material)
    return {
        "has_report": has_report,
        "has_embedded_receipt": embedded_receipt is not None,
        "embedded_receipt": embedded_receipt,
        "report_excerpt": material.strip()[:4000] if has_report else "",
    }
```

Add helper functions in the same file:

```python
def _unwrap_text_field(raw: str) -> str:
    parsed = _parse_json_object(raw)
    if isinstance(parsed, dict):
        value = parsed.get("text") or parsed.get("output") or parsed.get("result")
        if isinstance(value, str):
            return value
    return raw


def _looks_thought_only(material: str, raw: str) -> bool:
    parsed = _parse_json_object(raw)
    if isinstance(parsed, dict) and parsed.get("stopReason") == "Cancelled" and parsed.get("thought"):
        return True
    lowered = material.lower()
    return "i need to continue" in lowered and "findings:" not in lowered and "summary" not in lowered


def _looks_like_report(material: str) -> bool:
    stripped = material.strip()
    if len(stripped) < 80:
        return False
    lowered = stripped.lower()
    markers = ("findings:", "summary", "conclusion", "recommendation", "结论", "建议", "发现")
    return any(marker in lowered for marker in markers)


def _extract_embedded_receipt(material: str) -> dict[str, Any] | None:
    from agpair.terminal_receipts import parse_structured_terminal_receipt

    protocol = parse_structured_terminal_receipt(material)
    if protocol.receipt is not None:
        return {
            "schema_version": protocol.receipt.schema_version,
            "task_id": protocol.receipt.task_id,
            "attempt_no": protocol.receipt.attempt_no,
            "review_round": protocol.receipt.review_round,
            "status": protocol.receipt.status,
            "summary": protocol.receipt.summary,
            "payload": protocol.receipt.payload,
        }
    return None
```

- [ ] **Step 4: Attach salvage to node payloads**

In `build_workflow_evidence_pack`, after artifact paths are collected, add:

```python
stdout_salvage = extract_stdout_salvage(artifact_paths.get("stdout"))
```

Then add to each node payload:

```python
"stdout_salvage": stdout_salvage,
```

Only include a residual risk for salvage when the node is treated as successful without a durable receipt:

```python
if stdout_salvage.get("has_report") and node.phase not in SUCCESS_NODE_PHASES:
    residual_risks.append(f"node {node.node_id} has stdout report salvage but is not terminal")
```

- [ ] **Step 5: Run tests**

Run:

```bash
PYTHONPATH=. pytest -q tests/unit/test_workflow_evidence_salvage.py tests/unit/test_workflow_synthesis.py
```

Expected:

```text
all tests pass
```

## 8. Task 4: Feed Lane Cards Into Synthesis Nodes

**Files:**

- Modify: `agpair/workflows/scheduler.py`
- Modify: `tests/unit/test_workflow_scheduler.py`

- [ ] **Step 1: Write scheduler body test**

Add to `tests/unit/test_workflow_scheduler.py`:

```python
def test_synthesis_node_body_contains_lane_cards(tmp_path):
    paths, repo_path = setup_workflow_test(tmp_path)
    workflow_id = create_workflow_with_completed_lane(paths, repo_path)
    scheduler = WorkflowScheduler(paths)
    workflow = scheduler.workflows.require_workflow(workflow_id)
    synthesis = scheduler.workflows.get_node(workflow_id, "synthesis")

    body = scheduler._node_body(workflow, synthesis)

    assert "Workflow lane cards for synthesis, JSON:" in body
    assert '"node_id": "lane-1-primary"' in body
    assert '"agent_result"' in body
    assert "consensus" in body
    assert "contradictions" in body
    assert "blind_spots" in body
```

Use existing workflow test helpers if present. If helpers do not exist, create a small manifest with one completed lane node and one synthesis node through `WorkflowRepository`.

- [ ] **Step 2: Verify test fails**

Run:

```bash
PYTHONPATH=. pytest -q tests/unit/test_workflow_scheduler.py::test_synthesis_node_body_contains_lane_cards
```

Expected:

```text
FAIL because lane cards are not present
```

- [ ] **Step 3: Build lane cards in scheduler**

Modify `_node_body` in `agpair/workflows/scheduler.py` for `node.kind in {"synthesis", "verification"}`:

```python
from agpair.workflows.evidence import build_workflow_evidence_pack
from agpair.workflows.synthesis import build_lane_card
```

After dependency collection, add:

```python
evidence_pack = build_workflow_evidence_pack(self.paths, workflow.workflow_id, phase=workflow.phase)
payloads_by_id = {
    item.get("node_id"): item
    for item in evidence_pack.get("nodes", [])
    if isinstance(item, dict)
}
lane_cards = []
for dep in node.depends_list():
    dep_node = self.workflows.get_node(workflow.workflow_id, dep)
    payload = payloads_by_id.get(dep)
    if dep_node is not None and isinstance(payload, dict):
        lane_cards.append(build_lane_card(payload, role=dep_node.role, executor=dep_node.executor_backend))
body += "\n\nWorkflow lane cards for synthesis, JSON:\n"
body += json.dumps(lane_cards, ensure_ascii=False, sort_keys=True)
body += "\n\nRequired synthesis output fields: schema_version, workflow_id, synthesis_version, panel, consensus, contradictions, unique_insights, blind_spots, recommended_controller_action, controller_summary, evidence_paths.\n"
```

- [ ] **Step 4: Run scheduler tests**

Run:

```bash
PYTHONPATH=. pytest -q tests/unit/test_workflow_scheduler.py tests/unit/test_workflow_synthesis.py
```

Expected:

```text
all tests pass
```

## 9. Task 5: Validate Synthesis And Gate Panel Results

**Files:**

- Modify: `agpair/workflows/scheduler.py`
- Modify: `agpair/workflows/evidence.py`
- Modify: `agpair/workflows/watch.py`
- Modify: `tests/integration/test_workflow_fanout_synthesis.py`

- [ ] **Step 1: Write integration test for complete fanout workflow**

Create `tests/integration/test_workflow_fanout_synthesis.py`:

```python
import json

from agpair.workflows.evidence import build_workflow_evidence_pack
from agpair.workflows.synthesis import derive_panel_result, validate_synthesis_result


def test_gate_accepts_valid_synthesis_result() -> None:
    synthesis = {
        "schema_version": "1",
        "workflow_id": "WF-FUSION",
        "synthesis_version": "1.0.0",
        "panel": {"lane_count": 2, "usable_lane_count": 2, "partial_lane_count": 0, "blocked_lane_count": 0},
        "consensus": [{"claim": "Both lanes agree.", "supporting_nodes": ["lane-a", "lane-b"]}],
        "contradictions": [],
        "unique_insights": [],
        "blind_spots": [{"topic": "No real smoke.", "recommended_followup": "Run smoke_real_executors.py."}],
        "recommended_controller_action": "use_result",
        "controller_summary": "Use the synthesized report after normal verification.",
        "evidence_paths": {"workflow_evidence": "/tmp/evidence.json"},
    }
    lanes = [
        {"agent_result": {"state": "usable", "hard_blockers": [], "soft_warnings": []}},
        {"agent_result": {"state": "usable", "hard_blockers": [], "soft_warnings": []}},
    ]

    normalized = validate_synthesis_result(synthesis, workflow_id="WF-FUSION")
    panel = derive_panel_result(normalized, lanes)

    assert panel["state"] == "usable"
    assert panel["controller_action"] == "use_result"
```

- [ ] **Step 2: Add gate validation behavior**

In `_run_gate_node`, before marking gate passed:

```python
if any(item.kind == "synthesis" for item in nodes):
    synthesis_nodes = [item for item in nodes if item.kind == "synthesis"]
    latest_synthesis = synthesis_nodes[-1]
    synthesis_payload = _safe_json(latest_synthesis.result_json) or _safe_json(latest_synthesis.evidence_json)
    try:
        validate_synthesis_result(synthesis_payload, workflow_id=workflow.workflow_id)
    except SynthesisValidationError as exc:
        self.workflows.mark_node_phase(
            workflow.workflow_id,
            node.node_id,
            "blocked",
            error=f"gate failed: invalid synthesis result: {exc}",
        )
        return
```

Import:

```python
from agpair.workflows.synthesis import SynthesisValidationError, validate_synthesis_result
```

- [ ] **Step 3: Include panel result in evidence pack**

In `build_workflow_evidence_pack`, after `node_payloads` are built:

```python
from agpair.workflows.synthesis import build_lane_card, derive_panel_result, validate_synthesis_result, SynthesisValidationError

lane_cards = []
for payload in node_payloads:
    if payload["kind"] == "task":
        lane_cards.append(build_lane_card(payload, role=None, executor=None))
synthesis_result = None
panel_result = None
for payload in node_payloads:
    if payload["kind"] == "synthesis":
        candidate = _parse_json_object(payload.get("result_json")) or _parse_json_object(payload.get("evidence_json"))
        if isinstance(candidate, dict):
            try:
                synthesis_result = validate_synthesis_result(candidate, workflow_id=workflow.workflow_id)
            except SynthesisValidationError:
                synthesis_result = None
if synthesis_result is not None:
    panel_result = derive_panel_result(synthesis_result, lane_cards)
```

Add to returned dict:

```python
"lane_cards": lane_cards,
"synthesis_result": synthesis_result,
"panel_result": panel_result,
```

- [ ] **Step 4: Surface panel result in status/watch**

In `agpair/workflows/watch.py`, include `panel_result` from workflow `result_json` if available:

```python
result = _safe_json(workflow.result_json)
if isinstance(result, dict) and result.get("panel_result"):
    payload["panel_result"] = result["panel_result"]
```

- [ ] **Step 5: Run workflow tests**

Run:

```bash
PYTHONPATH=. pytest -q tests/unit/test_workflow_synthesis.py tests/integration/test_workflow_fanout_synthesis.py tests/unit/test_workflow_store.py tests/unit/test_workflow_scheduler.py
```

Expected:

```text
all tests pass
```

## 10. Task 6: Add `agpair workflow fanout`

**Files:**

- Modify: `agpair/cli/workflow.py`
- Modify: `tests/integration/test_workflow_cli.py`
- Create: `docs/examples/fanout-synthesis-review.json`
- Create: `docs/examples/fanout-synthesis-implementation.json`

- [ ] **Step 1: Write CLI test for dry-run manifest**

Add to `tests/integration/test_workflow_cli.py`:

```python
from typer.testing import CliRunner

from agpair.cli.app import app


def test_workflow_fanout_dry_run_emits_manifest(tmp_path, monkeypatch) -> None:
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "workflow",
            "fanout",
            "--repo-path",
            str(tmp_path),
            "--controller",
            "codex",
            "--mode",
            "review",
            "--topic",
            "Review stdout salvage",
            "--scope",
            "agpair/terminal_receipts.py",
            "--lane",
            "grok-cli:primary",
            "--lane",
            "grok-cli:adversarial",
            "--dry-run",
            "--json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["manifest"]["nodes"][-2]["kind"] == "synthesis"
    assert payload["manifest"]["nodes"][-1]["kind"] == "gate"
```

- [ ] **Step 2: Verify test fails**

Run:

```bash
PYTHONPATH=. pytest -q tests/integration/test_workflow_cli.py::test_workflow_fanout_dry_run_emits_manifest
```

Expected:

```text
Error: No such command 'fanout'
```

- [ ] **Step 3: Implement command**

In `agpair/cli/workflow.py`, add:

```python
@app.command("fanout")
def fanout(
    topic: str = typer.Option(..., "--topic", help="Fanout task topic."),
    scope: str = typer.Option("", "--scope", help="Allowed scope or relevant paths."),
    mode: str = typer.Option("review", "--mode", help="review, research, implementation, or test-fix."),
    lane: list[str] = typer.Option(["grok-cli:primary"], "--lane", help="executor:role lane, may repeat."),
    controller: str = typer.Option("generic", "--controller"),
    repo_path: str | None = typer.Option(None, "--repo-path"),
    target: str | None = typer.Option(None, "--target"),
    isolated_worktree: bool = typer.Option(False, "--isolated-worktree"),
    workflow_id: str | None = typer.Option(None, "--workflow-id"),
    wait: bool = typer.Option(False, "--wait"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    paths = _paths()
    effective_repo_path = _resolve_workflow_repo_path(repo_path=repo_path, target=target, paths=paths)
    lanes = [_parse_lane(value) for value in lane]
    from agpair.workflows.presets import build_fanout_manifest

    manifest = build_fanout_manifest(
        name=f"{mode}: {topic}"[:120],
        repo_path=effective_repo_path,
        controller=controller,
        mode=mode,
        topic=topic,
        scope=scope,
        lanes=lanes,
        isolated_worktree=isolated_worktree,
    )
    validate_manifest(manifest, require_repo_path=True)
    if dry_run:
        payload = {"ok": True, "manifest": manifest}
        _emit_json(payload) if json_output else typer.echo(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))
        return
    repo = WorkflowRepository(paths.db_path)
    final_workflow_id = repo.create_workflow(validate_manifest(manifest, require_repo_path=True), workflow_id=workflow_id, repo_path=effective_repo_path)
    tick = WorkflowScheduler(paths).tick(final_workflow_id, repo_path=effective_repo_path)
    payload = _wait_for_workflow(paths, final_workflow_id, repo_path=effective_repo_path, interval_seconds=2.0, timeout_seconds=3600.0) if wait else workflow_status_payload(paths, final_workflow_id)
    payload.update({"ok": bool(payload.get("ok", True)), "tick": tick, "repo_path": effective_repo_path})
    _emit_json(payload) if json_output else typer.echo(f"workflow_id: {payload['workflow_id']}\nphase: {payload['phase']}")
```

Add helper:

```python
def _parse_lane(value: str) -> tuple[str, str]:
    if ":" not in value:
        return value, value
    executor, role = value.split(":", 1)
    return executor.strip(), role.strip() or executor.strip()
```

- [ ] **Step 4: Create example review manifest**

Create `docs/examples/fanout-synthesis-review.json`:

```json
{
  "version": 1,
  "name": "Fusion-style review panel",
  "controller": "codex",
  "mode": "review",
  "limits": {
    "max_parallel_tasks": 3,
    "max_child_tasks": 5,
    "max_retries_per_node": 1,
    "max_runtime_seconds": 14400,
    "max_watch_events": 500
  },
  "nodes": [
    {
      "id": "lane-1-primary",
      "kind": "task",
      "role": "primary",
      "executor": "grok-cli",
      "authorization_profile": "local_readonly",
      "completion_policy": "report",
      "body": "Goal: Review the requested change. Scope: repository paths provided by the controller. Required changes: None. Report only. Exit criteria: return findings, risks, and evidence paths."
    },
    {
      "id": "lane-2-adversarial",
      "kind": "task",
      "role": "adversarial",
      "executor": "grok-cli",
      "authorization_profile": "local_readonly",
      "completion_policy": "report",
      "body": "Goal: Challenge the primary review and look for missing risks. Scope: repository paths provided by the controller. Required changes: None. Report only. Exit criteria: return disagreements, blind spots, and evidence paths."
    },
    {
      "id": "synthesis",
      "kind": "synthesis",
      "role": "synthesizer",
      "depends_on": ["lane-1-primary", "lane-2-adversarial"],
      "authorization_profile": "local_readonly",
      "completion_policy": "report",
      "synthesis_contract": "fusion_v1",
      "body": "Goal: Synthesize lane evidence into consensus, contradictions, unique insights, blind spots, and recommended_controller_action. Required changes: None. Report only."
    },
    {
      "id": "gate",
      "kind": "gate",
      "role": "controller-gate",
      "depends_on": ["synthesis"],
      "authorization_profile": "local_readonly",
      "completion_policy": "report"
    }
  ]
}
```

- [ ] **Step 5: Create example implementation manifest**

Create `docs/examples/fanout-synthesis-implementation.json` with two isolated implementation lanes and one synthesis node. Use `completion_policy: "evidence"`, `authorization_profile: "local_mutating"`, and `isolated_worktree: true` for task lanes.

- [ ] **Step 6: Run CLI tests**

Run:

```bash
PYTHONPATH=. pytest -q tests/integration/test_workflow_cli.py tests/unit/test_workflow_presets.py
```

Expected:

```text
all tests pass
```

## 11. Task 7: Add Offline Rubric Evaluation Without Online Auto-Routing

**Files:**

- Create: `agpair/workflows/rubric.py`
- Create: `tests/unit/test_workflow_rubric.py`
- Modify: `docs/usage.md`
- Modify: `docs/usage.zh-CN.md`

- [ ] **Step 1: Write rubric tests**

Create `tests/unit/test_workflow_rubric.py`:

```python
from agpair.workflows.rubric import score_panel_result


def test_score_panel_result_rewards_adoptable_synthesis_and_penalizes_rework() -> None:
    panel_result = {
        "state": "usable",
        "controller_action": "use_result",
        "metrics": {
            "lane_count": 3,
            "usable_lane_count": 2,
            "partial_lane_count": 1,
            "blocked_lane_count": 0,
            "contradiction_count": 1,
            "blind_spot_count": 2,
        },
    }
    score = score_panel_result(panel_result, controller_rework="minor")

    assert score["normalized_score"] == 82
    assert score["dimensions"]["adoptability"] == 35
    assert score["dimensions"]["coverage"] == 25
    assert score["dimensions"]["cost_control"] == 15
    assert score["dimensions"]["controller_rework"] == 7


def test_score_panel_result_penalizes_blocked_panel() -> None:
    panel_result = {
        "state": "blocked",
        "controller_action": "retry_or_switch_executor",
        "metrics": {
            "lane_count": 3,
            "usable_lane_count": 0,
            "partial_lane_count": 1,
            "blocked_lane_count": 2,
            "contradiction_count": 0,
            "blind_spot_count": 0,
        },
    }
    score = score_panel_result(panel_result, controller_rework="redone")

    assert score["normalized_score"] < 40
```

- [ ] **Step 2: Implement rubric scoring**

Create `agpair/workflows/rubric.py`:

```python
from __future__ import annotations

from typing import Any, Mapping


def score_panel_result(panel_result: Mapping[str, Any], *, controller_rework: str) -> dict[str, Any]:
    metrics = panel_result.get("metrics") if isinstance(panel_result.get("metrics"), Mapping) else {}
    lane_count = int(metrics.get("lane_count") or 0)
    usable = int(metrics.get("usable_lane_count") or 0)
    partial = int(metrics.get("partial_lane_count") or 0)
    blocked = int(metrics.get("blocked_lane_count") or 0)
    contradiction_count = int(metrics.get("contradiction_count") or 0)
    blind_spot_count = int(metrics.get("blind_spot_count") or 0)
    adoptability = 35 if panel_result.get("state") == "usable" else 20 if panel_result.get("state") == "needs_review" else 5
    coverage = min(25, usable * 10 + partial * 5 + min(contradiction_count + blind_spot_count, 5))
    cost_control = 15 if lane_count <= 3 else 10 if lane_count <= 5 else 5
    reliability = max(0, 15 - blocked * 5)
    rework_score = {"none": 10, "minor": 7, "major": 3, "redone": 0, "unknown": 5}.get(controller_rework, 5)
    total = adoptability + coverage + cost_control + reliability + rework_score
    return {
        "normalized_score": min(100, total),
        "dimensions": {
            "adoptability": adoptability,
            "coverage": coverage,
            "cost_control": cost_control,
            "reliability": reliability,
            "controller_rework": rework_score,
        },
    }
```

- [ ] **Step 3: Document rubric boundaries**

Add to `docs/usage.md` and `docs/usage.zh-CN.md`:

```markdown
### Fanout Rubric

Rubric scores are offline evaluation signals. They help tune workflow presets and executor ordering. They must not be used as online automatic routing authority. Online routing still follows controller policy, executor health, task scope, and controller judgment.
```

- [ ] **Step 4: Run rubric tests**

Run:

```bash
PYTHONPATH=. pytest -q tests/unit/test_workflow_rubric.py
```

Expected:

```text
2 passed
```

## 12. Task 8: Add Source Policy For Evaluation And Research Panels

**Files:**

- Modify: `agpair/workflows/schema.py`
- Modify: `agpair/workflows/scheduler.py`
- Modify: `tests/unit/test_workflow_presets.py`
- Modify: `docs/usage.md`
- Modify: `docs/usage.zh-CN.md`

- [ ] **Step 1: Add source policy test**

Add to `tests/unit/test_workflow_presets.py`:

```python
def test_manifest_preserves_source_policy_for_eval_leakage_control() -> None:
    manifest = build_fanout_manifest(
        name="Research eval",
        repo_path="/tmp/repo",
        controller="codex",
        mode="research",
        topic="Compare deep research systems",
        scope="docs only",
        lanes=[("grok-cli", "primary")],
        isolated_worktree=False,
    )
    manifest["source_policy"] = {
        "excluded_domains": ["example-eval-rubric.test"],
        "blocked_paths": ["docs/private-rubric.md"],
    }

    validated = validate_manifest(manifest, require_repo_path=True)

    assert validated.manifest["source_policy"]["excluded_domains"] == ["example-eval-rubric.test"]
```

- [ ] **Step 2: Inject source policy into lane bodies**

In `_node_body`, add the manifest `source_policy` to the JSON context for task and synthesis nodes:

```python
manifest_payload = _safe_json(workflow.manifest_json)
source_policy = manifest_payload.get("source_policy") if isinstance(manifest_payload, dict) else None
if isinstance(source_policy, dict):
    body += "\n\nSource policy for this workflow, JSON:\n"
    body += json.dumps(source_policy, ensure_ascii=False, sort_keys=True)
    body += "\nDo not use excluded domains, blocked paths, or benchmark rubric sources listed above.\n"
```

- [ ] **Step 3: Document source policy**

Add examples:

```json
{
  "source_policy": {
    "excluded_domains": ["internal-rubric.example"],
    "blocked_paths": ["docs/private-eval-rubric.md"]
  }
}
```

Explain that source policy is a task instruction and evidence hint, not an OS-level network sandbox.

- [ ] **Step 4: Run tests**

Run:

```bash
PYTHONPATH=. pytest -q tests/unit/test_workflow_presets.py tests/unit/test_workflow_scheduler.py
```

Expected:

```text
all tests pass
```

## 13. Task 9: Update Skills And Public Docs

**Files:**

- Modify: `skills/Codex/SKILL.md`
- Modify: `skills/Claude/SKILL.md`
- Modify: `README.md`
- Modify: `README.zh-CN.md`
- Modify: `docs/usage.md`
- Modify: `docs/usage.zh-CN.md`
- Modify: `docs/external-agent-first-decision-map.zh-CN.html`

- [ ] **Step 1: Update Codex skill fanout guidance**

Add this exact section to `skills/Codex/SKILL.md` under "Routing Budget And Fanout":

```markdown
### Fusion-Style Fanout

For high-value research, review, design, or implementation choices, prefer a fanout-synthesis workflow instead of manually starting unrelated tasks. The useful pattern is panel lanes plus synthesis plus controller gate:

```bash
agpair workflow fanout \
  --repo-path "$REPO" \
  --controller codex \
  --mode review \
  --topic "$TOPIC" \
  --scope "$SCOPE" \
  --lane grok-cli:primary \
  --lane grok-cli:adversarial \
  --lane antigravity-cli:second-opinion \
  --wait
```

Read `panel_result`, `lane_cards`, `synthesis_result`, and `evidence_path` before answering. The synthesis result is evidence, not final truth. Controller verification still decides whether to use, apply, retry, switch executor, or fall back to a native helper.
```
```

- [ ] **Step 2: Update Claude skill with controller-specific executor order**

Add the same section to `skills/Claude/SKILL.md`, but use:

```bash
agpair workflow fanout \
  --repo-path "$REPO" \
  --controller claude-code \
  --mode review \
  --topic "$TOPIC" \
  --scope "$SCOPE" \
  --lane grok-cli:primary \
  --lane grok-cli:adversarial \
  --lane codex:codex-reviewer \
  --wait
```

Keep the warning that Claude Code should not default to external `claude-code`.

- [ ] **Step 3: Update README docs**

Add a short section:

```markdown
## Fusion-Style Fanout

AGPair can run multiple external executor lanes and synthesize their evidence into one controller-readable panel result. This is inspired by OpenRouter Fusion's panel/synthesis pattern, but AGPair remains a local auditable control plane: every lane has task status, artifacts, raw logs, receipts, adoption result, and controller gate.
```

Mention:

- budget panels;
- role-distinct lanes;
- no duplicate prompts;
- synthesis cannot override hard gates;
- offline rubric, not online benchmark routing.

- [ ] **Step 4: Update decision map HTML**

In `docs/external-agent-first-decision-map.zh-CN.html`, add a small decision branch:

```text
High-value multi-angle task?
  yes -> workflow fanout -> synthesis -> gate -> controller verifies
  no  -> single strongest external lane
```

- [ ] **Step 5: Run documentation checks**

Run:

```bash
git diff --check
rg -ni "todo|tbd|Gemini CLI" README.md README.zh-CN.md docs skills \
  -g '!docs/superpowers/plans/2026-06-14-fusion-style-fanout-synthesis-v2-7.md'
rg -n "managed-restricted|isolated-bare" README.md README.zh-CN.md docs skills \
  -g '!docs/superpowers/plans/2026-06-14-fusion-style-fanout-synthesis-v2-7.md'
```

Expected:

```text
git diff --check has no output.
rg has no matches for placeholder markers or Gemini CLI.
managed-restricted and isolated-bare appear only in this plan's non-goal sentence, not in user-facing runtime docs.
```

## 14. Task 10: Add Realistic Smoke And Acceptance Flow

**Files:**

- Modify: `scripts/smoke_real_executors.py`
- Create: `tests/integration/test_workflow_fanout_smoke_contract.py`
- Modify: `docs/usage.md`
- Modify: `docs/usage.zh-CN.md`

- [ ] **Step 1: Add smoke contract test**

Create `tests/integration/test_workflow_fanout_smoke_contract.py`:

```python
from agpair.workflows.presets import build_fanout_manifest
from agpair.workflows.schema import validate_manifest


def test_real_smoke_can_generate_fanout_manifest_without_dispatch() -> None:
    manifest = build_fanout_manifest(
        name="Smoke fanout",
        repo_path="/tmp/repo",
        controller="codex",
        mode="review",
        topic="Review AGPair smoke contract",
        scope="scripts/smoke_real_executors.py",
        lanes=[("grok-cli", "primary"), ("grok-cli", "adversarial")],
        isolated_worktree=False,
    )

    validated = validate_manifest(manifest, require_repo_path=True)

    assert validated.max_parallel == 2
    assert any(node["kind"] == "synthesis" for node in validated.nodes)
    assert any(node["kind"] == "gate" for node in validated.nodes)
```

- [ ] **Step 2: Extend smoke script with fanout mode**

Add options to `scripts/smoke_real_executors.py`:

```text
--fanout
--fanout-mode review|research|implementation|test-fix
--fanout-lane executor:role
```

The script should:

1. generate a fanout manifest through `build_fanout_manifest`;
2. validate it;
3. run `agpair workflow start --wait --json`;
4. require `panel_result` to exist;
5. require each attempted lane to have either `agent_result.state` or explicit blocked evidence;
6. print `all_success=true` only when the synthesis and gate are both inspectable.

- [ ] **Step 3: Add manual smoke command**

Document:

```bash
python scripts/smoke_real_executors.py \
  --repo-path /path/to/agpair \
  --fanout \
  --fanout-mode review \
  --fanout-lane grok-cli:primary \
  --fanout-lane grok-cli:adversarial \
  --fanout-lane antigravity-cli:second-opinion
```

Expected:

```text
all_success=true
panel_result.controller_action is one of use_result, inspect_evidence, review_then_apply
```

- [ ] **Step 4: Run smoke contract tests**

Run:

```bash
PYTHONPATH=. pytest -q tests/integration/test_workflow_fanout_smoke_contract.py
```

Expected:

```text
1 passed
```

## 15. Verification Plan

Run the narrow suite after each task:

```bash
PYTHONPATH=. pytest -q tests/unit/test_workflow_synthesis.py
PYTHONPATH=. pytest -q tests/unit/test_workflow_presets.py
PYTHONPATH=. pytest -q tests/unit/test_workflow_evidence_salvage.py
PYTHONPATH=. pytest -q tests/unit/test_workflow_scheduler.py
PYTHONPATH=. pytest -q tests/integration/test_workflow_fanout_synthesis.py
PYTHONPATH=. pytest -q tests/integration/test_workflow_cli.py
PYTHONPATH=. pytest -q tests/unit/test_workflow_rubric.py
PYTHONPATH=. pytest -q tests/integration/test_workflow_fanout_smoke_contract.py
```

Run the broader suite before commit:

```bash
PYTHONPATH=. pytest -q
python -m compileall -q agpair
git diff --check
```

Run privacy and secret checks before push:

```bash
ggshield secret scan pre-commit --json
git diff --cached | rg -q '(/Users/[A-Za-z0-9._-]+|[A-Za-z0-9._%+-]+@[^[:space:]]+|sk-[A-Za-z0-9_-]{20,})' && exit 1 || true
```

Run one real fanout smoke only after unit/integration tests pass:

```bash
python scripts/smoke_real_executors.py \
  --repo-path /path/to/agpair \
  --fanout \
  --fanout-mode review \
  --fanout-lane grok-cli:primary \
  --fanout-lane grok-cli:adversarial
```

If `antigravity-cli` and `claude-code` are healthy in `agpair doctor --fresh`, run a broader smoke:

```bash
python scripts/smoke_real_executors.py \
  --repo-path /path/to/agpair \
  --fanout \
  --fanout-mode review \
  --fanout-lane grok-cli:primary \
  --fanout-lane antigravity-cli:second-opinion \
  --fanout-lane claude-code:quality-escalation
```

## 16. Explicit Non-Goals

- Do not add OpenRouter as a required dependency.
- Do not call `openrouter/fusion` from AGPair core.
- Do not replace AGPair executors with model slugs.
- Do not make online benchmark scores choose the executor order.
- Do not remove controller verification.
- Do not hide raw lane evidence behind the synthesis report.
- Do not treat a synthesis node as proof that a code diff is safe.
- Do not add per-project target requirements.
- Do not add MCP-specific routing.

## 17. Done Criteria

V2.7 is complete when:

- `agpair workflow fanout --dry-run --json` emits a valid manifest.
- `agpair workflow fanout --wait --json` can run at least a two-lane review panel with synthesis and gate nodes.
- Workflow evidence packs include `lane_cards`, `synthesis_result`, and `panel_result`.
- Synthesis output includes `consensus`, `contradictions`, `unique_insights`, `blind_spots`, and `recommended_controller_action`.
- Gate validation blocks invalid synthesis and hard safety failures.
- Stdout report salvage is visible as partial evidence and never silently counted as completed success.
- Skills and docs explain when to use single-lane, fanout, native helper, or direct controller work.
- Tests and smoke commands above pass.
- `ggshield` and privacy checks pass before any public push.
