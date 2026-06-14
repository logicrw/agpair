from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal

AgentResultState = Literal["usable", "needs_review", "blocked"]
ControllerAction = Literal[
    "use_result",
    "review_then_apply",
    "inspect_evidence",
    "wait_background",
    "retry_same_executor",
    "switch_executor",
    "native_fallback",
    "repair_executor",
    "retry_or_switch_executor",
]

LOW_RISK_PROTOCOL_WARNINGS = frozenset(
    {
        "schema_version_alias",
        "status_alias",
        "mixed_text_json",
        "wrapped_text_json",
        "artifact_path_missing",
    }
)


@dataclass(frozen=True, slots=True)
class AgentResult:
    state: AgentResultState
    controller_action: ControllerAction
    summary: str
    hard_blockers: tuple[str, ...] = ()
    soft_warnings: tuple[str, ...] = ()
    evidence_paths: dict[str, str] | None = None

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["hard_blockers"] = list(self.hard_blockers)
        payload["soft_warnings"] = list(self.soft_warnings)
        if self.evidence_paths is None:
            payload.pop("evidence_paths", None)
        return payload


def unique(values: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


def warning_is_low_risk(warning: str) -> bool:
    return warning in LOW_RISK_PROTOCOL_WARNINGS


def blocking_protocol_warnings(warnings: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(warning for warning in unique(warnings) if not warning_is_low_risk(warning))
