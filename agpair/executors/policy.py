from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from typing import Any

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
_FALSE_ENV_VALUES = {"0", "false", "no", "off"}


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

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


EXECUTOR_SPECS: dict[str, ExecutorSpec] = {
    "antigravity-cli": ExecutorSpec(
        executor_id="antigravity-cli",
        env_var="AGPAIR_ANTIGRAVITY_CLI_BIN",
        env_aliases=("AGPAIR_ANTIGRAVITY_CLI",),
        default_binary="agy",
        default_priority=10,
        enabled_by_default=True,
        default_authorization_profile="local_mutating",
        is_mutating=True,
        is_concurrency_safe=False,
        display_name="Antigravity CLI",
        receipt_capable="prompt_contract",
        isolation_profile={
            "supports_isolated_config_home": False,
            "supports_turn_budget": "no",
            "supports_streaming_json": "no",
            "default_output_mode": "print",
            "noninteractive_flags": ["--print", "--print-timeout"],
            "isolated_auth_env_vars": [],
            "isolation_disable_env_var": None,
        },
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
        display_name="Grok CLI",
        receipt_capable="prompt_contract",
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
                "--max-turns",
                "--no-memory",
                "--no-subagents",
                "--disable-web-search",
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
        isolation_profile={
            "supports_isolated_config_home": "bare",
            "supports_turn_budget": "unknown",
            "supports_streaming_json": True,
            "default_output_mode": "json",
            "default_auth_mode": "oauth",
            "auth_modes": ["oauth", "api"],
            "default_retry_env": {"CLAUDE_CODE_MAX_RETRIES": "0"},
            "default_oauth_profile": "quiet",
            "oauth_profile_env_var": "AGPAIR_CLAUDE_CODE_OAUTH_PROFILE",
            "oauth_quiet_flags": [
                "--strict-mcp-config",
                "--mcp-config",
                "--disable-slash-commands",
                "--no-chrome",
                "--print",
                "--output-format",
                "--no-session-persistence",
            ],
            "noninteractive_flags": [
                "--strict-mcp-config",
                "--mcp-config",
                "--disable-slash-commands",
                "--no-chrome",
                "--print",
                "--output-format",
                "--no-session-persistence",
            ],
            "api_mode_flags": ["--bare", "--print", "--output-format", "--no-session-persistence"],
            "isolated_auth_env_vars": ["ANTHROPIC_API_KEY", "AGPAIR_CLAUDE_CODE_SETTINGS"],
            "isolation_disable_env_var": "AGPAIR_CLAUDE_CODE_BARE",
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
        isolation_profile={
            "supports_isolated_config_home": True,
            "supports_turn_budget": "unknown",
            "supports_streaming_json": True,
            "default_output_mode": "json",
            "noninteractive_flags": [
                "exec",
                "--ignore-user-config",
                "--ignore-rules",
                "--ephemeral",
                "--json",
                "-C",
            ],
            "isolated_auth_env_vars": [],
            "isolation_disable_env_var": "AGPAIR_CODEX_IGNORE_USER_CONFIG",
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


def _env_flag_disabled(env_var: str | None) -> bool:
    if not env_var:
        return False
    return os.environ.get(env_var, "1").strip().lower() in _FALSE_ENV_VALUES


def _claude_code_auth_mode() -> str:
    explicit = os.environ.get("AGPAIR_CLAUDE_CODE_AUTH_MODE", "").strip().lower()
    if explicit in {"api", "bare"}:
        return "api"
    if explicit in {"oauth", "subscription"}:
        return "oauth"
    legacy_bare = os.environ.get("AGPAIR_CLAUDE_CODE_BARE")
    if legacy_bare is not None and legacy_bare.strip().lower() not in _FALSE_ENV_VALUES:
        return "api"
    return "oauth"


def _claude_code_oauth_live_probe_error(binary_path: str) -> str | None:
    env = os.environ.copy()
    env["CLAUDE_CODE_MAX_RETRIES"] = os.environ.get("AGPAIR_CLAUDE_CODE_MAX_RETRIES", "0").strip() or "0"
    cmd = [
        binary_path,
        "--strict-mcp-config",
        "--mcp-config",
        '{"mcpServers":{}}',
        "--disable-slash-commands",
        "--no-chrome",
        "--permission-mode",
        "default",
        "--no-session-persistence",
        "--output-format",
        "json",
        "--print",
        "Return exactly: agpair-oauth-health-ok",
    ]
    try:
        proc = subprocess.run(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=20.0,
            check=False,
            env=env,
        )
    except (OSError, subprocess.SubprocessError, subprocess.TimeoutExpired) as exc:
        return f"Claude Code OAuth/subscription live auth check failed: {type(exc).__name__}: {exc}"
    stdout = (proc.stdout or "").strip()
    stderr = (proc.stderr or "").strip()
    combined_output = f"{stdout}\n{stderr}"
    if "Invalid Authentication" in combined_output or ("api_error_status" in combined_output and "401" in combined_output):
        return "Claude Code OAuth/subscription live auth check failed: Invalid Authentication; run `claude auth login`"
    if proc.returncode != 0:
        detail = (stderr or stdout).splitlines()
        return f"Claude Code OAuth/subscription live auth check failed: {detail[-1] if detail else proc.returncode}"
    if not stdout:
        return "Claude Code OAuth/subscription live auth check produced no output"
    try:
        payload = json.loads(stdout.splitlines()[-1])
    except json.JSONDecodeError:
        return None
    if payload.get("api_error_status") == 401:
        return "Claude Code OAuth/subscription live auth check failed: Invalid Authentication; run `claude auth login`"
    if payload.get("is_error") is True:
        summary = payload.get("result") or payload.get("error") or payload.get("subtype") or "unknown error"
        return f"Claude Code OAuth/subscription live auth check failed: {summary}"
    return None


def _claude_code_oauth_error(binary_path: str | None, *, live_probe: bool = False) -> str | None:
    if not binary_path:
        return "Claude Code OAuth/subscription auth requires a claude binary"
    try:
        proc = subprocess.run(
            [binary_path, "auth", "status"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=5.0,
            check=False,
        )
    except (OSError, subprocess.SubprocessError, subprocess.TimeoutExpired) as exc:
        return f"Claude Code OAuth/subscription auth check failed: {type(exc).__name__}: {exc}"
    if proc.returncode != 0:
        excerpt = (proc.stderr or proc.stdout or "").strip().splitlines()
        detail = excerpt[-1] if excerpt else f"claude auth status exited {proc.returncode}"
        return f"Claude Code OAuth/subscription auth check failed: {detail}"
    try:
        status = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError as exc:
        return f"Claude Code OAuth/subscription auth check returned invalid JSON: {exc}"
    if status.get("loggedIn") is True:
        if live_probe:
            return _claude_code_oauth_live_probe_error(binary_path)
        return None
    return "Claude Code OAuth/subscription auth is not logged in; run `claude auth login`"


def _claude_code_settings_error(value: str) -> str | None:
    stripped = value.strip()
    if not stripped:
        return None
    try:
        if stripped.startswith("{"):
            settings = json.loads(stripped)
        else:
            settings_path = pathlib.Path(stripped).expanduser()
            if not settings_path.is_file():
                return f"AGPAIR_CLAUDE_CODE_SETTINGS points to a missing file: {stripped}"
            settings = json.loads(settings_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return f"AGPAIR_CLAUDE_CODE_SETTINGS is not readable JSON: {exc}"
    if not isinstance(settings, dict):
        return "AGPAIR_CLAUDE_CODE_SETTINGS must be a JSON object or a path to one"
    api_key_helper = str(settings.get("apiKeyHelper", "")).strip()
    if (
        api_key_helper == "printenv ANTHROPIC_API_KEY"
        and not os.environ.get("ANTHROPIC_API_KEY", "").strip()
    ):
        return (
            "AGPAIR_CLAUDE_CODE_SETTINGS uses the default API key helper, "
            "but ANTHROPIC_API_KEY is empty"
        )
    return None


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
    if _env_flag_disabled(str(disable_env_var) if disable_env_var else None):
        return None
    if spec.executor_id == "claude-code":
        if _claude_code_auth_mode() == "oauth":
            return _claude_code_oauth_error(binary_path, live_probe=live_probe)
        settings_error = _claude_code_settings_error(
            os.environ.get("AGPAIR_CLAUDE_CODE_SETTINGS", "")
        )
        if settings_error:
            return settings_error
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


def executor_health_snapshot(*, run_launch_probe: bool = False) -> dict[str, dict[str, Any]]:
    snapshot: dict[str, dict[str, Any]] = {}
    for executor_id, spec in EXECUTOR_SPECS.items():
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
        auth_mode = _claude_code_auth_mode() if executor_id == "claude-code" else None
        isolation_auth_error = _isolation_auth_error(
            spec,
            binary_path=binary,
            live_probe=run_launch_probe,
        )
        last_failure_type = None
        if not lifecycle.allowed_for_new_tasks:
            last_failure_type = lifecycle.blocker_type
        elif not binary_available:
            last_failure_type = "executor_unavailable"
        elif launch_clean is False:
            last_failure_type = "launch_probe_failed"
        elif isolation_auth_error:
            last_failure_type = "executor_auth_required"
        available = (
            lifecycle.allowed_for_new_tasks
            and spec.enabled_by_default
            and binary_available
            and launch_clean is not False
            and isolation_auth_error is None
        )
        snapshot[executor_id] = {
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
            "auth_mode": auth_mode,
            "launch_clean": launch_clean,
            "isolation_auth_satisfied": isolation_auth_error is None,
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


def resolve_controller_policy(
    *,
    controller: str | None = None,
    requested_executor: str | None = None,
    allow_self_executor: bool = False,
    require_available: bool = False,
) -> ExecutorPolicyDecision:
    controller_id = _controller_id(controller)
    suppressed = list(_suppressed_executors_for_controller(controller_id, allow_self_executor=allow_self_executor))
    reasons: list[str] = []
    for executor_id in suppressed:
        reasons.append(f"{controller_id} controller suppresses external {executor_id} by default")

    requested_normalized = normalize_executor_id(requested_executor) if requested_executor else None
    health = executor_health_snapshot(run_launch_probe=require_available)
    eligible = []
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
        if require_available and not health[spec.executor_id]["available"]:
            continue
        eligible.append(spec.executor_id)

    selected = requested_normalized
    rejected = None
    if selected and selected in suppressed:
        rejected = selected
        selected = None
        reasons.append(f"requested executor {rejected} suppressed for controller {controller_id}")
    elif selected and not health[selected]["lifecycle_allowed_for_new_tasks"]:
        rejected = selected
        selected = None
        reason = str(health[rejected].get("lifecycle_reason") or f"requested executor {rejected} is not active")
        reasons.append(reason)
    elif selected and require_available and not health[selected]["available"]:
        rejected = selected
        selected = None
        item = health[rejected]
        reason = item.get("last_error_excerpt")
        if not reason:
            reason = (
                f"requested executor {rejected} is unavailable; install {item['binary_name']} "
                f"or set {item['env_var']}"
            )
        reasons.append(str(reason))

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
