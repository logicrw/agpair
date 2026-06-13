from __future__ import annotations

import json

import pytest

from agpair.config import AppPaths
from agpair.executors.config import ExecutorPolicyManager, ExecutorPolicyOverlay
from agpair.executors.policy import EXECUTOR_SPECS, resolve_controller_policy


def test_codex_can_disable_external_claude_code_without_affecting_generic_policy() -> None:
    overlay = ExecutorPolicyOverlay.from_dict(
        {
            "version": 1,
            "controllers": {"codex": {"disabled": ["claude-code"]}},
        }
    )

    codex = resolve_controller_policy(controller="codex", overlay=overlay)
    assert "claude-code" not in codex.eligible_executors
    assert any("executor_disabled_by_policy" in reason for reason in codex.reasons)

    generic = resolve_controller_policy(controller="generic", overlay=overlay)
    assert "claude-code" in generic.eligible_executors


def test_global_disable_affects_every_controller() -> None:
    overlay = ExecutorPolicyOverlay.from_dict(
        {
            "version": 1,
            "global": {"disabled": ["grok-cli"]},
        }
    )

    for controller in ("generic", "codex", "claude-code"):
        decision = resolve_controller_policy(controller=controller, overlay=overlay)
        assert "grok-cli" not in decision.eligible_executors
        assert any("grok-cli" in reason and "executor_disabled_by_policy" in reason for reason in decision.reasons)


def test_direct_request_for_policy_disabled_executor_fails_fast() -> None:
    overlay = ExecutorPolicyOverlay.from_dict(
        {
            "version": 1,
            "controllers": {"codex": {"disabled": ["claude-code"]}},
        }
    )

    decision = resolve_controller_policy(
        controller="codex",
        requested_executor="claude-code",
        overlay=overlay,
    )

    assert decision.selected_executor is None
    assert decision.rejected_executor == "claude-code"
    assert decision.eligible_executors == ()
    assert any("executor_disabled_by_policy" in reason for reason in decision.reasons)


def test_controller_priority_overlay_reorders_without_changing_static_specs() -> None:
    overlay = ExecutorPolicyOverlay.from_dict(
        {
            "version": 1,
            "controllers": {
                "codex": {"priority": ["grok-cli", "antigravity-cli", "claude-code"]},
            },
        }
    )

    decision = resolve_controller_policy(controller="codex", overlay=overlay)

    assert decision.eligible_executors[:3] == ("grok-cli", "antigravity-cli", "claude-code")
    assert EXECUTOR_SPECS["antigravity-cli"].default_priority == 20
    assert EXECUTOR_SPECS["grok-cli"].default_priority == 10


def test_self_suppression_is_independent_from_runtime_disable() -> None:
    overlay = ExecutorPolicyOverlay.from_dict(
        {
            "version": 1,
            "controllers": {"codex": {"disabled": ["grok-cli"]}},
        }
    )

    decision = resolve_controller_policy(controller="codex", overlay=overlay)
    assert "codex" in decision.suppressed_executors
    assert "grok-cli" not in decision.suppressed_executors
    assert "grok-cli" not in decision.eligible_executors

    diagnostic = resolve_controller_policy(controller="codex", overlay=overlay, allow_self_executor=True)
    assert diagnostic.suppressed_executors == ()
    assert "codex" in diagnostic.eligible_executors
    assert "grok-cli" not in diagnostic.eligible_executors


def test_overlay_rejects_unknown_and_removed_executor_ids() -> None:
    with pytest.raises(ValueError, match="gemini is no longer supported"):
        ExecutorPolicyOverlay.from_dict({"global": {"disabled": ["gemini-cli"]}})

    with pytest.raises(ValueError, match="executor must be one of"):
        ExecutorPolicyOverlay.from_dict({"global": {"disabled": ["not-real"]}})


def test_overlay_ignores_removed_startup_profile_section() -> None:
    overlay = ExecutorPolicyOverlay.from_dict(
        {
            "version": 1,
            "controllers": {"codex": {"startup_profile": {"grok-cli": "natural"}}},
        }
    )

    decision = resolve_controller_policy(controller="codex", requested_executor="grok-cli", overlay=overlay)
    assert decision.selected_executor == "grok-cli"
    assert "startup_profile" not in overlay.to_dict().get("controllers", {}).get("codex", {})


def test_policy_manager_round_trips_controller_overlay(tmp_path) -> None:
    manager = ExecutorPolicyManager(tmp_path / "executors.json")

    manager.disable("claude-code", controller="codex")
    manager.set_priority(["grok-cli", "antigravity-cli"], controller="codex")

    payload = json.loads((tmp_path / "executors.json").read_text(encoding="utf-8"))
    assert payload["version"] == 1
    assert payload["controllers"]["codex"]["disabled"] == ["claude-code"]
    assert payload["controllers"]["codex"]["priority"] == ["grok-cli", "antigravity-cli"]

    overlay = manager.read()
    assert overlay.disabled_for("codex") == ("claude-code",)
    assert overlay.priority_for("codex") == ("grok-cli", "antigravity-cli")

    manager.enable("claude-code", controller="codex")
    manager.reset(controller="codex")
    assert manager.read().disabled_for("codex") == ()


def test_app_paths_include_executor_policy_path(tmp_path) -> None:
    paths = AppPaths.from_root(tmp_path / ".agpair")
    assert paths.executor_policy_path == paths.root / "executors.json"


def test_policy_cli_manages_controller_overlay(tmp_path, monkeypatch) -> None:
    from typer.testing import CliRunner

    from agpair.cli.app import app

    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    runner = CliRunner()

    disabled = runner.invoke(app, ["policy", "disable", "claude-code", "--controller", "codex", "--json"])
    assert disabled.exit_code == 0, disabled.stdout
    payload = json.loads(disabled.stdout)
    assert "claude-code" not in payload["controller_policy"]["eligible_executors"]

    enabled = runner.invoke(app, ["policy", "enable", "claude-code", "--controller", "codex", "--json"])
    assert enabled.exit_code == 0, enabled.stdout
    payload = json.loads(enabled.stdout)
    assert "claude-code" in payload["controller_policy"]["eligible_executors"]

    removed = runner.invoke(app, ["policy", "startup-profile", "grok-cli", "fast", "--controller", "codex"])
    assert removed.exit_code != 0
    assert "No such command" in removed.output
