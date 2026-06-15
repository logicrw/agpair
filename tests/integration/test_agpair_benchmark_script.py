from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from tests.integration.test_real_executor_smoke_harness import _env


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_benchmark_script_runs_fake_executor_matrix(tmp_path: Path) -> None:
    proc = subprocess.run(
        [
            sys.executable,
            "scripts/agpair_benchmark.py",
            "--controller",
            "codex",
            "--scenario",
            "report_smoke",
            "--executors",
            "antigravity-cli,grok-cli",
            "--timeout-seconds",
            "20",
            "--interval-seconds",
            "0.1",
            "--json",
        ],
        cwd=PROJECT_ROOT,
        env=_env(tmp_path),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=60,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr + proc.stdout
    payload = json.loads(proc.stdout)
    assert payload["selection_mode"] == "explicit"
    assert payload["executors"] == ["antigravity-cli", "grok-cli"]
    assert payload["summary"]["all_success"] is True
    assert payload["adaptive_wait_expected"] is True
    assert len(payload["results"]) == 2
    assert [item["executor_id"] for item in payload["results"]] == ["antigravity-cli", "grok-cli"]
    assert all(item["success"] is True for item in payload["results"])
    assert Path(payload["report_path"]).is_file()
