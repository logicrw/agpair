from __future__ import annotations

import json
import subprocess
from pathlib import Path

from typer.testing import CliRunner

from agpair.cli.app import app


def init_git_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)


def test_hermes_config_emits_pre_llm_call_hook(monkeypatch) -> None:
    monkeypatch.setenv("AGPAIR_HERMES_HOOK_COMMAND", "/tmp/agpair hermes hook pre-llm-call")
    result = CliRunner().invoke(app, ["hermes", "config"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload == {
        "hooks": {
            "pre_llm_call": [
                {
                    "command": "/tmp/agpair hermes hook pre-llm-call",
                    "timeout": 10,
                    "agpair_managed": True,
                }
            ]
        }
    }


def test_hermes_config_install_writes_config_and_skill(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    monkeypatch.setenv("AGPAIR_HERMES_HOOK_COMMAND", "/tmp/agpair hermes hook pre-llm-call")
    config_path = tmp_path / ".hermes" / "config.yaml"
    config_path.parent.mkdir()
    config_path.write_text("model:\n  default: test-model\nhooks: {}\nhooks_auto_accept: false\n", encoding="utf-8")

    result = CliRunner().invoke(
        app,
        ["hermes", "config", "--install", "--config-path", str(config_path), "--sync-skill"],
    )

    assert result.exit_code == 0
    config_text = config_path.read_text(encoding="utf-8")
    assert '    - command: "/tmp/agpair hermes hook pre-llm-call"' in config_text
    assert "      agpair_managed: true" in config_text
    assert "hooks_auto_accept: false" in config_text
    skill_path = config_path.parent / "skills" / "autonomous-ai-agents" / "agpair" / "SKILL.md"
    assert skill_path.exists()
    assert "AGPair for Hermes" in skill_path.read_text(encoding="utf-8")


def test_hermes_config_install_preserves_existing_hooks(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    monkeypatch.setenv("AGPAIR_HERMES_HOOK_COMMAND", "/tmp/agpair hermes hook pre-llm-call")
    config_path = tmp_path / ".hermes" / "config.yaml"
    config_path.parent.mkdir()
    config_path.write_text(
        "\n".join(
            [
                "hooks:",
                "  pre_tool_call:",
                "    - matcher: \"terminal\"",
                "      command: \"~/.hermes/agent-hooks/custom.sh\"",
                "      timeout: 5",
                "hooks_auto_accept: false",
                "",
            ]
        ),
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        app,
        ["hermes", "config", "--install", "--config-path", str(config_path), "--no-sync-skill"],
    )

    assert result.exit_code == 0
    config_text = config_path.read_text(encoding="utf-8")
    assert 'command: "~/.hermes/agent-hooks/custom.sh"' in config_text
    assert '    - command: "/tmp/agpair hermes hook pre-llm-call"' in config_text


def test_hermes_config_uninstall_removes_only_managed_hook(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    config_path = tmp_path / ".hermes" / "config.yaml"
    config_path.parent.mkdir()
    config_path.write_text(
        "\n".join(
            [
                "hooks:",
                "  pre_llm_call:",
                "    - command: \"agpair hermes hook pre-llm-call\"",
                "      timeout: 10",
                "    - command: \"~/.hermes/agent-hooks/other-context.sh\"",
                "      timeout: 4",
                "hooks_auto_accept: false",
                "",
            ]
        ),
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        app,
        ["hermes", "config", "--uninstall", "--config-path", str(config_path), "--no-sync-skill"],
    )

    assert result.exit_code == 0
    config_text = config_path.read_text(encoding="utf-8")
    assert "agpair hermes hook pre-llm-call" not in config_text
    assert "other-context.sh" in config_text


def test_hermes_config_install_preserves_other_managed_hook_events(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    monkeypatch.setenv("AGPAIR_HERMES_HOOK_COMMAND", "/tmp/agpair hermes hook pre-llm-call")
    config_path = tmp_path / ".hermes" / "config.yaml"
    config_path.parent.mkdir()
    config_path.write_text(
        "\n".join(
            [
                "hooks:",
                "  pre_tool_call:",
                "    - command: \"/opt/agpair-tool-hook\"",
                "      timeout: 5",
                "      agpair_managed: true",
                "",
            ]
        ),
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        app,
        ["hermes", "config", "--install", "--config-path", str(config_path), "--no-sync-skill"],
    )

    assert result.exit_code == 0
    config_text = config_path.read_text(encoding="utf-8")
    assert "/opt/agpair-tool-hook" in config_text
    assert "/tmp/agpair hermes hook pre-llm-call" in config_text


def test_hermes_config_install_accepts_hook_header_comments(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    monkeypatch.setenv("AGPAIR_HERMES_HOOK_COMMAND", "/tmp/agpair hermes hook pre-llm-call")
    config_path = tmp_path / ".hermes" / "config.yaml"
    config_path.parent.mkdir()
    config_path.write_text(
        "\n".join(
            [
                "hooks: # shell hook events",
                "  pre_tool_call:",
                "    - command: \"~/.hermes/agent-hooks/custom.sh\"",
                "      timeout: 5",
                "",
            ]
        ),
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        app,
        ["hermes", "config", "--install", "--config-path", str(config_path), "--no-sync-skill"],
    )

    assert result.exit_code == 0
    config_text = config_path.read_text(encoding="utf-8")
    assert "hooks: # shell hook events" in config_text
    assert "pre_tool_call" in config_text
    assert "/tmp/agpair hermes hook pre-llm-call" in config_text


def test_hermes_config_dry_run_does_not_write(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    monkeypatch.setenv("AGPAIR_HERMES_HOOK_COMMAND", "/tmp/agpair hermes hook pre-llm-call")
    config_path = tmp_path / ".hermes" / "config.yaml"
    config_path.parent.mkdir()
    config_path.write_text("hooks: {}\n", encoding="utf-8")

    result = CliRunner().invoke(
        app,
        ["hermes", "config", "--install", "--config-path", str(config_path), "--dry-run"],
    )

    assert result.exit_code == 0
    assert "---" in result.stdout
    assert "agpair hermes hook pre-llm-call" in result.stdout
    assert config_path.read_text(encoding="utf-8") == "hooks: {}\n"


def test_hermes_config_install_is_idempotent(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    monkeypatch.setenv("AGPAIR_HERMES_HOOK_COMMAND", "/tmp/agpair hermes hook pre-llm-call")
    config_path = tmp_path / ".hermes" / "config.yaml"
    config_path.parent.mkdir()
    config_path.write_text("hooks: {}\n", encoding="utf-8")

    for _ in range(2):
        result = CliRunner().invoke(
            app,
            ["hermes", "config", "--install", "--config-path", str(config_path), "--no-sync-skill"],
        )
        assert result.exit_code == 0

    config_text = config_path.read_text(encoding="utf-8")
    assert config_text.count("agpair hermes hook pre-llm-call") == 1
    assert config_text.count("agpair_managed: true") == 1


def test_hermes_config_install_rejects_inline_pre_llm_call(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    monkeypatch.setenv("AGPAIR_HERMES_HOOK_COMMAND", "/tmp/agpair hermes hook pre-llm-call")
    config_path = tmp_path / ".hermes" / "config.yaml"
    config_path.parent.mkdir()
    config_path.write_text(
        'hooks:\n  pre_llm_call: [{command: "~/.hermes/agent-hooks/context.sh", timeout: 5}]\n',
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        app,
        ["hermes", "config", "--install", "--config-path", str(config_path), "--no-sync-skill"],
    )

    assert result.exit_code == 1
    assert "inline YAML" in result.stderr
    assert "agpair hermes hook pre-llm-call" not in config_path.read_text(encoding="utf-8")


def test_hermes_config_quotes_hook_command_for_yaml(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    monkeypatch.setenv("AGPAIR_HERMES_HOOK_COMMAND", '/tmp/ag"pair hermes hook pre-llm-call')
    config_path = tmp_path / ".hermes" / "config.yaml"
    config_path.parent.mkdir()
    config_path.write_text("hooks: {}\n", encoding="utf-8")

    result = CliRunner().invoke(
        app,
        ["hermes", "config", "--install", "--config-path", str(config_path), "--no-sync-skill"],
    )

    assert result.exit_code == 0
    assert 'command: "/tmp/ag\\"pair hermes hook pre-llm-call"' in config_path.read_text(encoding="utf-8")


def test_hermes_config_uninstall_removes_marked_custom_command(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    config_path = tmp_path / ".hermes" / "config.yaml"
    config_path.parent.mkdir()
    config_path.write_text(
        "\n".join(
            [
                "hooks:",
                "  pre_llm_call:",
                "    - command: \"/opt/agpair-wrapper.sh\"",
                "      timeout: 10",
                "      agpair_managed: true",
                "    - command: \"~/.hermes/agent-hooks/other-context.sh\"",
                "      timeout: 4",
                "",
            ]
        ),
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        app,
        ["hermes", "config", "--uninstall", "--config-path", str(config_path), "--no-sync-skill"],
    )

    assert result.exit_code == 0
    config_text = config_path.read_text(encoding="utf-8")
    assert "/opt/agpair-wrapper.sh" not in config_text
    assert "other-context.sh" in config_text


def test_hermes_config_uninstall_removes_split_command_item(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    config_path = tmp_path / ".hermes" / "config.yaml"
    config_path.parent.mkdir()
    config_path.write_text(
        "\n".join(
            [
                "hooks:",
                "  pre_llm_call:",
                "    - matcher: \"*\"",
                "      command: \"agpair hermes hook pre-llm-call\"",
                "      timeout: 10",
                "    - command: \"~/.hermes/agent-hooks/other-context.sh\"",
                "      timeout: 4",
                "",
            ]
        ),
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        app,
        ["hermes", "config", "--uninstall", "--config-path", str(config_path), "--no-sync-skill"],
    )

    assert result.exit_code == 0
    config_text = config_path.read_text(encoding="utf-8")
    assert "agpair hermes hook pre-llm-call" not in config_text
    assert "other-context.sh" in config_text


def test_hermes_config_uninstall_prunes_empty_hooks_block(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    config_path = tmp_path / ".hermes" / "config.yaml"
    config_path.parent.mkdir()
    config_path.write_text(
        "\n".join(
            [
                "hooks:",
                "  pre_llm_call:",
                "    - command: \"agpair hermes hook pre-llm-call\"",
                "      timeout: 10",
                "",
            ]
        ),
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        app,
        ["hermes", "config", "--uninstall", "--config-path", str(config_path), "--no-sync-skill"],
    )

    assert result.exit_code == 0
    assert config_path.read_text(encoding="utf-8") == "hooks: {}\n"


def test_hermes_pre_llm_hook_noops_without_repo(monkeypatch) -> None:
    monkeypatch.setenv("AGPAIR_HOME", "/path/that/does/not/exist")

    result = CliRunner().invoke(
        app,
        ["hermes", "hook", "pre-llm-call"],
        input=json.dumps({"hook_event_name": "pre_llm_call", "cwd": "/path/that/does/not/exist"}),
    )

    assert result.exit_code == 0
    assert json.loads(result.stdout) == {}


def test_hermes_pre_llm_hook_resolves_workspace_payload(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    repo_path = tmp_path / "repo"
    init_git_repo(repo_path)

    result = CliRunner().invoke(
        app,
        ["hermes", "hook", "pre-llm-call"],
        input=json.dumps({"hook_event_name": "pre_llm_call", "workspace": {"current_dir": str(repo_path)}}),
    )

    assert result.exit_code == 0
    assert "AGPair external-first routing" in json.loads(result.stdout)["context"]


def test_hermes_pre_llm_hook_emits_context_for_repo(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    repo_path = tmp_path / "repo"
    init_git_repo(repo_path)

    result = CliRunner().invoke(
        app,
        ["hermes", "hook", "pre-llm-call"],
        input=json.dumps({"hook_event_name": "pre_llm_call", "cwd": str(repo_path)}),
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert "AGPair external-first routing is available" in payload["context"]
    assert "Hermes native `delegate_task`" in payload["context"]


def test_hermes_pre_llm_hook_noops_inside_agpair_internal_probe(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    monkeypatch.setenv("AGPAIR_INTERNAL_ROLE", "probe")
    repo_path = tmp_path / "repo"
    init_git_repo(repo_path)

    result = CliRunner().invoke(
        app,
        ["hermes", "hook", "pre-llm-call"],
        input=json.dumps({"hook_event_name": "pre_llm_call", "cwd": str(repo_path)}),
    )

    assert result.exit_code == 0
    assert json.loads(result.stdout) == {}
