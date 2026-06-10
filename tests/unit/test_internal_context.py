from agpair.internal_context import (
    INTERNAL_ROLE_ENV,
    NONINTERACTIVE_ENV,
    SUPPRESS_CLIENT_HOOKS_ENV,
    build_internal_executor_env,
    build_internal_probe_env,
    client_hooks_suppressed,
    internal_role,
)


def test_client_hooks_suppressed_by_explicit_flag() -> None:
    assert client_hooks_suppressed({SUPPRESS_CLIENT_HOOKS_ENV: "1"}) is True
    assert client_hooks_suppressed({SUPPRESS_CLIENT_HOOKS_ENV: "true"}) is True
    assert client_hooks_suppressed({SUPPRESS_CLIENT_HOOKS_ENV: "0"}) is False


def test_client_hooks_suppressed_for_internal_roles() -> None:
    assert client_hooks_suppressed({INTERNAL_ROLE_ENV: "probe"}) is True
    assert client_hooks_suppressed({INTERNAL_ROLE_ENV: "executor"}) is True
    assert client_hooks_suppressed({INTERNAL_ROLE_ENV: "controller"}) is False


def test_build_internal_probe_env_marks_noninteractive_probe() -> None:
    env = build_internal_probe_env({"PATH": "/bin", "AGPAIR_DELEGATION_DEPTH": "9"})

    assert env["PATH"] == "/bin"
    assert env[INTERNAL_ROLE_ENV] == "probe"
    assert env[SUPPRESS_CLIENT_HOOKS_ENV] == "1"
    assert env[NONINTERACTIVE_ENV] == "1"
    assert env["CI"] == "1"
    assert env["AGPAIR_DELEGATION_DEPTH"] == "1"


def test_build_internal_executor_env_increments_depth_and_records_parent() -> None:
    env = build_internal_executor_env("TASK-123", {"AGPAIR_DELEGATION_DEPTH": "2"})

    assert env[INTERNAL_ROLE_ENV] == "executor"
    assert env[SUPPRESS_CLIENT_HOOKS_ENV] == "1"
    assert env[NONINTERACTIVE_ENV] == "1"
    assert env["AGPAIR_PARENT_TASK_ID"] == "TASK-123"
    assert env["AGPAIR_DELEGATION_DEPTH"] == "3"
    assert internal_role(env) == "executor"
