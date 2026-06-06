import pathlib

from agpair.executors.claude_code import ClaudeCodeExecutor
from agpair.models import ContinuationCapability


def test_claude_code_executor_uses_env_binary(monkeypatch) -> None:
    monkeypatch.setenv("AGPAIR_CLAUDE_CODE_CLI", "/tmp/fake-claude")

    executor = ClaudeCodeExecutor()

    assert executor.bin_path == "/tmp/fake-claude"
    assert executor.backend_id == "claude-code"
    assert executor.continuation_capability == ContinuationCapability.FRESH_RESUME_FIRST
    assert executor.safety_metadata.requires_human_interaction is False


def test_claude_code_command_is_print_json_and_permission_scoped() -> None:
    executor = ClaudeCodeExecutor(claude_bin="fake-claude")

    cmd = executor._build_claude_cmd(
        "Goal: edit the repo",
        "/tmp/repo",
        pathlib.Path("/tmp/agpair"),
    )

    assert cmd == [
        "env",
        "CLAUDE_CODE_MAX_RETRIES=0",
        "fake-claude",
        "--strict-mcp-config",
        "--mcp-config",
        '{"mcpServers":{}}',
        "--disable-slash-commands",
        "--no-chrome",
        "--permission-mode",
        "bypassPermissions",
        "--no-session-persistence",
        "--output-format",
        "json",
        "--print",
        "Goal: edit the repo",
    ]


def test_claude_code_oauth_profile_can_be_natural(monkeypatch) -> None:
    monkeypatch.setenv("AGPAIR_CLAUDE_CODE_OAUTH_PROFILE", "natural")
    executor = ClaudeCodeExecutor(claude_bin="fake-claude")

    cmd = executor._build_claude_cmd(
        "Goal: inspect the repo",
        "/tmp/repo",
        pathlib.Path("/tmp/agpair"),
    )

    assert "--bare" not in cmd
    assert "--strict-mcp-config" not in cmd
    assert "--disable-slash-commands" not in cmd
    assert cmd[:5] == ["env", "CLAUDE_CODE_MAX_RETRIES=0", "fake-claude", "--permission-mode", "bypassPermissions"]


def test_claude_code_legacy_bare_mode_can_be_explicitly_enabled(monkeypatch) -> None:
    monkeypatch.setenv("AGPAIR_CLAUDE_CODE_BARE", "1")
    executor = ClaudeCodeExecutor(claude_bin="fake-claude")

    cmd = executor._build_claude_cmd(
        "Goal: inspect the repo",
        "/tmp/repo",
        pathlib.Path("/tmp/agpair"),
    )

    assert cmd[:6] == ["env", "CLAUDE_CODE_MAX_RETRIES=0", "fake-claude", "--bare", "--permission-mode", "bypassPermissions"]


def test_claude_code_settings_file_is_passed_to_api_worker(monkeypatch) -> None:
    monkeypatch.setenv("AGPAIR_CLAUDE_CODE_AUTH_MODE", "api")
    monkeypatch.setenv("AGPAIR_CLAUDE_CODE_SETTINGS", "/tmp/claude-settings.json")
    executor = ClaudeCodeExecutor(claude_bin="fake-claude")

    cmd = executor._build_claude_cmd(
        "Goal: inspect the repo",
        "/tmp/repo",
        pathlib.Path("/tmp/agpair"),
    )

    assert cmd[:6] == ["env", "CLAUDE_CODE_MAX_RETRIES=0", "fake-claude", "--bare", "--settings", "/tmp/claude-settings.json"]
