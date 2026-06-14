from agpair.workflows.rubric import evaluate_panel_result


def test_evaluate_panel_result_scores_adoptable_panel_without_routing() -> None:
    result = evaluate_panel_result(
        panel_result={
            "state": "usable",
            "usable_lane_count": 2,
            "partial_lane_count": 0,
            "blocked_lane_count": 0,
            "hard_blockers": [],
            "contradiction_count": 0,
            "blind_spot_count": 1,
        },
        synthesis_result={
            "consensus": ["Use lane result."],
            "contradictions": [],
            "unique_insights": ["One implementation shortcut."],
            "blind_spots": ["No real smoke."],
        },
    )

    assert result["score"] >= 80
    assert result["classification"] == "strong"
    assert "selected_executor" not in result
    assert "route_to" not in result


def test_evaluate_panel_result_penalizes_partial_and_blocked_lanes() -> None:
    result = evaluate_panel_result(
        panel_result={
            "state": "needs_review",
            "usable_lane_count": 1,
            "partial_lane_count": 1,
            "blocked_lane_count": 1,
            "hard_blockers": ["scope_violation"],
            "contradiction_count": 2,
            "blind_spot_count": 2,
        },
        synthesis_result={
            "consensus": [],
            "contradictions": ["A", "B"],
            "unique_insights": [],
            "blind_spots": ["C", "D"],
        },
    )

    assert result["score"] < 50
    assert result["classification"] == "weak"
    assert "scope_violation" in result["reasons"]
