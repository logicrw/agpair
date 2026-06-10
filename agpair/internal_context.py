from __future__ import annotations

import os
from collections.abc import Mapping

from agpair.delegation_guard import DELEGATION_DEPTH_ENV, next_delegation_env

INTERNAL_ROLE_ENV = "AGPAIR_INTERNAL_ROLE"
SUPPRESS_CLIENT_HOOKS_ENV = "AGPAIR_SUPPRESS_CLIENT_HOOKS"
NONINTERACTIVE_ENV = "AGPAIR_NONINTERACTIVE"

INTERNAL_ROLE_PROBE = "probe"
INTERNAL_ROLE_EXECUTOR = "executor"
INTERNAL_ROLE_SMOKE = "smoke"
INTERNAL_ROLES = frozenset(
    {INTERNAL_ROLE_PROBE, INTERNAL_ROLE_EXECUTOR, INTERNAL_ROLE_SMOKE}
)
TRUE_ENV_VALUES = frozenset({"1", "true", "yes", "on"})


def _source(env: Mapping[str, str] | None = None) -> Mapping[str, str]:
    return os.environ if env is None else env


def internal_role(env: Mapping[str, str] | None = None) -> str | None:
    role = _source(env).get(INTERNAL_ROLE_ENV, "").strip().lower()
    return role or None


def client_hooks_suppressed(env: Mapping[str, str] | None = None) -> bool:
    source = _source(env)
    explicit = source.get(SUPPRESS_CLIENT_HOOKS_ENV, "").strip().lower()
    if explicit in TRUE_ENV_VALUES:
        return True
    return internal_role(source) in INTERNAL_ROLES


def build_internal_probe_env(base_env: Mapping[str, str] | None = None) -> dict[str, str]:
    env = dict(os.environ if base_env is None else base_env)
    env.update(
        {
            INTERNAL_ROLE_ENV: INTERNAL_ROLE_PROBE,
            SUPPRESS_CLIENT_HOOKS_ENV: "1",
            NONINTERACTIVE_ENV: "1",
            "CI": "1",
            DELEGATION_DEPTH_ENV: "1",
        }
    )
    return env


def build_internal_executor_env(
    task_id: str,
    base_env: Mapping[str, str] | None = None,
) -> dict[str, str]:
    env = dict(os.environ if base_env is None else base_env)
    env.update(next_delegation_env(task_id, env))
    env.update(
        {
            INTERNAL_ROLE_ENV: INTERNAL_ROLE_EXECUTOR,
            SUPPRESS_CLIENT_HOOKS_ENV: "1",
            NONINTERACTIVE_ENV: "1",
            "CI": "1",
        }
    )
    return env
