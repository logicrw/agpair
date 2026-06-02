import pathlib

from agpair.executors.antigravity_cli import AntigravityCLIExecutor
from agpair.models import ContinuationCapability


def test_antigravity_cli_executor_uses_env_binary(monkeypatch) -> None:
    monkeypatch.setenv("AGPAIR_ANTIGRAVITY_CLI", "/tmp/fake-antigravity")

    executor = AntigravityCLIExecutor()

    assert executor.bin_path == "/tmp/fake-antigravity"
    assert executor.backend_id == "antigravity-cli"
    assert executor.continuation_capability == ContinuationCapability.FRESH_RESUME_FIRST
    assert executor.safety_metadata.requires_human_interaction is False


def test_antigravity_cli_command_is_noninteractive_and_prompt_driven() -> None:
    executor = AntigravityCLIExecutor(antigravity_bin="fake-antigravity")

    cmd = executor._build_antigravity_cmd(
        "Goal: edit the repo",
        "/tmp/repo",
        pathlib.Path("/tmp/agpair"),
    )

    assert cmd == [
        "fake-antigravity",
        "-y",
        "--output-format",
        "json",
        "-p",
        "Goal: edit the repo",
    ]
