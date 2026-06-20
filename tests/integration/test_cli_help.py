from typer.testing import CliRunner
import json

from agpair.cli.app import app


def test_cli_help_lists_top_level_groups() -> None:
    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "task" in result.stdout
    assert "daemon" in result.stdout
    assert "doctor" in result.stdout
    assert "codex" in result.stdout
    assert "hermes" in result.stdout


def test_cli_version_option() -> None:
    result = CliRunner().invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.stdout.strip() == "agpair 1.0.0"


def test_doctor_is_a_top_level_command() -> None:
    result = CliRunner().invoke(app, ["doctor"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert "config_root" in payload
