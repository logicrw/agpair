/**
 * Tests for bridge HTTP server hardening.
 *
 * Covers:
 *   - constantTimeEqual rejects mismatched tokens
 *   - constantTimeEqual accepts matching tokens
 *   - constantTimeEqual rejects empty strings
 *   - readBody enforced size limit (via createBridgeServer integration)
 *   - Auth bypass when token is empty (local-demo mode)
 *   - Auth rejection with wrong token
 *   - Auth acceptance with correct token
 */

import { describe, it, afterEach } from "node:test";
import * as assert from "node:assert/strict";
import * as http from "node:http";
import {
  constantTimeEqual,
  createBridgeServer,
  MAX_BODY_BYTES,
} from "../bridge/httpServer";
import { DelegationTaskTracker } from "../state/delegationTaskTracker";

// ── Unit tests for constantTimeEqual ────────────────────────────

describe("constantTimeEqual", () => {
  it("returns true for identical strings", () => {
    assert.equal(constantTimeEqual("my-secret-token", "my-secret-token"), true);
  });

  it("returns false for different strings of same length", () => {
    assert.equal(constantTimeEqual("aaaa", "bbbb"), false);
  });

  it("returns false for different lengths", () => {
    assert.equal(constantTimeEqual("short", "a-much-longer-string"), false);
  });

  it("returns false when first arg is empty", () => {
    assert.equal(constantTimeEqual("", "nonempty"), false);
  });

  it("returns false when second arg is empty", () => {
    assert.equal(constantTimeEqual("nonempty", ""), false);
  });

  it("returns false when both are empty", () => {
    assert.equal(constantTimeEqual("", ""), false);
  });
});

// ── Minimal stub services for integration tests ─────────────────

function makeStubConfig(authToken: string) {
  return {
    port: 0, // not used directly in tests
    authToken,
    taskExecService: {
      runTask: async () => ({ ok: true }),
      continueTask: async () => ({ ok: true }),
    } as any,
    healthService: {
      getHealth: () => ({ ok: true, status: "healthy" }),
    } as any,
    sessionStore: { get: () => null, remove: () => {} } as any,
    eventStore: {
      getPending: () => [],
      markDeliveredByIds: () => 0,
      push: () => {},
    } as any,
  };
}

function listenOnRandomPort(server: http.Server): Promise<number> {
  return new Promise((resolve, reject) => {
    server.listen(0, "127.0.0.1", () => {
      const addr = server.address();
      if (addr && typeof addr === "object") {
        resolve(addr.port);
      } else {
        reject(new Error("Failed to get server address"));
      }
    });
    server.on("error", reject);
  });
}

function httpRequest(
  port: number,
  options: {
    method: string;
    path: string;
    headers?: Record<string, string>;
    body?: string;
  },
): Promise<{ status: number; body: any }> {
  return new Promise((resolve, reject) => {
    const req = http.request(
      {
        hostname: "127.0.0.1",
        port,
        path: options.path,
        method: options.method,
        headers: {
          "Content-Type": "application/json",
          ...options.headers,
        },
      },
      (res) => {
        let data = "";
        res.on("data", (chunk: string) => (data += chunk));
        res.on("end", () => {
          try {
            resolve({ status: res.statusCode || 0, body: JSON.parse(data) });
          } catch {
            resolve({ status: res.statusCode || 0, body: data });
          }
        });
      },
    );
    req.on("error", reject);
    if (options.body) req.write(options.body);
    req.end();
  });
}

// ── Integration tests ───────────────────────────────────────────

describe("Bridge auth integration", () => {
  let server: http.Server;
  let port: number;

  afterEach(() => {
    if (server) server.close();
  });

  it("allows requests when authToken is empty (local-demo mode)", async () => {
    server = createBridgeServer(makeStubConfig(""));
    port = await listenOnRandomPort(server);
    const res = await httpRequest(port, { method: "GET", path: "/health" });
    assert.equal(res.status, 200);
    assert.equal(res.body.ok, true);
  });

  it("/health is always accessible even with token set", async () => {
    server = createBridgeServer(makeStubConfig("correct-token"));
    port = await listenOnRandomPort(server);
    // /health should be accessible WITHOUT auth
    const res = await httpRequest(port, { method: "GET", path: "/health" });
    assert.equal(res.status, 200);
    assert.equal(res.body.ok, true);
  });

  it("rejects mutating request with wrong bearer token", async () => {
    server = createBridgeServer(makeStubConfig("correct-token"));
    port = await listenOnRandomPort(server);
    const res = await httpRequest(port, {
      method: "POST",
      path: "/wake",
      headers: { Authorization: "Bearer wrong-token" },
      body: JSON.stringify({ wake_id: "w1", task_id: "t1", reason: "test" }),
    });
    assert.equal(res.status, 401);
    assert.equal(res.body.ok, false);
  });

  it("accepts mutating request with correct bearer token", async () => {
    server = createBridgeServer(makeStubConfig("correct-token"));
    port = await listenOnRandomPort(server);
    const res = await httpRequest(port, {
      method: "POST",
      path: "/wake",
      headers: { Authorization: "Bearer correct-token" },
      body: JSON.stringify({ wake_id: "w1", task_id: "t1", reason: "test" }),
    });
    assert.equal(res.status, 200);
    assert.equal(res.body.ok, true);
  });

  it("rejects mutating request with no auth header when token is set", async () => {
    server = createBridgeServer(makeStubConfig("my-secret"));
    port = await listenOnRandomPort(server);
    const res = await httpRequest(port, {
      method: "POST",
      path: "/wake",
      body: JSON.stringify({ wake_id: "w1", task_id: "t1", reason: "test" }),
    });
    assert.equal(res.status, 401);
  });

  it("cancel_task also releases pending delegation tracker state", async () => {
    const tracker = new DelegationTaskTracker();
    tracker.register({
      taskId: "TASK-CANCEL-TRACKER",
      sessionId: "sess-cancel-1",
      repoPath: "/tmp/repo-cancel",
      receiptPath: "/tmp/repo-cancel/.agpair/receipts/TASK-CANCEL-TRACKER.json",
      status: "ACKED",
      ackedAt: "2026-03-30T00:00:00Z",
      lastActivityAt: "2026-03-30T00:00:00Z",
      terminalSentAt: null,
      terminalStatus: null,
      terminalBody: null,
      pendingTerminalStatus: null,
      pendingTerminalBody: null,
      pendingTerminalPreparedAt: null,
    });

    server = createBridgeServer({
      ...makeStubConfig(""),
      delegationTracker: tracker,
    });
    port = await listenOnRandomPort(server);

    const res = await httpRequest(port, {
      method: "POST",
      path: "/cancel_task",
      body: JSON.stringify({ task_id: "TASK-CANCEL-TRACKER", attempt_no: 0 }),
    });

    assert.equal(res.status, 200);
    assert.equal(res.body.ok, true);
    assert.equal(tracker.pendingCount(), 0);
    assert.equal(tracker.get("TASK-CANCEL-TRACKER")?.status, "BLOCKED");
  });
});

describe("Bridge body size limit", () => {
  let server: http.Server;
  let port: number;

  afterEach(() => {
    if (server) server.close();
  });

  it("rejects POST bodies exceeding MAX_BODY_BYTES with 413", async () => {
    server = createBridgeServer(makeStubConfig(""));
    port = await listenOnRandomPort(server);

    // Create a body larger than MAX_BODY_BYTES
    const oversizedBody = "x".repeat(MAX_BODY_BYTES + 1024);
    const res = await httpRequest(port, {
      method: "POST",
      path: "/wake",
      body: oversizedBody,
    });
    assert.equal(res.status, 413);
  });

  it("accepts POST bodies within the size limit", async () => {
    server = createBridgeServer(makeStubConfig(""));
    port = await listenOnRandomPort(server);

    const smallBody = JSON.stringify({
      wake_id: "w1",
      task_id: "t1",
      reason: "test",
    });
    const res = await httpRequest(port, {
      method: "POST",
      path: "/wake",
      body: smallBody,
    });
    assert.equal(res.status, 200);
    assert.equal(res.body.ok, true);
  });

  it("exports MAX_BODY_BYTES as 1 MiB", () => {
    assert.equal(MAX_BODY_BYTES, 1 * 1024 * 1024);
  });
});
