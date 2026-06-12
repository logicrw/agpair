from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WatchEvent:
    task_id: str
    state: str
    cursor: str
    heartbeat: str | None = None
    summary: str | None = None
    receipt_path: str | None = None
    raw_log_path: str | None = None
    signal_state: str | None = None
    controller_action: str | None = None
    stdout_bytes: int | None = None
    stderr_bytes: int | None = None
    last_signal_at: str | None = None
    agent_result: dict[str, object] | None = None
    event: str = "state"

    def to_json_dict(self) -> dict[str, object]:
        payload = {
            "schema_version": "1",
            "event": self.event,
            "task_id": self.task_id,
            "state": self.state,
            "cursor": self.cursor,
        }
        for key in (
            "heartbeat",
            "summary",
            "receipt_path",
            "raw_log_path",
            "signal_state",
            "controller_action",
            "stdout_bytes",
            "stderr_bytes",
            "last_signal_at",
            "agent_result",
        ):
            value = getattr(self, key)
            if value is not None:
                payload[key] = value
        return payload


def should_emit_watch_event(previous: WatchEvent | None, current: WatchEvent) -> bool:
    if previous is None:
        return True
    return (
        previous.state != current.state
        or previous.cursor != current.cursor
        or previous.heartbeat != current.heartbeat
        or previous.receipt_path != current.receipt_path
        or previous.raw_log_path != current.raw_log_path
        or previous.signal_state != current.signal_state
        or previous.controller_action != current.controller_action
        or previous.stdout_bytes != current.stdout_bytes
        or previous.stderr_bytes != current.stderr_bytes
        or previous.last_signal_at != current.last_signal_at
        or previous.agent_result != current.agent_result
    )
