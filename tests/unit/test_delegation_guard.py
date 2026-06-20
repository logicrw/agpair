from __future__ import annotations

from typer.testing import CliRunner

from agpair.cli.app import app
from agpair.delegation_guard import (
    current_delegation_depth,
    nested_delegation_authorized,
    nested_delegation_blocked,
    next_delegation_env,
)


def test_delegation_depth_is_read_from_environment() -> None:
    assert current_delegation_depth({"AGPAIR_DELEGATION_DEPTH": "2"}) == 2
    assert current_delegation_depth({"AGPAIR_DELEGATION_DEPTH": "bad"}) == 0
    assert nested_delegation_blocked({"AGPAIR_DELEGATION_DEPTH": "1"}) is True
    assert nested_delegation_blocked({"AGPAIR_DELEGATION_DEPTH": "0"}) is False
    assert nested_delegation_authorized({"AGPAIR_ALLOW_NESTED_DELEGATION": "1"}) is True
    assert nested_delegation_authorized({"AGPAIR_ALLOW_NESTED_DELEGATION": "0"}) is False


def test_next_delegation_env_sets_noninteractive_parent_and_depth() -> None:
    env = next_delegation_env("TASK-PARENT", {"AGPAIR_DELEGATION_DEPTH": "1"})

    assert env["AGPAIR_PARENT_TASK_ID"] == "TASK-PARENT"
    assert env["AGPAIR_DELEGATION_DEPTH"] == "2"
    assert env["AGPAIR_NONINTERACTIVE"] == "1"
    assert env["CI"] == "1"


def test_task_start_blocks_nested_delegation_by_default(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    monkeypatch.setenv("AGPAIR_DELEGATION_DEPTH", "1")

    result = CliRunner().invoke(
        app,
        [
            "task",
            "start",
            "--body",
            "Goal: nested\nScope: nested\nRequired changes: nested\nExit criteria: nested",
        ],
    )

    assert result.exit_code != 0
    output = result.stderr or result.output
    assert "nested_delegation_blocked" in output
    assert "finish the current AGPair task directly" in output
    assert "ask the controller to start a separate lane" in output


def test_task_start_rejects_self_authorized_nested_delegation(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AGPAIR_HOME", str(tmp_path / ".agpair"))
    monkeypatch.setenv("AGPAIR_DELEGATION_DEPTH", "1")
    monkeypatch.delenv("AGPAIR_ALLOW_NESTED_DELEGATION", raising=False)

    result = CliRunner().invoke(
        app,
        [
            "task",
            "start",
            "--allow-nested-delegation",
            "--body",
            "Goal: nested\nScope: nested\nRequired changes: nested\nExit criteria: nested",
        ],
    )

    assert result.exit_code != 0
    assert "nested_delegation_not_authorized" in (result.stderr or result.output)
