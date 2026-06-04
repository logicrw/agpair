import pathlib

import pytest

from agpair.executors.antigravity_cli import AntigravityCLIExecutor
from agpair.models import ContinuationCapability


def test_antigravity_cli_executor_uses_env_binary(monkeypatch) -> None:
    monkeypatch.setenv("AGPAIR_ANTIGRAVITY_CLI_BIN", "/tmp/fake-agy")

    executor = AntigravityCLIExecutor()

    assert executor.bin_path == "/tmp/fake-agy"
    assert executor.backend_id == "antigravity-cli"
    assert executor.continuation_capability == ContinuationCapability.FRESH_RESUME_FIRST
    assert executor.safety_metadata.requires_human_interaction is False


def test_antigravity_cli_executor_defaults_to_agy(monkeypatch) -> None:
    monkeypatch.delenv("AGPAIR_ANTIGRAVITY_CLI_BIN", raising=False)
    monkeypatch.delenv("AGPAIR_ANTIGRAVITY_CLI", raising=False)

    executor = AntigravityCLIExecutor()

    assert executor.bin_path == "agy"


def test_antigravity_cli_command_is_agy_print_driven(monkeypatch) -> None:
    monkeypatch.delenv("AGPAIR_ANTIGRAVITY_APPROVAL_MODE", raising=False)
    monkeypatch.delenv("AGPAIR_ANTIGRAVITY_PRINT_TIMEOUT", raising=False)
    executor = AntigravityCLIExecutor(antigravity_bin="fake-agy")

    cmd = executor._build_antigravity_cmd(
        "Goal: edit the repo",
        "/tmp/repo",
        pathlib.Path("/tmp/agpair"),
    )

    assert cmd == [
        "fake-agy",
        "--dangerously-skip-permissions",
        "--add-dir",
        "/tmp/repo",
        "--print-timeout",
        "30m0s",
        "--print",
        "Goal: edit the repo",
    ]


def test_antigravity_cli_command_honors_print_timeout_env(monkeypatch) -> None:
    monkeypatch.delenv("AGPAIR_ANTIGRAVITY_APPROVAL_MODE", raising=False)
    monkeypatch.setenv("AGPAIR_ANTIGRAVITY_PRINT_TIMEOUT", "10m0s")
    executor = AntigravityCLIExecutor(antigravity_bin="fake-agy")

    cmd = executor._build_antigravity_cmd(
        "Goal: edit the repo",
        "/tmp/repo",
        pathlib.Path("/tmp/agpair"),
    )

    assert cmd[cmd.index("--print-timeout") + 1] == "10m0s"


def test_antigravity_cli_rejects_invalid_print_timeout(monkeypatch) -> None:
    monkeypatch.setenv("AGPAIR_ANTIGRAVITY_PRINT_TIMEOUT", "--bad")
    executor = AntigravityCLIExecutor(antigravity_bin="fake-agy")

    with pytest.raises(
        ValueError,
        match="Unsupported AGPAIR_ANTIGRAVITY_PRINT_TIMEOUT",
    ):
        executor._build_antigravity_cmd(
            "Goal: edit",
            "/tmp/repo",
            pathlib.Path("/tmp/agpair"),
        )


def test_antigravity_cli_default_approval_mode_is_non_escalating(monkeypatch) -> None:
    monkeypatch.setenv("AGPAIR_ANTIGRAVITY_APPROVAL_MODE", "default")
    executor = AntigravityCLIExecutor(antigravity_bin="fake-agy")

    cmd = executor._build_antigravity_cmd(
        "Goal: inspect only",
        "/tmp/repo",
        pathlib.Path("/tmp/agpair"),
    )

    assert "--dangerously-skip-permissions" not in cmd
    assert "--approval-mode" not in cmd
    assert "-y" not in cmd


def test_antigravity_cli_rejects_legacy_auto_edit_mode(monkeypatch) -> None:
    monkeypatch.setenv("AGPAIR_ANTIGRAVITY_APPROVAL_MODE", "auto_edit")
    executor = AntigravityCLIExecutor(antigravity_bin="fake-agy")

    with pytest.raises(
        ValueError,
        match="does not support AGPAIR_ANTIGRAVITY_APPROVAL_MODE=auto_edit",
    ):
        executor._build_antigravity_cmd(
            "Goal: edit",
            "/tmp/repo",
            pathlib.Path("/tmp/agpair"),
        )
