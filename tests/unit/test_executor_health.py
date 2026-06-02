from agpair.executors.health import (
    ExecutorHealth,
    choose_healthy_executor,
    executor_is_eligible,
)


def test_available_executor_is_eligible() -> None:
    health = ExecutorHealth(executor_id="antigravity-cli", available=True)

    assert executor_is_eligible(health)


def test_malformed_receipts_make_executor_ineligible() -> None:
    health = ExecutorHealth(
        executor_id="antigravity-cli",
        available=True,
        malformed_receipt_count=3,
    )

    assert not executor_is_eligible(health)


def test_explicit_unavailable_executor_fails_without_silent_fallback() -> None:
    health = {
        "antigravity-cli": ExecutorHealth(executor_id="antigravity-cli", available=False),
        "grok-cli": ExecutorHealth(executor_id="grok-cli", available=True),
    }

    chosen = choose_healthy_executor(
        ["antigravity-cli", "grok-cli"],
        health,
        explicit_executor="antigravity-cli",
    )

    assert chosen is None


def test_implicit_routing_uses_next_healthy_external_executor() -> None:
    health = {
        "antigravity-cli": ExecutorHealth(executor_id="antigravity-cli", available=False),
        "grok-cli": ExecutorHealth(executor_id="grok-cli", available=True),
        "codex": ExecutorHealth(executor_id="codex", available=True),
    }

    assert choose_healthy_executor(["antigravity-cli", "grok-cli", "codex"], health) == "grok-cli"
