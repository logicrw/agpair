from agpair.watch import WatchEvent, should_emit_watch_event


def test_watch_emits_state_changes() -> None:
    previous = WatchEvent(task_id="TASK-123", state="acked", cursor="1")
    current = WatchEvent(task_id="TASK-123", state="ready_for_review", cursor="2")

    assert should_emit_watch_event(previous, current)


def test_watch_suppresses_unchanged_heartbeat() -> None:
    previous = WatchEvent(task_id="TASK-123", state="acked", cursor="1", heartbeat="same")
    current = WatchEvent(task_id="TASK-123", state="acked", cursor="1", heartbeat="same")

    assert not should_emit_watch_event(previous, current)


def test_watch_event_references_raw_log_path_without_streaming_log_body() -> None:
    event = WatchEvent(
        task_id="TASK-123",
        state="ready_for_review",
        cursor="attempt-1:receipt:3",
        raw_log_path=".agpair/tasks/TASK-123/attempt-1/stdout.log",
        summary="Terminal receipt available.",
    )

    payload = event.to_json_dict()

    assert payload["raw_log_path"].endswith("stdout.log")
    assert "log_body" not in payload
