from __future__ import annotations

from enum import StrEnum
from typing import Final


class CoordinationRole(StrEnum):
    THINKER = "thinker"
    WORKER = "worker"
    VERIFIER = "verifier"
    SYNTHESIZER = "synthesizer"
    GATE = "gate"
    GENERAL = "general"


ROLE_VALUES: Final = frozenset(item.value for item in CoordinationRole)

_ROLE_PROMPT_HINTS: Final[dict[str, str]] = {
    CoordinationRole.THINKER.value: (
        "Act as a thinker: explore assumptions, alternatives, risks, and tradeoffs. "
        "Prefer a useful report with evidence and uncertainty over pretending to finish implementation work."
    ),
    CoordinationRole.WORKER.value: (
        "Act as a worker: produce the smallest complete implementation or test-fix candidate allowed by the task. "
        "Report changed files and validation evidence clearly."
    ),
    CoordinationRole.VERIFIER.value: (
        "Act as a verifier: check claims, diffs, tests, evidence, and failure modes. "
        "Report confirmed findings, unverified areas, and concrete risks without taking over final adoption."
    ),
    CoordinationRole.SYNTHESIZER.value: (
        "Act as a synthesizer: combine lane evidence, identify consensus and contradictions, and keep partial or failed lanes visible."
    ),
    CoordinationRole.GATE.value: (
        "Act as a gate: evaluate whether the workflow has enough evidence for the controller to inspect, without treating role labels as proof."
    ),
    CoordinationRole.GENERAL.value: (
        "Act as a general AGPair executor: follow the task contract and return the most useful evidence available."
    ),
}


def normalize_coordination_role(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().lower().replace("-", "_")
    if normalized in ROLE_VALUES:
        return normalized
    allowed = ", ".join(sorted(ROLE_VALUES))
    raise ValueError(f"coordination role must be one of: {allowed}")


def role_prompt_hint(value: str | None) -> str:
    role = normalize_coordination_role(value) or CoordinationRole.GENERAL.value
    return _ROLE_PROMPT_HINTS[role]
