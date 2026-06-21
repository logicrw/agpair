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


def test_watch_event_emits_signal_metadata_without_log_body() -> None:
    previous = WatchEvent(
        task_id="TASK-123",
        state="acked",
        cursor="same",
        signal_state="silent",
        stdout_bytes=0,
        stderr_bytes=0,
    )
    current = WatchEvent(
        task_id="TASK-123",
        state="acked",
        cursor="same",
        signal_state="active_via_output",
        controller_action="continue_waiting",
        stdout_bytes=128,
        stderr_bytes=0,
        last_signal_at="2026-06-11T12:00:00+00:00",
    )

    payload = current.to_json_dict()

    assert should_emit_watch_event(previous, current)
    assert payload["signal_state"] == "active_via_output"
    assert payload["stdout_bytes"] == 128
    assert payload["controller_action"] == "continue_waiting"
    assert "log_body" not in payload


def test_watch_event_emits_agent_result_changed_without_log_body() -> None:
    previous = WatchEvent(task_id="TASK-123", state="acked", cursor="1")
    current = WatchEvent(
        task_id="TASK-123",
        state="ready_for_review",
        cursor="2",
        event="agent_result_changed",
        agent_result={
            "state": "usable",
            "controller_action": "review_then_apply",
        },
        artifact_result={
            "state": "usable",
            "primary_artifact": "diff",
            "global_hard_blockers": [],
        },
        recovery_decision={
            "action": "review_then_apply",
            "reason": "External executor produced code or diff evidence that must be reviewed before applying.",
        },
    )

    payload = current.to_json_dict()

    assert should_emit_watch_event(previous, current)
    assert payload["event"] == "agent_result_changed"
    assert payload["agent_result"]["controller_action"] == "review_then_apply"
    assert payload["artifact_result"]["primary_artifact"] == "diff"
    assert payload["recovery_decision"]["action"] == "review_then_apply"
    assert "log_body" not in payload


def test_watch_event_emits_when_artifact_result_changes() -> None:
    previous = WatchEvent(
        task_id="TASK-123",
        state="ready_for_review",
        cursor="2",
        artifact_result={"state": "needs_review", "primary_artifact": "stdout_salvage"},
    )
    current = WatchEvent(
        task_id="TASK-123",
        state="ready_for_review",
        cursor="2",
        artifact_result={"state": "usable", "primary_artifact": "report"},
    )

    assert should_emit_watch_event(previous, current)
