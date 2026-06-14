from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

VALID_CONTROLLER_ACTIONS: Final = frozenset({
    "use_result",
    "inspect_evidence",
    "review_then_apply",
    "retry",
    "switch_executor",
    "fall_back",
})


@dataclass(frozen=True, slots=True)
class SynthesisValidationError(Exception):
    message: str

    def __str__(self) -> str:
        return self.message


def build_lane_card(node_payload: dict[str, Any], *, role: str | None = None, executor: str | None = None) -> dict[str, Any]:
    adoption_result = _dict_value(node_payload.get("adoption_result"))
    terminal_receipt = _dict_value(node_payload.get("terminal_receipt"))
    artifact_paths = _artifact_paths(node_payload.get("artifacts"))
    agent_result = _agent_result(adoption_result, artifact_paths=artifact_paths, terminal_receipt=terminal_receipt)
    terminal_payload = _terminal_payload(terminal_receipt)
    summary = _summary_excerpt(adoption_result=adoption_result, terminal_payload=terminal_payload)
    return {
        "node_id": str(node_payload.get("node_id") or ""),
        "role": role or _optional_str(node_payload.get("role")) or str(node_payload.get("kind") or "task"),
        "executor": executor or _optional_str(node_payload.get("executor_backend")) or "",
        "task_id": _optional_str(node_payload.get("task_id")),
        "phase": str(node_payload.get("phase") or "unknown"),
        "agent_result": agent_result,
        "artifacts": artifact_paths,
        "summary_excerpt": summary,
        "changed_files": _string_list(terminal_payload.get("changed_files")),
        "scope_violations": _list_value(terminal_payload.get("scope_violations")),
        "adoptable_result": _adoptable_result(adoption_result, agent_result),
        "error": _optional_str(node_payload.get("error")),
    }


def validate_synthesis_result(raw: dict[str, Any]) -> dict[str, Any]:
    required_list_fields = ("consensus", "contradictions", "unique_insights", "blind_spots")
    workflow_id = raw.get("workflow_id")
    if not isinstance(workflow_id, str) or not workflow_id.strip():
        raise SynthesisValidationError("synthesis result requires workflow_id")
    for field in required_list_fields:
        if field not in raw:
            raise SynthesisValidationError(f"synthesis result requires {field}")
        if not _all_strings(raw[field]):
            raise SynthesisValidationError(f"synthesis result {field} must be a string array")
    action = raw.get("recommended_controller_action")
    if action not in VALID_CONTROLLER_ACTIONS:
        raise SynthesisValidationError("synthesis result requires a valid recommended_controller_action")
    return {
        "schema_version": str(raw.get("schema_version") or "1"),
        "workflow_id": workflow_id.strip(),
        "synthesis_version": str(raw.get("synthesis_version") or "1.0.0"),
        "consensus": list(raw["consensus"]),
        "contradictions": list(raw["contradictions"]),
        "unique_insights": list(raw["unique_insights"]),
        "blind_spots": list(raw["blind_spots"]),
        "recommended_controller_action": str(action),
        "summary": _optional_str(raw.get("summary")),
    }


def derive_panel_result(
    *,
    workflow_id: str,
    lane_cards: list[dict[str, Any]],
    synthesis_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    usable_count = 0
    partial_count = 0
    blocked_count = 0
    hard_blockers: list[str] = []
    soft_warnings: list[str] = []
    for lane in lane_cards:
        agent_result = _dict_value(lane.get("agent_result"))
        state = str(agent_result.get("state") or "needs_review")
        lane_blockers = _string_list(agent_result.get("hard_blockers"))
        lane_warnings = _string_list(agent_result.get("soft_warnings"))
        soft_warnings.extend(lane_warnings)
        if lane_blockers:
            blocked_count += 1
            hard_blockers.extend(lane_blockers)
        elif state == "usable":
            usable_count += 1
        elif state == "needs_review":
            partial_count += 1
        else:
            blocked_count += 1
            hard_blockers.append(f"{lane.get('node_id') or 'lane'}:{state}")
        if _list_value(lane.get("scope_violations")):
            hard_blockers.append("scope_violation")
    normalized_synthesis = synthesis_result or _empty_synthesis(workflow_id)
    controller_action = str(normalized_synthesis.get("recommended_controller_action") or "inspect_evidence")
    state = "usable"
    if hard_blockers or partial_count or blocked_count or normalized_synthesis.get("contradictions"):
        state = "needs_review"
        controller_action = "inspect_evidence"
    if not lane_cards:
        state = "blocked"
        controller_action = "fall_back"
        hard_blockers.append("no_lanes")
    return {
        "schema_version": "1",
        "workflow_id": workflow_id,
        "state": state,
        "controller_action": controller_action,
        "lane_count": len(lane_cards),
        "usable_lane_count": usable_count,
        "partial_lane_count": partial_count,
        "blocked_lane_count": blocked_count,
        "consensus_count": len(_list_value(normalized_synthesis.get("consensus"))),
        "contradiction_count": len(_list_value(normalized_synthesis.get("contradictions"))),
        "unique_insight_count": len(_list_value(normalized_synthesis.get("unique_insights"))),
        "blind_spot_count": len(_list_value(normalized_synthesis.get("blind_spots"))),
        "hard_blockers": sorted(set(hard_blockers)),
        "soft_warnings": sorted(set(soft_warnings)),
    }


def _agent_result(
    adoption_result: dict[str, Any],
    *,
    artifact_paths: dict[str, str],
    terminal_receipt: dict[str, Any],
) -> dict[str, Any]:
    existing = _dict_value(adoption_result.get("agent_result"))
    if existing:
        return {
            "state": str(existing.get("state") or "needs_review"),
            "controller_action": str(existing.get("controller_action") or "inspect_evidence"),
            "summary": _optional_str(existing.get("summary")),
            "hard_blockers": _string_list(existing.get("hard_blockers")),
            "soft_warnings": _string_list(existing.get("soft_warnings")),
        }
    warnings: list[str] = []
    if artifact_paths.get("stdout") and not terminal_receipt:
        warnings.extend(["terminal_receipt_missing", "stdout_report_salvaged"])
    return {
        "state": "needs_review",
        "controller_action": "inspect_evidence",
        "summary": None,
        "hard_blockers": [],
        "soft_warnings": warnings,
    }


def _adoptable_result(adoption_result: dict[str, Any], agent_result: dict[str, Any]) -> str:
    if "stdout_report_salvaged" in _string_list(agent_result.get("soft_warnings")):
        return "partial"
    value = adoption_result.get("adoptable_result")
    if isinstance(value, str) and value and value != "unknown":
        return value
    if agent_result.get("state") == "usable":
        return "yes"
    return "no"


def _artifact_paths(raw: Any) -> dict[str, str]:
    paths: dict[str, str] = {}
    if not isinstance(raw, list):
        return paths
    for item in raw:
        artifact = _dict_value(item)
        artifact_type = artifact.get("type")
        path = artifact.get("path")
        if isinstance(artifact_type, str) and isinstance(path, str):
            paths[artifact_type] = path
    return paths


def _summary_excerpt(*, adoption_result: dict[str, Any], terminal_payload: dict[str, Any]) -> str | None:
    report = terminal_payload.get("report")
    if isinstance(report, str) and report.strip():
        return report.strip()[:500]
    agent_result = _dict_value(adoption_result.get("agent_result"))
    summary = agent_result.get("summary")
    if isinstance(summary, str) and summary.strip():
        return summary.strip()
    return None


def _terminal_payload(terminal_receipt: dict[str, Any]) -> dict[str, Any]:
    payload = terminal_receipt.get("payload")
    if isinstance(payload, dict):
        return payload
    return terminal_receipt


def _empty_synthesis(workflow_id: str) -> dict[str, Any]:
    return {
        "schema_version": "1",
        "workflow_id": workflow_id,
        "synthesis_version": "1.0.0",
        "consensus": [],
        "contradictions": [],
        "unique_insights": [],
        "blind_spots": [],
        "recommended_controller_action": "inspect_evidence",
        "summary": None,
    }


def _dict_value(raw: Any) -> dict[str, Any]:
    return raw if isinstance(raw, dict) else {}


def _list_value(raw: Any) -> list[Any]:
    return raw if isinstance(raw, list) else []


def _string_list(raw: Any) -> list[str]:
    return [item for item in _list_value(raw) if isinstance(item, str)]


def _all_strings(raw: Any) -> bool:
    return isinstance(raw, list) and all(isinstance(item, str) for item in raw)


def _optional_str(raw: Any) -> str | None:
    return raw if isinstance(raw, str) and raw else None
