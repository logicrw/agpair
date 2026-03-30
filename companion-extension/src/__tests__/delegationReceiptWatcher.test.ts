import { afterEach, describe, it } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";

import { DelegationReceiptWatcher } from "../services/delegationReceiptWatcher";
import { DelegationTaskTracker } from "../state/delegationTaskTracker";

const tempDirs: string[] = [];

function makeTempDir(): string {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "delegation-receipt-test-"));
  tempDirs.push(dir);
  return dir;
}

afterEach(() => {
  for (const dir of tempDirs.splice(0)) {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

function parseTransportBody(wireBody: string): any {
  return JSON.parse(wireBody.replace(/^X-Delivery-Id:\s*\S+\n/, ""));
}

describe("DelegationReceiptWatcher", () => {
  it("detects a receipt file and sends terminal status", async () => {
    const dir = makeTempDir();
    const tracker = new DelegationTaskTracker();
    const sent: Array<{ taskId: string; status: string; body: string }> = [];
    const output: string[] = [];

    const watcher = new DelegationReceiptWatcher({
      tracker,
      receiptDir: dir,
      pollIntervalMs: 60000, // won't auto-fire; we call poll() manually
      outputChannel: { appendLine: (msg: string) => output.push(msg) },
      sendTerminal: async (taskId, status, body) => {
        sent.push({ taskId, status, body });
      },
    });

    const receiptPath = DelegationReceiptWatcher.receiptPath(dir, "TASK-R1");

    // Register a pending task
    tracker.register({
      taskId: "TASK-R1",
      sessionId: "sess-001",
      repoPath: "/tmp/repo",
      receiptPath,
      status: "ACKED",
      ackedAt: new Date().toISOString(),
      terminalSentAt: null,
      terminalStatus: null,
      terminalBody: null,
      pendingTerminalStatus: null,
      pendingTerminalBody: null,
      pendingTerminalPreparedAt: null,
    });

    // Write a valid receipt
    fs.writeFileSync(
      receiptPath,
      JSON.stringify({
        task_id: "TASK-R1",
        status: "EVIDENCE_PACK",
        body: "## Evidence\n- diff: +10 -5\n- tests: all passed",
      }),
      "utf-8",
    );

    // Poll manually
    await watcher.poll();

    assert.equal(sent.length, 1);
    assert.equal(sent[0].taskId, "TASK-R1");
    assert.equal(sent[0].status, "EVIDENCE_PACK");
    assert.match(sent[0].body, /diff: \+10 -5/);

    // Tracker should be marked terminal
    assert.ok(tracker.isTerminalSent("TASK-R1"));
    const task = tracker.get("TASK-R1");
    assert.ok(task?.terminalSentAt);
    assert.equal(task?.terminalStatus, "EVIDENCE_PACK");

    // Receipt file should be cleaned up
    assert.equal(fs.existsSync(receiptPath), false);

    watcher.dispose();
  });

  it("prevents duplicate terminal sends", async () => {
    const dir = makeTempDir();
    const tracker = new DelegationTaskTracker();
    const sent: Array<{ taskId: string; status: string }> = [];

    const watcher = new DelegationReceiptWatcher({
      tracker,
      receiptDir: dir,
      pollIntervalMs: 60000,
      outputChannel: { appendLine: () => undefined },
      sendTerminal: async (taskId, status) => {
        sent.push({ taskId, status });
      },
    });

    const receiptPath = DelegationReceiptWatcher.receiptPath(dir, "TASK-R2");
    tracker.register({
      taskId: "TASK-R2",
      sessionId: "sess-002",
      repoPath: "/tmp/repo",
      receiptPath,
      status: "ACKED",
      ackedAt: new Date().toISOString(),
      terminalSentAt: null,
      terminalStatus: null,
      terminalBody: null,
      pendingTerminalStatus: null,
      pendingTerminalBody: null,
      pendingTerminalPreparedAt: null,
    });

    fs.writeFileSync(
      receiptPath,
      JSON.stringify({ task_id: "TASK-R2", status: "BLOCKED", body: "stuck" }),
      "utf-8",
    );

    await watcher.poll();
    assert.equal(sent.length, 1);

    // Write another receipt (shouldn't be picked up since terminal already sent)
    const receiptPath2 = DelegationReceiptWatcher.receiptPath(dir, "TASK-R2");
    fs.writeFileSync(
      receiptPath2,
      JSON.stringify({ task_id: "TASK-R2", status: "EVIDENCE_PACK", body: "done" }),
      "utf-8",
    );

    await watcher.poll();
    assert.equal(sent.length, 1, "should NOT send a second terminal");

    watcher.dispose();
  });

  it("retries terminal send on a later poll when the first send fails", async () => {
    const dir = makeTempDir();
    const tracker = new DelegationTaskTracker();
    const attempts: string[] = [];
    const output: string[] = [];

    const watcher = new DelegationReceiptWatcher({
      tracker,
      receiptDir: dir,
      pollIntervalMs: 60000,
      outputChannel: { appendLine: (msg: string) => output.push(msg) },
      sendTerminal: async () => {
        attempts.push("send");
        if (attempts.length === 1) {
          throw new Error("temporary send failure");
        }
      },
    });

    const receiptPath = DelegationReceiptWatcher.receiptPath(dir, "TASK-RETRY-1");
    tracker.register({
      taskId: "TASK-RETRY-1",
      sessionId: "sess-retry-1",
      repoPath: "/tmp/repo",
      receiptPath,
      status: "ACKED",
      ackedAt: new Date().toISOString(),
      terminalSentAt: null,
      terminalStatus: null,
      terminalBody: null,
      pendingTerminalStatus: null,
      pendingTerminalBody: null,
      pendingTerminalPreparedAt: null,
    });

    fs.writeFileSync(
      receiptPath,
      JSON.stringify({ task_id: "TASK-RETRY-1", status: "EVIDENCE_PACK", body: "done" }),
      "utf-8",
    );

    await watcher.poll();
    assert.equal(attempts.length, 1);
    assert.equal(tracker.isTerminalSent("TASK-RETRY-1"), false, "must stay pending after failed send");
    assert.equal(fs.existsSync(receiptPath), true, "receipt must remain for retry");

    await watcher.poll();
    assert.equal(attempts.length, 2);
    assert.equal(tracker.isTerminalSent("TASK-RETRY-1"), true, "must mark terminal only after successful send");
    assert.equal(fs.existsSync(receiptPath), false, "receipt should be cleaned after successful retry");
    assert.ok(output.some((line) => /temporary send failure/.test(line)));

    watcher.dispose();
  });

  it("ignores receipt with wrong task_id", async () => {
    const dir = makeTempDir();
    const tracker = new DelegationTaskTracker();
    const sent: Array<{ taskId: string }> = [];

    const watcher = new DelegationReceiptWatcher({
      tracker,
      receiptDir: dir,
      pollIntervalMs: 60000,
      outputChannel: { appendLine: () => undefined },
      sendTerminal: async (taskId) => {
        sent.push({ taskId });
      },
    });

    const receiptPath = DelegationReceiptWatcher.receiptPath(dir, "TASK-R3");
    tracker.register({
      taskId: "TASK-R3",
      sessionId: "sess-003",
      repoPath: "/tmp/repo",
      receiptPath,
      status: "ACKED",
      ackedAt: new Date().toISOString(),
      terminalSentAt: null,
      terminalStatus: null,
      terminalBody: null,
      pendingTerminalStatus: null,
      pendingTerminalBody: null,
      pendingTerminalPreparedAt: null,
    });

    // Wrong task_id in receipt
    fs.writeFileSync(
      receiptPath,
      JSON.stringify({ task_id: "WRONG-ID", status: "EVIDENCE_PACK", body: "done" }),
      "utf-8",
    );

    await watcher.poll();
    assert.equal(sent.length, 0, "should not send for mismatched task_id");

    watcher.dispose();
  });

  it("ignores receipt with invalid status", async () => {
    const dir = makeTempDir();
    const tracker = new DelegationTaskTracker();
    const sent: Array<{ taskId: string }> = [];

    const watcher = new DelegationReceiptWatcher({
      tracker,
      receiptDir: dir,
      pollIntervalMs: 60000,
      outputChannel: { appendLine: () => undefined },
      sendTerminal: async (taskId) => {
        sent.push({ taskId });
      },
    });

    const receiptPath = DelegationReceiptWatcher.receiptPath(dir, "TASK-R4");
    tracker.register({
      taskId: "TASK-R4",
      sessionId: "sess-004",
      repoPath: "/tmp/repo",
      receiptPath,
      status: "ACKED",
      ackedAt: new Date().toISOString(),
      terminalSentAt: null,
      terminalStatus: null,
      terminalBody: null,
      pendingTerminalStatus: null,
      pendingTerminalBody: null,
      pendingTerminalPreparedAt: null,
    });

    // Invalid status
    fs.writeFileSync(
      receiptPath,
      JSON.stringify({ task_id: "TASK-R4", status: "RUNNING", body: "" }),
      "utf-8",
    );

    await watcher.poll();
    assert.equal(sent.length, 0, "should not send for non-terminal status");

    watcher.dispose();
  });

  it("handles BLOCKED receipt correctly", async () => {
    const dir = makeTempDir();
    const tracker = new DelegationTaskTracker();
    const sent: Array<{ taskId: string; status: string; body: string }> = [];

    const watcher = new DelegationReceiptWatcher({
      tracker,
      receiptDir: dir,
      pollIntervalMs: 60000,
      outputChannel: { appendLine: () => undefined },
      sendTerminal: async (taskId, status, body) => {
        sent.push({ taskId, status, body });
      },
    });

    const receiptPath = DelegationReceiptWatcher.receiptPath(dir, "TASK-R5");
    tracker.register({
      taskId: "TASK-R5",
      sessionId: "sess-005",
      repoPath: "/tmp/repo",
      receiptPath,
      status: "ACKED",
      ackedAt: new Date().toISOString(),
      terminalSentAt: null,
      terminalStatus: null,
      terminalBody: null,
      pendingTerminalStatus: null,
      pendingTerminalBody: null,
      pendingTerminalPreparedAt: null,
    });

    fs.writeFileSync(
      receiptPath,
      JSON.stringify({ task_id: "TASK-R5", status: "BLOCKED", body: "Cannot access API" }),
      "utf-8",
    );

    await watcher.poll();
    assert.equal(sent.length, 1);
    assert.equal(sent[0].status, "BLOCKED");
    assert.match(sent[0].body, /Cannot access API/);

    watcher.dispose();
  });

  it("times out a stale delegated task and sends BLOCKED", async () => {
    const dir = makeTempDir();
    const tracker = new DelegationTaskTracker();
    const sent: Array<{ taskId: string; status: string; body: string }> = [];

    const watcher = new DelegationReceiptWatcher({
      tracker,
      receiptDir: dir,
      pollIntervalMs: 60000,
      staleAfterMs: 1000,
      outputChannel: { appendLine: () => undefined },
      sendTerminal: async (taskId, status, body) => {
        sent.push({ taskId, status, body });
      },
    });

    tracker.register({
      taskId: "TASK-STALE-1",
      sessionId: "sess-stale-1",
      repoPath: "/tmp/repo",
      receiptPath: DelegationReceiptWatcher.receiptPath(dir, "TASK-STALE-1"),
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

    await watcher.poll(() => Date.parse("2026-01-01T00:00:05Z"));

    assert.equal(sent.length, 1);
    assert.equal(sent[0].taskId, "TASK-STALE-1");
    assert.equal(sent[0].status, "BLOCKED");
    assert.match(sent[0].body, /timed out/i);
    assert.equal(tracker.isTerminalSent("TASK-STALE-1"), true);

    watcher.dispose();
  });

  it("recovers a pending delegated task after restart and still sends terminal receipt", async () => {
    const dir = makeTempDir();
    const statePath = path.join(dir, "delegation-state.json");
    const receiptPath = DelegationReceiptWatcher.receiptPath(dir, "TASK-RECOVER-1");

    const trackerBeforeRestart = new DelegationTaskTracker(statePath);
    trackerBeforeRestart.register({
      taskId: "TASK-RECOVER-1",
      sessionId: "sess-recover-1",
      repoPath: "/tmp/repo",
      receiptPath,
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

    fs.writeFileSync(
      receiptPath,
      JSON.stringify({ task_id: "TASK-RECOVER-1", status: "EVIDENCE_PACK", body: "recovered" }),
      "utf-8",
    );

    const trackerAfterRestart = new DelegationTaskTracker(statePath);
    const sent: Array<{ taskId: string; status: string; body: string }> = [];
    const watcher = new DelegationReceiptWatcher({
      tracker: trackerAfterRestart,
      receiptDir: dir,
      pollIntervalMs: 60000,
      outputChannel: { appendLine: () => undefined },
      sendTerminal: async (taskId, status, body) => {
        sent.push({ taskId, status, body });
      },
    });

    await watcher.poll();

    assert.equal(sent.length, 1);
    assert.equal(sent[0].taskId, "TASK-RECOVER-1");
    assert.equal(sent[0].status, "EVIDENCE_PACK");
    assert.equal(trackerAfterRestart.isTerminalSent("TASK-RECOVER-1"), true);

    watcher.dispose();
  });

  it("does nothing when no pending tasks", async () => {
    const dir = makeTempDir();
    const tracker = new DelegationTaskTracker();
    const sent: Array<{ taskId: string }> = [];

    const watcher = new DelegationReceiptWatcher({
      tracker,
      receiptDir: dir,
      pollIntervalMs: 60000,
      outputChannel: { appendLine: () => undefined },
      sendTerminal: async (taskId) => {
        sent.push({ taskId });
      },
    });

    await watcher.poll();
    assert.equal(sent.length, 0);

    watcher.dispose();
  });

  it("start/stop lifecycle works", () => {
    const dir = makeTempDir();
    const tracker = new DelegationTaskTracker();

    const watcher = new DelegationReceiptWatcher({
      tracker,
      receiptDir: dir,
      pollIntervalMs: 60000,
      outputChannel: { appendLine: () => undefined },
      sendTerminal: async () => {},
    });

    assert.equal(watcher.isRunning, false);
    watcher.start();
    assert.equal(watcher.isRunning, true);
    watcher.stop();
    assert.equal(watcher.isRunning, false);

    watcher.dispose();
  });

  it("detects a COMMITTED receipt and sends terminal status", async () => {
    const dir = makeTempDir();
    const tracker = new DelegationTaskTracker();
    const sent: Array<{ taskId: string; status: string; body: string }> = [];

    const watcher = new DelegationReceiptWatcher({
      tracker,
      receiptDir: dir,
      pollIntervalMs: 60000,
      outputChannel: { appendLine: () => undefined },
      sendTerminal: async (taskId, status, body) => {
        sent.push({ taskId, status, body });
      },
    });

    const receiptPath = DelegationReceiptWatcher.receiptPath(dir, "TASK-COMMIT-1");
    tracker.register({
      taskId: "TASK-COMMIT-1",
      sessionId: "sess-commit-1",
      repoPath: "/tmp/repo",
      receiptPath,
      status: "ACKED",
      ackedAt: new Date().toISOString(),
      terminalSentAt: null,
      terminalStatus: null,
      terminalBody: null,
      pendingTerminalStatus: null,
      pendingTerminalBody: null,
      pendingTerminalPreparedAt: null,
    });

    fs.writeFileSync(
      receiptPath,
      JSON.stringify({
        task_id: "TASK-COMMIT-1",
        status: "COMMITTED",
        body: "## Commit\n- hash: abc123\n- branch: main",
      }),
      "utf-8",
    );

    await watcher.poll();

    assert.equal(sent.length, 1);
    assert.equal(sent[0].taskId, "TASK-COMMIT-1");
    assert.equal(sent[0].status, "COMMITTED");
    assert.match(sent[0].body, /hash: abc123/);

    assert.ok(tracker.isTerminalSent("TASK-COMMIT-1"));
    const task = tracker.get("TASK-COMMIT-1");
    assert.ok(task?.terminalSentAt);
    assert.equal(task?.terminalStatus, "COMMITTED");
    assert.equal(fs.existsSync(receiptPath), false);

    watcher.dispose();
  });

  it("COMMITTED receipt is still protected by terminal dedup", async () => {
    const dir = makeTempDir();
    const tracker = new DelegationTaskTracker();
    const sent: Array<{ taskId: string; status: string }> = [];

    const watcher = new DelegationReceiptWatcher({
      tracker,
      receiptDir: dir,
      pollIntervalMs: 60000,
      outputChannel: { appendLine: () => undefined },
      sendTerminal: async (taskId, status) => {
        sent.push({ taskId, status });
      },
    });

    const receiptPath = DelegationReceiptWatcher.receiptPath(dir, "TASK-COMMIT-DUP");
    tracker.register({
      taskId: "TASK-COMMIT-DUP",
      sessionId: "sess-commit-dup",
      repoPath: "/tmp/repo",
      receiptPath,
      status: "ACKED",
      ackedAt: new Date().toISOString(),
      terminalSentAt: null,
      terminalStatus: null,
      terminalBody: null,
      pendingTerminalStatus: null,
      pendingTerminalBody: null,
      pendingTerminalPreparedAt: null,
    });

    fs.writeFileSync(
      receiptPath,
      JSON.stringify({ task_id: "TASK-COMMIT-DUP", status: "COMMITTED", body: "done" }),
      "utf-8",
    );

    await watcher.poll();
    assert.equal(sent.length, 1);

    // Write another receipt — shouldn't be picked up
    fs.writeFileSync(
      receiptPath,
      JSON.stringify({ task_id: "TASK-COMMIT-DUP", status: "COMMITTED", body: "done again" }),
      "utf-8",
    );

    await watcher.poll();
    assert.equal(sent.length, 1, "should NOT send duplicate COMMITTED");

    watcher.dispose();
  });

  it("detects a v1 EVIDENCE_PACK receipt", async () => {
    const dir = makeTempDir();
    const tracker = new DelegationTaskTracker();
    const sent: Array<{ taskId: string; status: string; body: string }> = [];

    const watcher = new DelegationReceiptWatcher({
      tracker,
      receiptDir: dir,
      pollIntervalMs: 60000,
      outputChannel: { appendLine: () => undefined },
      sendTerminal: async (taskId, status, body) => {
        sent.push({ taskId, status, body });
      },
    });

    const receiptPath = DelegationReceiptWatcher.receiptPath(dir, "TASK-V1-EP");
    tracker.register({
      taskId: "TASK-V1-EP",
      sessionId: "sess-v1-ep",
      repoPath: "/tmp/repo",
      receiptPath,
      status: "ACKED",
      ackedAt: new Date().toISOString(),
      terminalSentAt: null,
      terminalStatus: null,
      terminalBody: null,
      pendingTerminalStatus: null,
      pendingTerminalBody: null,
      pendingTerminalPreparedAt: null,
    });

    fs.writeFileSync(
      receiptPath,
      JSON.stringify({
        schema_version: "1",
        task_id: "TASK-V1-EP",
        attempt_no: 1,
        review_round: 0,
        status: "EVIDENCE_PACK",
        summary: "Done v1",
        payload: { diff_stat: "+10 -5", validation: "passed" }
      }),
      "utf-8",
    );

    await watcher.poll();

    assert.equal(sent.length, 1);
    assert.equal(sent[0].taskId, "TASK-V1-EP");
    assert.equal(sent[0].status, "EVIDENCE_PACK");

    const parsedBody = parseTransportBody(sent[0].body);
    assert.equal(parsedBody.schema_version, "1");
    assert.equal(parsedBody.task_id, "TASK-V1-EP");
    assert.equal(parsedBody.payload.diff_stat, "+10 -5");

    watcher.dispose();
  });

  it("detects a v1 BLOCKED receipt", async () => {
    const dir = makeTempDir();
    const tracker = new DelegationTaskTracker();
    const sent: Array<{ taskId: string; status: string; body: string }> = [];

    const watcher = new DelegationReceiptWatcher({
      tracker,
      receiptDir: dir,
      pollIntervalMs: 60000,
      outputChannel: { appendLine: () => undefined },
      sendTerminal: async (taskId, status, body) => {
        sent.push({ taskId, status, body });
      },
    });

    const receiptPath = DelegationReceiptWatcher.receiptPath(dir, "TASK-V1-BLK");
    tracker.register({
      taskId: "TASK-V1-BLK",
      sessionId: "sess-v1-blk",
      repoPath: "/tmp/repo",
      receiptPath,
      status: "ACKED",
      ackedAt: new Date().toISOString(),
      terminalSentAt: null,
      terminalStatus: null,
      terminalBody: null,
      pendingTerminalStatus: null,
      pendingTerminalBody: null,
      pendingTerminalPreparedAt: null,
    });

    fs.writeFileSync(
      receiptPath,
      JSON.stringify({
        schema_version: "1",
        task_id: "TASK-V1-BLK",
        attempt_no: 1,
        review_round: 0,
        status: "BLOCKED",
        summary: "Blocked v1",
        payload: { message: "Cannot access network" }
      }),
      "utf-8",
    );

    await watcher.poll();

    assert.equal(sent.length, 1);
    assert.equal(sent[0].taskId, "TASK-V1-BLK");
    assert.equal(sent[0].status, "BLOCKED");

    const parsedBody = parseTransportBody(sent[0].body);
    assert.equal(parsedBody.payload.message, "Cannot access network");

    watcher.dispose();
  });

  it("detects a v1 COMMITTED receipt", async () => {
    const dir = makeTempDir();
    const tracker = new DelegationTaskTracker();
    const sent: Array<{ taskId: string; status: string; body: string }> = [];

    const watcher = new DelegationReceiptWatcher({
      tracker,
      receiptDir: dir,
      pollIntervalMs: 60000,
      outputChannel: { appendLine: () => undefined },
      sendTerminal: async (taskId, status, body) => {
        sent.push({ taskId, status, body });
      },
    });

    const receiptPath = DelegationReceiptWatcher.receiptPath(dir, "TASK-V1-COM");
    tracker.register({
      taskId: "TASK-V1-COM",
      sessionId: "sess-v1-com",
      repoPath: "/tmp/repo",
      receiptPath,
      status: "ACKED",
      ackedAt: new Date().toISOString(),
      terminalSentAt: null,
      terminalStatus: null,
      terminalBody: null,
      pendingTerminalStatus: null,
      pendingTerminalBody: null,
      pendingTerminalPreparedAt: null,
    });

    fs.writeFileSync(
      receiptPath,
      JSON.stringify({
        schema_version: "1",
        task_id: "TASK-V1-COM",
        attempt_no: 1,
        review_round: 0,
        status: "COMMITTED",
        summary: "Committed v1",
        payload: { commit_sha: "abc1234" }
      }),
      "utf-8",
    );

    await watcher.poll();

    assert.equal(sent.length, 1);
    assert.equal(sent[0].taskId, "TASK-V1-COM");
    assert.equal(sent[0].status, "COMMITTED");

    const parsedBody = parseTransportBody(sent[0].body);
    assert.equal(parsedBody.payload.commit_sha, "abc1234");

    watcher.dispose();
  });

  it("rejects a malformed v1 receipt", async () => {
    const dir = makeTempDir();
    const tracker = new DelegationTaskTracker();
    const sent: Array<{ taskId: string; status: string; body: string }> = [];

    const watcher = new DelegationReceiptWatcher({
      tracker,
      receiptDir: dir,
      pollIntervalMs: 60000,
      outputChannel: { appendLine: () => undefined },
      sendTerminal: async (taskId, status, body) => {
        sent.push({ taskId, status, body });
      },
    });

    const receiptPath = DelegationReceiptWatcher.receiptPath(dir, "TASK-V1-MAL");
    tracker.register({
      taskId: "TASK-V1-MAL",
      sessionId: "sess-v1-mal",
      repoPath: "/tmp/repo",
      receiptPath,
      status: "ACKED",
      ackedAt: new Date().toISOString(),
      terminalSentAt: null,
      terminalStatus: null,
      terminalBody: null,
      pendingTerminalStatus: null,
      pendingTerminalBody: null,
      pendingTerminalPreparedAt: null,
    });

    fs.writeFileSync(
      receiptPath,
      JSON.stringify({
        schema_version: "1",
        task_id: "TASK-V1-MAL",
        // missing attempt_no, review_round, summary, payload 
        status: "COMMITTED",
      }),
      "utf-8",
    );

    await watcher.poll();

    assert.equal(sent.length, 0, "Should reject malformed v1 receipt");

    watcher.dispose();
  });
});

describe("DelegationTaskTracker", () => {
  it("register and get", () => {
    const tracker = new DelegationTaskTracker();
    const ok = tracker.register({
      taskId: "T1",
      sessionId: "s1",
      repoPath: "/tmp",
      receiptPath: "/tmp/r.json",
      status: "ACKED",
      ackedAt: "2026-01-01T00:00:00Z",
      terminalSentAt: null,
      terminalStatus: null,
      terminalBody: null,
      pendingTerminalStatus: null,
      pendingTerminalBody: null,
      pendingTerminalPreparedAt: null,
    });
    assert.ok(ok);
    assert.equal(tracker.count(), 1);
    assert.equal(tracker.pendingCount(), 1);

    const task = tracker.get("T1");
    assert.ok(task);
    assert.equal(task!.sessionId, "s1");
  });

  it("markTerminal prevents double send", () => {
    const tracker = new DelegationTaskTracker();
    tracker.register({
      taskId: "T2",
      sessionId: "s2",
      repoPath: "/tmp",
      receiptPath: "/tmp/r2.json",
      status: "ACKED",
      ackedAt: "2026-01-01T00:00:00Z",
      terminalSentAt: null,
      terminalStatus: null,
      terminalBody: null,
      pendingTerminalStatus: null,
      pendingTerminalBody: null,
      pendingTerminalPreparedAt: null,
    });

    const first = tracker.markTerminal("T2", "EVIDENCE_PACK", "done");
    assert.ok(first);
    assert.equal(tracker.pendingCount(), 0);

    const second = tracker.markTerminal("T2", "BLOCKED", "other");
    assert.equal(second, false, "should reject duplicate markTerminal");
  });

  it("getSummary returns correct counts", () => {
    const tracker = new DelegationTaskTracker();
    tracker.register({
      taskId: "T3",
      sessionId: "s3",
      repoPath: "/tmp",
      receiptPath: "/tmp/r3.json",
      status: "ACKED",
      ackedAt: "2026-01-01T00:00:00Z",
      terminalSentAt: null,
      terminalStatus: null,
      terminalBody: null,
      pendingTerminalStatus: null,
      pendingTerminalBody: null,
      pendingTerminalPreparedAt: null,
    });
    tracker.register({
      taskId: "T4",
      sessionId: "s4",
      repoPath: "/tmp",
      receiptPath: "/tmp/r4.json",
      status: "ACKED",
      ackedAt: "2026-01-01T00:00:00Z",
      terminalSentAt: null,
      terminalStatus: null,
      terminalBody: null,
      pendingTerminalStatus: null,
      pendingTerminalBody: null,
      pendingTerminalPreparedAt: null,
    });
    tracker.markTerminal("T3", "EVIDENCE_PACK", "done");

    const summary = tracker.getSummary();
    assert.equal(summary.total, 2);
    assert.equal(summary.pending, 1);
    assert.equal(summary.completed, 1);
    assert.equal(summary.tasks.length, 2);
  });

  it("getPendingForRepo returns the active task for that repo", () => {
    const tracker = new DelegationTaskTracker();
    tracker.register({
      taskId: "T6",
      sessionId: "s6",
      repoPath: "/tmp/repo-a",
      receiptPath: "/tmp/r6.json",
      status: "ACKED",
      ackedAt: "2026-01-01T00:00:00Z",
      terminalSentAt: null,
      terminalStatus: null,
      terminalBody: null,
      pendingTerminalStatus: null,
      pendingTerminalBody: null,
      pendingTerminalPreparedAt: null,
    });
    tracker.register({
      taskId: "T7",
      sessionId: "s7",
      repoPath: "/tmp/repo-b",
      receiptPath: "/tmp/r7.json",
      status: "ACKED",
      ackedAt: "2026-01-01T00:00:00Z",
      terminalSentAt: null,
      terminalStatus: null,
      terminalBody: null,
      pendingTerminalStatus: null,
      pendingTerminalBody: null,
      pendingTerminalPreparedAt: null,
    });

    const match = tracker.getPendingForRepo("/tmp/repo-a");
    assert.ok(match);
    assert.equal(match!.taskId, "T6");
  });

  it("rejects registration of in-flight duplicate task", () => {
    const tracker = new DelegationTaskTracker();
    tracker.register({
      taskId: "T5",
      sessionId: "s5",
      repoPath: "/tmp",
      receiptPath: "/tmp/r5.json",
      status: "ACKED",
      ackedAt: "2026-01-01T00:00:00Z",
      terminalSentAt: null,
      terminalStatus: null,
      terminalBody: null,
      pendingTerminalStatus: null,
      pendingTerminalBody: null,
      pendingTerminalPreparedAt: null,
    });

    const dup = tracker.register({
      taskId: "T5",
      sessionId: "s5b",
      repoPath: "/tmp",
      receiptPath: "/tmp/r5b.json",
      status: "ACKED",
      ackedAt: "2026-01-01T00:00:00Z",
      terminalSentAt: null,
      terminalStatus: null,
      terminalBody: null,
      pendingTerminalStatus: null,
      pendingTerminalBody: null,
      pendingTerminalPreparedAt: null,
    });
    assert.equal(dup, false, "should reject duplicate in-flight task");

    // After terminal, re-registration should work
    tracker.markTerminal("T5", "EVIDENCE_PACK", "done");
    const ok = tracker.register({
      taskId: "T5",
      sessionId: "s5c",
      repoPath: "/tmp",
      receiptPath: "/tmp/r5c.json",
      status: "ACKED",
      ackedAt: "2026-01-01T00:00:00Z",
      terminalSentAt: null,
      terminalStatus: null,
      terminalBody: null,
      pendingTerminalStatus: null,
      pendingTerminalBody: null,
      pendingTerminalPreparedAt: null,
    });
    assert.ok(ok, "should allow re-registration after terminal");
  });

  it("persists tasks to disk and restores them in a new tracker instance", () => {
    const dir = makeTempDir();
    const statePath = path.join(dir, "delegation-state.json");

    const tracker = new DelegationTaskTracker(statePath);
    tracker.register({
      taskId: "T-PERSIST-1",
      sessionId: "s-persist-1",
      repoPath: "/tmp",
      receiptPath: "/tmp/persist.receipt.json",
      status: "ACKED",
      ackedAt: "2026-01-01T00:00:00Z",
      lastActivityAt: "2026-01-01T00:05:00Z",
      terminalSentAt: null,
      terminalStatus: null,
      terminalBody: null,
      pendingTerminalStatus: null,
      pendingTerminalBody: null,
      pendingTerminalPreparedAt: null,
    });

    const restored = new DelegationTaskTracker(statePath).get("T-PERSIST-1");
    assert.ok(restored);
    assert.equal(restored!.sessionId, "s-persist-1");
    assert.equal(restored!.lastActivityAt, "2026-01-01T00:05:00Z");
  });

  it("can reopen a terminal task so continuation can emit another terminal receipt", () => {
    const tracker = new DelegationTaskTracker();
    tracker.register({
      taskId: "T-REOPEN-1",
      sessionId: "s-reopen-1",
      repoPath: "/tmp",
      receiptPath: "/tmp/reopen.json",
      status: "ACKED",
      ackedAt: "2026-01-01T00:00:00Z",
      terminalSentAt: null,
      terminalStatus: null,
      terminalBody: null,
      pendingTerminalStatus: null,
      pendingTerminalBody: null,
      pendingTerminalPreparedAt: null,
    });

    tracker.markTerminal("T-REOPEN-1", "EVIDENCE_PACK", "round one");
    const reopened = tracker.reopen("T-REOPEN-1", "RUNNING", "2026-01-01T00:10:00Z");

    assert.equal(reopened, true);
    const task = tracker.get("T-REOPEN-1");
    assert.ok(task);
    assert.equal(task!.status, "RUNNING");
    assert.equal(task!.terminalSentAt, null);
    assert.equal(task!.terminalStatus, null);
    assert.equal(task!.terminalBody, null);
    assert.equal(task!.lastActivityAt, "2026-01-01T00:10:00Z");
    assert.equal(tracker.pendingCount(), 1);
  });
});
