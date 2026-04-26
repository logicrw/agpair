import { describe, it } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";

import { DelegationHeartbeatService } from "../services/delegationHeartbeatService";
import { DelegationReceiptWatcher } from "../services/delegationReceiptWatcher";
import { DelegationTaskTracker } from "../state/delegationTaskTracker";
import {
  AgentBusDelegationService,
  type AgentBusDelegationReply,
} from "../services/agentBusDelegationService";

// ── Helpers ──────────────────────────────────────────────────────

function makeTempDir(): string {
  return fs.mkdtempSync(path.join(os.tmpdir(), "heartbeat-test-"));
}

function registerPendingTask(
  tracker: DelegationTaskTracker,
  taskId: string,
  opts?: Partial<{ sessionId: string; repoPath: string; ackedAt: string; ackSentAt: string | null }>,
): void {
  const ackSentAt =
    opts && Object.prototype.hasOwnProperty.call(opts, "ackSentAt")
      ? opts.ackSentAt ?? null
      : opts?.ackedAt ?? "2026-01-01T00:00:00Z";
  tracker.register({
    taskId,
    sessionId: opts?.sessionId ?? `sess-${taskId}`,
    repoPath: opts?.repoPath ?? "/tmp/repo",
    receiptPath: `/tmp/receipts/${taskId}.receipt.json`,
    status: "ACKED",
    ackedAt: opts?.ackedAt ?? "2026-01-01T00:00:00Z",
    ackSentAt,
    lastActivityAt: opts?.ackedAt ?? "2026-01-01T00:00:00Z",
    terminalSentAt: null,
    terminalStatus: null,
    terminalBody: null,
    pendingAckBody: null,
    pendingAckPreparedAt: null,
    pendingAckInflightAt: null,
    pendingAckDeliveryId: null,
    pendingTerminalStatus: null,
    pendingTerminalBody: null,
    pendingTerminalPreparedAt: null,
  });
}

// ── DelegationHeartbeatService unit tests ────────────────────────

describe("DelegationHeartbeatService", () => {
  it("sends RUNNING heartbeat for all pending tasks on tick()", async () => {
    const tracker = new DelegationTaskTracker();
    const sent: Array<{ taskId: string; status: string; body: string }> = [];

    registerPendingTask(tracker, "TASK-HB-1");
    registerPendingTask(tracker, "TASK-HB-2");

    const hb = new DelegationHeartbeatService({
      tracker,
      intervalMs: 60000, // won't auto-fire; we call tick() manually
      outputChannel: { appendLine: () => undefined },
      sendRunning: async (reply) => {
        sent.push({
          taskId: reply.taskId,
          status: reply.status,
          body: reply.body,
        });
      },
    });

    await hb.tick();

    assert.equal(sent.length, 2);
    assert.equal(sent[0].taskId, "TASK-HB-1");
    assert.equal(sent[0].status, "RUNNING");
    assert.match(sent[0].body, /Liveness heartbeat/);
    assert.match(sent[0].body, /NOT a progress indicator/);
    assert.equal(sent[1].taskId, "TASK-HB-2");
    assert.equal(sent[1].status, "RUNNING");

    // Tracker should have updated lastHeartbeatAt but NOT lastActivityAt or status
    const t1 = tracker.get("TASK-HB-1")!;
    assert.equal(t1.status, "ACKED", "heartbeat must NOT change task status");
    assert.ok(t1.lastHeartbeatAt);
    assert.notEqual(t1.lastHeartbeatAt, null);
    // lastActivityAt should be unchanged from registration
    assert.equal(
      t1.lastActivityAt,
      "2026-01-01T00:00:00Z",
      "heartbeat must NOT update lastActivityAt",
    );

    hb.dispose();
  });

  it("does not send heartbeat for terminal tasks", async () => {
    const tracker = new DelegationTaskTracker();
    const sent: Array<{ taskId: string }> = [];

    registerPendingTask(tracker, "TASK-HB-TERM");
    tracker.markTerminal("TASK-HB-TERM", "EVIDENCE_PACK", "done");

    const hb = new DelegationHeartbeatService({
      tracker,
      intervalMs: 60000,
      outputChannel: { appendLine: () => undefined },
      sendRunning: async (reply) => {
        sent.push({ taskId: reply.taskId });
      },
    });

    await hb.tick();

    assert.equal(sent.length, 0, "should not heartbeat terminal tasks");
    hb.dispose();
  });

  it("start/stop lifecycle works", () => {
    const tracker = new DelegationTaskTracker();
    const hb = new DelegationHeartbeatService({
      tracker,
      intervalMs: 60000,
      outputChannel: { appendLine: () => undefined },
      sendRunning: async () => undefined,
    });

    assert.equal(hb.isRunning, false);
    hb.start();
    assert.equal(hb.isRunning, true);
    hb.stop();
    assert.equal(hb.isRunning, false);
    hb.dispose();
  });

  it("heartbeat failure is non-fatal (does not crash the tick)", async () => {
    const tracker = new DelegationTaskTracker();
    const output: string[] = [];

    registerPendingTask(tracker, "TASK-HB-FAIL-1");
    registerPendingTask(tracker, "TASK-HB-FAIL-2");

    let callCount = 0;
    const hb = new DelegationHeartbeatService({
      tracker,
      intervalMs: 60000,
      outputChannel: { appendLine: (msg: string) => output.push(msg) },
      sendRunning: async (reply) => {
        callCount++;
        if (reply.taskId === "TASK-HB-FAIL-1") {
          throw new Error("send failure");
        }
      },
    });

    await hb.tick();

    // Both tasks attempted; first failed but second still processed
    assert.equal(callCount, 2);
    assert.ok(output.some((l) => /send failure/.test(l)));

    hb.dispose();
  });

  it("exposes heartbeatIntervalMs", () => {
    const tracker = new DelegationTaskTracker();
    const hb = new DelegationHeartbeatService({
      tracker,
      intervalMs: 15000,
      outputChannel: { appendLine: () => undefined },
      sendRunning: async () => undefined,
    });

    assert.equal(hb.heartbeatIntervalMs, 15000);
    hb.dispose();
  });

  it("enforces minimum 1000ms interval", () => {
    const tracker = new DelegationTaskTracker();
    const hb = new DelegationHeartbeatService({
      tracker,
      intervalMs: 100, // too low
      outputChannel: { appendLine: () => undefined },
      sendRunning: async () => undefined,
    });

    assert.ok(hb.heartbeatIntervalMs >= 1000);
    hb.dispose();
  });
});

// ── Integration: TASK → ACK → RUNNING heartbeat → EVIDENCE_PACK ──

describe("Heartbeat integration scenarios", () => {
  it("ACK send failure leaves a durable pending ACK that can be replayed after restart", async () => {
    const dir = makeTempDir();
    const statePath = path.join(dir, "delegation-state.json");
    const tracker = new DelegationTaskTracker(statePath);
    let ackAttempts = 0;

    const service = new AgentBusDelegationService({
      enabled: true,
      command: "agent-bus",
      workspacePathsProvider: () => ["/tmp/repo-ack-pending"],
      outputChannel: { appendLine: () => undefined },
      sessionCtrl: {
        async createBackgroundSession() {
          return { ok: true, session_id: "sess-ack-pending-1" };
        },
        async sendPrompt() {
          return { ok: true };
        },
      } as any,
      tracker,
      receiptDir: dir,
      receiptPollIntervalMs: 60000,
      heartbeatIntervalMs: 60000,
      sendReply: async ({ status }: AgentBusDelegationReply) => {
        if (status === "ACK") {
          ackAttempts += 1;
          throw new Error("ack transport down");
        }
      },
    });

    await service.handleMessages([
      { id: 501, task_id: "TASK-ACK-PENDING", status: "TASK", body: "Goal:\nAck persistence." },
    ]);

    assert.equal(ackAttempts, 1);
    const task = tracker.get("TASK-ACK-PENDING");
    assert.ok(task);
    assert.equal(task!.ackSentAt, null);
    assert.ok(task!.pendingAckBody);
    assert.ok(task!.pendingAckPreparedAt);
    assert.ok(task!.pendingAckDeliveryId);

    const restored = new DelegationTaskTracker(statePath).get("TASK-ACK-PENDING");
    assert.ok(restored);
    assert.equal(restored!.ackSentAt, null);
    assert.ok(restored!.pendingAckBody);
    assert.ok(restored!.pendingAckDeliveryId);

    service.dispose();
    fs.rmSync(dir, { recursive: true, force: true });
  });

  it("replays pending ACK on startup before heartbeat or terminal delivery", async () => {
    const dir = makeTempDir();
    const statePath = path.join(dir, "delegation-state.json");
    const trackerBefore = new DelegationTaskTracker(statePath);
    trackerBefore.register({
      taskId: "TASK-ACK-REPLAY",
      sessionId: "sess-ack-replay-1",
      repoPath: "/tmp/repo-ack-replay",
      receiptPath: DelegationReceiptWatcher.receiptPath(dir, "TASK-ACK-REPLAY"),
      status: "ACKED",
      ackedAt: "2026-01-01T00:00:00Z",
      ackSentAt: null,
      lastActivityAt: "2026-01-01T00:00:00Z",
      terminalSentAt: null,
      terminalStatus: null,
      terminalBody: null,
      pendingAckBody: "Accepted by Antigravity auto-handoff. session_id=sess-ack-replay-1 repo_path=/tmp/repo-ack-replay",
      pendingAckPreparedAt: "2026-01-01T00:00:01Z",
      pendingAckInflightAt: null,
      pendingAckDeliveryId: "ack_abc123",
      pendingTerminalStatus: null,
      pendingTerminalBody: null,
      pendingTerminalPreparedAt: null,
    });

    const trackerAfter = new DelegationTaskTracker(statePath);
    const sent: Array<{ status: string; body: string }> = [];

    const service = new AgentBusDelegationService({
      enabled: true,
      command: "agent-bus",
      workspacePathsProvider: () => ["/tmp/repo-ack-replay"],
      outputChannel: { appendLine: () => undefined },
      sessionCtrl: {
        async createBackgroundSession() {
          return { ok: true, session_id: "unused" };
        },
        async sendPrompt() {
          return { ok: true };
        },
      } as any,
      tracker: trackerAfter,
      receiptDir: dir,
      receiptPollIntervalMs: 60000,
      heartbeatIntervalMs: 60000,
      sendReply: async ({ status, body }: AgentBusDelegationReply) => {
        sent.push({ status, body });
      },
    });

    await new Promise((resolve) => setTimeout(resolve, 0));

    assert.equal(sent.length, 1);
    assert.equal(sent[0].status, "ACK");
    assert.match(sent[0].body, /^X-Delivery-Id:\s*ack_abc123\n/);

    const restored = trackerAfter.get("TASK-ACK-REPLAY");
    assert.ok(restored);
    assert.ok(restored!.ackSentAt);
    assert.equal(restored!.pendingAckBody, null);
    assert.equal(restored!.pendingAckDeliveryId, null);

    service.dispose();
    fs.rmSync(dir, { recursive: true, force: true });
  });

  it("redelivered TASK after ACK failure reuses the existing session and retries ACK", async () => {
    const tracker = new DelegationTaskTracker();
    const terminated: string[] = [];
    let created = 0;
    let ackAttempts = 0;

    const service = new AgentBusDelegationService({
      enabled: true,
      command: "agent-bus",
      workspacePathsProvider: () => ["/tmp/repo-ack-redelivery"],
      outputChannel: { appendLine: () => undefined },
      sessionCtrl: {
        async createBackgroundSession() {
          created += 1;
          return { ok: true, session_id: "sess-ack-redelivery-1" };
        },
        async terminateSession(sessionId: string) {
          terminated.push(sessionId);
          return true;
        },
        async sendPrompt() {
          return { ok: true };
        },
      } as any,
      tracker,
      receiptDir: makeTempDir(),
      receiptPollIntervalMs: 60000,
      heartbeatIntervalMs: 60000,
      sendReply: async ({ status }: AgentBusDelegationReply) => {
        if (status === "ACK") {
          ackAttempts += 1;
          if (ackAttempts === 1) {
            throw new Error("ack transport down");
          }
        }
      },
    });

    try {
      await service.handleMessages([
        {
          id: 801,
          task_id: "TASK-ACK-REDLIVERY",
          status: "TASK",
          body: "Goal:\nAck redelivery.",
        },
      ]);
      assert.equal(created, 1);
      assert.equal(tracker.get("TASK-ACK-REDLIVERY")!.ackSentAt, null);

      await service.handleMessages([
        {
          id: 801,
          task_id: "TASK-ACK-REDLIVERY",
          status: "TASK",
          body: "Goal:\nAck redelivery.",
        },
      ]);

      assert.equal(created, 1, "redelivery must not create a new session");
      assert.deepEqual(terminated, []);
      assert.equal(ackAttempts, 2, "redelivery should retry the pending ACK");
      assert.ok(tracker.get("TASK-ACK-REDLIVERY")!.ackSentAt);
    } finally {
      service.dispose();
    }
  });

  it("does not send RUNNING heartbeat until ACK is durably delivered", async () => {
    const tracker = new DelegationTaskTracker();
    const sent: Array<{ status: string }> = [];
    registerPendingTask(tracker, "TASK-HB-GATED", { ackSentAt: null });
    tracker.preparePendingAck(
      "TASK-HB-GATED",
      "Accepted by Antigravity auto-handoff. session_id=sess-TASK-HB-GATED repo_path=/tmp/repo",
      "2026-01-01T00:00:01Z",
    );

    const hb = new DelegationHeartbeatService({
      tracker,
      intervalMs: 60000,
      outputChannel: { appendLine: () => undefined },
      sendRunning: async (reply) => {
        sent.push({ status: reply.status });
      },
    });

    await hb.tick();

    assert.deepEqual(sent, []);
    assert.equal(tracker.get("TASK-HB-GATED")!.lastHeartbeatAt, null);
    hb.dispose();
  });

  it("TASK handoff uses the stable interactive session creation fallback path", async () => {
    const tracker = new DelegationTaskTracker();
    let capturedOptions: unknown = null;
    let capturedPrompt = "";

    const service = new AgentBusDelegationService({
      enabled: true,
      command: "agent-bus",
      workspacePathsProvider: () => ["/tmp/repo-strict"],
      outputChannel: { appendLine: () => undefined },
      sessionCtrl: {
        async createBackgroundSession(prompt: string, options?: unknown) {
          capturedPrompt = prompt;
          capturedOptions = options ?? null;
          return { ok: true, session_id: "sess-strict-1" };
        },
        async sendPrompt() {
          return { ok: true };
        },
      } as any,
      tracker,
      receiptDir: makeTempDir(),
      receiptPollIntervalMs: 60000,
      heartbeatIntervalMs: 60000,
      sendReply: async () => undefined,
    });

    await service.handleMessages([
      {
        id: 77,
        task_id: "TASK-STRICT",
        status: "TASK",
        body: "Goal:\nStay headless.",
      },
    ]);

    assert.deepEqual(capturedOptions, {
      allowInteractiveFallback: true,
      contextLabel: "delegated task TASK-STRICT",
    });
    assert.match(capturedPrompt, /"schema_version": "1"/);
    assert.match(capturedPrompt, /"payload"/);
    assert.match(capturedPrompt, /"changed_files"/);
    assert.match(capturedPrompt, /"validation"/);

    service.dispose();
  });

  it("fresh TASK retry with the same task_id replaces the old tracked session", async () => {
    const tracker = new DelegationTaskTracker();
    const replies: Array<{ taskId: string; status: string; body: string }> = [];
    const terminated: string[] = [];
    const created: string[] = [];

    const service = new AgentBusDelegationService({
      enabled: true,
      command: "agent-bus",
      workspacePathsProvider: () => ["/tmp/repo-retry-same-id"],
      outputChannel: { appendLine: () => undefined },
      sessionCtrl: {
        async createBackgroundSession() {
          const sessionId = created.length === 0 ? "sess-old-1" : "sess-new-2";
          created.push(sessionId);
          return { ok: true, session_id: sessionId };
        },
        async terminateSession(sessionId: string) {
          terminated.push(sessionId);
          return true;
        },
        async sendPrompt() {
          return { ok: true };
        },
      } as any,
      tracker,
      receiptDir: makeTempDir(),
      receiptPollIntervalMs: 60000,
      heartbeatIntervalMs: 60000,
      sendReply: async ({ taskId, status, body }: AgentBusDelegationReply) => {
        replies.push({ taskId, status, body });
      },
    });

    try {
      await service.handleMessages([
        {
          id: 101,
          task_id: "TASK-SAME-ID",
          status: "TASK",
          body: "Goal:\nFirst attempt.",
        },
      ]);
      await service.handleMessages([
        {
          id: 102,
          task_id: "TASK-SAME-ID",
          status: "TASK",
          body: "Goal:\nFresh retry.",
        },
      ]);

      assert.deepEqual(created, ["sess-old-1", "sess-new-2"]);
      assert.deepEqual(
        terminated,
        ["sess-old-1"],
        "retry should terminate the old tracked session",
      );
      assert.equal(
        replies.filter((r) => r.status === "ACK").length,
        2,
        "both attempts should ACK",
      );

      const tracked = tracker.get("TASK-SAME-ID");
      assert.ok(tracked, "task should remain tracked");
      assert.equal(
        tracked.sessionId,
        "sess-new-2",
        "tracker must point at the fresh retry session",
      );
      assert.equal(
        tracked.terminalSentAt,
        null,
        "fresh retry must remain pending",
      );
      assert.equal(
        tracker.pendingCount(),
        1,
        "old pending entry must be replaced, not duplicated",
      );
    } finally {
      service.dispose();
    }
  });

  it("redelivered TASK with the same source message id replays ACK without recreating the session", async () => {
    const tracker = new DelegationTaskTracker();
    const replies: Array<{ taskId: string; status: string; body: string }> = [];
    const terminated: string[] = [];
    let created = 0;

    const service = new AgentBusDelegationService({
      enabled: true,
      command: "agent-bus",
      workspacePathsProvider: () => ["/tmp/repo-redelivery"],
      outputChannel: { appendLine: () => undefined },
      sessionCtrl: {
        async createBackgroundSession() {
          created += 1;
          return { ok: true, session_id: "sess-redelivery-1" };
        },
        async terminateSession(sessionId: string) {
          terminated.push(sessionId);
          return true;
        },
        async sendPrompt() {
          return { ok: true };
        },
      } as any,
      tracker,
      receiptDir: makeTempDir(),
      receiptPollIntervalMs: 60000,
      heartbeatIntervalMs: 60000,
      sendReply: async ({ taskId, status, body }: AgentBusDelegationReply) => {
        replies.push({ taskId, status, body });
      },
    });

    try {
      await service.handleMessages([
        {
          id: 701,
          task_id: "TASK-REDLIVERY",
          status: "TASK",
          body: "Goal:\nFirst delivery.",
        },
      ]);
      await service.handleMessages([
        {
          id: 701,
          task_id: "TASK-REDLIVERY",
          status: "TASK",
          body: "Goal:\nFirst delivery.",
        },
      ]);

      assert.equal(created, 1);
      assert.deepEqual(terminated, []);
      assert.equal(replies.filter((r) => r.status === "ACK").length, 1);
      assert.equal(tracker.get("TASK-REDLIVERY")!.sessionId, "sess-redelivery-1");
    } finally {
      service.dispose();
    }
  });

  it("same task_id with a different source message id still preempts as a fresh retry", async () => {
    const tracker = new DelegationTaskTracker();
    const terminated: string[] = [];
    let created = 0;

    const service = new AgentBusDelegationService({
      enabled: true,
      command: "agent-bus",
      workspacePathsProvider: () => ["/tmp/repo-redelivery-retry"],
      outputChannel: { appendLine: () => undefined },
      sessionCtrl: {
        async createBackgroundSession() {
          created += 1;
          return { ok: true, session_id: created === 1 ? "sess-retry-1" : "sess-retry-2" };
        },
        async terminateSession(sessionId: string) {
          terminated.push(sessionId);
          return true;
        },
        async sendPrompt() {
          return { ok: true };
        },
      } as any,
      tracker,
      receiptDir: makeTempDir(),
      receiptPollIntervalMs: 60000,
      heartbeatIntervalMs: 60000,
      sendReply: async () => undefined,
    });

    try {
      await service.handleMessages([
        { id: 901, task_id: "TASK-REDLIVERY-RETRY", status: "TASK", body: "Goal:\nAttempt one." },
      ]);
      await service.handleMessages([
        { id: 902, task_id: "TASK-REDLIVERY-RETRY", status: "TASK", body: "Goal:\nAttempt two." },
      ]);

      assert.equal(created, 2);
      assert.deepEqual(terminated, ["sess-retry-1"]);
      assert.equal(tracker.get("TASK-REDLIVERY-RETRY")!.sessionId, "sess-retry-2");
    } finally {
      service.dispose();
    }
  });

  it("TASK → ACK → RUNNING heartbeat → terminal EVIDENCE_PACK", async () => {
    const dir = makeTempDir();
    const tracker = new DelegationTaskTracker();
    const replies: Array<{ taskId: string; status: string; body: string }> = [];

    // Step 1: Create service and handle TASK → ACK
    const service = new AgentBusDelegationService({
      enabled: true,
      command: "agent-bus",
      workspacePathsProvider: () => ["/tmp/repo-hb"],
      outputChannel: { appendLine: () => undefined },
      sessionCtrl: {
        async createBackgroundSession() {
          return { ok: true, session_id: "sess-hb-int" };
        },
        async sendPrompt() {
          return { ok: true };
        },
      } as any,
      tracker,
      receiptDir: dir,
      receiptPollIntervalMs: 60000, // manual poll
      heartbeatIntervalMs: 60000, // manual tick
      sendReply: async ({ taskId, status, body }: AgentBusDelegationReply) => {
        replies.push({ taskId, status, body });
      },
    });

    await service.handleMessages([
      {
        id: 1,
        task_id: "TASK-INT-1",
        status: "TASK",
        body: "Goal:\nDo something.",
      },
    ]);

    // Verify ACK
    assert.equal(replies.length, 1);
    assert.equal(replies[0].status, "ACK");

    // Step 2: Simulate heartbeat tick (would normally be automatic)
    const hbService = new DelegationHeartbeatService({
      tracker,
      intervalMs: 60000,
      outputChannel: { appendLine: () => undefined },
      sendRunning: async (reply) => {
        replies.push({
          taskId: reply.taskId,
          status: reply.status,
          body: reply.body,
        });
      },
    });

    await hbService.tick();

    // Verify RUNNING was sent
    assert.equal(replies.length, 2);
    assert.equal(replies[1].status, "RUNNING");
    assert.equal(replies[1].taskId, "TASK-INT-1");
    assert.match(replies[1].body, /Liveness heartbeat/);

    // Verify tracker state — heartbeat does NOT change status or lastActivityAt
    const task = tracker.get("TASK-INT-1")!;
    assert.equal(task.status, "ACKED", "heartbeat must not change status");
    assert.ok(task.lastHeartbeatAt);
    assert.equal(task.terminalSentAt, null); // RUNNING is NOT terminal

    // Step 3: Write EVIDENCE_PACK receipt and poll
    const receiptPath = DelegationReceiptWatcher.receiptPath(dir, "TASK-INT-1");
    fs.writeFileSync(
      receiptPath,
      JSON.stringify({
        task_id: "TASK-INT-1",
        status: "EVIDENCE_PACK",
        body: "## Evidence\n- diff: +10 -5",
      }),
      "utf-8",
    );

    const terminalSent: Array<{
      taskId: string;
      status: string;
      body: string;
    }> = [];
    const watcher = new DelegationReceiptWatcher({
      tracker,
      receiptDir: dir,
      pollIntervalMs: 60000,
      outputChannel: { appendLine: () => undefined },
      sendTerminal: async (taskId, status, body) => {
        terminalSent.push({ taskId, status, body });
      },
    });

    await watcher.poll();

    assert.equal(terminalSent.length, 1);
    assert.equal(terminalSent[0].status, "EVIDENCE_PACK");
    assert.ok(tracker.isTerminalSent("TASK-INT-1"));

    // Step 4: Heartbeat should NOT fire for terminal task
    await hbService.tick();
    assert.equal(replies.length, 2, "no additional heartbeat after terminal");

    hbService.dispose();
    watcher.dispose();
    service.dispose();
    fs.rmSync(dir, { recursive: true, force: true });
  });

  it("stale task without receipt still terminal-BLOCKEDs (heartbeat does not prevent timeout)", async () => {
    const dir = makeTempDir();
    const tracker = new DelegationTaskTracker();
    const terminalSent: Array<{
      taskId: string;
      status: string;
      body: string;
    }> = [];

    registerPendingTask(tracker, "TASK-STALE-HB", {
      ackedAt: "2026-01-01T00:00:00Z",
    });

    const watcher = new DelegationReceiptWatcher({
      tracker,
      receiptDir: dir,
      pollIntervalMs: 60000,
      staleAfterMs: 1000,
      outputChannel: { appendLine: () => undefined },
      sendTerminal: async (taskId, status, body) => {
        terminalSent.push({ taskId, status, body });
      },
    });

    // Poll with a time far enough in the future
    await watcher.poll(() => Date.parse("2026-01-01T00:00:05Z"));

    assert.equal(terminalSent.length, 1);
    assert.equal(terminalSent[0].taskId, "TASK-STALE-HB");
    assert.equal(terminalSent[0].status, "BLOCKED");
    assert.match(terminalSent[0].body, /timed out/i);
    assert.ok(tracker.isTerminalSent("TASK-STALE-HB"));

    watcher.dispose();
    fs.rmSync(dir, { recursive: true, force: true });
  });

  it("heartbeats fire before timeout but no-receipt task still becomes BLOCKED (regression)", async () => {
    const dir = makeTempDir();
    const tracker = new DelegationTaskTracker();
    const hbSent: Array<{ taskId: string; status: string }> = [];
    const terminalSent: Array<{
      taskId: string;
      status: string;
      body: string;
    }> = [];

    // Register task acked at T=0
    registerPendingTask(tracker, "TASK-HB-STALE", {
      ackedAt: "2026-01-01T00:00:00Z",
    });

    // Create heartbeat service
    const hb = new DelegationHeartbeatService({
      tracker,
      intervalMs: 60000,
      outputChannel: { appendLine: () => undefined },
      sendRunning: async (reply) => {
        hbSent.push({ taskId: reply.taskId, status: reply.status });
      },
    });

    // Fire multiple heartbeats (simulating heartbeats over time)
    await hb.tick();
    await hb.tick();
    await hb.tick();

    // Heartbeats should have fired
    assert.equal(hbSent.length, 3, "three heartbeats should have fired");
    assert.equal(hbSent[0].status, "RUNNING");

    // Verify heartbeat did NOT update lastActivityAt
    const task = tracker.get("TASK-HB-STALE")!;
    assert.equal(
      task.lastActivityAt,
      "2026-01-01T00:00:00Z",
      "heartbeat must not refresh lastActivityAt",
    );
    assert.ok(task.lastHeartbeatAt, "lastHeartbeatAt should be set");

    // Now stale timeout should still BLOCK despite heartbeats having fired
    const watcher = new DelegationReceiptWatcher({
      tracker,
      receiptDir: dir,
      pollIntervalMs: 60000,
      staleAfterMs: 1000, // 1 second stale window
      outputChannel: { appendLine: () => undefined },
      sendTerminal: async (taskId, status, body) => {
        terminalSent.push({ taskId, status, body });
      },
    });

    // Poll at T=5s — well past the 1s stale window
    await watcher.poll(() => Date.parse("2026-01-01T00:00:05Z"));

    // Task MUST be BLOCKED despite heartbeats
    assert.equal(terminalSent.length, 1, "stale timeout must fire");
    assert.equal(terminalSent[0].taskId, "TASK-HB-STALE");
    assert.equal(terminalSent[0].status, "BLOCKED");
    assert.match(terminalSent[0].body, /timed out/i);
    assert.ok(tracker.isTerminalSent("TASK-HB-STALE"));

    // And further heartbeats should not fire for the now-terminal task
    hbSent.length = 0;
    await hb.tick();
    assert.equal(hbSent.length, 0, "no heartbeat after terminal");

    hb.dispose();
    watcher.dispose();
    fs.rmSync(dir, { recursive: true, force: true });
  });

  it("positively lost session triggers cleanup/recovery earlier than stale timeout", async () => {
    const dir = makeTempDir();
    const tracker = new DelegationTaskTracker();
    const terminalSent: Array<{
      taskId: string;
      status: string;
      body: string;
    }> = [];

    // Register task acked at T=0
    registerPendingTask(tracker, "TASK-LOST-EARLY", {
      ackedAt: "2026-01-01T00:00:00Z",
    });

    const watcher = new DelegationReceiptWatcher({
      tracker,
      receiptDir: dir,
      pollIntervalMs: 60000,
      staleAfterMs: 60000, // 60s window (would normally not trigger at 5s)
      lostSessionReceiptGraceMs: 0,
      outputChannel: { appendLine: () => undefined },
      sendTerminal: async (taskId, status, body) => {
        terminalSent.push({ taskId, status, body });
      },
      sessionCtrl: {
        async hasPositiveEvidenceOfLoss() {
          return true; // Fake positive loss detection
        },
      } as any,
    });

    // Poll at T=5s — BEFORE the 60s stale window
    await watcher.poll(() => Date.parse("2026-01-01T00:00:05Z"));

    // Task MUST be BLOCKED early due to positive loss detection
    assert.equal(terminalSent.length, 1, "early cleanup must fire");
    assert.equal(terminalSent[0].taskId, "TASK-LOST-EARLY");
    assert.equal(terminalSent[0].status, "BLOCKED");
    assert.match(terminalSent[0].body, /positively detected as lost/i);
    assert.ok(tracker.isTerminalSent("TASK-LOST-EARLY"));

    watcher.dispose();
    fs.rmSync(dir, { recursive: true, force: true });
  });

  it("synthetic or ambiguous session falls back to normal stale timeout", async () => {
    const dir = makeTempDir();
    const tracker = new DelegationTaskTracker();
    const terminalSent: Array<{
      taskId: string;
      status: string;
      body: string;
    }> = [];

    // Register task acked at T=0
    registerPendingTask(tracker, "TASK-STALE-AMBIG", {
      ackedAt: "2026-01-01T00:00:00Z",
    });

    const watcher = new DelegationReceiptWatcher({
      tracker,
      receiptDir: dir,
      pollIntervalMs: 60000,
      staleAfterMs: 60000, // 60s window
      outputChannel: { appendLine: () => undefined },
      sendTerminal: async (taskId, status, body) => {
        terminalSent.push({ taskId, status, body });
      },
      sessionCtrl: {
        async hasPositiveEvidenceOfLoss() {
          return false; // Ambiguous or synthetic, no positive proof
        },
      } as any,
    });

    // Poll at T=5s — BEFORE the 60s stale window
    await watcher.poll(() => Date.parse("2026-01-01T00:00:05Z"));

    // Task must NOT be blown away yet, because we lack positive proof and it's not stale
    assert.equal(
      terminalSent.length,
      0,
      "must not cleanup early for ambiguous state",
    );

    // Poll at T=65s — AFTER the 60s stale window
    await watcher.poll(() => Date.parse("2026-01-01T00:01:05Z"));

    // Task MUST be BLOCKED now due to normal stale timeout
    assert.equal(terminalSent.length, 1, "fallback stale timeout must fire");
    assert.equal(terminalSent[0].taskId, "TASK-STALE-AMBIG");
    assert.equal(terminalSent[0].status, "BLOCKED");
    assert.match(terminalSent[0].body, /timed out/i);
    assert.ok(tracker.isTerminalSent("TASK-STALE-AMBIG"));

    watcher.dispose();
    fs.rmSync(dir, { recursive: true, force: true });
  });

  it("reopened tasks still allow heartbeat before the next terminal", async () => {
    const tracker = new DelegationTaskTracker();

    registerPendingTask(tracker, "TASK-REOPEN-HB");
    tracker.markTerminal("TASK-REOPEN-HB", "EVIDENCE_PACK", "round one");
    assert.ok(tracker.isTerminalSent("TASK-REOPEN-HB"));

    tracker.reopen("TASK-REOPEN-HB", "RUNNING");

    const reopened = tracker.get("TASK-REOPEN-HB")!;
    assert.equal(reopened.status, "RUNNING");
    assert.equal(reopened.terminalSentAt, null);
    assert.equal(reopened.lastHeartbeatAt, null);

    const hbReplies: Array<{ taskId: string; status: string }> = [];
    const hb = new DelegationHeartbeatService({
      tracker,
      intervalMs: 60000,
      outputChannel: { appendLine: () => undefined },
      sendRunning: async (reply) => {
        hbReplies.push({ taskId: reply.taskId, status: reply.status });
      },
    });

    await hb.tick();
    assert.equal(hbReplies.length, 1);
    assert.equal(hbReplies[0].taskId, "TASK-REOPEN-HB");
    assert.equal(hbReplies[0].status, "RUNNING");

    // Verify heartbeat timestamp was set
    const afterHb = tracker.get("TASK-REOPEN-HB")!;
    assert.ok(afterHb.lastHeartbeatAt);

    hb.dispose();
  });

  it("restart / restored pending tasks can resume heartbeat behavior", async () => {
    const dir = makeTempDir();
    const statePath = path.join(dir, "delegation-state.json");

    // Phase 1: register a task and persist to disk
    const trackerBefore = new DelegationTaskTracker(statePath);
    registerPendingTask(trackerBefore, "TASK-RESTART-HB");

    // Phase 2: create a new tracker (simulating restart)
    const trackerAfter = new DelegationTaskTracker(statePath);

    // Verify restored
    const restored = trackerAfter.get("TASK-RESTART-HB");
    assert.ok(restored);
    assert.equal(restored!.status, "ACKED");
    assert.equal(restored!.terminalSentAt, null);

    // Heartbeat should work for restored task
    const hbReplies: Array<{ taskId: string; status: string }> = [];
    const hb = new DelegationHeartbeatService({
      tracker: trackerAfter,
      intervalMs: 60000,
      outputChannel: { appendLine: () => undefined },
      sendRunning: async (reply) => {
        hbReplies.push({ taskId: reply.taskId, status: reply.status });
      },
    });

    await hb.tick();
    assert.equal(hbReplies.length, 1);
    assert.equal(hbReplies[0].taskId, "TASK-RESTART-HB");
    assert.equal(hbReplies[0].status, "RUNNING");

    // Verify the restored tracker now has heartbeat timestamp
    const afterHb = trackerAfter.get("TASK-RESTART-HB")!;
    assert.ok(afterHb.lastHeartbeatAt);

    hb.dispose();
    fs.rmSync(dir, { recursive: true, force: true });
  });
});

// ── Health output includes heartbeat-related detail ──────────────

describe("Heartbeat health output", () => {
  it("getDelegationStatus includes heartbeat_running and heartbeat_interval_ms", () => {
    const tracker = new DelegationTaskTracker();
    registerPendingTask(tracker, "TASK-HEALTH-HB");

    const service = new AgentBusDelegationService({
      enabled: true,
      command: "agent-bus",
      workspacePathsProvider: () => ["/tmp/repo"],
      outputChannel: { appendLine: () => undefined },
      sessionCtrl: {
        async createBackgroundSession() {
          return { ok: true, session_id: "sess" };
        },
      } as any,
      tracker,
      receiptDir: makeTempDir(),
      receiptPollIntervalMs: 60000,
      heartbeatIntervalMs: 15000,
      sendReply: async () => undefined,
    });

    const status = service.getDelegationStatus();
    assert.equal(status.heartbeat_running, true); // started on enabled=true
    assert.equal(status.heartbeat_interval_ms, 15000);
    assert.equal(status.receipt_watcher_running, true);
    assert.equal(status.enabled, true);

    // Tracker summary includes lastHeartbeatAt
    const taskSummary = status.tracker_summary.tasks.find(
      (t) => t.taskId === "TASK-HEALTH-HB",
    );
    assert.ok(taskSummary);
    assert.equal(taskSummary!.lastHeartbeatAt, null); // not yet heartbeated

    service.dispose();
  });

  it("tracker summary includes lastHeartbeatAt after heartbeat tick", async () => {
    const tracker = new DelegationTaskTracker();
    registerPendingTask(tracker, "TASK-HEALTH-HB2");

    const hb = new DelegationHeartbeatService({
      tracker,
      intervalMs: 60000,
      outputChannel: { appendLine: () => undefined },
      sendRunning: async () => undefined,
    });

    await hb.tick();

    const summary = tracker.getSummary();
    const taskSummary = summary.tasks.find(
      (t) => t.taskId === "TASK-HEALTH-HB2",
    );
    assert.ok(taskSummary);
    assert.ok(taskSummary!.lastHeartbeatAt);
    assert.notEqual(taskSummary!.lastHeartbeatAt, null);

    hb.dispose();
  });
});

// ── DelegationTaskTracker heartbeat additions ────────────────────

describe("DelegationTaskTracker heartbeat fields", () => {
  it("touchHeartbeat updates lastHeartbeatAt", () => {
    const tracker = new DelegationTaskTracker();
    registerPendingTask(tracker, "TASK-TH-1");

    const t1 = tracker.get("TASK-TH-1")!;
    assert.equal(t1.lastHeartbeatAt, null);

    tracker.touchHeartbeat("TASK-TH-1", "2026-01-01T01:00:00Z");

    const t2 = tracker.get("TASK-TH-1")!;
    assert.equal(t2.lastHeartbeatAt, "2026-01-01T01:00:00Z");
  });

  it("touchHeartbeat returns false for terminal tasks", () => {
    const tracker = new DelegationTaskTracker();
    registerPendingTask(tracker, "TASK-TH-2");
    tracker.markTerminal("TASK-TH-2", "EVIDENCE_PACK", "done");

    const result = tracker.touchHeartbeat("TASK-TH-2", "2026-01-01T01:00:00Z");
    assert.equal(result, false);
  });

  it("reopen clears lastHeartbeatAt", () => {
    const tracker = new DelegationTaskTracker();
    registerPendingTask(tracker, "TASK-TH-3");
    tracker.touchHeartbeat("TASK-TH-3", "2026-01-01T01:00:00Z");
    assert.ok(tracker.get("TASK-TH-3")!.lastHeartbeatAt);

    tracker.markTerminal("TASK-TH-3", "EVIDENCE_PACK", "done");
    tracker.reopen("TASK-TH-3", "RUNNING");

    const t = tracker.get("TASK-TH-3")!;
    assert.equal(t.lastHeartbeatAt, null);
  });

  it("lastHeartbeatAt is persisted and restored", () => {
    const dir = makeTempDir();
    const statePath = path.join(dir, "delegation-state.json");

    const tracker = new DelegationTaskTracker(statePath);
    registerPendingTask(tracker, "TASK-TH-PERSIST");
    tracker.touchHeartbeat("TASK-TH-PERSIST", "2026-01-01T02:00:00Z");

    const restored = new DelegationTaskTracker(statePath).get(
      "TASK-TH-PERSIST",
    )!;
    assert.equal(restored.lastHeartbeatAt, "2026-01-01T02:00:00Z");

    fs.rmSync(dir, { recursive: true, force: true });
  });
});
