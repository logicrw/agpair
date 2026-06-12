from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sqlite3
import time

import pytest

from agpair.executors.claude_auth import DEFAULT_LIVE_PROBE_TIMEOUT_SECONDS, _live_probe_timeout, _run_probe
from agpair.executors import get_executor, is_local_cli_backend
from agpair.executors.policy import EXECUTOR_SPECS, executor_health_snapshot, resolve_controller_policy
from agpair.executors.registry import active_executor_ids, executor_start_blocker
from agpair.executors.routing import is_supported_executor, supported_executor_ids, validate_supported_executor


def _write_ccswitch_provider(home: Path) -> None:
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


def _process_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_claude_live_probe_timeout_default_is_realistic(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGPAIR_CLAUDE_CODE_LIVE_PROBE_TIMEOUT_SECONDS", raising=False)
    assert _live_probe_timeout() == DEFAULT_LIVE_PROBE_TIMEOUT_SECONDS
    assert _live_probe_timeout() >= 30

    monkeypatch.setenv("AGPAIR_CLAUDE_CODE_LIVE_PROBE_TIMEOUT_SECONDS", "12.5")
    assert _live_probe_timeout() == 12.5

    monkeypatch.setenv("AGPAIR_CLAUDE_CODE_LIVE_PROBE_TIMEOUT_SECONDS", "bad")
    assert _live_probe_timeout() == DEFAULT_LIVE_PROBE_TIMEOUT_SECONDS


def test_claude_probe_uses_internal_env_and_neutral_cwd(monkeypatch, tmp_path: Path) -> None:
    probe_cwd = tmp_path / "neutral"
    probe_cwd.mkdir()
    fake_binary = tmp_path / "claude"
    fake_binary.write_text(
        """#!/usr/bin/env python3
import json
import os

print(json.dumps({
    "cwd": os.getcwd(),
    "role": os.environ.get("AGPAIR_INTERNAL_ROLE"),
    "suppress_hooks": os.environ.get("AGPAIR_SUPPRESS_CLIENT_HOOKS"),
    "noninteractive": os.environ.get("AGPAIR_NONINTERACTIVE"),
    "ci": os.environ.get("CI"),
}))
""",
        encoding="utf-8",
    )
    fake_binary.chmod(0o755)
    monkeypatch.setenv("AGPAIR_CLAUDE_CODE_PROBE_CWD", str(probe_cwd))

    result = _run_probe(
        str(fake_binary),
        ["--print", "probe"],
        env={"PATH": os.environ["PATH"]},
        timeout_seconds=5.0,
    )

    payload = json.loads(result.stdout)
    assert payload["cwd"] == str(probe_cwd)
    assert payload["role"] == "probe"
    assert payload["suppress_hooks"] == "1"
    assert payload["noninteractive"] == "1"
    assert payload["ci"] == "1"


def test_claude_probe_timeout_kills_child_process_group(tmp_path: Path) -> None:
    pid_file = tmp_path / "child.pid"
    fake_binary = tmp_path / "claude"
    fake_binary.write_text(
        f"""#!/usr/bin/env python3
import pathlib
import subprocess
import time

child = subprocess.Popen(["sleep", "30"])
pathlib.Path({str(pid_file)!r}).write_text(str(child.pid), encoding="utf-8")
time.sleep(30)
""",
        encoding="utf-8",
    )
    fake_binary.chmod(0o755)

    with pytest.raises(subprocess.TimeoutExpired):
        _run_probe(str(fake_binary), ["--print", "probe"], timeout_seconds=1.0)

    child_pid = int(pid_file.read_text(encoding="utf-8").strip())
    for _ in range(20):
        if not _process_exists(child_pid):
            break
        time.sleep(0.05)

    assert not _process_exists(child_pid)


def test_claude_probe_timeout_kills_descendant_in_new_process_group(tmp_path: Path) -> None:
    pid_file = tmp_path / "child.pid"
    fake_binary = tmp_path / "claude"
    fake_binary.write_text(
        f"""#!/usr/bin/env python3
import os
import pathlib
import subprocess
import time

child = subprocess.Popen(["sleep", "30"], preexec_fn=os.setsid)
pathlib.Path({str(pid_file)!r}).write_text(str(child.pid), encoding="utf-8")
time.sleep(30)
""",
        encoding="utf-8",
    )
    fake_binary.chmod(0o755)

    with pytest.raises(subprocess.TimeoutExpired):
        _run_probe(str(fake_binary), ["--print", "probe"], timeout_seconds=1.0)

    child_pid = int(pid_file.read_text(encoding="utf-8").strip())
    for _ in range(20):
        if not _process_exists(child_pid):
            break
        time.sleep(0.05)

    assert not _process_exists(child_pid)


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
    assert decision.selected_executor is None
    assert decision.eligible_executors == ()
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
    monkeypatch.delenv("AGPAIR_CLAUDE_CODE_AUTH_MODE", raising=False)
    monkeypatch.setenv("AGPAIR_CC_SWITCH_HOME", str(tmp_path / ".missing-cc-switch"))

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


def test_claude_live_probe_timeout_is_not_reported_as_auth_required(monkeypatch, tmp_path: Path) -> None:
    fake_binary = tmp_path / "claude"
    fake_binary.write_text(
        """#!/bin/sh
if [ "$1" = "--help" ]; then
  exit 0
fi
if [ "$1" = "auth" ] && [ "$2" = "status" ]; then
  printf '{"loggedIn":true,"authMethod":"oauth_token"}\\n'
  exit 0
fi
sleep 30
""",
        encoding="utf-8",
    )
    fake_binary.chmod(0o755)
    monkeypatch.setenv("AGPAIR_CLAUDE_CODE_BIN", str(fake_binary))
    monkeypatch.setenv("AGPAIR_CLAUDE_CODE_LIVE_PROBE_TIMEOUT_SECONDS", "0.2")
    monkeypatch.delenv("AGPAIR_CLAUDE_CODE_AUTH_MODE", raising=False)

    health = executor_health_snapshot(run_launch_probe=True)["claude-code"]

    assert health["available"] is False
    assert health["auth_state"] == "executor_probe_timeout"
    assert health["last_failure_type"] == "executor_probe_timeout"
    assert "timed out" in health["last_error_excerpt"]


def test_claude_live_probe_hook_interference_is_not_reported_as_auth_required(monkeypatch, tmp_path: Path) -> None:
    fake_binary = tmp_path / "claude"
    fake_binary.write_text(
        """#!/bin/sh
if [ "$1" = "--help" ]; then
  exit 0
fi
if [ "$1" = "auth" ] && [ "$2" = "status" ]; then
  printf '{"loggedIn":true,"authMethod":"oauth_token"}\\n'
  exit 0
fi
printf 'AGPair external-first routing is available in this repository.\\n'
exit 1
""",
        encoding="utf-8",
    )
    fake_binary.chmod(0o755)
    monkeypatch.setenv("AGPAIR_CLAUDE_CODE_BIN", str(fake_binary))
    monkeypatch.delenv("AGPAIR_CLAUDE_CODE_AUTH_MODE", raising=False)

    health = executor_health_snapshot(run_launch_probe=True)["claude-code"]

    assert health["available"] is False
    assert health["auth_state"] == "executor_hook_interference"
    assert health["last_failure_type"] == "executor_hook_interference"
    assert "AGPair external-first routing" in health["last_error_excerpt"]


def test_claude_auto_auth_falls_back_to_ccswitch_provider(monkeypatch, tmp_path: Path) -> None:
    fake_binary = tmp_path / "claude"
    args_log = tmp_path / "claude-args.log"
    fake_binary.write_text(
        f"""#!/bin/sh
printf '%s\\n' "$*" >> {str(args_log)!r}
if [ "$1" = "auth" ] && [ "$2" = "status" ]; then
  printf '{{"loggedIn":true,"authMethod":"oauth_token"}}\\n'
  exit 0
fi
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
    monkeypatch.setenv("AGPAIR_CLAUDE_CODE_BIN", str(fake_binary))
    monkeypatch.setenv("AGPAIR_CC_SWITCH_HOME", str(tmp_path / ".cc-switch"))
    monkeypatch.delenv("AGPAIR_CLAUDE_CODE_AUTH_MODE", raising=False)

    health = executor_health_snapshot(run_launch_probe=True)["claude-code"]

    assert health["available"] is True
    assert health["isolation_auth_satisfied"] is True
    assert health["last_failure_type"] is None
    assert health["auth_mode"] == "ccswitch"
    assert health["ccswitch_provider"] == "Kimi Claude Code"
    assert health["auth_satisfied"] is True
    assert health["auth_probe_environment_mode"] == "managed-natural"
    assert health["auth_probe_skill_policy"] == "inherit"
    assert health["auth_probe_mcp_policy"] == "inherit"
    assert "test-secret" not in json.dumps(health)
    args = args_log.read_text(encoding="utf-8")
    for forbidden in ("--bare", "--strict-mcp-config", "--mcp-config", "--disable-slash-commands", "--no-chrome"):
        assert forbidden not in args


def test_claude_oauth_live_probe_accepts_success(monkeypatch, tmp_path: Path) -> None:
    fake_binary = tmp_path / "claude"
    args_log = tmp_path / "claude-args.log"
    fake_binary.write_text(
        f"""#!/bin/sh
printf '%s\\n' "$*" >> {str(args_log)!r}
if [ "$1" = "auth" ] && [ "$2" = "status" ]; then
  printf '{{"loggedIn":true,"authMethod":"oauth_token"}}\\n'
  exit 0
fi
printf '{{"type":"result","subtype":"success","is_error":false}}\\n'
exit 0
""",
        encoding="utf-8",
    )
    fake_binary.chmod(0o755)
    monkeypatch.setenv("AGPAIR_CLAUDE_CODE_BIN", str(fake_binary))
    monkeypatch.delenv("AGPAIR_CLAUDE_CODE_AUTH_MODE", raising=False)

    health = executor_health_snapshot(run_launch_probe=True)["claude-code"]

    assert health["available"] is True
    assert health["isolation_auth_satisfied"] is True
    assert health["auth_satisfied"] is True
    assert health["auth_probe_environment_mode"] == "managed-natural"
    assert health["last_failure_type"] is None
    args = args_log.read_text(encoding="utf-8")
    for forbidden in ("--bare", "--strict-mcp-config", "--mcp-config", "--disable-slash-commands", "--no-chrome"):
        assert forbidden not in args


def test_claude_live_probe_redacts_ccswitch_secret_from_errors(monkeypatch, tmp_path: Path) -> None:
    fake_binary = tmp_path / "claude"
    fake_binary.write_text(
        """#!/bin/sh
if [ "$1" = "--help" ]; then
  exit 0
fi
if [ "$1" = "auth" ] && [ "$2" = "status" ]; then
  printf '{"loggedIn":false,"authMethod":null}\\n'
  exit 0
fi
printf 'bad token %s\\n' "$ANTHROPIC_API_KEY" >&2
exit 1
""",
        encoding="utf-8",
    )
    fake_binary.chmod(0o755)
    _write_ccswitch_provider(tmp_path / ".cc-switch")
    monkeypatch.setenv("AGPAIR_CLAUDE_CODE_BIN", str(fake_binary))
    monkeypatch.setenv("AGPAIR_CC_SWITCH_HOME", str(tmp_path / ".cc-switch"))
    monkeypatch.delenv("AGPAIR_CLAUDE_CODE_AUTH_MODE", raising=False)

    health = executor_health_snapshot(run_launch_probe=True)["claude-code"]

    assert health["available"] is False
    assert health["last_failure_type"] == "executor_auth_required"
    assert "test-secret" not in json.dumps(health)
    assert "<redacted>" in health["last_error_excerpt"]


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

    health = executor_health_snapshot()["claude-code"]

    assert health["available"] is False
    assert health["isolation_auth_satisfied"] is False
    assert health["last_failure_type"] == "executor_auth_required"
    assert "ANTHROPIC_API_KEY is empty" in health["last_error_excerpt"]
