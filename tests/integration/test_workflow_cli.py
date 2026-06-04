import json
from pathlib import Path

from typer.testing import CliRunner

from agpair.cli.app import app
from agpair.config import AppPaths
from agpair.storage.db import ensure_database
from agpair.workflows.store import WorkflowRepository


def write_manifest(tmp_path: Path) -> Path:
    path = tmp_path / "workflow.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "name": "cli-test",
                "controller": "codex",
                "authorization_profile": "local_readonly",
                "completion_policy": "report",
                "nodes": [
                    {
                        "id": "scan",
                        "kind": "task",
                        "body": "Goal: scan. Required changes: none.",
                        "authorization_profile": "local_readonly",
                        "completion_policy": "report",
                        "depends_on": [],
                    },
                    {"id": "gate", "kind": "gate", "depends_on": ["scan"]},
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


def test_workflow_validate_accepts_valid_manifest(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    result = CliRunner().invoke(app, ["workflow", "validate", "--file", str(write_manifest(tmp_path)), "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["manifest"]["nodes"][0]["completion_policy"] == "report"


def test_workflow_validate_rejects_script_field(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    path = write_manifest(tmp_path)
    raw = json.loads(path.read_text())
    raw["nodes"][0]["metadata"] = {"shell": "pytest"}
    path.write_text(json.dumps(raw), encoding="utf-8")

    result = CliRunner().invoke(app, ["workflow", "validate", "--file", str(path), "--json"])

    assert result.exit_code != 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert "metadata.shell" in payload["error"]


def test_workflow_start_creates_rows(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / ".agpair"
    monkeypatch.setenv("AGPAIR_HOME", str(home))
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    result = CliRunner().invoke(
        app,
        [
            "workflow",
            "start",
            "--file",
            str(write_manifest(tmp_path)),
            "--repo-path",
            str(repo_path),
            "--workflow-id",
            "WF-CLI",
            "--no-dispatch",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["workflow_id"] == "WF-CLI"
    assert payload["phase"] == "running"

    paths = AppPaths.from_root(home)
    ensure_database(paths.db_path)
    repo = WorkflowRepository(paths.db_path)
    assert repo.require_workflow("WF-CLI").repo_path == str(repo_path.resolve())
    assert len(repo.list_nodes("WF-CLI")) == 2


def test_workflow_cancel_marks_running_nodes_abandoned(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / ".agpair"
    monkeypatch.setenv("AGPAIR_HOME", str(home))
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    runner = CliRunner()
    runner.invoke(
        app,
        [
            "workflow",
            "start",
            "--file",
            str(write_manifest(tmp_path)),
            "--repo-path",
            str(repo_path),
            "--workflow-id",
            "WF-CANCEL",
            "--no-dispatch",
            "--json",
        ],
    )
    result = runner.invoke(app, ["workflow", "cancel", "WF-CANCEL", "--reason", "test", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["phase"] == "abandoned"
    assert any(node["phase"] == "abandoned" for node in payload["nodes"])
