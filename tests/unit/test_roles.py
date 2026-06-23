import pytest

from agpair.roles import ROLE_VALUES, normalize_coordination_role, role_prompt_hint


def test_normalize_coordination_role_accepts_known_roles() -> None:
    assert normalize_coordination_role("Thinker") == "thinker"
    assert normalize_coordination_role("worker") == "worker"
    assert normalize_coordination_role("verifier") == "verifier"
    assert normalize_coordination_role("synthesizer") == "synthesizer"
    assert normalize_coordination_role("gate") == "gate"
    assert normalize_coordination_role("general") == "general"
    assert normalize_coordination_role(None) is None


def test_normalize_coordination_role_rejects_unknown_role() -> None:
    with pytest.raises(ValueError, match="coordination role"):
        normalize_coordination_role("judge")


def test_role_prompt_hint_is_advisory_contract_text() -> None:
    hint = role_prompt_hint("verifier")

    assert "Act as a verifier" in hint
    assert "without taking over final adoption" in hint
    assert ROLE_VALUES == frozenset({"thinker", "worker", "verifier", "synthesizer", "gate", "general"})
