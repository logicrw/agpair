/**
 * Tests for PendingEventStore semantics.
 *
 * Covers:
 *   - push and getPending returns undelivered events
 *   - markDeliveredByIds marks specific events as delivered
 *   - getPending excludes delivered events
 *   - markDeliveredByIds returns correct count
 *   - stable source_event_id across operations
 */

import { describe, it } from "node:test";
import * as assert from "node:assert/strict";
import { PendingEventStore, PendingEvent } from "../state/pendingEventStore";

function makeEvent(task_id: string, eventId: string, seq: number, status = "RUNNING"): PendingEvent {
  return {
    source_event_id: eventId,
    task_id,
    attempt_no: 1,
    review_round: 0,
    session_id: "session-1",
    source_seq: seq,
    status,
    payload: {},
    emitted_at: new Date().toISOString(),
    delivered_at: null,
  };
}

describe("PendingEventStore", () => {
  it("push and getPending returns undelivered events", () => {
    const store = new PendingEventStore();
    const evt = makeEvent("task-1", "evt-1", 1);
    store.push("task-1", evt);

    const pending = store.getPending("task-1");
    assert.equal(pending.length, 1);
    assert.equal(pending[0].source_event_id, "evt-1");
  });

  it("markDeliveredByIds marks events and removes from getPending", () => {
    const store = new PendingEventStore();
    store.push("task-1", makeEvent("task-1", "evt-1", 1));
    store.push("task-1", makeEvent("task-1", "evt-2", 2));
    store.push("task-1", makeEvent("task-1", "evt-3", 3));

    const acked = store.markDeliveredByIds("task-1", ["evt-1", "evt-2"]);
    assert.equal(acked, 2);

    const pending = store.getPending("task-1");
    assert.equal(pending.length, 1);
    assert.equal(pending[0].source_event_id, "evt-3");
  });

  it("markDeliveredByIds returns 0 for unknown ids", () => {
    const store = new PendingEventStore();
    store.push("task-1", makeEvent("task-1", "evt-1", 1));

    const acked = store.markDeliveredByIds("task-1", ["evt-999"]);
    assert.equal(acked, 0);

    const pending = store.getPending("task-1");
    assert.equal(pending.length, 1);
  });

  it("getPending returns empty for unknown task", () => {
    const store = new PendingEventStore();
    assert.deepEqual(store.getPending("nonexistent"), []);
  });

  it("markDelivered marks all pending events", () => {
    const store = new PendingEventStore();
    store.push("task-1", makeEvent("task-1", "evt-1", 1));
    store.push("task-1", makeEvent("task-1", "evt-2", 2));

    store.markDelivered("task-1");

    const pending = store.getPending("task-1");
    assert.equal(pending.length, 0);
  });

  it("idempotent: marking already-delivered events returns 0", () => {
    const store = new PendingEventStore();
    store.push("task-1", makeEvent("task-1", "evt-1", 1));

    assert.equal(store.markDeliveredByIds("task-1", ["evt-1"]), 1);
    assert.equal(store.markDeliveredByIds("task-1", ["evt-1"]), 0);
  });

  it("source_event_id is stable across operations", () => {
    const store = new PendingEventStore();
    const evt = makeEvent("task-1", "session-1:evt:1", 1);
    store.push("task-1", evt);

    const pending1 = store.getPending("task-1");
    const pending2 = store.getPending("task-1");

    assert.equal(pending1[0].source_event_id, pending2[0].source_event_id);
    assert.equal(pending1[0].source_event_id, "session-1:evt:1");
  });

  it("deduplicates pushes with the same source_event_id", () => {
    const store = new PendingEventStore();
    store.push("task-1", makeEvent("task-1", "session-1:evt:1000", 1000, "EVIDENCE_PACK"));
    store.push("task-1", makeEvent("task-1", "session-1:evt:1000", 1000, "EVIDENCE_PACK"));

    const pending = store.getPending("task-1");
    assert.equal(pending.length, 1);
    assert.equal(pending[0].source_event_id, "session-1:evt:1000");
  });
});
