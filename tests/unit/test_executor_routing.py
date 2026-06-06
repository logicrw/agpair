from agpair.executors.routing import (
    default_executor_id,
    is_legacy_executor,
    is_supported_executor,
    normalize_executor_id,
    supported_executor_ids,
)
from agpair.executors import LOCAL_CLI_BACKENDS, get_executor, is_local_cli_backend
from agpair.executors.antigravity import AntigravityExecutor
from agpair.executors.antigravity_cli import AntigravityCLIExecutor
from agpair.executors.claude_code import ClaudeCodeExecutor
from agpair.executors.codex import CodexExecutor
from agpair.executors.gemini import GeminiExecutor
from agpair.executors.grok_cli import GrokCLIExecutor
from agpair.executors.policy import EXECUTOR_SPECS


def test_default_executor_is_antigravity_cli() -> None:
    assert default_executor_id() == "antigravity-cli"
    assert EXECUTOR_SPECS["antigravity-cli"].default_binary == "agy"


def test_supported_executor_ids_exclude_gemini() -> None:
    assert supported_executor_ids() == ("antigravity-cli", "grok-cli", "claude-code", "codex")
    assert is_supported_executor("antigravity-cli")
    assert is_supported_executor("grok-cli")
    assert is_supported_executor("claude-code")
    assert is_supported_executor("codex")
    assert not is_supported_executor("gemini")
    assert not is_supported_executor("gemini_cli")


def test_legacy_executor_ids_remain_detectable_but_not_supported_for_new_work() -> None:
    assert is_legacy_executor("antigravity")
    assert is_legacy_executor("codex_cli")
    assert is_legacy_executor("gemini_cli")
    assert not is_supported_executor("codex_cli")
    assert not is_supported_executor("gemini_cli")


def test_normalize_executor_id_strips_and_lowercases_supported_ids() -> None:
    assert normalize_executor_id(" Antigravity-CLI ") == "antigravity-cli"
    assert normalize_executor_id("GROK-CLI") == "grok-cli"
    assert normalize_executor_id("Claude-Code") == "claude-code"


def test_registry_returns_external_cli_adapters_for_canonical_ids() -> None:
    assert isinstance(get_executor("antigravity-cli"), AntigravityCLIExecutor)
    assert isinstance(get_executor("grok-cli"), GrokCLIExecutor)
    assert isinstance(get_executor("claude-code"), ClaudeCodeExecutor)
    assert isinstance(get_executor("codex"), CodexExecutor)


def test_registry_factories_preserve_environment_binary_overrides(monkeypatch) -> None:
    monkeypatch.setenv("AGPAIR_CODEX_BIN", "/tmp/fake-codex")

    executor = get_executor("codex")
    legacy_executor = get_executor("codex_cli")

    assert isinstance(executor, CodexExecutor)
    assert executor.bin_path == "/tmp/fake-codex"
    assert isinstance(legacy_executor, CodexExecutor)
    assert legacy_executor.bin_path == "/tmp/fake-codex"


def test_registry_keeps_legacy_executors_readable_without_marking_them_active() -> None:
    assert isinstance(get_executor("antigravity", agent_bus_bin="agent-bus"), AntigravityExecutor)
    assert isinstance(get_executor("codex_cli"), CodexExecutor)
    assert isinstance(get_executor("gemini_cli"), GeminiExecutor)
    assert not is_local_cli_backend("antigravity")
    assert is_local_cli_backend("codex_cli")
    assert not is_local_cli_backend("gemini_cli")


def test_local_cli_backends_include_new_ids_and_legacy_codex_cleanup_id() -> None:
    assert LOCAL_CLI_BACKENDS == frozenset(
        {"antigravity-cli", "grok-cli", "claude-code", "codex", "codex_cli"}
    )
