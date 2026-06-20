from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from collections.abc import Iterable
from typing import Any

from agpair.executors.claude_auth import (
    claude_code_settings_error,
    claude_oauth_error,
    explicit_claude_auth_mode,
    resolve_claude_auth,
)
from agpair.executors.lifecycle import ACTIVE, lifecycle_decision

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
ENVIRONMENT_MODES = {
    "managed-natural",
}
SKILL_POLICIES = {"inherit"}
MCP_POLICIES = {"inherit"}


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
    display_name: str | None = None
    lifecycle_status: str = ACTIVE
    replacement_executor: str | None = None
    supported_completion_policies: tuple[str, ...] = ("auto", "evidence", "report", "commit")
    receipt_capable: str = "prompt_contract"
    controller_suppression: tuple[str, ...] = ()
    recommended_for_controllers: tuple[str, ...] = ("generic", "codex", "claude-code")
    default_environment_mode: str = "managed-natural"
    default_skill_policy: str = "inherit"
    default_mcp_policy: str = "inherit"
    isolation_profile: dict[str, Any] = field(default_factory=dict)
    launch_probe: tuple[str, ...] = ("--help",)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["binary_name"] = self.default_binary
        payload["display_name"] = self.display_name or self.executor_id
        payload["lifecycle"] = lifecycle_decision(
            executor_id=self.executor_id,
            lifecycle_status=self.lifecycle_status,
            replacement_executor=self.replacement_executor,
        ).to_dict()
        return payload


@dataclass(frozen=True)
class ExecutorPolicyDecision:
    controller: str
    selected_executor: str | None
    eligible_executors: tuple[str, ...]
    suppressed_executors: tuple[str, ...]
    rejected_executor: str | None
    allow_self_executor: bool
    reasons: tuple[str, ...]
    skipped_executors: tuple[dict[str, Any], ...] = ()
    policy_sources: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ExecutorEnvironmentMetadata:
    environment_mode: str
    environment_mode_source: str
    skill_policy: str
    mcp_policy: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


EXECUTOR_SPECS: dict[str, ExecutorSpec] = {
    "antigravity-cli": ExecutorSpec(
        executor_id="antigravity-cli",
        env_var="AGPAIR_ANTIGRAVITY_CLI_BIN",
        env_aliases=("AGPAIR_ANTIGRAVITY_CLI",),
        default_binary="agy",
        default_priority=20,
        enabled_by_default=True,
        default_authorization_profile="local_mutating",
        is_mutating=True,
        is_concurrency_safe=False,
        display_name="Antigravity CLI",
        receipt_capable="prompt_contract",
        default_environment_mode="managed-natural",
        default_skill_policy="inherit",
        default_mcp_policy="inherit",
        isolation_profile={
            "supports_isolated_config_home": False,
            "supports_turn_budget": "no",
            "supports_streaming_json": "no",
            "default_output_mode": "print",
            "model_env_vars": ["AGPAIR_ANTIGRAVITY_MODEL", "AGPAIR_ANTIGRAVITY_CLI_MODEL"],
            "noninteractive_flags": [
                "--dangerously-skip-permissions",
                "--print",
                "--print-timeout",
                "--log-file",
            ],
            "isolated_auth_env_vars": [],
            "isolation_disable_env_var": None,
        },
    ),
    "grok-cli": ExecutorSpec(
        executor_id="grok-cli",
        env_var="AGPAIR_GROK_CLI_BIN",
        env_aliases=("AGPAIR_GROK_CLI",),
        default_binary="grok",
        default_priority=10,
        enabled_by_default=True,
        default_authorization_profile="local_readonly",
        is_mutating=True,
        is_concurrency_safe=False,
        display_name="Grok CLI",
        receipt_capable="prompt_contract",
        default_environment_mode="managed-natural",
        default_skill_policy="inherit",
        default_mcp_policy="inherit",
        isolation_profile={
            "supports_isolated_config_home": "limited",
            "supports_turn_budget": True,
            "supports_streaming_json": True,
            "default_output_mode": "json",
            "noninteractive_flags": [
                "--single",
                "--cwd",
                "--output-format",
                "--always-approve",
            ],
            "isolated_auth_env_vars": [],
            "isolation_disable_env_var": None,
        },
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
        display_name="Claude Code CLI",
        controller_suppression=("claude", "claude-code", "claude_code"),
        receipt_capable="prompt_contract",
        default_environment_mode="managed-natural",
        default_skill_policy="inherit",
        default_mcp_policy="inherit",
        isolation_profile={
            "supports_isolated_config_home": False,
            "supports_turn_budget": "unknown",
            "supports_streaming_json": True,
            "default_output_mode": "json",
            "default_auth_mode": "auto",
            "auth_modes": ["auto", "oauth", "ccswitch", "api"],
            "default_retry_env": {"CLAUDE_CODE_MAX_RETRIES": "0"},
            "default_oauth_profile": "natural",
            "noninteractive_flags": [
                "--print",
                "--debug-file",
                "--no-chrome",
                "--output-format",
                "--no-session-persistence",
            ],
            "isolated_auth_env_vars": [
                "ANTHROPIC_API_KEY",
                "AGPAIR_CLAUDE_CODE_SETTINGS",
                "AGPAIR_CC_SWITCH_HOME",
            ],
            "isolation_disable_env_var": None,
        },
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
        display_name="Codex CLI worker",
        controller_suppression=("codex",),
        receipt_capable="prompt_contract",
        default_environment_mode="managed-natural",
        default_skill_policy="inherit",
        default_mcp_policy="inherit",
        isolation_profile={
            "supports_isolated_config_home": False,
            "supports_turn_budget": "unknown",
            "supports_streaming_json": True,
            "default_output_mode": "json",
            "noninteractive_flags": [
                "exec",
                "--ephemeral",
                "--json",
                "-C",
            ],
            "isolated_auth_env_vars": [],
            "isolation_disable_env_var": None,
        },
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


def supported_environment_modes(executor_id: str | None) -> tuple[str, ...]:
    normalized = normalize_executor_id(executor_id or "antigravity-cli")
    assert normalized is not None
    spec = EXECUTOR_SPECS[normalized]
    return (spec.default_environment_mode,)


def _policies_for_environment_mode(spec: ExecutorSpec, environment_mode: str) -> tuple[str, str]:
    if environment_mode == spec.default_environment_mode:
        return spec.default_skill_policy, spec.default_mcp_policy
    return spec.default_skill_policy, spec.default_mcp_policy


def _environment_mode_from_env(executor_id: str) -> str | None:
    del executor_id
    return None


def resolve_environment_metadata(
    executor_id: str | None,
    *,
    environment_mode: str | None = None,
    source: str | None = None,
) -> ExecutorEnvironmentMetadata:
    normalized = normalize_executor_id(executor_id or "antigravity-cli")
    assert normalized is not None
    spec = EXECUTOR_SPECS[normalized]
    requested = environment_mode.strip() if isinstance(environment_mode, str) and environment_mode.strip() else None
    env_requested = None if requested else _environment_mode_from_env(normalized)
    selected = requested or spec.default_environment_mode
    if env_requested:
        selected = env_requested
    if selected not in ENVIRONMENT_MODES:
        allowed = ", ".join(sorted(ENVIRONMENT_MODES))
        raise ValueError(f"environment mode must be one of: {allowed}")
    supported = supported_environment_modes(normalized)
    if selected not in supported:
        allowed = ", ".join(supported)
        raise ValueError(f"{normalized} does not support environment mode {selected}; supported modes: {allowed}")
    skill_policy, mcp_policy = _policies_for_environment_mode(spec, selected)
    return ExecutorEnvironmentMetadata(
        environment_mode=selected,
        environment_mode_source=source or ("task_start_override" if requested else "executor_env_var" if env_requested else "executor_default"),
        skill_policy=skill_policy,
        mcp_policy=mcp_policy,
    )


def executor_binary_path(executor_id: str) -> str | None:
    spec = EXECUTOR_SPECS[executor_id]
    for env_var in (spec.env_var, *spec.env_aliases):
        configured = os.environ.get(env_var, "").strip()
        if configured:
            return configured if os.path.exists(configured) or shutil.which(configured) else None
    return shutil.which(spec.default_binary)


def _configured_executor_binary(spec: ExecutorSpec) -> tuple[str | None, str | None, str | None]:
    for env_var in (spec.env_var, *spec.env_aliases):
        configured = os.environ.get(env_var, "").strip()
        if configured:
            return configured, env_var, configured if os.path.exists(configured) or shutil.which(configured) else None
    return spec.default_binary, None, shutil.which(spec.default_binary)


def _launch_probe(binary_path: str, spec: ExecutorSpec, *, timeout_seconds: float = 3.0) -> tuple[bool | str, str | None]:
    if not spec.launch_probe:
        return "unknown", None
    try:
        proc = subprocess.run(
            [binary_path, *spec.launch_probe],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except (OSError, subprocess.SubprocessError, subprocess.TimeoutExpired) as exc:
        return False, f"{type(exc).__name__}: {exc}"
    if proc.returncode == 0:
        return True, None
    excerpt = (proc.stderr or proc.stdout or "").strip().splitlines()
    return False, excerpt[-1] if excerpt else f"launch probe exited {proc.returncode}"


def _claude_code_auth_mode() -> str:
    return explicit_claude_auth_mode() or "auto"


def _claude_code_oauth_live_probe_error(binary_path: str) -> str | None:
    return claude_oauth_error(binary_path, live_probe=True)


def _claude_code_oauth_error(binary_path: str | None, *, live_probe: bool = False) -> str | None:
    return claude_oauth_error(binary_path, live_probe=live_probe)


def _claude_code_settings_error(value: str) -> str | None:
    return claude_code_settings_error(value)


def _isolation_auth_error(
    spec: ExecutorSpec,
    *,
    binary_path: str | None = None,
    live_probe: bool = False,
) -> str | None:
    profile = spec.isolation_profile or {}
    required_env_vars = tuple(str(item) for item in profile.get("isolated_auth_env_vars") or ())
    if not required_env_vars:
        return None
    disable_env_var = profile.get("isolation_disable_env_var")
    if spec.executor_id == "claude-code":
        return resolve_claude_auth(binary_path, live_probe=live_probe).error
    if any(os.environ.get(env_var, "").strip() for env_var in required_env_vars):
        return None
    required = ", ".join(required_env_vars)
    disable_hint = f". Set {disable_env_var}=0 for diagnostics only" if disable_env_var else ""
    setup_hint = ""
    if spec.executor_id == "claude-code":
        setup_hint = (
            ". Run `agpair claude worker-settings > ~/.agpair/claude-worker-settings.json` "
            "and set AGPAIR_CLAUDE_CODE_SETTINGS to that file, or export ANTHROPIC_API_KEY. "
            "Use AGPAIR_CLAUDE_CODE_AUTH_MODE=oauth to reuse Claude Code OAuth/subscription login"
        )
    return (
        f"executor {spec.executor_id} requires one of {required} for isolated external-worker auth"
        f"{setup_hint}{disable_hint}"
    )


def executor_health_snapshot(
    *,
    run_launch_probe: bool = False,
    executor_ids: Iterable[str] | None = None,
) -> dict[str, dict[str, Any]]:
    snapshot: dict[str, dict[str, Any]] = {}
    selected_ids = None
    if executor_ids is not None:
        selected_ids = {normalize_executor_id(item) for item in executor_ids}
    for executor_id, spec in EXECUTOR_SPECS.items():
        if selected_ids is not None and executor_id not in selected_ids:
            continue
        configured_binary, configured_env_var, binary = _configured_executor_binary(spec)
        lifecycle = lifecycle_decision(
            executor_id=executor_id,
            lifecycle_status=spec.lifecycle_status,
            replacement_executor=spec.replacement_executor,
        )
        binary_available = bool(binary)
        launch_clean: bool | str = "unknown"
        launch_error: str | None = None
        if run_launch_probe and binary:
            launch_clean, launch_error = _launch_probe(binary, spec)
        claude_auth_resolution = (
            resolve_claude_auth(binary, live_probe=run_launch_probe)
            if executor_id == "claude-code"
            else None
        )
        auth_mode = claude_auth_resolution.mode if claude_auth_resolution else None
        isolation_auth_error = (
            claude_auth_resolution.error
            if claude_auth_resolution
            else _isolation_auth_error(
                spec,
                binary_path=binary,
                live_probe=run_launch_probe,
            )
        )
        ccswitch_provider = (
            claude_auth_resolution.ccswitch_provider
            if claude_auth_resolution
            else None
        )
        auth_failure_type = (
            claude_auth_resolution.failure_class
            if claude_auth_resolution and isolation_auth_error
            else "executor_auth_required"
            if isolation_auth_error
            else None
        )
        last_failure_type = None
        if not lifecycle.allowed_for_new_tasks:
            last_failure_type = lifecycle.blocker_type
        elif not binary_available:
            last_failure_type = "executor_unavailable"
        elif launch_clean is False:
            last_failure_type = "launch_probe_failed"
        elif isolation_auth_error:
            last_failure_type = auth_failure_type or "executor_auth_required"
        auth_satisfied = isolation_auth_error is None
        auth_source = None
        if executor_id == "claude-code":
            auth_source = auth_mode
            if ccswitch_provider:
                auth_source = "ccswitch"
        auth_state = "ok" if auth_satisfied else auth_failure_type or "executor_auth_required"
        launch_probe_status = (
            "ok"
            if launch_clean is True
            else "failed"
            if launch_clean is False
            else "not_run"
        )
        available = (
            lifecycle.allowed_for_new_tasks
            and spec.enabled_by_default
            and binary_available
            and launch_clean is not False
            and auth_satisfied
        )
        snapshot[executor_id] = {
            "executor_id": executor_id,
            "available": available,
            "binary": configured_binary,
            "binary_name": spec.default_binary,
            "binary_available": binary_available,
            "binary_path": binary,
            "env_var": spec.env_var,
            "env_aliases": list(spec.env_aliases),
            "env_vars": [spec.env_var, *spec.env_aliases],
            "configured_env_var": configured_env_var,
            "default_binary": spec.default_binary,
            "display_name": spec.display_name or executor_id,
            "enabled_by_default": spec.enabled_by_default,
            "lifecycle_status": lifecycle.lifecycle_status,
            "replacement_executor": lifecycle.replacement_executor,
            "lifecycle_allowed_for_new_tasks": lifecycle.allowed_for_new_tasks,
            "lifecycle_reason": lifecycle.reason,
            "receipt_capable": spec.receipt_capable,
            "supported_completion_policies": list(spec.supported_completion_policies),
            "controller_suppression": list(spec.controller_suppression),
            "recommended_for_controllers": list(spec.recommended_for_controllers),
            "isolation_profile": spec.isolation_profile,
            "launch_probe": list(spec.launch_probe),
            "launch_probe_status": launch_probe_status,
            "auth_mode": auth_mode,
            "auth_state": auth_state,
            "auth_source": auth_source,
            "ccswitch_provider": ccswitch_provider.name if ccswitch_provider else None,
            "ccswitch_provider_id": ccswitch_provider.provider_id if ccswitch_provider else None,
            "ccswitch_source": os.path.basename(ccswitch_provider.source) if ccswitch_provider else None,
            "launch_clean": launch_clean,
            "auth_satisfied": auth_satisfied,
            "auth_probe_environment_mode": spec.default_environment_mode if executor_id == "claude-code" else None,
            "auth_probe_skill_policy": spec.default_skill_policy if executor_id == "claude-code" else None,
            "auth_probe_mcp_policy": spec.default_mcp_policy if executor_id == "claude-code" else None,
            "environment_mode": spec.default_environment_mode,
            "skill_policy": spec.default_skill_policy,
            "mcp_policy": spec.default_mcp_policy,
            "isolation_auth_satisfied": auth_satisfied,
            "last_failure_type": last_failure_type,
            "last_error_excerpt": launch_error or isolation_auth_error,
        }
    return snapshot


def _controller_id(value: str | None) -> str:
    normalized = (value or "generic").strip().lower().replace("_", "-")
    return normalized or "generic"


def _suppressed_executors_for_controller(controller_id: str, *, allow_self_executor: bool) -> tuple[str, ...]:
    if allow_self_executor:
        return ()
    suppressed = [
        spec.executor_id
        for spec in sorted(EXECUTOR_SPECS.values(), key=lambda item: item.default_priority)
        if controller_id in {item.replace("_", "-") for item in spec.controller_suppression}
    ]
    return tuple(suppressed)


def _static_eligible_executors(suppressed: list[str]) -> list[str]:
    eligible: list[str] = []
    for spec in sorted(EXECUTOR_SPECS.values(), key=lambda item: item.default_priority):
        lifecycle = lifecycle_decision(
            executor_id=spec.executor_id,
            lifecycle_status=spec.lifecycle_status,
            replacement_executor=spec.replacement_executor,
        )
        if not spec.enabled_by_default or not lifecycle.allowed_for_new_tasks:
            continue
        if spec.executor_id in suppressed:
            continue
        eligible.append(spec.executor_id)
    return eligible


def _load_default_overlay():
    from agpair.config import AppPaths
    from agpair.executors.config import ExecutorPolicyConfigError, ExecutorPolicyManager

    try:
        return ExecutorPolicyManager(AppPaths.default().executor_policy_path).read()
    except ExecutorPolicyConfigError as exc:
        raise ValueError(str(exc)) from exc


def _ordered_specs_for_controller(controller_id: str, overlay) -> list[ExecutorSpec]:
    priority = overlay.priority_for(controller_id)
    priority_index = {executor_id: index for index, executor_id in enumerate(priority)}
    return sorted(
        EXECUTOR_SPECS.values(),
        key=lambda spec: (
            0 if spec.executor_id in priority_index else 1,
            priority_index.get(spec.executor_id, spec.default_priority),
            spec.default_priority,
        ),
    )


def _skip_reason(
    *,
    spec: ExecutorSpec,
    controller_id: str,
    suppressed: set[str],
    policy_disabled: set[str],
    require_available: bool,
    health: dict[str, dict[str, Any]],
) -> tuple[str | None, str | None]:
    lifecycle = lifecycle_decision(
        executor_id=spec.executor_id,
        lifecycle_status=spec.lifecycle_status,
        replacement_executor=spec.replacement_executor,
    )
    if not spec.enabled_by_default:
        return "executor_disabled", f"executor {spec.executor_id} is disabled by its static profile"
    if not lifecycle.allowed_for_new_tasks:
        return lifecycle.blocker_type, str(lifecycle.reason)
    if spec.executor_id in suppressed:
        return "executor_suppressed", f"{controller_id} controller suppresses external {spec.executor_id} by default [executor_suppressed]"
    if spec.executor_id in policy_disabled:
        return (
            "executor_disabled_by_policy",
            f"executor {spec.executor_id} is disabled by runtime policy for controller {controller_id} [executor_disabled_by_policy]",
        )
    if require_available and not health[spec.executor_id]["available"]:
        item = health[spec.executor_id]
        reason = item.get("last_error_excerpt")
        if not reason:
            reason = (
                f"requested executor {spec.executor_id} is unavailable; install {item['binary_name']} "
                f"or set {item['env_var']}"
            )
        return str(item.get("last_failure_type") or "executor_unavailable"), str(reason)
    return None, None


def resolve_controller_policy(
    *,
    controller: str | None = None,
    requested_executor: str | None = None,
    allow_self_executor: bool = False,
    require_available: bool = False,
    overlay: Any | None = None,
) -> ExecutorPolicyDecision:
    controller_id = _controller_id(controller)
    active_overlay = overlay if overlay is not None else _load_default_overlay()
    suppressed = tuple(_suppressed_executors_for_controller(controller_id, allow_self_executor=allow_self_executor))
    suppressed_set = set(suppressed)
    policy_disabled = set(active_overlay.disabled_for(controller_id))
    reasons: list[str] = []
    for executor_id in suppressed:
        reasons.append(f"{controller_id} controller suppresses external {executor_id} by default")
    for executor_id in sorted(policy_disabled):
        reasons.append(
            f"executor {executor_id} is disabled by runtime policy for controller {controller_id} "
            "[executor_disabled_by_policy]"
        )

    requested_normalized = normalize_executor_id(requested_executor) if requested_executor else None
    specs_to_consider = (
        [EXECUTOR_SPECS[requested_normalized]]
        if requested_normalized
        else _ordered_specs_for_controller(controller_id, active_overlay)
    )
    health: dict[str, dict[str, Any]] = {}
    if require_available:
        health = executor_health_snapshot(
            run_launch_probe=True,
            executor_ids=(requested_normalized,) if requested_normalized else [spec.executor_id for spec in specs_to_consider],
        )
    eligible: list[str] = []
    skipped: list[dict[str, Any]] = []
    rejected: str | None = None
    specs_to_consider = (
        [EXECUTOR_SPECS[requested_normalized]]
        if requested_normalized
        else specs_to_consider
    )
    for spec in specs_to_consider:
        blocker_type, reason = _skip_reason(
            spec=spec,
            controller_id=controller_id,
            suppressed=suppressed_set,
            policy_disabled=policy_disabled,
            require_available=require_available,
            health=health,
        )
        if blocker_type:
            skipped.append(
                {
                    "executor_id": spec.executor_id,
                    "blocker_type": blocker_type,
                    "reason": reason,
                }
            )
            if requested_normalized == spec.executor_id:
                rejected = spec.executor_id
                if reason:
                    reasons.append(reason)
            continue
        eligible.append(spec.executor_id)

    selected = requested_normalized if requested_normalized in eligible else None
    if selected is None and not requested_normalized and eligible:
        selected = eligible[0]
    policy_sources = {
        "static_registry": "agpair.executors.policy.EXECUTOR_SPECS",
        "runtime_overlay": "executor_policy_overlay",
    }
    return ExecutorPolicyDecision(
        controller=controller_id,
        selected_executor=selected,
        eligible_executors=tuple(eligible),
        suppressed_executors=suppressed,
        rejected_executor=rejected,
        allow_self_executor=allow_self_executor,
        reasons=tuple(reasons),
        skipped_executors=tuple(skipped),
        policy_sources=policy_sources,
    )


def next_eligible_executor(
    *,
    controller: str | None,
    current_executor: str | None,
    requested_executor: str | None = None,
    allow_self_executor: bool = False,
    require_available: bool = False,
) -> str | None:
    if requested_executor:
        return None
    resolved = resolve_controller_policy(
        controller=controller,
        requested_executor=None,
        allow_self_executor=allow_self_executor,
        require_available=require_available,
    )
    for candidate in resolved.eligible_executors:
        if candidate != current_executor:
            return candidate
    return None
