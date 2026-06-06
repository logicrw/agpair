from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from agpair.executors import get_executor, is_local_cli_backend
from agpair.executors.policy import EXECUTOR_SPECS, executor_health_snapshot, resolve_controller_policy
from agpair.executors.registry import active_executor_ids, executor_start_blocker
from agpair.executors.routing import is_supported_executor, supported_executor_ids, validate_supported_executor


@pytest.mark.parametrize("status", ["disabled", "deprecated", "removed"])
def test_non_active_lifecycle_states_are_not_eligible_for_new_dispatch(monkeypatch, status: str) -> None:
    original = EXECUTOR_SPECS["grok-cli"]
    monkeypatch.setitem(
        EXECUTOR_SPECS,
        "grok-cli",
        replace(original, lifecycle_status=status, replacement_executor="antigravity-cli"),
    )

    assert "grok-cli" not in active_executor_ids()
    assert "grok-cli" not in supported_executor_ids()
    assert not is_supported_executor("grok-cli")
    assert not is_local_cli_backend("grok-cli")
    assert get_executor("grok-cli") is None
    with pytest.raises(ValueError, match="active for new AGPair work"):
        validate_supported_executor("grok-cli")

    decision = resolve_controller_policy(requested_executor="grok-cli")
    assert decision.rejected_executor == "grok-cli"
    assert decision.selected_executor == "antigravity-cli"
    assert status in decision.reasons[-1]

    blocker = executor_start_blocker("grok-cli")
    assert blocker is not None
    assert blocker["blocker_type"] == f"executor_{status}"
    assert blocker["replacement_executor"] == "antigravity-cli"

    health = executor_health_snapshot()["grok-cli"]
    assert health["available"] is False
    assert health["lifecycle_status"] == status
    assert health["replacement_executor"] == "antigravity-cli"
    assert health["last_failure_type"] == f"executor_{status}"


def test_direct_selection_of_unavailable_active_executor_reports_precise_blocker(monkeypatch, tmp_path) -> None:
    missing_binary = tmp_path / "missing-grok"
    monkeypatch.setenv("AGPAIR_GROK_CLI_BIN", str(missing_binary))

    decision = resolve_controller_policy(
        requested_executor="grok-cli",
        require_available=True,
    )

    assert decision.rejected_executor == "grok-cli"
    assert decision.selected_executor in {"antigravity-cli", "claude-code", "codex", None}
    assert "requested executor grok-cli is unavailable" in decision.reasons[-1]
    assert "AGPAIR_GROK_CLI_BIN" in decision.reasons[-1]

    blocker = executor_start_blocker("grok-cli", require_available=True)
    assert blocker is not None
    assert blocker["blocker_type"] == "executor_unavailable"
    assert "AGPAIR_GROK_CLI_BIN" in blocker["reason"]


def test_launch_probe_failure_marks_executor_unavailable(monkeypatch, tmp_path: Path) -> None:
    fake_binary = tmp_path / "grok"
    fake_binary.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake_binary.chmod(0o755)
    monkeypatch.setenv("AGPAIR_GROK_CLI_BIN", str(fake_binary))
    monkeypatch.setattr(
        "agpair.executors.policy._launch_probe",
        lambda binary_path, spec: (False, "probe failed"),
    )

    health = executor_health_snapshot(run_launch_probe=True)["grok-cli"]

    assert health["available"] is False
    assert health["launch_clean"] is False
    assert health["last_failure_type"] == "launch_probe_failed"
    assert health["last_error_excerpt"] == "probe failed"

    decision = resolve_controller_policy(
        requested_executor="grok-cli",
        require_available=True,
    )
    assert decision.rejected_executor == "grok-cli"
    assert "probe failed" in decision.reasons[-1]

    blocker = executor_start_blocker("grok-cli", require_available=True)
    assert blocker is not None
    assert blocker["blocker_type"] == "launch_probe_failed"
    assert blocker["reason"] == "probe failed"


def test_isolated_auth_requirement_marks_executor_unavailable(monkeypatch, tmp_path: Path) -> None:
    fake_binary = tmp_path / "claude"
    fake_binary.write_text(
        '#!/bin/sh\nif [ "$1" = "auth" ] && [ "$2" = "status" ]; then '
        'printf "{\\"loggedIn\\":false,\\"authMethod\\":null}\\n"; exit 0; fi\nexit 0\n',
        encoding="utf-8",
    )
    fake_binary.chmod(0o755)
    monkeypatch.setenv("AGPAIR_CLAUDE_CODE_BIN", str(fake_binary))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("AGPAIR_CLAUDE_CODE_SETTINGS", raising=False)
    monkeypatch.delenv("AGPAIR_CLAUDE_CODE_BARE", raising=False)
    monkeypatch.delenv("AGPAIR_CLAUDE_CODE_AUTH_MODE", raising=False)

    health = executor_health_snapshot()["claude-code"]

    assert health["available"] is False
    assert health["binary_available"] is True
    assert health["isolation_auth_satisfied"] is False
    assert health["last_failure_type"] == "executor_auth_required"
    assert "claude auth login" in health["last_error_excerpt"]
    assert "OAuth/subscription" in health["last_error_excerpt"]

    decision = resolve_controller_policy(
        requested_executor="claude-code",
        require_available=True,
    )
    assert decision.rejected_executor == "claude-code"
    assert "claude auth login" in decision.reasons[-1]

    blocker = executor_start_blocker("claude-code", require_available=True)
    assert blocker is not None
    assert blocker["blocker_type"] == "executor_auth_required"
    assert "claude auth login" in blocker["reason"]


def test_claude_oauth_subscription_auth_satisfies_default_worker(monkeypatch, tmp_path: Path) -> None:
    fake_binary = tmp_path / "claude"
    fake_binary.write_text(
        '#!/bin/sh\nif [ "$1" = "auth" ] && [ "$2" = "status" ]; then '
        'printf "{\\"loggedIn\\":true,\\"authMethod\\":\\"oauth_token\\",\\"apiProvider\\":\\"firstParty\\"}\\n"; exit 0; fi\nexit 0\n',
        encoding="utf-8",
    )
    fake_binary.chmod(0o755)
    monkeypatch.setenv("AGPAIR_CLAUDE_CODE_BIN", str(fake_binary))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("AGPAIR_CLAUDE_CODE_SETTINGS", raising=False)
    monkeypatch.delenv("AGPAIR_CLAUDE_CODE_BARE", raising=False)
    monkeypatch.delenv("AGPAIR_CLAUDE_CODE_AUTH_MODE", raising=False)

    health = executor_health_snapshot()["claude-code"]

    assert health["available"] is True
    assert health["isolation_auth_satisfied"] is True
    assert health["last_failure_type"] is None
    assert health["auth_mode"] == "oauth"


def test_claude_oauth_live_probe_detects_invalid_auth(monkeypatch, tmp_path: Path) -> None:
    fake_binary = tmp_path / "claude"
    fake_binary.write_text(
        '#!/bin/sh\nif [ "$1" = "auth" ] && [ "$2" = "status" ]; then '
        'printf "{\\"loggedIn\\":true,\\"authMethod\\":\\"oauth_token\\"}\\n"; exit 0; fi\n'
        'printf "{\\"type\\":\\"result\\",\\"is_error\\":true,\\"api_error_status\\":401}\\n"\nexit 0\n',
        encoding="utf-8",
    )
    fake_binary.chmod(0o755)
    monkeypatch.setenv("AGPAIR_CLAUDE_CODE_BIN", str(fake_binary))
    monkeypatch.delenv("AGPAIR_CLAUDE_CODE_AUTH_MODE", raising=False)

    health = executor_health_snapshot(run_launch_probe=True)["claude-code"]

    assert health["available"] is False
    assert health["isolation_auth_satisfied"] is False
    assert health["last_failure_type"] == "executor_auth_required"
    assert "Invalid Authentication" in health["last_error_excerpt"]


def test_claude_oauth_live_probe_accepts_success(monkeypatch, tmp_path: Path) -> None:
    fake_binary = tmp_path / "claude"
    fake_binary.write_text(
        '#!/bin/sh\nif [ "$1" = "auth" ] && [ "$2" = "status" ]; then '
        'printf "{\\"loggedIn\\":true,\\"authMethod\\":\\"oauth_token\\"}\\n"; exit 0; fi\n'
        'printf "{\\"type\\":\\"result\\",\\"subtype\\":\\"success\\",\\"is_error\\":false}\\n"\nexit 0\n',
        encoding="utf-8",
    )
    fake_binary.chmod(0o755)
    monkeypatch.setenv("AGPAIR_CLAUDE_CODE_BIN", str(fake_binary))
    monkeypatch.delenv("AGPAIR_CLAUDE_CODE_AUTH_MODE", raising=False)

    health = executor_health_snapshot(run_launch_probe=True)["claude-code"]

    assert health["available"] is True
    assert health["isolation_auth_satisfied"] is True
    assert health["last_failure_type"] is None


def test_isolated_auth_requirement_can_be_satisfied_by_settings(monkeypatch, tmp_path: Path) -> None:
    fake_binary = tmp_path / "claude"
    fake_binary.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake_binary.chmod(0o755)
    settings_path = tmp_path / "settings.json"
    settings_path.write_text('{"apiKeyHelper":"op read op://Private/anthropic/api-key"}', encoding="utf-8")
    monkeypatch.setenv("AGPAIR_CLAUDE_CODE_BIN", str(fake_binary))
    monkeypatch.setenv("AGPAIR_CLAUDE_CODE_AUTH_MODE", "api")
    monkeypatch.setenv("AGPAIR_CLAUDE_CODE_SETTINGS", str(settings_path))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    health = executor_health_snapshot()["claude-code"]

    assert health["available"] is True
    assert health["isolation_auth_satisfied"] is True
    assert health["last_failure_type"] is None


def test_claude_missing_worker_settings_file_marks_executor_unavailable(monkeypatch, tmp_path: Path) -> None:
    fake_binary = tmp_path / "claude"
    fake_binary.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake_binary.chmod(0o755)
    monkeypatch.setenv("AGPAIR_CLAUDE_CODE_BIN", str(fake_binary))
    monkeypatch.setenv("AGPAIR_CLAUDE_CODE_AUTH_MODE", "api")
    monkeypatch.setenv("AGPAIR_CLAUDE_CODE_SETTINGS", str(tmp_path / "missing.json"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.delenv("AGPAIR_CLAUDE_CODE_BARE", raising=False)

    health = executor_health_snapshot()["claude-code"]

    assert health["available"] is False
    assert health["isolation_auth_satisfied"] is False
    assert health["last_failure_type"] == "executor_auth_required"
    assert "missing file" in health["last_error_excerpt"]


def test_claude_default_worker_settings_requires_api_key(monkeypatch, tmp_path: Path) -> None:
    fake_binary = tmp_path / "claude"
    fake_binary.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake_binary.chmod(0o755)
    settings_path = tmp_path / "claude-worker-settings.json"
    settings_path.write_text('{"apiKeyHelper":"printenv ANTHROPIC_API_KEY"}', encoding="utf-8")
    monkeypatch.setenv("AGPAIR_CLAUDE_CODE_BIN", str(fake_binary))
    monkeypatch.setenv("AGPAIR_CLAUDE_CODE_AUTH_MODE", "api")
    monkeypatch.setenv("AGPAIR_CLAUDE_CODE_SETTINGS", str(settings_path))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("AGPAIR_CLAUDE_CODE_BARE", raising=False)

    health = executor_health_snapshot()["claude-code"]

    assert health["available"] is False
    assert health["isolation_auth_satisfied"] is False
    assert health["last_failure_type"] == "executor_auth_required"
    assert "ANTHROPIC_API_KEY is empty" in health["last_error_excerpt"]
