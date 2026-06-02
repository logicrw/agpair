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
    event: str = "state"

    def to_json_dict(self) -> dict[str, str]:
        payload = {
            "schema_version": "1",
            "event": self.event,
            "task_id": self.task_id,
            "state": self.state,
            "cursor": self.cursor,
        }
        for key in ("heartbeat", "summary", "receipt_path", "raw_log_path"):
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
    )
