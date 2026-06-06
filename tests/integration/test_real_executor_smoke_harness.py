from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = PROJECT_ROOT / "scripts" / "smoke_real_executors.py"


def _run(cmd: list[str], *, cwd: Path) -> None:
    subprocess.run(cmd, cwd=cwd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "agpair" / "cli").mkdir(parents=True)
    (repo / "pyproject.toml").write_text("[project]\nname = 'smoke-fixture'\n", encoding="utf-8")
    (repo / "agpair" / "cli" / "task.py").write_text("# smoke fixture\n", encoding="utf-8")
    _run(["git", "init"], cwd=repo)
    _run(["git", "config", "user.email", "test@example.com"], cwd=repo)
    _run(["git", "config", "user.name", "Test User"], cwd=repo)
    _run(["git", "add", "."], cwd=repo)
    _run(["git", "commit", "-m", "initial"], cwd=repo)
    return repo


def _fake_executor(path: Path) -> Path:
    path.write_text(
        """#!/bin/sh
if [ "${1:-}" = "auth" ] && [ "${2:-}" = "status" ]; then
  if [ "${FAKE_CLAUDE_LOGGED_IN:-1}" = "0" ]; then
    printf '{"loggedIn":false,"authMethod":null}\\n'
  else
    printf '{"loggedIn":true,"authMethod":"oauth_token","apiProvider":"firstParty"}\\n'
  fi
  exit 0
fi
last=""
output_file=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    -o)
      output_file="$2"
      shift 2
      ;;
    *)
      last="$1"
      shift
      ;;
  esac
done
task_id=$(printf '%s' "$last" | sed -n 's/.*\\(TASK-[A-Z0-9-]*\\).*/\\1/p' | head -n 1)
if [ -z "$task_id" ]; then
  task_id="TASK-UNKNOWN"
fi
mkdir -p tests/fixtures/external_executor_smoke
printf 'fake-executor %s\\n' "$task_id" > tests/fixtures/external_executor_smoke/fake.txt
printf 'Smoke report for %s\\n' "$task_id"
receipt=$(printf '{"schema_version":"1","task_id":"%s","attempt_no":1,"review_round":0,"status":"EVIDENCE_PACK","summary":"Smoke report","payload":{"claimed_state":"ready_for_review","changed_files":["tests/fixtures/external_executor_smoke/fake.txt"],"validation_not_run":"fake executor smoke","scope_violations":[],"report":"Smoke report","raw_log_path":"stdout.log","receipt_path":"receipt.json"}}' "$task_id")
printf '%s\\n' "$receipt"
if [ -n "$output_file" ]; then
  printf '%s\\n' "$receipt" > "$output_file"
fi
exit 0
""",
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path


def _hanging_executor(path: Path) -> Path:
    path.write_text(
        """#!/bin/sh
if [ "${1:-}" = "--help" ]; then
  echo "fake grok help"
  exit 0
fi
i=0
while [ "$i" -lt 60 ]; do
  echo '{"type":"thought","data":"still thinking"}'
  i=$((i + 1))
  sleep 0.5
done
""",
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path


def _env(
    tmp_path: Path,
    *,
    missing_grok: bool = False,
    hanging_grok: bool = False,
    missing_claude_auth: bool = False,
) -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(PROJECT_ROOT)
    env["AGPAIR_HOME"] = str(tmp_path / ".agpair")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    env["AGPAIR_ANTIGRAVITY_CLI_BIN"] = str(_fake_executor(bin_dir / "agy"))
    if missing_grok:
        env["AGPAIR_GROK_CLI_BIN"] = str(bin_dir / "missing-grok")
    elif hanging_grok:
        env["AGPAIR_GROK_CLI_BIN"] = str(_hanging_executor(bin_dir / "grok"))
    else:
        env["AGPAIR_GROK_CLI_BIN"] = str(_fake_executor(bin_dir / "grok"))
    env["AGPAIR_CLAUDE_CODE_BIN"] = str(_fake_executor(bin_dir / "claude"))
    if missing_claude_auth:
        env["FAKE_CLAUDE_LOGGED_IN"] = "0"
    else:
        env["FAKE_CLAUDE_LOGGED_IN"] = "1"
    env["AGPAIR_CODEX_BIN"] = str(_fake_executor(bin_dir / "codex"))
    return env


def _run_smoke(
    tmp_path: Path,
    repo: Path,
    *args: str,
    missing_grok: bool = False,
    hanging_grok: bool = False,
    missing_claude_auth: bool = False,
) -> dict:
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--repo-path", str(repo), *args],
        cwd=PROJECT_ROOT,
        env=_env(
            tmp_path,
            missing_grok=missing_grok,
            hanging_grok=hanging_grok,
            missing_claude_auth=missing_claude_auth,
        ),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=60,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    return json.loads(proc.stdout)


def test_smoke_harness_runs_codex_controller_fake_executor_matrix(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)

    payload = _run_smoke(
        tmp_path,
        repo,
        "--controller",
        "codex",
        "--executors",
        "antigravity-cli,grok-cli,claude-code",
        "--timeout-seconds",
        "10",
        "--interval-seconds",
        "0.1",
    )

    assert payload["all_success"] is True
    assert [result["executor_id"] for result in payload["results"]] == [
        "antigravity-cli",
        "grok-cli",
        "claude-code",
    ]
    for result in payload["results"]:
        assert result["outcome"] == "ready_for_review"
        assert result["phase"] == "ready_for_review"
        assert result["artifacts"]["stdout_path"]
        assert "tests/fixtures/external_executor_smoke/fake.txt" in result["git_status_short"]
        assert result["cleanup"]["removed"] is True
        assert not Path(result["worktree_path"]).exists()
    assert Path(payload["report_path"]).is_file()
    assert ".agpair/smoke/reports" in payload["report_path"]
    report_file_payload = json.loads(Path(payload["report_path"]).read_text(encoding="utf-8"))
    assert report_file_payload["all_success"] is True
    assert report_file_payload["harness_completed"] is True
    assert report_file_payload["report_path"] == payload["report_path"]


def test_smoke_harness_runs_diagnostic_all_registered_with_self_allowed(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)

    payload = _run_smoke(
        tmp_path,
        repo,
        "--controller",
        "diagnostic",
        "--all-registered",
        "--allow-self-executor",
        "--timeout-seconds",
        "10",
        "--interval-seconds",
        "0.1",
    )

    assert payload["all_registered"] is True
    assert payload["allow_self_executor"] is True
    assert [result["executor_id"] for result in payload["results"]] == [
        "antigravity-cli",
        "grok-cli",
        "claude-code",
        "codex",
    ]
    assert all(result["outcome"] == "ready_for_review" for result in payload["results"])


def test_smoke_harness_reports_controller_suppressed_self_executor(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)

    payload = _run_smoke(
        tmp_path,
        repo,
        "--controller",
        "codex",
        "--executors",
        "codex",
    )

    assert payload["all_success"] is False
    result = payload["results"][0]
    assert result["executor_id"] == "codex"
    assert result["attempted"] is False
    assert result["blocker_type"] == "executor_suppressed"
    assert "suppressed" in result["reason"]
    assert "codex" in result["reason"]


def test_smoke_harness_reports_missing_binary_without_dispatch(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)

    payload = _run_smoke(
        tmp_path,
        repo,
        "--controller",
        "codex",
        "--executors",
        "grok-cli",
        missing_grok=True,
    )

    result = payload["results"][0]
    assert result["attempted"] is False
    assert result["blocker_type"] == "executor_unavailable"
    assert "AGPAIR_GROK_CLI_BIN" in result["reason"]


def test_smoke_harness_reports_claude_oauth_auth_without_dispatch(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)

    payload = _run_smoke(
        tmp_path,
        repo,
        "--controller",
        "codex",
        "--executors",
        "claude-code",
        missing_claude_auth=True,
    )

    result = payload["results"][0]
    assert result["attempted"] is False
    assert result["blocker_type"] == "executor_auth_required"
    assert "claude auth login" in result["reason"]


def test_smoke_harness_abandons_silent_executor_after_no_progress(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)

    payload = _run_smoke(
        tmp_path,
        repo,
        "--controller",
        "codex",
        "--executors",
        "grok-cli",
        "--timeout-seconds",
        "10",
        "--interval-seconds",
        "0.1",
        "--no-progress-seconds",
        "0.5",
        hanging_grok=True,
    )

    assert payload["all_success"] is False
    result = payload["results"][0]
    assert result["attempted"] is True
    assert result["outcome"] == "blocked"
    assert result["blocker_type"] == "no_progress_timeout"
    assert result["wait_payload"]["watchdog_triggered"] is True
    assert result["phase"] == "abandoned"
    assert result["cleanup"]["removed"] is True
    assert not Path(result["worktree_path"]).exists()
