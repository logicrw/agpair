from __future__ import annotations

from agpair.executors import get_executor
import agpair.executors.policy as policy
from agpair.executors.policy import (
    ENVIRONMENT_MODES,
    EXECUTOR_SPECS,
    MCP_POLICIES,
    SKILL_POLICIES,
    executor_health_snapshot,
    resolve_controller_policy,
)
from agpair.executors.registry import executor_profile, registered_executor_ids


def test_every_registered_executor_has_required_profile_fields() -> None:
    required_profile_fields = {
        "executor_id",
        "display_name",
        "binary_name",
        "env_var",
        "env_aliases",
        "default_binary",
        "default_priority",
        "default_authorization_profile",
        "supported_completion_policies",
        "receipt_capable",
        "controller_suppression",
        "default_environment_mode",
        "default_skill_policy",
        "default_mcp_policy",
        "isolation_profile",
        "lifecycle_status",
        "replacement_executor",
        "launch_probe",
    }
    required_isolation_fields = {
        "supports_isolated_config_home",
        "supports_turn_budget",
        "supports_streaming_json",
        "default_output_mode",
        "noninteractive_flags",
        "isolated_auth_env_vars",
        "isolation_disable_env_var",
    }
    required_health_fields = {
        "binary_name",
        "binary_available",
        "launch_clean",
        "receipt_capable",
        "lifecycle_status",
        "replacement_executor",
        "last_failure_type",
        "isolation_auth_satisfied",
        "isolation_profile",
    }

    health = executor_health_snapshot()
    assert registered_executor_ids() == ("antigravity-cli", "grok-cli", "claude-code", "codex")
    for executor_id in registered_executor_ids():
        profile = executor_profile(executor_id)
        assert required_profile_fields <= set(profile)
        assert required_isolation_fields <= set(profile["isolation_profile"])
        assert required_health_fields <= set(health[executor_id])
        assert profile["executor_id"] == executor_id
        assert profile["lifecycle_status"] == "active"
        assert profile["receipt_capable"]
        assert "auto" in profile["supported_completion_policies"]
        assert profile["default_environment_mode"] in ENVIRONMENT_MODES
        assert profile["default_skill_policy"] in SKILL_POLICIES
        assert profile["default_mcp_policy"] in MCP_POLICIES
        assert "startup_profiles" not in profile
        assert "default_startup_profile" not in profile


def test_profile_noninteractive_flags_match_adapter_command(monkeypatch, tmp_path) -> None:
    """Profiles are the shared contract; adapters must not silently diverge."""
    for env_var in (
        "AGPAIR_ANTIGRAVITY_APPROVAL_MODE",
        "AGPAIR_ANTIGRAVITY_PRINT_TIMEOUT",
        "AGPAIR_GROK_OUTPUT_FORMAT",
        "AGPAIR_GROK_MAX_TURNS",
        "AGPAIR_CLAUDE_CODE_PERMISSION_MODE",
        "AGPAIR_CODEX_APPROVAL_MODE",
    ):
        monkeypatch.delenv(env_var, raising=False)

    repo_path = tmp_path / "repo"
    repo_path.mkdir()

    for executor_id in registered_executor_ids():
        spec = EXECUTOR_SPECS[executor_id]
        monkeypatch.setenv(spec.env_var, f"fake-{executor_id}")
        executor = get_executor(executor_id)
        assert executor is not None
        build_cmd = getattr(executor, "_build_cmd", None)
        assert callable(build_cmd)

        cmd = build_cmd("Goal: inspect the repo", str(repo_path), tmp_path / executor_id)

        for flag in spec.isolation_profile["noninteractive_flags"]:
            assert flag in cmd, f"{executor_id} profile flag {flag!r} missing from adapter command"


def test_executor_environment_defaults_match_cross_controller_routing_policy() -> None:
    assert EXECUTOR_SPECS["antigravity-cli"].default_environment_mode == "managed-natural"
    assert EXECUTOR_SPECS["grok-cli"].default_environment_mode == "managed-natural"
    assert EXECUTOR_SPECS["claude-code"].default_environment_mode == "managed-natural"
    assert EXECUTOR_SPECS["codex"].default_environment_mode == "managed-natural"
    for spec in EXECUTOR_SPECS.values():
        assert spec.default_skill_policy == "inherit"
        assert spec.default_mcp_policy == "inherit"


def test_controller_suppression_is_profile_driven_for_codex_and_claude_code() -> None:
    codex_policy = resolve_controller_policy(controller="codex")

    assert codex_policy.suppressed_executors == ("codex",)
    assert codex_policy.eligible_executors == ("grok-cli", "antigravity-cli", "claude-code")

    claude_policy = resolve_controller_policy(controller="claude-code")

    assert claude_policy.suppressed_executors == ("claude-code",)
    assert claude_policy.eligible_executors == ("grok-cli", "antigravity-cli", "codex")


def test_diagnostic_policy_can_include_all_registered_executors_when_self_allowed() -> None:
    decision = resolve_controller_policy(controller="codex", allow_self_executor=True)

    assert decision.suppressed_executors == ()
    assert decision.eligible_executors == ("grok-cli", "antigravity-cli", "claude-code", "codex")


def test_requested_executor_availability_probe_is_targeted(monkeypatch, tmp_path) -> None:
    fake_grok = tmp_path / "grok"
    fake_grok.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake_grok.chmod(0o755)
    monkeypatch.setenv("AGPAIR_GROK_CLI_BIN", str(fake_grok))

    def fail_if_claude_is_probed(*args, **kwargs):
        raise AssertionError("requested grok-cli preflight must not probe claude-code auth")

    monkeypatch.setattr(policy, "resolve_claude_auth", fail_if_claude_is_probed)

    decision = resolve_controller_policy(
        controller="codex",
        requested_executor="grok-cli",
        require_available=True,
    )

    assert decision.rejected_executor is None
    assert decision.selected_executor == "grok-cli"
    assert decision.eligible_executors == ("grok-cli",)
