import pathlib
import sqlite3
import json

from agpair.executors.claude_code import ClaudeCodeExecutor
from agpair.models import ContinuationCapability


def _write_ccswitch_provider(home: pathlib.Path) -> None:
    home.mkdir(parents=True)
    conn = sqlite3.connect(home / "cc-switch.db")
    conn.execute(
        "create table providers (id text, app_type text, name text, settings_config text, is_current integer)"
    )
    conn.execute(
        "insert into providers values (?, ?, ?, ?, ?)",
        (
            "kimi",
            "claude",
            "Kimi Claude Code",
            json.dumps(
                {
                    "env": {
                        "ANTHROPIC_BASE_URL": "https://api.moonshot.ai/anthropic",
                        "ANTHROPIC_AUTH_TOKEN": "test-secret",
                        "ANTHROPIC_MODEL": "kimi-k2.5",
                    }
                }
            ),
            1,
        ),
    )
    conn.commit()
    conn.close()


def test_claude_code_executor_uses_env_binary(monkeypatch) -> None:
    monkeypatch.setenv("AGPAIR_CLAUDE_CODE_CLI", "/tmp/fake-claude")

    executor = ClaudeCodeExecutor()

    assert executor.bin_path == "/tmp/fake-claude"
    assert executor.backend_id == "claude-code"
    assert executor.continuation_capability == ContinuationCapability.FRESH_RESUME_FIRST
    assert executor.safety_metadata.requires_human_interaction is False


def test_claude_code_command_is_print_json_and_managed_natural_by_default() -> None:
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
        "--debug-file",
        "/tmp/agpair/claude-code-debug.log",
        "--no-chrome",
        "--permission-mode",
        "bypassPermissions",
        "--no-session-persistence",
        "--output-format",
        "json",
        "--print",
        "Goal: edit the repo",
    ]
    assert "--bare" not in cmd
    assert "--strict-mcp-config" not in cmd
    assert "--mcp-config" not in cmd
    assert "--disable-slash-commands" not in cmd
    assert "--bare" not in cmd


def test_claude_code_ignores_removed_oauth_profile_restriction(monkeypatch) -> None:
    monkeypatch.setenv("AGPAIR_CLAUDE_CODE_OAUTH_PROFILE", "quiet")
    executor = ClaudeCodeExecutor(claude_bin="fake-claude")

    cmd = executor._build_claude_cmd(
        "Goal: inspect the repo",
        "/tmp/repo",
        pathlib.Path("/tmp/agpair"),
    )

    assert "--bare" not in cmd
    assert "--strict-mcp-config" not in cmd
    assert "--mcp-config" not in cmd
    assert "--disable-slash-commands" not in cmd
    assert cmd[:7] == [
        "env",
        "CLAUDE_CODE_MAX_RETRIES=0",
        "fake-claude",
        "--debug-file",
        "/tmp/agpair/claude-code-debug.log",
        "--no-chrome",
        "--permission-mode",
    ]
    assert cmd[cmd.index("--permission-mode") + 1] == "bypassPermissions"


def test_claude_code_settings_file_is_passed_to_api_worker(monkeypatch) -> None:
    monkeypatch.setenv("AGPAIR_CLAUDE_CODE_AUTH_MODE", "api")
    monkeypatch.setenv("AGPAIR_CLAUDE_CODE_SETTINGS", "/tmp/claude-settings.json")
    executor = ClaudeCodeExecutor(claude_bin="fake-claude")

    cmd = executor._build_claude_cmd(
        "Goal: inspect the repo",
        "/tmp/repo",
        pathlib.Path("/tmp/agpair"),
    )

    assert cmd[:7] == [
        "env",
        "CLAUDE_CODE_MAX_RETRIES=0",
        "fake-claude",
        "--settings",
        "/tmp/claude-settings.json",
        "--debug-file",
        "/tmp/agpair/claude-code-debug.log",
    ]
    assert "--bare" not in cmd
    assert "--strict-mcp-config" not in cmd
    assert "--mcp-config" not in cmd
    assert "--no-chrome" in cmd
    assert "--settings" in cmd
    assert cmd[cmd.index("--settings") + 1] == "/tmp/claude-settings.json"


def test_claude_code_ccswitch_mode_uses_provider_env_without_command_secret(monkeypatch, tmp_path) -> None:
    _write_ccswitch_provider(tmp_path / ".cc-switch")
    monkeypatch.setenv("AGPAIR_CLAUDE_CODE_AUTH_MODE", "ccswitch")
    monkeypatch.setenv("AGPAIR_CC_SWITCH_HOME", str(tmp_path / ".cc-switch"))
    executor = ClaudeCodeExecutor(claude_bin="fake-claude")

    cmd = executor._build_claude_cmd(
        "Goal: inspect the repo",
        "/tmp/repo",
        pathlib.Path("/tmp/agpair"),
    )
    env = executor._build_claude_env(
        "Goal: inspect the repo",
        "/tmp/repo",
        pathlib.Path("/tmp/agpair"),
    )

    assert cmd[:6] == [
        "env",
        "CLAUDE_CODE_MAX_RETRIES=0",
        "fake-claude",
        "--debug-file",
        "/tmp/agpair/claude-code-debug.log",
        "--no-chrome",
    ]
    assert "--bare" not in cmd
    assert "test-secret" not in " ".join(cmd)
    assert "--no-chrome" in cmd
    assert env["ANTHROPIC_API_KEY"] == "test-secret"
    assert env["ANTHROPIC_BASE_URL"] == "https://api.moonshot.ai/anthropic"
    assert env["ANTHROPIC_MODEL"] == "kimi-k2.5"


def test_claude_code_auto_uses_ccswitch_after_oauth_live_probe_failure(monkeypatch, tmp_path) -> None:
    fake_binary = tmp_path / "claude"
    live_probe_count = tmp_path / "live-probe-count"
    fake_binary.write_text(
        f"""#!/bin/sh
if [ "$1" = "auth" ] && [ "$2" = "status" ]; then
  printf '{{"loggedIn":true,"authMethod":"oauth_token"}}\\n'
  exit 0
fi
count=0
if [ -f {live_probe_count} ]; then
  count=$(cat {live_probe_count})
fi
count=$((count + 1))
printf '%s' "$count" > {live_probe_count}
if [ "$ANTHROPIC_API_KEY" = "test-secret" ]; then
  printf '{{"type":"result","subtype":"success","is_error":false}}\\n'
  exit 0
fi
printf '{{"type":"result","is_error":true,"api_error_status":401}}\\n'
exit 0
""",
        encoding="utf-8",
    )
    fake_binary.chmod(0o755)
    _write_ccswitch_provider(tmp_path / ".cc-switch")
    monkeypatch.setenv("AGPAIR_CC_SWITCH_HOME", str(tmp_path / ".cc-switch"))
    monkeypatch.delenv("AGPAIR_CLAUDE_CODE_AUTH_MODE", raising=False)
    executor = ClaudeCodeExecutor(claude_bin=str(fake_binary))

    cmd = executor._build_claude_cmd(
        "Goal: inspect the repo",
        "/tmp/repo",
        pathlib.Path("/tmp/agpair"),
    )
    env = executor._build_claude_env(
        "Goal: inspect the repo",
        "/tmp/repo",
        pathlib.Path("/tmp/agpair"),
    )

    assert cmd[:6] == [
        "env",
        "CLAUDE_CODE_MAX_RETRIES=0",
        str(fake_binary),
        "--debug-file",
        "/tmp/agpair/claude-code-debug.log",
        "--no-chrome",
    ]
    assert env["ANTHROPIC_API_KEY"] == "test-secret"
    assert live_probe_count.read_text(encoding="utf-8") == "2"
