from __future__ import annotations

import json
import os
import pathlib
import signal
import sqlite3
import subprocess
from dataclasses import dataclass

FALSE_ENV_VALUES = {"0", "false", "no", "off"}
DEFAULT_LIVE_PROBE_TIMEOUT_SECONDS = 30.0
_SENSITIVE_ENV_KEY_MARKERS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "AUTH")


@dataclass(frozen=True)
class CCSwitchProvider:
    provider_id: str | None
    name: str | None
    source: str
    env: dict[str, str]


@dataclass(frozen=True)
class ClaudeAuthResolution:
    mode: str
    error: str | None = None
    ccswitch_provider: CCSwitchProvider | None = None
    env_overrides: dict[str, str] | None = None


def explicit_claude_auth_mode() -> str | None:
    explicit = os.environ.get("AGPAIR_CLAUDE_CODE_AUTH_MODE", "").strip().lower()
    if explicit == "api":
        return "api"
    if explicit in {"oauth", "subscription"}:
        return "oauth"
    if explicit in {"ccswitch", "provider", "cc-switch"}:
        return "ccswitch"
    return None


def claude_retry_env() -> dict[str, str]:
    retries = os.environ.get("AGPAIR_CLAUDE_CODE_MAX_RETRIES", "0").strip() or "0"
    return {"CLAUDE_CODE_MAX_RETRIES": retries}


def ccswitch_home() -> pathlib.Path:
    configured = os.environ.get("AGPAIR_CC_SWITCH_HOME", "").strip()
    if configured:
        return pathlib.Path(configured).expanduser()
    return pathlib.Path.home() / ".cc-switch"


def _coerce_env(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    env: dict[str, str] = {}
    for key, item in value.items():
        if key is None or item is None:
            continue
        text = str(item)
        if text:
            env[str(key)] = text
    return env


def _provider_from_settings_config(
    *,
    provider_id: str | None,
    name: str | None,
    source: str,
    settings_config: str,
) -> CCSwitchProvider | None:
    try:
        payload = json.loads(settings_config or "{}")
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    env = _coerce_env(payload.get("env"))
    if not env:
        return None
    return CCSwitchProvider(
        provider_id=provider_id,
        name=name,
        source=source,
        env=env,
    )


def _load_current_ccswitch_db_provider(home: pathlib.Path) -> CCSwitchProvider | None:
    db_path = home / "cc-switch.db"
    if not db_path.is_file():
        return None
    try:
        with sqlite3.connect(db_path) as conn:
            row = conn.execute(
                "select id, name, settings_config from providers "
                "where app_type = ? and is_current = 1 limit 1",
                ("claude",),
            ).fetchone()
    except sqlite3.Error:
        return None
    if not row:
        return None
    provider_id, name, settings_config = row
    return _provider_from_settings_config(
        provider_id=str(provider_id) if provider_id is not None else None,
        name=str(name) if name is not None else None,
        source=str(db_path),
        settings_config=str(settings_config or "{}"),
    )


def _load_claude_settings_provider() -> CCSwitchProvider | None:
    settings_path = pathlib.Path.home() / ".claude" / "settings.json"
    if not settings_path.is_file():
        return None
    try:
        payload = json.loads(settings_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    env = _coerce_env(payload.get("env"))
    if not env:
        return None
    return CCSwitchProvider(
        provider_id=None,
        name="Claude Code settings",
        source=str(settings_path),
        env=env,
    )


def load_current_ccswitch_provider() -> CCSwitchProvider | None:
    configured_home = os.environ.get("AGPAIR_CC_SWITCH_HOME", "").strip()
    provider = _load_current_ccswitch_db_provider(ccswitch_home())
    if provider or configured_home:
        return provider
    return _load_claude_settings_provider()


def ccswitch_env_overrides(provider: CCSwitchProvider | None = None) -> dict[str, str]:
    selected = provider or load_current_ccswitch_provider()
    if not selected:
        return {}
    env = dict(selected.env)
    token = env.get("ANTHROPIC_AUTH_TOKEN", "").strip()
    if token and not env.get("ANTHROPIC_API_KEY", "").strip():
        env["ANTHROPIC_API_KEY"] = token
    env.update(claude_retry_env())
    return env


def claude_code_settings_error(value: str) -> str | None:
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


def _last_output_line(stdout: str, stderr: str) -> str:
    excerpt = (stderr or stdout or "").strip().splitlines()
    return excerpt[-1] if excerpt else "unknown error"


def _redact_sensitive_text(text: str, env: dict[str, str]) -> str:
    redacted = text
    for key, value in env.items():
        if not value or len(value) < 6:
            continue
        upper_key = key.upper()
        if any(marker in upper_key for marker in _SENSITIVE_ENV_KEY_MARKERS):
            redacted = redacted.replace(value, "<redacted>")
    return redacted


def _managed_natural_probe_args(prompt: str) -> list[str]:
    return [
        "--permission-mode",
        "default",
        "--no-session-persistence",
        "--output-format",
        "json",
        "--print",
        prompt,
    ]


def _json_result_error(stdout: str, label: str, auth_hint: str, *, redaction_env: dict[str, str]) -> str | None:
    if not stdout.strip():
        return f"{label} live auth check produced no output"
    try:
        payload = json.loads(stdout.strip().splitlines()[-1])
    except json.JSONDecodeError:
        return None
    if payload.get("api_error_status") == 401:
        return f"{label} live auth check failed: Invalid Authentication; {auth_hint}"
    if payload.get("is_error") is True:
        summary = (
            payload.get("result")
            or payload.get("error")
            or payload.get("subtype")
            or "unknown error"
        )
        summary = _redact_sensitive_text(str(summary), redaction_env)
        return f"{label} live auth check failed: {summary}"
    return None


def _live_probe_timeout() -> float:
    raw = os.environ.get("AGPAIR_CLAUDE_CODE_LIVE_PROBE_TIMEOUT_SECONDS", "").strip()
    if raw:
        try:
            value = float(raw)
        except ValueError:
            value = DEFAULT_LIVE_PROBE_TIMEOUT_SECONDS
        if value > 0:
            return value
    return DEFAULT_LIVE_PROBE_TIMEOUT_SECONDS


def _run_probe(
    binary_path: str,
    args: list[str],
    *,
    env: dict[str, str] | None = None,
    timeout_seconds: float,
) -> subprocess.CompletedProcess[str]:
    process = subprocess.Popen(
        [binary_path, *args],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout_seconds)
        return subprocess.CompletedProcess(
            [binary_path, *args],
            process.returncode,
            stdout,
            stderr,
        )
    except subprocess.TimeoutExpired as exc:
        _terminate_process_tree(process.pid, signal.SIGTERM)
        try:
            stdout, stderr = process.communicate(timeout=2.0)
        except subprocess.TimeoutExpired:
            _terminate_process_tree(process.pid, signal.SIGKILL)
            stdout, stderr = process.communicate()
        message = f"command timed out after {timeout_seconds:g}s"
        stderr = (stderr or "") + ("\n" if stderr else "") + message
        raise subprocess.TimeoutExpired(
            exc.cmd,
            timeout_seconds,
            output=stdout,
            stderr=stderr,
        ) from exc


def _terminate_process_tree(root_pid: int, sig: signal.Signals) -> None:
    descendants = _process_descendants(root_pid)
    try:
        os.killpg(root_pid, sig)
    except OSError:
        try:
            os.kill(root_pid, sig)
        except OSError:
            pass
    for pid in sorted(descendants, reverse=True):
        try:
            os.kill(pid, sig)
        except OSError:
            pass


def _process_descendants(root_pid: int) -> set[int]:
    try:
        output = subprocess.check_output(["ps", "-axo", "pid=,ppid="], text=True, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return set()
    children: dict[int, list[int]] = {}
    for line in output.splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue
        try:
            pid = int(parts[0])
            ppid = int(parts[1])
        except ValueError:
            continue
        children.setdefault(ppid, []).append(pid)
    descendants: set[int] = set()
    stack = list(children.get(root_pid, ()))
    while stack:
        pid = stack.pop()
        if pid in descendants:
            continue
        descendants.add(pid)
        stack.extend(children.get(pid, ()))
    return descendants


def _live_probe_error(
    binary_path: str,
    *,
    label: str,
    auth_hint: str,
    args: list[str],
    env_overrides: dict[str, str] | None = None,
    timeout_seconds: float | None = None,
) -> str | None:
    timeout = _live_probe_timeout() if timeout_seconds is None else timeout_seconds
    env = os.environ.copy()
    env.update(claude_retry_env())
    if env_overrides:
        env.update(env_overrides)
    try:
        proc = _run_probe(binary_path, args, env=env, timeout_seconds=timeout)
    except (OSError, subprocess.SubprocessError, subprocess.TimeoutExpired) as exc:
        message = _redact_sensitive_text(str(exc), env)
        return f"{label} live auth check failed: {type(exc).__name__}: {message}"
    stdout = (proc.stdout or "").strip()
    stderr = (proc.stderr or "").strip()
    combined_output = f"{stdout}\n{stderr}"
    if "Invalid Authentication" in combined_output or (
        "api_error_status" in combined_output and "401" in combined_output
    ):
        return f"{label} live auth check failed: Invalid Authentication; {auth_hint}"
    if proc.returncode != 0:
        return f"{label} live auth check failed: {_redact_sensitive_text(_last_output_line(stdout, stderr), env)}"
    return _json_result_error(stdout, label, auth_hint, redaction_env=env)


def claude_oauth_error(binary_path: str | None, *, live_probe: bool = False) -> str | None:
    if not binary_path:
        return "Claude Code OAuth/subscription auth requires a claude binary"
    try:
        proc = _run_probe(binary_path, ["auth", "status"], timeout_seconds=5.0)
    except (OSError, subprocess.SubprocessError, subprocess.TimeoutExpired) as exc:
        return f"Claude Code OAuth/subscription auth check failed: {type(exc).__name__}: {exc}"
    if proc.returncode != 0:
        return (
            "Claude Code OAuth/subscription auth check failed: "
            f"{_last_output_line(proc.stdout or '', proc.stderr or '')}"
        )
    try:
        status = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError as exc:
        return f"Claude Code OAuth/subscription auth check returned invalid JSON: {exc}"
    if status.get("loggedIn") is not True:
        return "Claude Code OAuth/subscription auth is not logged in; run `claude auth login`"
    if not live_probe:
        return None
    return _live_probe_error(
        binary_path,
        label="Claude Code OAuth/subscription",
        auth_hint="run `claude auth login`",
        args=_managed_natural_probe_args("Return exactly: agpair-oauth-health-ok"),
    )


def claude_api_error(*, live_probe: bool = False, binary_path: str | None = None) -> str | None:
    del live_probe, binary_path
    settings_error = claude_code_settings_error(os.environ.get("AGPAIR_CLAUDE_CODE_SETTINGS", ""))
    if settings_error:
        return settings_error
    if not (
        os.environ.get("ANTHROPIC_API_KEY", "").strip()
        or os.environ.get("AGPAIR_CLAUDE_CODE_SETTINGS", "").strip()
    ):
        return "Claude Code API mode requires ANTHROPIC_API_KEY or AGPAIR_CLAUDE_CODE_SETTINGS"
    return None


def claude_ccswitch_error(
    binary_path: str | None,
    *,
    live_probe: bool = False,
    provider: CCSwitchProvider | None = None,
) -> tuple[str | None, CCSwitchProvider | None]:
    selected = provider or load_current_ccswitch_provider()
    if not selected:
        return (
            "Claude Code CC Switch provider is not configured; "
            "select a Claude provider in CC Switch",
            None,
        )
    env = ccswitch_env_overrides(selected)
    has_token = (
        env.get("ANTHROPIC_API_KEY", "").strip()
        or env.get("ANTHROPIC_AUTH_TOKEN", "").strip()
    )
    if not has_token:
        provider_name = selected.name or selected.provider_id or "<unnamed>"
        return (
            f"Claude Code CC Switch provider {provider_name} has no API token",
            selected,
        )
    if live_probe:
        if not binary_path:
            return "Claude Code CC Switch provider auth requires a claude binary", selected
        provider_name = selected.name or selected.provider_id or "<unnamed>"
        error = _live_probe_error(
            binary_path,
            label=f"Claude Code CC Switch provider {provider_name}",
            auth_hint="update the current Claude provider in CC Switch",
            args=_managed_natural_probe_args("Return exactly: agpair-ccswitch-health-ok"),
            env_overrides=env,
        )
        if error:
            return error, selected
    return None, selected


def resolve_claude_auth(
    binary_path: str | None,
    *,
    live_probe: bool = False,
) -> ClaudeAuthResolution:
    explicit = explicit_claude_auth_mode()
    if explicit == "oauth":
        error = claude_oauth_error(binary_path, live_probe=live_probe)
        return ClaudeAuthResolution(mode="oauth", error=error)
    if explicit == "api":
        error = claude_api_error(live_probe=live_probe, binary_path=binary_path)
        return ClaudeAuthResolution(mode="api", error=error)
    if explicit == "ccswitch":
        error, provider = claude_ccswitch_error(binary_path, live_probe=live_probe)
        return ClaudeAuthResolution(
            mode="ccswitch",
            error=error,
            ccswitch_provider=provider,
            env_overrides=ccswitch_env_overrides(provider) if provider and not error else None,
        )

    oauth_error = claude_oauth_error(binary_path, live_probe=live_probe)
    if oauth_error is None:
        return ClaudeAuthResolution(mode="oauth")

    ccswitch_error, provider = claude_ccswitch_error(binary_path, live_probe=live_probe)
    if ccswitch_error is None:
        return ClaudeAuthResolution(
            mode="ccswitch",
            ccswitch_provider=provider,
            env_overrides=ccswitch_env_overrides(provider),
        )
    return ClaudeAuthResolution(
        mode="auto",
        error=f"{oauth_error}; {ccswitch_error}",
        ccswitch_provider=provider,
    )
