import { describe, it } from "node:test";
import * as assert from "node:assert/strict";
import * as os from "node:os";
import * as path from "node:path";
import * as fs from "node:fs";

import { DelegationTaskTracker } from "../state/delegationTaskTracker";
import {
  AgentBusDelegationService,
  type AgentBusDelegationReply,
} from "../services/agentBusDelegationService";

function makeTempDir(): string {
  return fs.mkdtempSync(path.join(os.tmpdir(), "delegation-cont-test-"));
}

function registerPendingTask(tracker: DelegationTaskTracker, taskId: string): void {
  tracker.register({
    taskId,
    sessionId: `sess-${taskId}`,
    repoPath: "/tmp/repo",
    receiptPath: `/tmp/receipts/${taskId}.receipt.json`,
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
}

describe("AgentBusDelegationService Continuation ACKs", () => {
  it("emits REVIEW_ACK when sendPrompt succeeds for REVIEW", async () => {
    const tracker = new DelegationTaskTracker();
    registerPendingTask(tracker, "TASK-REV-ACK");

    const replies: AgentBusDelegationReply[] = [];
    const service = new AgentBusDelegationService({
      enabled: true,
      command: "agent-bus",
      workspacePathsProvider: () => ["/tmp/repo"],
      outputChannel: { appendLine: () => undefined },
      sessionCtrl: {
        async sendPrompt() {
          return { ok: true };
        },
      } as any,
      tracker,
      receiptDir: makeTempDir(),
      receiptPollIntervalMs: 60000,
      heartbeatIntervalMs: 60000,
      sendReply: async (reply: AgentBusDelegationReply) => {
        replies.push(reply);
      },
    });

    await service.handleMessages([
      { id: 1, task_id: "TASK-REV-ACK", status: "REVIEW", body: "Please fix this." },
    ]);

    assert.equal(replies.length, 1);
    assert.equal(replies[0].status, "REVIEW_ACK");
    assert.match(replies[0].body, /^reply_to_message_id=1\n/);
    service.dispose();
  });

  it("emits REVIEW_NACK when sendPrompt fails for REVIEW", async () => {
    const tracker = new DelegationTaskTracker();
    registerPendingTask(tracker, "TASK-REV-NACK");

    const replies: AgentBusDelegationReply[] = [];
    const service = new AgentBusDelegationService({
      enabled: true,
      command: "agent-bus",
      workspacePathsProvider: () => ["/tmp/repo"],
      outputChannel: { appendLine: () => undefined },
      sessionCtrl: {
        async sendPrompt() {
          throw new Error("send failed");
        },
      } as any,
      tracker,
      receiptDir: makeTempDir(),
      receiptPollIntervalMs: 60000,
      heartbeatIntervalMs: 60000,
      sendReply: async (reply: AgentBusDelegationReply) => {
        replies.push(reply);
      },
    });

    await service.handleMessages([
      { id: 2, task_id: "TASK-REV-NACK", status: "REVIEW", body: "Please fix this." },
    ]);

    assert.equal(replies.length, 1);
    assert.equal(replies[0].status, "REVIEW_NACK");
    assert.match(replies[0].body, /^reply_to_message_id=2\n/);
    service.dispose();
  });

  it("emits APPROVE_ACK when sendPrompt succeeds for APPROVED", async () => {
    const tracker = new DelegationTaskTracker();
    registerPendingTask(tracker, "TASK-APP-ACK");

    const replies: AgentBusDelegationReply[] = [];
    const service = new AgentBusDelegationService({
      enabled: true,
      command: "agent-bus",
      workspacePathsProvider: () => ["/tmp/repo"],
      outputChannel: { appendLine: () => undefined },
      sessionCtrl: {
        async sendPrompt() {
          return { ok: true };
        },
      } as any,
      tracker,
      receiptDir: makeTempDir(),
      receiptPollIntervalMs: 60000,
      heartbeatIntervalMs: 60000,
      sendReply: async (reply: AgentBusDelegationReply) => {
        replies.push(reply);
      },
    });

    await service.handleMessages([
      { id: 3, task_id: "TASK-APP-ACK", status: "APPROVED", body: "LGTM." },
    ]);

    assert.equal(replies.length, 1);
    assert.equal(replies[0].status, "APPROVE_ACK");
    assert.match(replies[0].body, /^reply_to_message_id=3\n/);
    service.dispose();
  });

  it("emits APPROVE_NACK when sendPrompt fails for APPROVED", async () => {
    const tracker = new DelegationTaskTracker();
    registerPendingTask(tracker, "TASK-APP-NACK");

    const replies: AgentBusDelegationReply[] = [];
    const service = new AgentBusDelegationService({
      enabled: true,
      command: "agent-bus",
      workspacePathsProvider: () => ["/tmp/repo"],
      outputChannel: { appendLine: () => undefined },
      sessionCtrl: {
        async sendPrompt() {
          throw new Error("Bridge closed");
        },
      } as any,
      tracker,
      receiptDir: makeTempDir(),
      receiptPollIntervalMs: 60000,
      heartbeatIntervalMs: 60000,
      sendReply: async (reply: AgentBusDelegationReply) => {
        replies.push(reply);
      },
    });

    await service.handleMessages([
      { id: 4, task_id: "TASK-APP-NACK", status: "APPROVED", body: "LGTM." },
    ]);

    assert.equal(replies.length, 1);
    assert.equal(replies[0].status, "APPROVE_NACK");
    assert.match(replies[0].body, /^reply_to_message_id=4\n/);
    service.dispose();
  });

  it("emits REVIEW_NACK when task is untracked", async () => {
    const tracker = new DelegationTaskTracker();
    const replies: Array<{ taskId: string; status: string }> = [];
    const service = new AgentBusDelegationService({
      enabled: true,
      command: "agent-bus",
      workspacePathsProvider: () => ["/tmp/repo"],
      outputChannel: { appendLine: () => undefined },
      sessionCtrl: {} as any,
      tracker,
      receiptDir: makeTempDir(),
      sendReply: async (reply: AgentBusDelegationReply) => {
        replies.push({ taskId: reply.taskId, status: reply.status });
      },
    });

    await service.handleMessages([{ id: 5, task_id: "UNTRACKED-TASK", status: "REVIEW", body: "Fix it." }]);

    assert.equal(replies.length, 1);
    assert.equal(replies[0].status, "REVIEW_NACK");
    service.dispose();
  });

  it("emits REVIEW_NACK when task has no session id", async () => {
    const tracker = new DelegationTaskTracker();
    // Register but with no session Id
    tracker.register({
      taskId: "NO-SESSION-TASK",
      sessionId: "",
      repoPath: "/tmp/repo",
      receiptPath: `/tmp/receipts/NO-SESSION-TASK.receipt.json`,
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

    const replies: Array<{ taskId: string; status: string }> = [];
    const service = new AgentBusDelegationService({
      enabled: true,
      command: "agent-bus",
      workspacePathsProvider: () => ["/tmp/repo"],
      outputChannel: { appendLine: () => undefined },
      sessionCtrl: {} as any,
      tracker,
      receiptDir: makeTempDir(),
      sendReply: async (reply: AgentBusDelegationReply) => {
        replies.push({ taskId: reply.taskId, status: reply.status });
      },
    });

    await service.handleMessages([{ id: 6, task_id: "NO-SESSION-TASK", status: "REVIEW", body: "Fix it." }]);

    assert.equal(replies.length, 1);
    assert.equal(replies[0].status, "REVIEW_NACK");
    service.dispose();
  });

  it("emits APPROVE_NACK when task is untracked", async () => {
    const tracker = new DelegationTaskTracker();
    const replies: Array<{ taskId: string; status: string }> = [];
    const service = new AgentBusDelegationService({
      enabled: true,
      command: "agent-bus",
      workspacePathsProvider: () => ["/tmp/repo"],
      outputChannel: { appendLine: () => undefined },
      sessionCtrl: {} as any,
      tracker,
      receiptDir: makeTempDir(),
      sendReply: async (reply: AgentBusDelegationReply) => {
        replies.push({ taskId: reply.taskId, status: reply.status });
      },
    });

    await service.handleMessages([{ id: 7, task_id: "UNTRACKED-TASK", status: "APPROVED", body: "LGTM." }]);

    assert.equal(replies.length, 1);
    assert.equal(replies[0].status, "APPROVE_NACK");
    service.dispose();
  });

  it("emits APPROVE_NACK when task has no session id", async () => {
    const tracker = new DelegationTaskTracker();
    // Register but with no session Id
    tracker.register({
      taskId: "NO-SESSION-TASK",
      sessionId: "",
      repoPath: "/tmp/repo",
      receiptPath: `/tmp/receipts/NO-SESSION-TASK.receipt.json`,
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

    const replies: Array<{ taskId: string; status: string }> = [];
    const service = new AgentBusDelegationService({
      enabled: true,
      command: "agent-bus",
      workspacePathsProvider: () => ["/tmp/repo"],
      outputChannel: { appendLine: () => undefined },
      sessionCtrl: {} as any,
      tracker,
      receiptDir: makeTempDir(),
      sendReply: async (reply: AgentBusDelegationReply) => {
        replies.push({ taskId: reply.taskId, status: reply.status });
      },
    });

    await service.handleMessages([{ id: 8, task_id: "NO-SESSION-TASK", status: "APPROVED", body: "LGTM." }]);

    assert.equal(replies.length, 1);
    assert.equal(replies[0].status, "APPROVE_NACK");
    service.dispose();
  });

  it("emits REVIEW_NACK when session is synthetic (ag-cmd-*)", async () => {
    const tracker = new DelegationTaskTracker();
    tracker.register({
      taskId: "SYNTH-TASK-REV",
      sessionId: "ag-cmd-1234",
      repoPath: "/tmp/repo",
      receiptPath: `/tmp/receipts/SYNTH-TASK-REV.receipt.json`,
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

    const replies: Array<{ taskId: string; status: string; body: string }> = [];
    const service = new AgentBusDelegationService({
      enabled: true,
      command: "agent-bus",
      workspacePathsProvider: () => ["/tmp/repo"],
      outputChannel: { appendLine: () => undefined },
      sessionCtrl: {
        async sendPrompt() {
          throw new Error("Should not be called");
        },
      } as any,
      tracker,
      receiptDir: makeTempDir(),
      sendReply: async (reply: AgentBusDelegationReply) => {
        replies.push(reply);
      },
    });

    await service.handleMessages([{ id: 9, task_id: "SYNTH-TASK-REV", status: "REVIEW", body: "Fix it." }]);

    assert.equal(replies.length, 1);
    assert.equal(replies[0].status, "REVIEW_NACK");
    assert.match(replies[0].body, /Please use --fresh-resume instead/);
    service.dispose();
  });

  it("emits APPROVE_NACK when session is synthetic (ag-cmd-*)", async () => {
    const tracker = new DelegationTaskTracker();
    tracker.register({
      taskId: "SYNTH-TASK-APP",
      sessionId: "ag-cmd-1234",
      repoPath: "/tmp/repo",
      receiptPath: `/tmp/receipts/SYNTH-TASK-APP.receipt.json`,
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

    const replies: Array<{ taskId: string; status: string; body: string }> = [];
    const service = new AgentBusDelegationService({
      enabled: true,
      command: "agent-bus",
      workspacePathsProvider: () => ["/tmp/repo"],
      outputChannel: { appendLine: () => undefined },
      sessionCtrl: {
        async sendPrompt() {
          throw new Error("Should not be called");
        },
      } as any,
      tracker,
      receiptDir: makeTempDir(),
      sendReply: async (reply: AgentBusDelegationReply) => {
        replies.push(reply);
      },
    });

    await service.handleMessages([{ id: 10, task_id: "SYNTH-TASK-APP", status: "APPROVED", body: "LGTM." }]);

    assert.equal(replies.length, 1);
    assert.equal(replies[0].status, "APPROVE_NACK");
    assert.match(replies[0].body, /Please use --fresh-resume instead/);
    service.dispose();
  });
  it("emits BLOCKED (not ACK) when TASK creation returns a phantom session (ag-cmd-*)", async () => {
    const tracker = new DelegationTaskTracker();
    const replies: Array<{ taskId: string; status: string; body: string }> = [];

    const service = new AgentBusDelegationService({
      enabled: true,
      command: "agent-bus",
      workspacePathsProvider: () => ["/tmp/repo"],
      outputChannel: { appendLine: () => undefined },
      sessionCtrl: {
        async createBackgroundSession() {
          return { ok: true, session_id: "ag-cmd-123456" };
        },
        async terminateSession() {
          return true;
        }
      } as any,
      tracker,
      receiptDir: makeTempDir(),
      sendReply: async (reply: AgentBusDelegationReply) => {
        replies.push(reply);
      },
    });

    await service.handleMessages([{ id: 11, task_id: "PHANTOM-TASK-START", status: "TASK", body: "Do it." }]);

    assert.equal(replies.length, 1);
    assert.equal(replies[0].status, "BLOCKED");
    assert.match(replies[0].body, /phantom ID/);
    
    // Should NOT be tracked
    const tracked = tracker.get("PHANTOM-TASK-START");
    assert.equal(tracked, undefined);

    service.dispose();
  });
});
