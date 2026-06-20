import pathlib

from agpair.executors.grok_cli import GrokCLIExecutor
from agpair.models import ContinuationCapability


def test_grok_cli_executor_uses_env_binary(monkeypatch) -> None:
    monkeypatch.setenv("AGPAIR_GROK_CLI", "/tmp/fake-grok")

    executor = GrokCLIExecutor()

    assert executor.bin_path == "/tmp/fake-grok"
    assert executor.backend_id == "grok-cli"
    assert executor.continuation_capability == ContinuationCapability.FRESH_RESUME_FIRST
    assert executor.safety_metadata.requires_human_interaction is False


def test_grok_cli_command_is_repo_scoped_and_managed_natural_by_default() -> None:
    executor = GrokCLIExecutor(grok_bin="fake-grok")

    cmd = executor._build_grok_cmd(
        "Goal: edit the repo",
        "/tmp/repo",
        pathlib.Path("/tmp/agpair"),
    )

    assert cmd == [
        "fake-grok",
        "--cwd",
        "/tmp/repo",
        "--output-format",
        "json",
        "--always-approve",
        "--single",
        "Goal: edit the repo",
    ]
    assert "--no-memory" not in cmd
    assert "--no-subagents" not in cmd
    assert "--disable-web-search" not in cmd


def test_grok_cli_command_allows_json_fallback_and_custom_turn_budget(monkeypatch) -> None:
    monkeypatch.setenv("AGPAIR_GROK_OUTPUT_FORMAT", "json")
    monkeypatch.setenv("AGPAIR_GROK_MAX_TURNS", "3")
    executor = GrokCLIExecutor(grok_bin="fake-grok")

    cmd = executor._build_grok_cmd("Goal: inspect", "/tmp/repo", pathlib.Path("/tmp/agpair"))

    assert cmd[cmd.index("--output-format") + 1] == "json"
    assert cmd[cmd.index("--max-turns") + 1] == "3"


def test_grok_cli_rejects_unsupported_output_format(monkeypatch) -> None:
    monkeypatch.setenv("AGPAIR_GROK_OUTPUT_FORMAT", "plain")
    executor = GrokCLIExecutor(grok_bin="fake-grok")

    try:
        executor._build_grok_cmd("Goal: inspect", "/tmp/repo", pathlib.Path("/tmp/agpair"))
    except ValueError as exc:
        assert "AGPAIR_GROK_OUTPUT_FORMAT" in str(exc)
    else:
        raise AssertionError("unsupported Grok output format should be rejected")


def test_grok_cli_rejects_invalid_turn_budget(monkeypatch) -> None:
    monkeypatch.setenv("AGPAIR_GROK_MAX_TURNS", "zero")
    executor = GrokCLIExecutor(grok_bin="fake-grok")

    try:
        executor._build_grok_cmd("Goal: inspect", "/tmp/repo", pathlib.Path("/tmp/agpair"))
    except ValueError as exc:
        assert "AGPAIR_GROK_MAX_TURNS" in str(exc)
    else:
        raise AssertionError("unsupported Grok turn budget should be rejected")
