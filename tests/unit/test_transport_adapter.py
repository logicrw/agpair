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
