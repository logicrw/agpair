from __future__ import annotations

from typing import Any


def evaluate_panel_result(*, panel_result: dict[str, Any], synthesis_result: dict[str, Any]) -> dict[str, Any]:
    score = 100
    reasons: list[str] = []
    partial_count = _int_value(panel_result.get("partial_lane_count"))
    blocked_count = _int_value(panel_result.get("blocked_lane_count"))
    hard_blockers = _string_list(panel_result.get("hard_blockers"))
    contradictions = _list_value(synthesis_result.get("contradictions"))
    blind_spots = _list_value(synthesis_result.get("blind_spots"))
    consensus = _list_value(synthesis_result.get("consensus"))
    unique_insights = _list_value(synthesis_result.get("unique_insights"))
    if not consensus:
        score -= 20
        reasons.append("no_consensus")
    if not unique_insights:
        score -= 10
        reasons.append("no_unique_insights")
    if partial_count:
        score -= partial_count * 15
        reasons.append("partial_lanes")
    if blocked_count:
        score -= blocked_count * 25
        reasons.append("blocked_lanes")
    if hard_blockers:
        score -= len(hard_blockers) * 20
        reasons.extend(hard_blockers)
    if contradictions:
        score -= len(contradictions) * 10
        reasons.append("contradictions")
    if len(blind_spots) > 1:
        score -= (len(blind_spots) - 1) * 5
        reasons.append("blind_spots")
    final_score = max(0, min(100, score))
    return {
        "schema_version": "1",
        "score": final_score,
        "classification": _classification(final_score),
        "reasons": sorted(set(reasons)),
    }


def _classification(score: int) -> str:
    if score >= 80:
        return "strong"
    if score >= 50:
        return "mixed"
    return "weak"


def _int_value(raw: Any) -> int:
    return raw if isinstance(raw, int) else 0


def _list_value(raw: Any) -> list[Any]:
    return raw if isinstance(raw, list) else []


def _string_list(raw: Any) -> list[str]:
    return [item for item in _list_value(raw) if isinstance(item, str)]
