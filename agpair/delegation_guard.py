from __future__ import annotations

import os

DELEGATION_DEPTH_ENV = "AGPAIR_DELEGATION_DEPTH"
PARENT_TASK_ID_ENV = "AGPAIR_PARENT_TASK_ID"
NONINTERACTIVE_ENV = "AGPAIR_NONINTERACTIVE"


def current_delegation_depth(env: dict[str, str] | None = None) -> int:
    source = env if env is not None else os.environ
    try:
        return max(0, int(source.get(DELEGATION_DEPTH_ENV, "0")))
    except (TypeError, ValueError):
        return 0


def nested_delegation_blocked(env: dict[str, str] | None = None) -> bool:
    return current_delegation_depth(env) >= 1


def next_delegation_env(task_id: str, env: dict[str, str] | None = None) -> dict[str, str]:
    depth = current_delegation_depth(env)
    return {
        PARENT_TASK_ID_ENV: task_id,
        DELEGATION_DEPTH_ENV: str(depth + 1),
        NONINTERACTIVE_ENV: "1",
        "CI": "1",
    }
