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
case "${1:-}" in
  --help|-h|help|--version|version)
    echo "fake executor help"
    exit 0
    ;;
esac
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
if printf '%s' "$last" | grep -q 'Report-only smoke'; then
  printf 'Smoke report for %s\\n' "$task_id"
  receipt=$(printf '{"schema_version":"1","task_id":"%s","attempt_no":1,"review_round":0,"status":"EVIDENCE_PACK","summary":"Report smoke","payload":{"claimed_state":"ready_for_review","changed_files":[],"validation_not_run":"report-only fake executor smoke","scope_violations":[],"report":"Smoke report","raw_log_path":"stdout.log","receipt_path":"receipt.json"}}' "$task_id")
  printf '%s\\n' "$receipt"
  if [ -n "$output_file" ]; then
    printf '%s\\n' "$receipt" > "$output_file"
  fi
  exit 0
fi
if [ "${FAKE_EXECUTOR_NO_DIFF:-0}" = "1" ]; then
  printf 'Smoke report for %s\\n' "$task_id"
  receipt=$(printf '{"schema_version":"1","task_id":"%s","attempt_no":1,"review_round":0,"status":"EVIDENCE_PACK","summary":"Smoke report without diff","payload":{"claimed_state":"ready_for_review","changed_files":[],"validation_not_run":"fake executor smoke without diff","scope_violations":[],"report":"Smoke report","raw_log_path":"stdout.log","receipt_path":"receipt.json"}}' "$task_id")
  printf '%s\\n' "$receipt"
  if [ -n "$output_file" ]; then
    printf '%s\\n' "$receipt" > "$output_file"
  fi
  exit 0
fi
mkdir -p tests/fixtures/external_executor_smoke
printf 'fake-executor %s\\n' "$task_id" > tests/fixtures/external_executor_smoke/fake.smoke
printf 'Smoke report for %s\\n' "$task_id"
receipt=$(printf '{"schema_version":"1","task_id":"%s","attempt_no":1,"review_round":0,"status":"EVIDENCE_PACK","summary":"Smoke report","payload":{"claimed_state":"ready_for_review","changed_files":["tests/fixtures/external_executor_smoke/fake.smoke"],"validation_not_run":"fake executor smoke","scope_violations":[],"report":"Smoke report","raw_log_path":"stdout.log","receipt_path":"receipt.json"}}' "$task_id")
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
    no_diff: bool = False,
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
        env["AGPAIR_CLAUDE_CODE_AUTH_MODE"] = "oauth"
    else:
        env["FAKE_CLAUDE_LOGGED_IN"] = "1"
    if no_diff:
        env["FAKE_EXECUTOR_NO_DIFF"] = "1"
    env["AGPAIR_CODEX_BIN"] = str(_fake_executor(bin_dir / "codex"))
    return env


def _run_smoke(
    tmp_path: Path,
    repo: Path,
    *args: str,
    missing_grok: bool = False,
    hanging_grok: bool = False,
    missing_claude_auth: bool = False,
    no_diff: bool = False,
) -> dict:
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--repo-path", str(repo), *args],
        cwd=PROJECT_ROOT,
        env=_env(
            tmp_path,
            missing_grok=missing_grok,
            hanging_grok=hanging_grok,
            missing_claude_auth=missing_claude_auth,
            no_diff=no_diff,
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
        "--scenario",
        "implementation_smoke",
        "--executors",
        "antigravity-cli,grok-cli,claude-code",
        "--timeout-seconds",
        "10",
        "--interval-seconds",
        "0.1",
    )

    assert payload["all_success"] is True
    assert payload["all_selected_attempted"] is True
    assert payload["selection_mode"] == "explicit"
    assert set(payload["registered_executors"]) >= {"antigravity-cli", "grok-cli", "claude-code"}
    assert payload["policy_expected_executors"] is None
    assert payload["policy_suppressed_executors"] == []
    assert payload["scenario"] == "implementation_smoke"
    assert payload["summary_metrics"]["completion_rate"] == 1.0
    assert payload["summary_metrics"]["adoptable_result_rate"] == 1.0
    assert payload["summary_metrics"]["fallback_recommended_rate"] == 0.0
    assert payload["summary_metrics"]["no_progress_rate"] == 0.0
    assert [result["executor_id"] for result in payload["results"]] == [
        "antigravity-cli",
        "grok-cli",
        "claude-code",
    ]
    for result in payload["results"]:
        assert result["executor"] == result["executor_id"]
        assert result["scenario"] == "implementation_smoke"
        assert result["outcome"] == "ready_for_review"
        assert result["internal_role_expected"] == "executor"
        assert result["client_hooks_suppressed_expected"] is True
        assert result["adoptable_result"] in {"yes", "partial"}
        assert result["adoptable"] is True
        assert result["agent_result"]["state"] in {"usable", "needs_review"}
        assert result["agent_result"]["controller_action"] == "review_then_apply"
        assert result["controller_action"] == "review_then_apply"
        assert result["recovery_decision"]["action"] == "review_then_apply"
        assert result["controller_rework"] in {"none", "minor"}
        assert result["fallback_suggestion"] is None
        assert result["failure_class"] is None
        assert result["no_progress"] is False
        assert isinstance(result["protocol_warnings"], list)
        assert result["adoption_blockers"] == []
        assert result["adoption_evidence"]["terminal_receipt"] is True
        assert result["adoption_evidence"]["report"] is True
        assert result["adoption_evidence"]["present_changed_files"] == [
            "tests/fixtures/external_executor_smoke/fake.smoke"
        ]
        assert result["phase"] == "ready_for_review"
        assert result["status"]["isolated_worktree"] is True
        assert result["status"]["dirty_snapshot"]["mode"] == "off"
        assert result["status"]["execution_repo_path"] == result["execution_repo_path"]
        assert result["diff_available"] is True
        assert result["apply_check_ok"] is True
        assert result["diff_payload"]["ok"] is True
        assert result["apply_check_payload"]["ok"] is True
        assert result["controller_wait_outcome"] in {"terminal_success", "controller_lease_expired", None}
        assert result["artifacts"]["stdout_path"]
        assert "tests/fixtures/external_executor_smoke/fake.smoke" in result["git_status_short"]
        assert result["cleanup"]["removed"] is True
        assert not Path(result["worktree_path"]).exists()
        assert not Path(result["execution_repo_path"]).exists()
        if result["executor_id"] == "claude-code":
            assert result["auth_source"] == "oauth"
            assert result["auth_state"] == "ok"
    assert Path(payload["report_path"]).is_file()
    assert ".agpair/smoke/reports" in payload["report_path"]
    report_file_payload = json.loads(Path(payload["report_path"]).read_text(encoding="utf-8"))
    assert report_file_payload["all_success"] is True
    assert report_file_payload["harness_completed"] is True
    assert report_file_payload["report_path"] == payload["report_path"]
    assert report_file_payload["summary_metrics"] == payload["summary_metrics"]


def test_smoke_harness_runs_report_smoke_without_diff_requirement(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)

    payload = _run_smoke(
        tmp_path,
        repo,
        "--controller",
        "codex",
        "--scenario",
        "report_smoke",
        "--executors",
        "antigravity-cli",
        "--timeout-seconds",
        "10",
        "--interval-seconds",
        "0.1",
    )

    assert payload["all_success"] is True
    result = payload["results"][0]
    assert result["scenario"] == "report_smoke"
    assert result["outcome"] == "ready_for_review"
    assert result["adoptable_result"] in {"yes", "partial"}
    assert result["agent_result"]["state"] in {"usable", "needs_review"}
    assert result["agent_result"]["controller_action"] == "use_result"
    assert result["recovery_decision"]["action"] == "use_result"
    assert result["adoption_blockers"] == []
    assert result["adoption_evidence"]["terminal_receipt"] is True
    assert result["adoption_evidence"]["report"] is True
    assert result["adoption_evidence"]["changed_files"] == []
    assert result["git_status_short"] == ""
    assert result["diff_available"] is False
    assert result["apply_check_ok"] is None


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
    assert all(result["scenario"] == "implementation_smoke" for result in payload["results"])
    assert all(result["adoptable_result"] in {"yes", "partial"} for result in payload["results"])
    assert all(result["agent_result"]["controller_action"] == "review_then_apply" for result in payload["results"])
    assert all(result["diff_available"] is True for result in payload["results"])
    assert all(result["apply_check_ok"] is True for result in payload["results"])
    assert all(result["status"]["isolated_worktree"] is True for result in payload["results"])
    assert all(result["status"]["dirty_snapshot"]["mode"] == "off" for result in payload["results"])


def test_smoke_harness_can_exercise_tracked_dirty_snapshot(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    tracked_file = repo / "agpair" / "cli" / "task.py"
    tracked_file.write_text("# dirty fixture\n", encoding="utf-8")

    payload = _run_smoke(
        tmp_path,
        repo,
        "--controller",
        "codex",
        "--executors",
        "grok-cli",
        "--dirty-snapshot",
        "tracked",
        "--timeout-seconds",
        "10",
        "--interval-seconds",
        "0.1",
    )

    result = payload["results"][0]
    assert result["outcome"] == "ready_for_review"
    assert result["status"]["dirty_snapshot"]["mode"] == "tracked"
    assert result["status"]["dirty_snapshot"]["applied"] is True
    assert "agpair/cli/task.py" in result["status"]["dirty_snapshot"]["snapshot"]["status_files"]


def test_smoke_harness_fails_implementation_without_diff(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)

    payload = _run_smoke(
        tmp_path,
        repo,
        "--controller",
        "codex",
        "--scenario",
        "implementation_smoke",
        "--executors",
        "antigravity-cli",
        "--timeout-seconds",
        "10",
        "--interval-seconds",
        "0.1",
        no_diff=True,
    )

    assert payload["all_success"] is False
    result = payload["results"][0]
    assert result["scenario"] == "implementation_smoke"
    assert result["outcome"] == "blocked"
    assert result["diff_available"] is False
    assert result["apply_check_ok"] is False
    assert "diff_missing" in result["adoption_blockers"]
    assert result["failure_class"] == "diff_missing"
    assert result["fallback_suggestion"] == "retry_bounded_slice_or_switch_executor"


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
    assert result["scenario"] == "implementation_smoke"
    assert result["attempted"] is False
    assert result["blocker_type"] == "executor_suppressed"
    assert "suppressed" in result["reason"]
    assert "codex" in result["reason"]


def test_smoke_harness_uses_controller_policy_when_executors_are_omitted(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    policy_path = tmp_path / ".agpair" / "executors.json"
    policy_path.parent.mkdir(parents=True)
    policy_path.write_text(
        json.dumps(
            {
                "version": 1,
                "controllers": {
                    "codex": {
                        "disabled": ["claude-code"],
                        "priority": ["grok-cli", "antigravity-cli", "claude-code"],
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    payload = _run_smoke(
        tmp_path,
        repo,
        "--controller",
        "codex",
        "--timeout-seconds",
        "10",
        "--interval-seconds",
        "0.1",
    )

    assert payload["executors"] == ["grok-cli", "antigravity-cli"]
    assert payload["selection_mode"] == "controller_policy"
    assert payload["policy_expected_executors"] == ["grok-cli", "antigravity-cli"]
    assert payload["policy_suppressed_executors"] == ["codex"]
    assert payload["all_selected_attempted"] is True
    assert payload["all_success"] is True


def test_smoke_harness_reports_policy_disabled_executor_without_dispatch(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    policy_path = tmp_path / ".agpair" / "executors.json"
    policy_path.parent.mkdir(parents=True)
    policy_path.write_text(
        json.dumps({"version": 1, "controllers": {"codex": {"disabled": ["grok-cli"]}}}),
        encoding="utf-8",
    )

    payload = _run_smoke(
        tmp_path,
        repo,
        "--controller",
        "codex",
        "--executors",
        "grok-cli",
    )

    assert payload["all_success"] is False
    result = payload["results"][0]
    assert result["scenario"] == "implementation_smoke"
    assert result["attempted"] is False
    assert result["blocker_type"] == "executor_disabled_by_policy"
    assert "disabled by runtime policy" in result["reason"]


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
    assert result["scenario"] == "implementation_smoke"
    assert result["attempted"] is False
    assert result["blocker_type"] == "executor_unavailable"
    assert result["recovery_decision"]["action"] == "repair_executor"
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
    assert result["scenario"] == "implementation_smoke"
    assert result["attempted"] is False
    assert result["blocker_type"] == "executor_auth_required"
    assert result["internal_role_expected"] == "executor"
    assert result["client_hooks_suppressed_expected"] is True
    assert result["auth_source"] == "oauth"
    assert result["auth_state"] == "executor_auth_required"
    assert result["recovery_decision"]["action"] == "repair_executor"
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
    assert result["scenario"] == "implementation_smoke"
    assert result["attempted"] is True
    assert result["outcome"] == "blocked"
    assert result["adoptable_result"] == "no"
    assert result["adoptable"] is False
    assert "task_phase_abandoned" in result["adoption_blockers"]
    assert "no_progress_budget_exceeded" in result["adoption_blockers"]
    assert result["blocker_type"] == "no_progress_budget_exceeded"
    assert result["failure_class"] == "no_progress_budget_exceeded"
    assert result["fallback_suggestion"] == "inspect_status_then_retry_or_switch_executor"
    assert result["recovery_decision"]["action"] == "retry_same_executor"
    assert result["no_progress"] is True
    assert payload["summary_metrics"]["fallback_recommended_rate"] == 1.0
    assert payload["summary_metrics"]["no_progress_rate"] == 1.0
    assert result["wait_payload"]["watchdog_triggered"] is True
    assert result["phase"] == "abandoned"
    assert result["cleanup"]["removed"] is True
    assert not Path(result["worktree_path"]).exists()
