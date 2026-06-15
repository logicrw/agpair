#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agpair.workflows.presets import build_fanout_manifest
from agpair.workflows.schema import validate_manifest


def _default_lanes(controller: str) -> list[str]:
    if controller == "claude-code":
        return ["antigravity-cli:antigravity", "grok-cli:grok", "codex:codex"]
    return ["antigravity-cli:antigravity", "grok-cli:grok", "claude-code:claude-code"]


def _node_kinds(manifest: dict[str, Any]) -> list[str]:
    nodes = manifest.get("nodes")
    if not isinstance(nodes, list):
        return []
    return [str(node.get("kind") or "task") for node in nodes if isinstance(node, dict)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build and validate an AGPair fanout workflow smoke manifest.")
    parser.add_argument("--controller", choices=("codex", "claude-code", "generic"), default="codex")
    parser.add_argument("--mode", choices=("review", "research", "implementation", "test-fix"), default="review")
    parser.add_argument("--topic", default="AGPair fanout smoke")
    parser.add_argument("--lane", action="append", dest="lanes", help="Executor lane as executor:role. Repeat for multiple lanes.")
    parser.add_argument("--scope")
    parser.add_argument("--repo-path")
    parser.add_argument("--isolated-worktree", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    lanes = args.lanes or _default_lanes(args.controller)
    manifest = build_fanout_manifest(
        controller=args.controller,
        mode=args.mode,
        topic=args.topic,
        lanes=lanes,
        scope=args.scope,
        repo_path=args.repo_path,
        isolated_worktree=args.isolated_worktree,
    )
    validated = validate_manifest(manifest, require_repo_path=bool(args.repo_path))
    kinds = _node_kinds(validated.manifest)
    payload = {
        "schema_version": "1",
        "fanout_smoke": True,
        "all_success": "synthesis" in kinds and "gate" in kinds and kinds.count("task") == len(lanes),
        "manifest_valid": True,
        "controller": validated.controller,
        "mode": validated.manifest.get("mode"),
        "lane_count": kinds.count("task"),
        "has_synthesis": "synthesis" in kinds,
        "has_gate": "gate" in kinds,
        "dispatch_attempted": False,
        "manifest": validated.manifest,
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"fanout smoke ok: {payload['lane_count']} lanes, synthesis={payload['has_synthesis']}, gate={payload['has_gate']}")
    return 0 if payload["all_success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
