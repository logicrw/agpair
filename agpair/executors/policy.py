from __future__ import annotations

import os
import shutil
from dataclasses import asdict, dataclass
from typing import Any

CANONICAL_EXECUTOR_IDS = ("antigravity-cli", "grok-cli", "claude-code", "codex")
LEGACY_EXECUTOR_ALIASES = {
    "antigravity": "antigravity-cli",
    "antigravity_cli": "antigravity-cli",
    "grok": "grok-cli",
    "grok_cli": "grok-cli",
    "claude": "claude-code",
    "claude_code": "claude-code",
    "codex_cli": "codex",
}
REJECTED_EXECUTOR_IDS = {"gemini", "gemini-cli", "gemini_cli"}


@dataclass(frozen=True)
class ExecutorSpec:
    executor_id: str
    env_var: str
    env_aliases: tuple[str, ...]
    default_binary: str
    default_priority: int
    enabled_by_default: bool
    default_authorization_profile: str
    is_mutating: bool
    is_concurrency_safe: bool
    requires_human_interaction: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ExecutorPolicyDecision:
    controller: str
    selected_executor: str | None
    eligible_executors: tuple[str, ...]
    suppressed_executors: tuple[str, ...]
    rejected_executor: str | None
    allow_self_executor: bool
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


EXECUTOR_SPECS: dict[str, ExecutorSpec] = {
    "antigravity-cli": ExecutorSpec(
        executor_id="antigravity-cli",
        env_var="AGPAIR_ANTIGRAVITY_CLI_BIN",
        env_aliases=("AGPAIR_ANTIGRAVITY_CLI",),
        default_binary="antigravity",
        default_priority=10,
        enabled_by_default=True,
        default_authorization_profile="local_mutating",
        is_mutating=True,
        is_concurrency_safe=False,
    ),
    "grok-cli": ExecutorSpec(
        executor_id="grok-cli",
        env_var="AGPAIR_GROK_CLI_BIN",
        env_aliases=("AGPAIR_GROK_CLI",),
        default_binary="grok",
        default_priority=20,
        enabled_by_default=True,
        default_authorization_profile="local_readonly",
        is_mutating=True,
        is_concurrency_safe=False,
    ),
    "claude-code": ExecutorSpec(
        executor_id="claude-code",
        env_var="AGPAIR_CLAUDE_CODE_BIN",
        env_aliases=("AGPAIR_CLAUDE_CODE_CLI",),
        default_binary="claude",
        default_priority=30,
        enabled_by_default=True,
        default_authorization_profile="local_mutating",
        is_mutating=True,
        is_concurrency_safe=False,
    ),
    "codex": ExecutorSpec(
        executor_id="codex",
        env_var="AGPAIR_CODEX_BIN",
        env_aliases=("AGPAIR_CODEX_CLI",),
        default_binary="codex",
        default_priority=40,
        enabled_by_default=True,
        default_authorization_profile="local_mutating",
        is_mutating=True,
        is_concurrency_safe=False,
    ),
}


def normalize_executor_id(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().lower().replace("_", "-")
    normalized = LEGACY_EXECUTOR_ALIASES.get(normalized, normalized)
    if normalized in REJECTED_EXECUTOR_IDS:
        raise ValueError("gemini is no longer supported for new AGPair work")
    if normalized not in EXECUTOR_SPECS:
        allowed = ", ".join(CANONICAL_EXECUTOR_IDS)
        raise ValueError(f"executor must be one of: {allowed}")
    return normalized


def executor_binary_path(executor_id: str) -> str | None:
    spec = EXECUTOR_SPECS[executor_id]
    for env_var in (spec.env_var, *spec.env_aliases):
        configured = os.environ.get(env_var, "").strip()
        if configured:
            return configured if os.path.exists(configured) or shutil.which(configured) else None
    return shutil.which(spec.default_binary)


def executor_health_snapshot() -> dict[str, dict[str, Any]]:
    snapshot: dict[str, dict[str, Any]] = {}
    for executor_id, spec in EXECUTOR_SPECS.items():
        binary = executor_binary_path(executor_id)
        configured_env_var = next(
            (env_var for env_var in (spec.env_var, *spec.env_aliases) if os.environ.get(env_var, "").strip()),
            None,
        )
        snapshot[executor_id] = {
            "available": bool(binary),
            "binary_path": binary,
            "env_var": spec.env_var,
            "env_aliases": list(spec.env_aliases),
            "env_vars": [spec.env_var, *spec.env_aliases],
            "configured_env_var": configured_env_var,
            "default_binary": spec.default_binary,
            "enabled_by_default": spec.enabled_by_default,
        }
    return snapshot


def resolve_controller_policy(
    *,
    controller: str | None = None,
    requested_executor: str | None = None,
    allow_self_executor: bool = False,
    require_available: bool = False,
) -> ExecutorPolicyDecision:
    controller_id = (controller or "generic").strip().lower() or "generic"
    suppressed: list[str] = []
    reasons: list[str] = []
    if controller_id == "codex" and not allow_self_executor:
        suppressed.append("codex")
        reasons.append("codex controller suppresses external codex by default")
    if controller_id in {"claude-code", "claude_code", "claude"} and not allow_self_executor:
        suppressed.append("claude-code")
        reasons.append("claude-code controller suppresses external claude-code by default")

    requested_normalized = normalize_executor_id(requested_executor) if requested_executor else None
    health = executor_health_snapshot()
    eligible = []
    for spec in sorted(EXECUTOR_SPECS.values(), key=lambda item: item.default_priority):
        if spec.executor_id in suppressed:
            continue
        if require_available and not health[spec.executor_id]["available"]:
            continue
        eligible.append(spec.executor_id)

    selected = requested_normalized
    rejected = None
    if selected and selected in suppressed:
        rejected = selected
        selected = None
        reasons.append(f"requested executor {rejected} suppressed for controller {controller_id}")
    elif selected and require_available and not health[selected]["available"]:
        rejected = selected
        selected = None
        reasons.append(f"requested executor {rejected} is unavailable")

    if selected is None and eligible:
        selected = eligible[0]
    return ExecutorPolicyDecision(
        controller=controller_id,
        selected_executor=selected,
        eligible_executors=tuple(eligible),
        suppressed_executors=tuple(suppressed),
        rejected_executor=rejected,
        allow_self_executor=allow_self_executor,
        reasons=tuple(reasons),
    )
