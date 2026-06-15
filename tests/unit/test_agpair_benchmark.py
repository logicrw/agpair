from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "agpair_benchmark.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("agpair_benchmark", SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class _PolicyDecision:
    eligible_executors = ("grok-cli", "antigravity-cli", "claude-code")
    suppressed_executors = ("codex",)


def test_select_executors_keeps_grok_and_antigravity_as_peer_lanes(monkeypatch) -> None:
    module = _load_module()
    monkeypatch.setattr(module, "resolve_controller_policy", lambda **kwargs: _PolicyDecision())

    selection = module.select_executors(controller="codex")

    assert selection.mode == "controller_policy"
    assert selection.executors[:2] == ["antigravity-cli", "grok-cli"]
    assert selection.executors == ["antigravity-cli", "grok-cli", "claude-code"]
    assert selection.policy_expected_executors == ["grok-cli", "antigravity-cli", "claude-code"]
    assert selection.policy_suppressed_executors == ["codex"]


def test_select_executors_all_registered_suppresses_controller_self(monkeypatch) -> None:
    module = _load_module()
    monkeypatch.setattr(
        module,
        "registered_executor_ids",
        lambda: ("antigravity-cli", "grok-cli", "claude-code", "codex"),
    )

    selection = module.select_executors(controller="claude-code", all_registered=True)

    assert selection.executors == ["antigravity-cli", "grok-cli", "codex"]
    assert "claude-code" not in selection.executors


def test_select_executors_explicit_normalizes_and_peer_sorts() -> None:
    module = _load_module()

    selection = module.select_executors(
        controller="codex",
        requested="grok,antigravity_cli",
    )

    assert selection.mode == "explicit"
    assert selection.executors == ["antigravity-cli", "grok-cli"]


def test_summarize_results_groups_executor_metrics() -> None:
    module = _load_module()

    summary = module.summarize_results([
        {
            "executor_id": "grok-cli",
            "success": True,
            "duration_seconds": 10.0,
            "time_to_first_useful_signal_seconds": 4.0,
        },
        {
            "executor_id": "grok-cli",
            "success": False,
            "duration_seconds": 20.0,
        },
        {
            "executor_id": "antigravity-cli",
            "success": True,
            "duration_seconds": 12.0,
            "time_to_first_useful_signal_seconds": 6.0,
        },
    ])

    assert summary["runs"] == 3
    assert summary["successes"] == 2
    assert summary["all_success"] is False
    assert summary["per_executor"]["grok-cli"]["success_rate"] == 0.5
    assert summary["per_executor"]["grok-cli"]["avg_duration_seconds"] == 15.0
    assert summary["per_executor"]["grok-cli"]["avg_time_to_first_useful_signal_seconds"] == 4.0
    assert summary["per_executor"]["antigravity-cli"]["success_rate"] == 1.0
