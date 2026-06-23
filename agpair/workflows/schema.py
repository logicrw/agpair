from __future__ import annotations

import json
import re
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from agpair.completion import normalize_completion_policy
from agpair.executors.routing import validate_supported_executor
from agpair.models import validate_authorization_profile
from agpair.roles import normalize_coordination_role
from agpair.workflows.models import NODE_KINDS

FORBIDDEN_FIELDS = frozenset({
    "workflow_script",
    "python",
    "javascript",
    "shell",
    "command",
    "commands",
    "setup_commands",
    "teardown_commands",
    "postinstall",
    "preinstall",
})
NODE_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
MAX_NODES_HARD = 1000
MAX_NODES_DEFAULT = 100
MAX_DEPENDENCIES_PER_NODE = 20
MAX_BODY_CHARS = 50000
DEFAULT_LIMITS = {
    "max_parallel_tasks": 4,
    "max_child_tasks": 20,
    "max_retries_per_node": 1,
    "max_runtime_seconds": 14400,
    "max_watch_events": 500,
}


@dataclass(frozen=True)
class WorkflowManifest:
    manifest: dict[str, Any]

    @property
    def version(self) -> int:
        return int(self.manifest.get("version", 1))

    @property
    def controller(self) -> str:
        return str(self.manifest.get("controller") or "generic")

    @property
    def name(self) -> str:
        return str(self.manifest.get("name") or "")

    @property
    def repo_path(self) -> str | None:
        value = self.manifest.get("repo_path")
        return value if isinstance(value, str) and value.strip() else None

    @property
    def limits(self) -> dict[str, int]:
        value = self.manifest.get("limits")
        return value if isinstance(value, dict) else dict(DEFAULT_LIMITS)

    @property
    def max_parallel(self) -> int:
        limits = self.limits
        return int(limits.get("max_parallel_tasks", DEFAULT_LIMITS["max_parallel_tasks"]))

    @property
    def max_child_tasks(self) -> int:
        limits = self.limits
        return int(limits.get("max_child_tasks", DEFAULT_LIMITS["max_child_tasks"]))

    @property
    def max_retries_per_node(self) -> int:
        limits = self.limits
        return int(limits.get("max_retries_per_node", DEFAULT_LIMITS["max_retries_per_node"]))

    @property
    def nodes(self) -> list[dict[str, Any]]:
        nodes = self.manifest.get("nodes")
        return nodes if isinstance(nodes, list) else []


class WorkflowManifestError(ValueError):
    pass


def load_manifest_file(
    path: Path,
    *,
    allow_large_workflow: bool = False,
    require_repo_path: bool = False,
    repo_path: str | None = None,
) -> WorkflowManifest:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() in {".yaml", ".yml"}:
        try:
            import yaml  # type: ignore
        except Exception as exc:
            raise WorkflowManifestError("YAML manifests require PyYAML; use JSON or install PyYAML") from exc
        parsed = yaml.safe_load(text)
    else:
        parsed = json.loads(text)
    return validate_manifest(
        parsed,
        allow_large_workflow=allow_large_workflow,
        require_repo_path=require_repo_path,
        repo_path=repo_path,
    )


def validate_manifest(
    raw: Any,
    *,
    allow_large_workflow: bool = False,
    require_repo_path: bool = False,
    repo_path: str | None = None,
) -> WorkflowManifest:
    if not isinstance(raw, dict):
        raise WorkflowManifestError("workflow manifest must be an object")
    _reject_forbidden_fields(raw)
    manifest = deepcopy(raw)
    version = manifest.get("version")
    if version != 1:
        raise WorkflowManifestError("workflow manifest version must be 1")
    name = manifest.get("name")
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 120:
        raise WorkflowManifestError("workflow name must be a non-empty string with max length 120")
    manifest["name"] = name.strip()
    controller = manifest.get("controller", "generic")
    if controller in {"claude", "claude_code"}:
        controller = "claude-code"
    if controller not in {"codex", "claude-code", "generic"}:
        raise WorkflowManifestError("workflow controller must be codex, claude-code, or generic")
    manifest["controller"] = controller
    if repo_path:
        manifest["repo_path"] = repo_path
    if require_repo_path and not (isinstance(manifest.get("repo_path"), str) and str(manifest.get("repo_path")).strip()):
        raise WorkflowManifestError("workflow requires repo_path or CLI --repo-path/--target")
    limits = _normalize_limits(manifest.get("limits"), allow_large_workflow=allow_large_workflow)
    manifest["limits"] = limits
    coordination_policy = _normalize_coordination_policy(manifest.get("coordination_policy"))
    if coordination_policy is not None:
        manifest["coordination_policy"] = coordination_policy
    nodes = manifest.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        raise WorkflowManifestError("workflow manifest requires a non-empty nodes array")
    if len(nodes) > MAX_NODES_HARD:
        raise WorkflowManifestError(f"workflow manifest exceeds hard max nodes: {MAX_NODES_HARD}")
    child_capable = sum(1 for node in nodes if isinstance(node, dict) and node.get("kind", "task") != "gate")
    if child_capable > limits["max_child_tasks"]:
        raise WorkflowManifestError("workflow child node count exceeds max_child_tasks")
    if child_capable > MAX_NODES_DEFAULT and not allow_large_workflow:
        raise WorkflowManifestError("large workflow requires --allow-large-workflow")
    default_auth = manifest.get("authorization_profile", "local_mutating")
    default_auth = validate_authorization_profile(str(default_auth))
    manifest["authorization_profile"] = default_auth
    default_completion = normalize_completion_policy(str(manifest.get("completion_policy", "auto")))
    manifest["completion_policy"] = default_completion
    ids: set[str] = set()
    by_id: dict[str, dict[str, Any]] = {}
    normalized_nodes: list[dict[str, Any]] = []
    for raw_node in nodes:
        if not isinstance(raw_node, dict):
            raise WorkflowManifestError("workflow nodes must be objects")
        node = deepcopy(raw_node)
        node_id = node.get("id")
        if not isinstance(node_id, str) or not node_id.strip() or not NODE_ID_RE.match(node_id):
            raise WorkflowManifestError("workflow node id must match ^[A-Za-z0-9_.-]+$")
        if node_id in ids:
            raise WorkflowManifestError(f"duplicate workflow node id: {node_id}")
        ids.add(node_id)
        kind = node.get("kind", "task")
        if kind not in NODE_KINDS:
            raise WorkflowManifestError(f"node {node_id} kind must be one of: {', '.join(sorted(NODE_KINDS))}")
        node["kind"] = kind
        node["coordination_role"] = normalize_coordination_role(
            node.get("coordination_role")
        ) or _default_coordination_role_for_kind(kind)
        deps = node.get("depends_on", [])
        if deps is None:
            deps = []
        if not isinstance(deps, list) or not all(isinstance(dep, str) for dep in deps):
            raise WorkflowManifestError(f"node {node_id} depends_on must be a string array")
        if len(deps) > MAX_DEPENDENCIES_PER_NODE:
            raise WorkflowManifestError(f"node {node_id} exceeds max dependency count")
        node["depends_on"] = deps
        body = node.get("body") or node.get("prompt") or ""
        if kind != "gate" and (not isinstance(body, str) or not body.strip()):
            raise WorkflowManifestError(f"node {node_id} requires body or prompt")
        if isinstance(body, str) and len(body) > MAX_BODY_CHARS:
            raise WorkflowManifestError(f"node {node_id} body exceeds max chars")
        if isinstance(body, str):
            node["body"] = body
        if kind == "synthesis":
            node.setdefault("authorization_profile", "local_readonly")
            node.setdefault("completion_policy", "report")
        if kind == "gate":
            node.setdefault("authorization_profile", "local_readonly")
            node.setdefault("completion_policy", "report")
        node["authorization_profile"] = validate_authorization_profile(str(node.get("authorization_profile", default_auth)))
        node["completion_policy"] = normalize_completion_policy(str(node.get("completion_policy", default_completion)))
        node["max_retries"] = _normalize_int(
            node.get("max_retries", limits["max_retries_per_node"]),
            field=f"node {node_id} max_retries",
            min_value=0,
            max_value=10,
        )
        node["allow_partial"] = bool(node.get("allow_partial", False))
        node["isolated_worktree"] = bool(node.get("isolated_worktree", False))
        if node.get("executor") is not None:
            node["executor"] = validate_supported_executor(str(node["executor"]))
        by_id[node_id] = node
        normalized_nodes.append(node)
    for node_id, node in by_id.items():
        for dep in node.get("depends_on") or []:
            if dep not in by_id:
                raise WorkflowManifestError(f"node {node_id} depends on unknown node {dep}")
    _assert_acyclic(by_id)
    if len(normalized_nodes) != 1 and not any(node.get("kind") in {"synthesis", "gate"} for node in normalized_nodes):
        raise WorkflowManifestError("workflow requires at least one synthesis or gate node unless it has exactly one task node")
    manifest["nodes"] = normalized_nodes
    return WorkflowManifest(manifest)


def _normalize_limits(raw: Any, *, allow_large_workflow: bool) -> dict[str, int]:
    limits = dict(DEFAULT_LIMITS)
    if raw is not None:
        if not isinstance(raw, dict):
            raise WorkflowManifestError("workflow limits must be an object")
        for key in DEFAULT_LIMITS:
            if key in raw:
                limits[key] = _normalize_int(raw[key], field=f"limits.{key}", min_value=1, max_value=604800)
    limits["max_parallel_tasks"] = _normalize_int(limits["max_parallel_tasks"], field="limits.max_parallel_tasks", min_value=1, max_value=16)
    limits["max_child_tasks"] = _normalize_int(limits["max_child_tasks"], field="limits.max_child_tasks", min_value=1, max_value=MAX_NODES_HARD)
    if limits["max_child_tasks"] > MAX_NODES_DEFAULT and not allow_large_workflow:
        raise WorkflowManifestError("large workflow requires --allow-large-workflow")
    limits["max_retries_per_node"] = _normalize_int(limits["max_retries_per_node"], field="limits.max_retries_per_node", min_value=0, max_value=10)
    limits["max_runtime_seconds"] = _normalize_int(limits["max_runtime_seconds"], field="limits.max_runtime_seconds", min_value=60, max_value=604800)
    limits["max_watch_events"] = _normalize_int(limits["max_watch_events"], field="limits.max_watch_events", min_value=1, max_value=100000)
    return limits


def _normalize_coordination_policy(raw: Any) -> dict[str, Any] | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise WorkflowManifestError("coordination_policy must be an object")
    policy = deepcopy(raw)
    style = str(policy.get("style") or "role_orchestrated").strip().lower()
    if style not in {"role_orchestrated", "fanout", "single_role"}:
        raise WorkflowManifestError("coordination_policy.style must be role_orchestrated, fanout, or single_role")
    policy["style"] = style
    policy["expected_roles"] = _normalize_role_list(
        policy.get("expected_roles", []),
        field="coordination_policy.expected_roles",
    )
    policy["optional_roles"] = _normalize_role_list(
        policy.get("optional_roles", []),
        field="coordination_policy.optional_roles",
    )
    stop_rule = str(policy.get("stop_rule") or "controller_verifies").strip().lower()
    if stop_rule not in {"controller_verifies", "budget_exhausted", "manual"}:
        raise WorkflowManifestError("coordination_policy.stop_rule must be controller_verifies, budget_exhausted, or manual")
    policy["stop_rule"] = stop_rule
    policy["max_coordination_turns"] = _normalize_int(
        policy.get("max_coordination_turns", 5),
        field="coordination_policy.max_coordination_turns",
        min_value=1,
        max_value=20,
    )
    routing_basis = policy.get("routing_basis", [])
    if not isinstance(routing_basis, list) or not all(isinstance(item, str) for item in routing_basis):
        raise WorkflowManifestError("coordination_policy.routing_basis must be a string array")
    policy["routing_basis"] = [item.strip() for item in routing_basis if item.strip()]
    return policy


def _normalize_role_list(raw: Any, *, field: str) -> list[str]:
    if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
        raise WorkflowManifestError(f"{field} must be a string array")
    return [normalize_coordination_role(item) or "general" for item in raw]


def _default_coordination_role_for_kind(kind: str) -> str:
    mapping = {
        "synthesis": "synthesizer",
        "gate": "gate",
        "verification": "verifier",
    }
    return mapping.get(kind, "general")


def _normalize_int(value: Any, *, field: str, min_value: int, max_value: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise WorkflowManifestError(f"{field} must be an integer")
    if value < min_value or value > max_value:
        raise WorkflowManifestError(f"{field} must be between {min_value} and {max_value}")
    return value


def _reject_forbidden_fields(value: Any, path: str = "$") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if isinstance(key, str) and key in FORBIDDEN_FIELDS:
                raise WorkflowManifestError(f"forbidden workflow field at {path}.{key}: {key}")
            _reject_forbidden_fields(child, f"{path}.{key}")
    elif isinstance(value, list):
        for idx, child in enumerate(value):
            _reject_forbidden_fields(child, f"{path}[{idx}]")


def _assert_acyclic(nodes: dict[str, dict[str, Any]]) -> None:
    visiting: list[str] = []
    visited: set[str] = set()

    def dfs(node_id: str) -> None:
        if node_id in visiting:
            cycle = [*visiting[visiting.index(node_id):], node_id]
            raise WorkflowManifestError("workflow dependency cycle detected: " + " -> ".join(cycle))
        if node_id in visited:
            return
        visiting.append(node_id)
        for dep in nodes[node_id].get("depends_on") or []:
            dfs(dep)
        visiting.pop()
        visited.add(node_id)

    for node_id in nodes:
        dfs(node_id)
