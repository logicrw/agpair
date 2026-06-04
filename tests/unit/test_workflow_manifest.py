import pytest

from agpair.workflows.schema import WorkflowManifestError, validate_manifest


def base_manifest():
    return {
        "version": 1,
        "name": "repo-wide-review",
        "controller": "codex",
        "authorization_profile": "local_readonly",
        "completion_policy": "auto",
        "nodes": [
            {
                "id": "scan",
                "kind": "task",
                "body": "Goal: scan. Required changes: none.",
                "authorization_profile": "local_readonly",
                "completion_policy": "report",
                "depends_on": [],
            },
            {
                "id": "synthesize",
                "kind": "synthesis",
                "body": "Goal: synthesize.",
                "depends_on": ["scan"],
            },
        ],
    }


def test_valid_minimal_manifest_passes() -> None:
    manifest = validate_manifest(base_manifest())
    assert manifest.version == 1
    assert manifest.controller == "codex"
    assert manifest.limits["max_parallel_tasks"] == 4
    assert manifest.nodes[1]["completion_policy"] == "report"


def test_rejects_cycle() -> None:
    raw = base_manifest()
    raw["nodes"][0]["depends_on"] = ["synthesize"]
    with pytest.raises(WorkflowManifestError, match="scan.*synthesize|synthesize.*scan"):
        validate_manifest(raw)


def test_rejects_unknown_dependency() -> None:
    raw = base_manifest()
    raw["nodes"][1]["depends_on"] = ["missing"]
    with pytest.raises(WorkflowManifestError, match="unknown node missing"):
        validate_manifest(raw)


def test_rejects_script_fields_at_any_depth() -> None:
    raw = base_manifest()
    raw["nodes"][0]["metadata"] = {"command": "pytest"}
    with pytest.raises(WorkflowManifestError, match=r"\$\.nodes\[0\]\.metadata\.command"):
        validate_manifest(raw)


def test_rejects_invalid_completion_policy() -> None:
    raw = base_manifest()
    raw["nodes"][0]["completion_policy"] = "merge"
    with pytest.raises(ValueError, match="completion policy"):
        validate_manifest(raw)


def test_rejects_large_workflow_without_flag() -> None:
    raw = base_manifest()
    raw["limits"] = {"max_child_tasks": 101}
    raw["nodes"] = [
        {
            "id": f"node{i}",
            "kind": "task",
            "body": "Goal: scan. Required changes: none.",
            "authorization_profile": "local_readonly",
            "completion_policy": "report",
            "depends_on": [],
        }
        for i in range(101)
    ] + [
        {
            "id": "synthesize",
            "kind": "synthesis",
            "body": "Goal: synthesize.",
            "depends_on": ["node0"],
        }
    ]
    with pytest.raises(WorkflowManifestError, match="large workflow"):
        validate_manifest(raw)


def test_report_nodes_keep_report_completion_policy() -> None:
    manifest = validate_manifest(base_manifest())
    assert manifest.nodes[0]["completion_policy"] == "report"
