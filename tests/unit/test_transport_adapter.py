from __future__ import annotations

from pathlib import Path
import json
from agpair.transport.bus import AgentBusClient
from tests.fixtures.fake_agent_bus import read_calls, write_fake_agent_bus


def test_send_task_shells_out_to_agent_bus_cli(tmp_path: Path, monkeypatch) -> None:
    binary, calls_path, pull_path = write_fake_agent_bus(tmp_path)
    monkeypatch.setenv("FAKE_AGENT_BUS_CALLS", str(calls_path))
    monkeypatch.setenv("FAKE_AGENT_BUS_PULL", str(pull_path))

    bus = AgentBusClient(binary)
    message_id = bus.send_task(task_id="TASK-1", body="Goal: test", repo_path="/tmp/repo")

    assert message_id == 101
    recorded = read_calls(calls_path)
    assert recorded[-1]["argv"][:4] == ["agent-bus", "send", "--sender", "desktop"]
    assert "Goal: test" in recorded[-1]["body"]
    assert "repo_path: /tmp/repo" in recorded[-1]["body"]


def test_pull_receipts_shells_out_to_agent_bus_cli(tmp_path: Path, monkeypatch) -> None:
    binary, calls_path, pull_path = write_fake_agent_bus(tmp_path)
    pull_path.write_text(
        json.dumps(
            {
                "ok": True,
                "reader": "desktop",
                "claimed": 1,
                "messages": [{"id": 1, "task_id": "TASK-1", "status": "ACK", "body": "ok"}],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("FAKE_AGENT_BUS_CALLS", str(calls_path))
    monkeypatch.setenv("FAKE_AGENT_BUS_PULL", str(pull_path))

    bus = AgentBusClient(binary)
    receipts = bus.pull_receipts()

    assert receipts[0]["status"] == "ACK"
    recorded = read_calls(calls_path)
    assert recorded[-1]["argv"][:6] == ["agent-bus", "pull", "--sender", "code", "--reader", "desktop"]


def test_pull_receipts_raises_bus_pull_error_on_subprocess_failure(tmp_path: Path, monkeypatch) -> None:
    """A failing agent-bus process should raise BusPullError, not CalledProcessError."""
    from agpair.transport.bus import BusPullError

    # Write a script that always exits 1
    failing_script = tmp_path / "agent-bus-fail"
    failing_script.write_text("#!/bin/sh\nexit 1\n")
    failing_script.chmod(0o755)

    bus = AgentBusClient(str(failing_script))
    import pytest

    with pytest.raises(BusPullError, match="agent-bus pull failed"):
        bus.pull_receipts()


def test_pull_receipts_raises_bus_pull_error_on_invalid_json(tmp_path: Path, monkeypatch) -> None:
    """Garbage stdout from agent-bus should raise BusPullError, not JSONDecodeError."""
    from agpair.transport.bus import BusPullError

    # Write a script that outputs invalid JSON
    garbage_script = tmp_path / "agent-bus-garbage"
    garbage_script.write_text('#!/bin/sh\necho "NOT-JSON{{{"\n')
    garbage_script.chmod(0o755)

    bus = AgentBusClient(str(garbage_script))
    import pytest

    with pytest.raises(BusPullError, match="invalid JSON"):
        bus.pull_receipts()


def test_send_raises_bus_send_error_on_subprocess_failure(tmp_path: Path, monkeypatch) -> None:
    from agpair.transport.bus import BusSendError

    failing_script = tmp_path / "agent-bus-fail"
    failing_script.write_text("#!/bin/sh\nexit 1\n")
    failing_script.chmod(0o755)

    bus = AgentBusClient(str(failing_script))
    import pytest

    with pytest.raises(BusSendError, match="agent-bus send failed"):
        bus.send_task(task_id="TASK-1", body="test", repo_path="/tmp/repo")


def test_send_raises_bus_send_error_on_invalid_json(tmp_path: Path, monkeypatch) -> None:
    from agpair.transport.bus import BusSendError

    garbage_script = tmp_path / "agent-bus-garbage"
    garbage_script.write_text('#!/bin/sh\necho "NOT-JSON{{{"\n')
    garbage_script.chmod(0o755)

    bus = AgentBusClient(str(garbage_script))
    import pytest

    with pytest.raises(BusSendError, match="invalid JSON"):
        bus.send_task(task_id="TASK-1", body="test", repo_path="/tmp/repo")
