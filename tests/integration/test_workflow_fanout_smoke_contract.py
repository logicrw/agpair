from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from agpair.workflows.presets import build_fanout_manifest
from agpair.workflows.schema import validate_manifest


def test_real_smoke_can_generate_fanout_manifest_without_dispatch(tmp_path: Path) -> None:
    manifest = build_fanout_manifest(
        controller="codex",
        mode="review",
        topic="Review fanout smoke",
        lanes=["grok-cli:primary", "grok-cli:adversarial"],
        repo_path=str(tmp_path),
    )

    validated = validate_manifest(manifest, require_repo_path=True)

    assert any(node["kind"] == "synthesis" for node in validated.nodes)
    assert any(node["kind"] == "gate" for node in validated.nodes)
    assert [node["role"] for node in validated.nodes if node["kind"] == "task"] == ["primary", "adversarial"]


def test_smoke_fanout_script_reports_contract() -> None:
    proc = subprocess.run(
        [
            sys.executable,
            "scripts/smoke_fanout.py",
            "--controller",
            "codex",
            "--mode",
            "review",
            "--lane",
            "grok-cli:primary",
            "--lane",
            "grok-cli:adversarial",
            "--json",
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["all_success"] is True
    assert payload["dispatch_attempted"] is False
    assert payload["lane_count"] == 2
    assert payload["has_synthesis"] is True
    assert payload["has_gate"] is True


def test_smoke_fanout_default_codex_lanes_include_grok_and_antigravity() -> None:
    proc = subprocess.run(
        [
            sys.executable,
            "scripts/smoke_fanout.py",
            "--controller",
            "codex",
            "--mode",
            "review",
            "--json",
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    task_executors = [
        node["executor"]
        for node in payload["manifest"]["nodes"]
        if node["kind"] == "task"
    ]
    assert task_executors == ["antigravity-cli", "grok-cli", "claude-code"]


def test_smoke_fanout_default_claude_lanes_suppress_external_claude_code() -> None:
    proc = subprocess.run(
        [
            sys.executable,
            "scripts/smoke_fanout.py",
            "--controller",
            "claude-code",
            "--mode",
            "review",
            "--json",
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    task_executors = [
        node["executor"]
        for node in payload["manifest"]["nodes"]
        if node["kind"] == "task"
    ]
    assert task_executors == ["antigravity-cli", "grok-cli", "codex"]
