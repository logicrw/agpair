import { describe, it } from "node:test";
import * as assert from "node:assert/strict";

import { MonitorController } from "../sdk/monitorController";
import { PendingEventStore } from "../state/pendingEventStore";
import { TaskSessionStore } from "../state/taskSessionStore";
import { DelegationTaskTracker } from "../state/delegationTaskTracker";

function createFakeSdk() {
  let stepHandler:
    | ((change: { sessionId: string; newCount: number; delta: number }) => void)
    | null = null;
  let activeHandler:
    | ((change: { previousSessionId: string; sessionId: string }) => void)
    | null = null;

  return {
    sdk: {
      monitor: {
        onStepCountChanged(handler: typeof stepHandler) {
          stepHandler = handler;
          return { dispose() {} };
        },
        onActiveSessionChanged(handler: typeof activeHandler) {
          activeHandler = handler;
          return { dispose() {} };
        },
        start() {},
        stop() {},
      },
    } as any,
    emitStep(change: { sessionId: string; newCount: number; delta: number }) {
      assert.ok(stepHandler, "step handler must be registered");
      stepHandler(change);
    },
    emitActive(change: { previousSessionId: string; sessionId: string }) {
      assert.ok(activeHandler, "active handler must be registered");
      activeHandler(change);
    },
  };
}

describe("MonitorController delegated activity tracking", () => {
  it("updates delegated task activity when the monitored session produces steps", () => {
    const fake = createFakeSdk();
    const eventStore = new PendingEventStore();
    const sessionStore = new TaskSessionStore();
    const tracker = new DelegationTaskTracker();

    tracker.register({
      taskId: "TASK-DELEGATED-1",
      sessionId: "sess-delegated-1",
      repoPath: "/tmp/repo",
      receiptPath: "/tmp/repo/.delegation/TASK-DELEGATED-1.receipt.json",
      status: "ACKED",
      ackedAt: "2026-01-01T00:00:00Z",
      lastActivityAt: "2026-01-01T00:00:00Z",
      terminalSentAt: null,
      terminalStatus: null,
      terminalBody: null,
      pendingTerminalStatus: null,
      pendingTerminalBody: null,
      pendingTerminalPreparedAt: null,
    });

    const controller = new (MonitorController as any)(
      fake.sdk,
      eventStore,
      sessionStore,
      tracker,
    ) as MonitorController;
    controller.start(60000, 60000);

    try {
      fake.emitStep({
        sessionId: "sess-delegated-1",
        newCount: 3,
        delta: 3,
      });

      const tracked = tracker.get("TASK-DELEGATED-1");
      assert.ok(tracked, "delegated task should remain tracked");
      assert.equal(tracked.status, "RUNNING");
      assert.notEqual(
        tracked.lastActivityAt,
        "2026-01-01T00:00:00Z",
        "real step activity must refresh delegated lastActivityAt",
      );
      assert.deepEqual(
        eventStore.getPending("TASK-DELEGATED-1"),
        [],
        "delegated step activity should refresh the tracker, not emit bridge pending events",
      );
    } finally {
      controller.dispose();
    }
  });
});
