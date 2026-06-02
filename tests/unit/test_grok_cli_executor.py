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


def test_grok_cli_command_is_repo_scoped_and_noninteractive() -> None:
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
