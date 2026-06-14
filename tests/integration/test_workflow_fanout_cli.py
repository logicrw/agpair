import json

from typer.testing import CliRunner

from agpair.cli.app import app


def test_workflow_fanout_dry_run_emits_valid_manifest(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    result = CliRunner().invoke(
        app,
        [
            "workflow",
            "fanout",
            "--repo-path",
            str(tmp_path),
            "--controller",
            "codex",
            "--mode",
            "review",
            "--topic",
            "Review fanout synthesis",
            "--lane",
            "grok-cli:primary",
            "--lane",
            "grok-cli:adversarial",
            "--dry-run",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["dry_run"] is True
    assert payload["node_count"] == 4
    assert payload["manifest"]["nodes"][0]["executor"] == "grok-cli"
    assert payload["manifest"]["nodes"][2]["kind"] == "synthesis"
    assert payload["manifest"]["nodes"][3]["kind"] == "gate"
