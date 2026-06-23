from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

from agpair.executors.routing import validate_supported_executor
from agpair.models import validate_authorization_profile
from agpair.workflows.schema import validate_manifest
from agpair.workflows.source_policy import build_source_policy

SUPPORTED_FANOUT_MODES: Final = frozenset({"review", "research", "implementation", "test-fix"})
ROLE_RE = re.compile(r"[^A-Za-z0-9_.-]+")
ROLE_TOKEN_RE = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True, slots=True)
class FanoutPresetError(Exception):
    message: str

    def __str__(self) -> str:
        return self.message


def build_fanout_manifest(
    *,
    controller: str,
    mode: str,
    topic: str,
    lanes: list[str],
    scope: str | None = None,
    repo_path: str | None = None,
    isolated_worktree: bool = False,
) -> dict:
    normalized_mode = _normalize_mode(mode)
    topic_text = topic.strip()
    if not topic_text:
        raise FanoutPresetError("fanout topic is required")
    lane_specs = [_parse_lane(item, index) for index, item in enumerate(lanes, start=1)]
    if not lane_specs:
        raise FanoutPresetError("fanout requires at least one lane")
    auth_profile, completion_policy = _mode_policy(normalized_mode)
    use_isolated_worktree = isolated_worktree or completion_policy != "report"
    task_nodes = [
        _task_node(
            executor=executor,
            role=role,
            mode=normalized_mode,
            topic=topic_text,
            scope=scope,
            authorization_profile=auth_profile,
            completion_policy=completion_policy,
            isolated_worktree=use_isolated_worktree,
        )
        for executor, role in lane_specs
    ]
    lane_ids = [str(node["id"]) for node in task_nodes]
    manifest = {
        "version": 1,
        "name": f"fanout-{normalized_mode}",
        "controller": controller,
        "mode": normalized_mode,
        "source_policy": build_source_policy(mode=normalized_mode),
        "coordination_policy": _coordination_policy(normalized_mode),
        "authorization_profile": auth_profile,
        "completion_policy": completion_policy,
        "limits": {"max_parallel_tasks": min(max(len(lane_ids), 1), 8), "max_child_tasks": len(lane_ids) + 2},
        "nodes": [
            *task_nodes,
            _synthesis_node(depends_on=lane_ids, mode=normalized_mode, topic=topic_text),
            _gate_node(),
        ],
    }
    if repo_path:
        manifest["repo_path"] = repo_path
    validate_manifest(manifest, require_repo_path=bool(repo_path))
    return manifest


def _parse_lane(raw: str, index: int) -> tuple[str, str]:
    if not raw.strip():
        raise FanoutPresetError("fanout lane cannot be empty")
    executor_raw, _, role_raw = raw.partition(":")
    executor = validate_supported_executor(executor_raw.strip())
    role = _safe_role(role_raw.strip() or f"lane-{index}")
    return executor, role


def _normalize_mode(mode: str) -> str:
    value = mode.strip().lower().replace("_", "-")
    if value not in SUPPORTED_FANOUT_MODES:
        raise FanoutPresetError(f"unsupported fanout mode: {mode}")
    return value


def _mode_policy(mode: str) -> tuple[str, str]:
    if mode in {"implementation", "test-fix"}:
        return validate_authorization_profile("local_mutating"), "direct_commit"
    return validate_authorization_profile("local_readonly"), "report"


def _safe_role(role: str) -> str:
    normalized = ROLE_RE.sub("-", role.strip()).strip("-")
    if not normalized:
        raise FanoutPresetError("fanout lane role cannot be empty")
    return normalized[:60]


def _coordination_policy(mode: str) -> dict:
    expected_roles = ["worker", "verifier"] if mode in {"implementation", "test-fix"} else ["thinker", "verifier"]
    return {
        "style": "fanout",
        "expected_roles": expected_roles,
        "optional_roles": ["worker"] if mode in {"review", "research"} else ["thinker"],
        "stop_rule": "controller_verifies",
        "max_coordination_turns": 5,
        "routing_basis": ["mode", "lane role", "executor availability"],
    }


def _role_for_lane(*, mode: str, role: str) -> str:
    tokens = {item for item in ROLE_TOKEN_RE.split(role.lower()) if item}
    verifier_tokens = {
        "adversarial",
        "audit",
        "critic",
        "qa",
        "review",
        "reviewer",
        "second",
        "test",
        "tester",
        "verification",
        "verifier",
        "verify",
    }
    thinker_tokens = {
        "analysis",
        "architect",
        "explore",
        "planner",
        "primary",
        "research",
        "researcher",
        "thinker",
    }
    worker_tokens = {"builder", "candidate", "fix", "implementer", "patch", "worker"}
    if tokens & verifier_tokens:
        return "verifier"
    if tokens & worker_tokens:
        return "worker"
    if tokens & thinker_tokens:
        return "thinker"
    return "worker" if mode in {"implementation", "test-fix"} else "thinker"


def _task_node(
    *,
    executor: str,
    role: str,
    mode: str,
    topic: str,
    scope: str | None,
    authorization_profile: str,
    completion_policy: str,
    isolated_worktree: bool,
) -> dict:
    coordination_role = _role_for_lane(mode=mode, role=role)
    required_changes = "none; return a report only" if completion_policy == "report" else "produce the smallest safe candidate diff"
    exit_criteria = (
        "Return a concise report with findings, evidence paths, uncertainties, and recommended controller action."
        if completion_policy == "report"
        else "Commit one complete candidate change, include tests, and report evidence for controller review."
    )
    return {
        "id": role,
        "kind": "task",
        "role": role,
        "coordination_role": coordination_role,
        "executor": executor,
        "authorization_profile": authorization_profile,
        "completion_policy": completion_policy,
        "isolated_worktree": isolated_worktree,
        "body": "\n".join([
            f"Goal: {topic}",
            f"Mode: {mode}",
            f"Lane role: {role}",
            f"Coordination role: {coordination_role}",
            f"Scope: {scope or 'Use the repository context relevant to the goal.'}",
            f"Required changes: {required_changes}.",
            f"Exit criteria: {exit_criteria}",
        ]),
    }


def _synthesis_node(*, depends_on: list[str], mode: str, topic: str) -> dict:
    return {
        "id": "synthesis",
        "kind": "synthesis",
        "role": "synthesis",
        "coordination_role": "synthesizer",
        "depends_on": depends_on,
        "authorization_profile": "local_readonly",
        "completion_policy": "report",
        "body": "\n".join([
            f"Goal: Synthesize fanout panel evidence for {topic}",
            f"Mode: {mode}",
            "Required changes: none.",
            "Exit criteria: Return consensus, contradictions, unique_insights, blind_spots, recommended_controller_action, and evidence references. Do not hide failed or partial lanes.",
        ]),
    }


def _gate_node() -> dict:
    return {
        "id": "gate",
        "kind": "gate",
        "role": "gate",
        "coordination_role": "gate",
        "depends_on": ["synthesis"],
        "authorization_profile": "local_readonly",
        "completion_policy": "report",
    }
