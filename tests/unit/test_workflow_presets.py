import pytest

from agpair.workflows.presets import FanoutPresetError, build_fanout_manifest
from agpair.workflows.schema import validate_manifest


def test_build_review_panel_manifest_is_valid_workflow() -> None:
    manifest = build_fanout_manifest(
        controller="codex",
        mode="review",
        topic="Review workflow synthesis risks",
        lanes=["grok-cli:primary", "antigravity-cli:second-opinion"],
        scope="agpair/workflows",
        repo_path="/tmp/repo",
    )

    validated = validate_manifest(manifest, require_repo_path=True)

    assert validated.name == "fanout-review"
    assert validated.controller == "codex"
    assert validated.repo_path == "/tmp/repo"
    assert validated.manifest["source_policy"]["enforcement"] == "instruction"
    assert [node["id"] for node in validated.nodes] == [
        "primary",
        "second-opinion",
        "synthesis",
        "gate",
    ]
    assert validated.nodes[0]["executor"] == "grok-cli"
    assert validated.nodes[1]["role"] == "second-opinion"
    assert validated.nodes[2]["kind"] == "synthesis"
    assert validated.nodes[2]["depends_on"] == ["primary", "second-opinion"]
    assert validated.nodes[3]["kind"] == "gate"
    assert validated.nodes[3]["depends_on"] == ["synthesis"]


def test_build_implementation_panel_uses_mutating_policy_and_isolated_worktree() -> None:
    manifest = build_fanout_manifest(
        controller="claude-code",
        mode="implementation",
        topic="Implement terminal receipt salvage",
        lanes=["grok-cli:candidate-a", "grok-cli:candidate-b"],
        scope="agpair/terminal_receipts.py",
    )

    task_nodes = [node for node in manifest["nodes"] if node["kind"] == "task"]

    assert manifest["authorization_profile"] == "local_mutating"
    assert manifest["completion_policy"] == "direct_commit"
    assert [node["isolated_worktree"] for node in task_nodes] == [True, True]
    assert task_nodes[0]["role"] == "candidate-a"
    assert task_nodes[1]["role"] == "candidate-b"


def test_build_fanout_manifest_rejects_missing_lanes() -> None:
    with pytest.raises(FanoutPresetError, match="at least one lane"):
        build_fanout_manifest(controller="codex", mode="review", topic="Review", lanes=[])
