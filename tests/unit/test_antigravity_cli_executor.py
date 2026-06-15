import json
import pathlib
from unittest import mock

import pytest

from agpair.executors.antigravity_cli import AntigravityCLIExecutor
from agpair.models import ContinuationCapability


def _write_session_state(session_dir: pathlib.Path, repo_path: pathlib.Path) -> None:
    session_dir.mkdir()
    (session_dir / "state.json").write_text(
        json.dumps({"version": 1, "pid": 1234, "pgid": 1234, "repo_path": str(repo_path)}),
        encoding="utf-8",
    )


def _write_project_config(
    config_path: pathlib.Path,
    *,
    resource_key: str,
    resource_value: str,
    project_id: str = "project",
) -> None:
    config_path.write_text(
        json.dumps(
            {
                "id": project_id,
                "projectResources": {"resources": [{resource_key: resource_value}]},
            },
        ),
        encoding="utf-8",
    )


def test_antigravity_cli_executor_uses_env_binary(monkeypatch) -> None:
    monkeypatch.setenv("AGPAIR_ANTIGRAVITY_CLI_BIN", "/tmp/fake-agy")

    executor = AntigravityCLIExecutor()

    assert executor.bin_path == "/tmp/fake-agy"
    assert executor.backend_id == "antigravity-cli"
    assert executor.continuation_capability == ContinuationCapability.FRESH_RESUME_FIRST
    assert executor.safety_metadata.requires_human_interaction is False


def test_antigravity_cli_executor_defaults_to_agy(monkeypatch) -> None:
    monkeypatch.delenv("AGPAIR_ANTIGRAVITY_CLI_BIN", raising=False)
    monkeypatch.delenv("AGPAIR_ANTIGRAVITY_CLI", raising=False)

    executor = AntigravityCLIExecutor()

    assert executor.bin_path == "agy"


def test_antigravity_cli_command_is_agy_print_driven(monkeypatch) -> None:
    monkeypatch.delenv("AGPAIR_ANTIGRAVITY_APPROVAL_MODE", raising=False)
    monkeypatch.delenv("AGPAIR_ANTIGRAVITY_PRINT_TIMEOUT", raising=False)
    executor = AntigravityCLIExecutor(antigravity_bin="fake-agy")

    cmd = executor._build_antigravity_cmd(
        "Goal: edit the repo",
        "/tmp/repo",
        pathlib.Path("/tmp/agpair"),
    )

    assert cmd == [
        "fake-agy",
        "--dangerously-skip-permissions",
        "--add-dir",
        "/tmp/repo",
        "--print-timeout",
        "30m0s",
        "--log-file",
        "/tmp/agpair/antigravity-cli.log",
        "--print",
        "Goal: edit the repo",
    ]


def test_antigravity_cli_raw_log_payload_includes_vendor_log() -> None:
    executor = AntigravityCLIExecutor(antigravity_bin="fake-agy")

    payload = executor._raw_log_payload(pathlib.Path("/tmp/agpair"))

    assert payload == {
        "raw_log_path": "/tmp/agpair/stdout.log",
        "stderr_log_path": "/tmp/agpair/stderr.log",
        "vendor_log_path": "/tmp/agpair/antigravity-cli.log",
    }


def test_antigravity_cli_error_summary_reads_vendor_log(tmp_path) -> None:
    (tmp_path / "stdout.log").write_text("", encoding="utf-8")
    (tmp_path / "stderr.log").write_text("", encoding="utf-8")
    (tmp_path / "antigravity-cli.log").write_text(
        "INFO startup\nERROR tool call timed out waiting for response from model\n",
        encoding="utf-8",
    )
    executor = AntigravityCLIExecutor(antigravity_bin="fake-agy")

    assert (
        executor._extract_error_summary(tmp_path)
        == "ERROR tool call timed out waiting for response from model"
    )


def test_antigravity_cli_command_honors_print_timeout_env(monkeypatch) -> None:
    monkeypatch.delenv("AGPAIR_ANTIGRAVITY_APPROVAL_MODE", raising=False)
    monkeypatch.setenv("AGPAIR_ANTIGRAVITY_PRINT_TIMEOUT", "10m0s")
    executor = AntigravityCLIExecutor(antigravity_bin="fake-agy")

    cmd = executor._build_antigravity_cmd(
        "Goal: edit the repo",
        "/tmp/repo",
        pathlib.Path("/tmp/agpair"),
    )

    assert cmd[cmd.index("--print-timeout") + 1] == "10m0s"


def test_antigravity_cli_command_honors_model_env(monkeypatch) -> None:
    monkeypatch.delenv("AGPAIR_ANTIGRAVITY_APPROVAL_MODE", raising=False)
    monkeypatch.delenv("AGPAIR_ANTIGRAVITY_PRINT_TIMEOUT", raising=False)
    monkeypatch.setenv("AGPAIR_ANTIGRAVITY_MODEL", "Gemini 3.1 Pro (Low)")
    executor = AntigravityCLIExecutor(antigravity_bin="fake-agy")

    cmd = executor._build_antigravity_cmd(
        "Goal: edit the repo",
        "/tmp/repo",
        pathlib.Path("/tmp/agpair"),
    )

    assert cmd[:3] == ["fake-agy", "--model", "Gemini 3.1 Pro (Low)"]
    assert cmd[cmd.index("--print") + 1] == "Goal: edit the repo"


def test_antigravity_cli_command_honors_legacy_model_env(monkeypatch) -> None:
    monkeypatch.delenv("AGPAIR_ANTIGRAVITY_MODEL", raising=False)
    monkeypatch.setenv("AGPAIR_ANTIGRAVITY_CLI_MODEL", "Claude Sonnet 4.6 (Thinking)")
    executor = AntigravityCLIExecutor(antigravity_bin="fake-agy")

    cmd = executor._build_antigravity_cmd(
        "Goal: inspect only",
        "/tmp/repo",
        pathlib.Path("/tmp/agpair"),
    )

    assert "--model" in cmd
    assert cmd[cmd.index("--model") + 1] == "Claude Sonnet 4.6 (Thinking)"


def test_antigravity_cli_rejects_invalid_print_timeout(monkeypatch) -> None:
    monkeypatch.setenv("AGPAIR_ANTIGRAVITY_PRINT_TIMEOUT", "--bad")
    executor = AntigravityCLIExecutor(antigravity_bin="fake-agy")

    with pytest.raises(
        ValueError,
        match="Unsupported AGPAIR_ANTIGRAVITY_PRINT_TIMEOUT",
    ):
        executor._build_antigravity_cmd(
            "Goal: edit",
            "/tmp/repo",
            pathlib.Path("/tmp/agpair"),
        )


def test_antigravity_cli_default_approval_mode_is_non_escalating(monkeypatch) -> None:
    monkeypatch.setenv("AGPAIR_ANTIGRAVITY_APPROVAL_MODE", "default")
    executor = AntigravityCLIExecutor(antigravity_bin="fake-agy")

    cmd = executor._build_antigravity_cmd(
        "Goal: inspect only",
        "/tmp/repo",
        pathlib.Path("/tmp/agpair"),
    )

    assert "--dangerously-skip-permissions" not in cmd
    assert "--approval-mode" not in cmd
    assert "-y" not in cmd


def test_antigravity_cli_rejects_legacy_auto_edit_mode(monkeypatch) -> None:
    monkeypatch.setenv("AGPAIR_ANTIGRAVITY_APPROVAL_MODE", "auto_edit")
    executor = AntigravityCLIExecutor(antigravity_bin="fake-agy")

    with pytest.raises(
        ValueError,
        match="does not support AGPAIR_ANTIGRAVITY_APPROVAL_MODE=auto_edit",
    ):
        executor._build_antigravity_cmd(
            "Goal: edit",
            "/tmp/repo",
            pathlib.Path("/tmp/agpair"),
        )


def test_antigravity_cleanup_removes_project_config_for_isolated_worktree(
    monkeypatch,
    tmp_path,
) -> None:
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    monkeypatch.setenv("AGPAIR_ANTIGRAVITY_PROJECTS_DIR", str(projects_dir))
    target_path = (tmp_path / "repo" / ".agpair" / "worktrees" / "TASK-AGY").resolve()
    session_dir = tmp_path / "agpair_antigravity-cli_TASK-AGY_cleanup"
    _write_session_state(session_dir, target_path)
    matching_config = projects_dir / "transient.json"
    _write_project_config(
        matching_config,
        resource_key="folderUri",
        resource_value=target_path.as_uri(),
        project_id="transient",
    )
    unrelated_config = projects_dir / "real-project.json"
    _write_project_config(
        unrelated_config,
        resource_key="folderUri",
        resource_value="file:///Users/example/real-project",
        project_id="real",
    )
    executor = AntigravityCLIExecutor(antigravity_bin="fake-agy")

    with mock.patch("agpair.executors.local_cli._is_process_alive", return_value=False):
        executor.cleanup(str(session_dir))

    assert not session_dir.exists()
    assert not matching_config.exists()
    assert unrelated_config.exists()


def test_antigravity_cleanup_keeps_project_config_while_process_alive(monkeypatch, tmp_path) -> None:
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    monkeypatch.setenv("AGPAIR_ANTIGRAVITY_PROJECTS_DIR", str(projects_dir))
    target_path = (tmp_path / "repo" / ".agpair" / "worktrees" / "TASK-AGY").resolve()
    session_dir = tmp_path / "agpair_antigravity-cli_TASK-AGY_cleanup"
    _write_session_state(session_dir, target_path)
    matching_config = projects_dir / "transient.json"
    _write_project_config(
        matching_config,
        resource_key="folderUri",
        resource_value=target_path.as_uri(),
        project_id="transient",
    )
    executor = AntigravityCLIExecutor(antigravity_bin="fake-agy")

    with (
        mock.patch("agpair.executors.local_cli._is_process_alive", return_value=True),
        mock.patch.object(executor, "_ensure_process_dead", return_value=(True, 143)),
    ):
        executor.cleanup(str(session_dir))

    assert session_dir.exists()
    assert matching_config.exists()


def test_antigravity_cleanup_ignores_non_transient_project_path(monkeypatch, tmp_path) -> None:
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    monkeypatch.setenv("AGPAIR_ANTIGRAVITY_PROJECTS_DIR", str(projects_dir))
    target_path = pathlib.Path("/Users/example/real-project").resolve(strict=False)
    session_dir = tmp_path / "agpair_antigravity-cli_TASK-AGY_cleanup"
    _write_session_state(session_dir, target_path)
    matching_config = projects_dir / "real-project.json"
    _write_project_config(
        matching_config,
        resource_key="folderUri",
        resource_value=target_path.as_uri(),
        project_id="real",
    )
    executor = AntigravityCLIExecutor(antigravity_bin="fake-agy")

    with mock.patch("agpair.executors.local_cli._is_process_alive", return_value=False):
        executor.cleanup(str(session_dir))

    assert not session_dir.exists()
    assert matching_config.exists()


def test_antigravity_cleanup_ignores_unprefixed_temp_project_path(monkeypatch, tmp_path) -> None:
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    monkeypatch.setenv("AGPAIR_ANTIGRAVITY_PROJECTS_DIR", str(projects_dir))
    target_path = (tmp_path / "real-project").resolve()
    session_dir = tmp_path / "agpair_antigravity-cli_TASK-AGY_cleanup"
    _write_session_state(session_dir, target_path)
    matching_config = projects_dir / "real-temp-project.json"
    _write_project_config(
        matching_config,
        resource_key="folderUri",
        resource_value=target_path.as_uri(),
        project_id="real-temp",
    )
    executor = AntigravityCLIExecutor(antigravity_bin="fake-agy")

    with mock.patch("agpair.executors.local_cli._is_process_alive", return_value=False):
        executor.cleanup(str(session_dir))

    assert not session_dir.exists()
    assert matching_config.exists()


def test_antigravity_cleanup_ignores_malformed_project_configs(monkeypatch, tmp_path) -> None:
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    monkeypatch.setenv("AGPAIR_ANTIGRAVITY_PROJECTS_DIR", str(projects_dir))
    target_path = (tmp_path / "repo" / ".agpair" / "worktrees" / "TASK-AGY").resolve()
    session_dir = tmp_path / "agpair_antigravity-cli_TASK-AGY_cleanup"
    _write_session_state(session_dir, target_path)
    malformed_config = projects_dir / "malformed.json"
    malformed_config.write_text("{not-json", encoding="utf-8")
    matching_config = projects_dir / "transient.json"
    _write_project_config(
        matching_config,
        resource_key="folderPath",
        resource_value=str(target_path),
        project_id="transient",
    )
    executor = AntigravityCLIExecutor(antigravity_bin="fake-agy")

    with mock.patch("agpair.executors.local_cli._is_process_alive", return_value=False):
        executor.cleanup(str(session_dir))

    assert malformed_config.exists()
    assert not matching_config.exists()
